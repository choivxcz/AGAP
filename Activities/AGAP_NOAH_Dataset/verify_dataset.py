"""Independent checks of the AGAP NOAH training dataset, for verification by the adviser.

Every check re-reads the published files and compares them with the original sources or with the documented rules.
Nothing in the dataset is changed.

Run:  ..\\.venv\\Scripts\\python.exe verify_dataset.py      (about 5 minutes; the GIS check calls the app's lookup)
Writes verification_results.json and prints a PASS/FAIL line per check.
"""
from __future__ import annotations

import hashlib
import json
import logging
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
FILES = {"training": HERE / "agap_noah_training.csv", "full": HERE / "agap_noah_dataset.csv",
         "raw": HERE / "agap_noah_dataset_raw.csv"}
REF_COLS = ["HouseholdID", "Barangay", "Latitude", "Longitude", "EventDate"]
PROFILE = ["Age", "HousingMaterial", "HouseMembers", "ChildrenCount", "ElderCount", "PWDCount"]
GIS = ["ElevationMeters", "SlopeDegrees", "DistanceToWaterwayM", "LocationType", "LandCoverType",
       "HistoricalWaterOccurrencePct"]
NOAH = ["FloodHazard5yr", "FloodHazard25yr", "FloodHazard100yr"]
HOUSE = ["HouseFloodHazard5yr", "HouseFloodHazard25yr", "HouseFloodHazard100yr"]
MGB_LEVEL = {"NotMapped": 0.0, "Moderate": 2.0, "High": 2.5, "VeryHigh": 3.0}   # documented in build_noah_dataset.py
RAIN = ["Rainfall24hMm", "Rainfall3DayMm", "Rainfall7DayMm"]
CATEGORIES = {
    "HousingMaterial": {"Concrete", "SemiConcrete", "LightMaterials"},
    "LocationType": {"Riverside", "NearWaterway", "Inland"},
    "LandCoverType": {"TreeCover", "Shrubland", "Grassland", "Cropland", "BuiltUp", "BareSparseVegetation",
                      "PermanentWater", "HerbaceousWetland", "Mangroves"},
    **{c: {"None", "Low", "Medium", "High"} for c in NOAH + HOUSE},
    "MGBFloodSusceptibility": set(MGB_LEVEL),
    "RiskLevel": {"Low", "Medium", "High"},
}

read = lambda p: pd.read_csv(p, keep_default_na=False, na_values=[""])   # "None" is a NOAH class, not missing
train, full = read(FILES["training"]), read(FILES["full"])
checks: list[dict] = []


def check(group, name, ok, detail):
    checks.append({"group": group, "check": name, "result": "PASS" if ok else "FAIL", "detail": detail})
    print(f"[{'PASS' if ok else 'FAIL'}] {group} | {name}: {detail}", flush=True)


# ------------------------------------------------------------------ A. structure
check("Structure", "Training file has 22 inputs and the target", list(train.columns) == [c for c in full.columns if c not in REF_COLS],
      f"{train.shape[1]} columns: {train.shape[1] - 1} inputs + RiskLevel")
check("Structure", "Training file equals the full file without the 5 reference columns",
      train.equals(full.drop(columns=REF_COLS)), f"{len(train):,} rows compared cell by cell")
check("Structure", "Every household has 30 rainfall events",
      bool((full.groupby("HouseholdID").size() == 30).all()), f"{full.HouseholdID.nunique():,} households")
events = pd.read_csv(HERE / "rainfall_events.csv")
mix = full.merge(events[["EventDate", "EventType"]], on="EventDate").groupby("HouseholdID").EventType.value_counts().unstack()
check("Structure", "Event mix per household is 9 extreme, 9 heavy, 6 moderate, 6 light",
      bool(((mix.Extreme == 9) & (mix.Heavy == 9) & (mix.Moderate == 6) & (mix.Light == 6)).all()),
      "checked for every household")

# ------------------------------------------------------------------ B. completeness and validity
check("Quality", "No missing values", int(train.isna().sum().sum()) == 0, f"{int(train.isna().sum().sum())} missing cells")
dup = int(full.duplicated(["HouseholdID", "EventDate"]).sum())
check("Quality", "No duplicate household-event records", dup == 0, f"{dup} duplicates")
bad = {c: sorted(set(train[c]) - allowed) for c, allowed in CATEGORIES.items() if set(train[c]) - allowed}
check("Quality", "All categories are valid", not bad, "unexpected values: " + (str(bad) if bad else "none"))
rules = {
    "Age 18 to 90": train.Age.between(18, 90),
    "HouseMembers 1 to 12": train.HouseMembers.between(1, 12),
    "Children + elders <= members": train.ChildrenCount + train.ElderCount <= train.HouseMembers,
    "PWD <= members": train.PWDCount <= train.HouseMembers,
    "Head is 60+ when an elder lives there": (train.ElderCount == 0) | (train.Age >= 60),
    "Slope 0 to 90 degrees": train.SlopeDegrees.between(0, 90),
    "Distance to waterway >= 0": train.DistanceToWaterwayM >= 0,
    "Water occurrence 0 to 100%": train.HistoricalWaterOccurrencePct.between(0, 100),
    "Rain: 24 h <= 3-day <= 7-day": (train.Rainfall24hMm <= train.Rainfall3DayMm + 0.01)
                                     & (train.Rainfall3DayMm <= train.Rainfall7DayMm + 0.01),
}
for name, ok in rules.items():
    check("Quality", name, bool(ok.all()), f"{int((~ok).sum())} rows break the rule")
loc = pd.cut(train.DistanceToWaterwayM, [-np.inf, 100, 300, np.inf], right=False,
             labels=["Riverside", "NearWaterway", "Inland"]).astype(str)
check("Quality", "LocationType agrees with DistanceToWaterwayM", bool((loc == train.LocationType).all()),
      f"{int((loc != train.LocationType).sum())} mismatches (Riverside <100 m, NearWaterway 100-300 m, Inland >=300 m)")
const = full.groupby("HouseholdID")[PROFILE + GIS + NOAH + HOUSE + ["MGBFloodSusceptibility", "Latitude", "Longitude", "Barangay"]].nunique().max()
check("Quality", "Household and location values are the same in all of a household's records",
      bool((const == 1).all()), f"checked {len(const)} household-level columns")

# ------------------------------------------------------------------ C. agreement with the real sources
rain_src = pd.read_csv(ROOT / "nasa_power_daily_rainfall_features_1991_2025.csv").rename(columns={"date": "EventDate"})
r = full[["EventDate"] + RAIN].drop_duplicates().merge(rain_src, on="EventDate", how="left")
diff = max((r.Rainfall24hMm - r.rainfall_24h_mm).abs().max(), (r.Rainfall3DayMm - r.rainfall_3day_mm).abs().max(),
           (r.Rainfall7DayMm - r.rainfall_7day_mm).abs().max())
check("Sources", "Rainfall equals the NASA POWER record for the event date", bool(diff < 0.011),
      f"{len(r)} event dates compared; largest difference {diff:.3f} mm")

hh = full.drop_duplicates("HouseholdID").reset_index(drop=True)
pts = gpd.GeoDataFrame(hh[["HouseholdID", "Barangay"]], geometry=gpd.points_from_xy(hh.Longitude, hh.Latitude),
                       crs="EPSG:4326")
brgy = gpd.read_file(ROOT / "Sta_Cruz_Laguna_GIS_Download/output/barangays.geojson")[["BRGY_NAME", "geometry"]]
j = gpd.sjoin(pts, brgy, how="left", predicate="within")
check("Sources", "Every household pin lies inside its stated Santa Cruz barangay",
      bool((j.BRGY_NAME == j.Barangay).all()), f"{int((j.BRGY_NAME == j.Barangay).sum()):,} of {len(j):,} households")
# Flood hazard is per barangay: recompute each barangay's area shares directly from the clipped NOAH maps,
# take the highest class covering at least 25% of the barangay, and compare with every household's value.
check("Quality", "All households in a barangay have the same flood hazard classes",
      bool((hh.groupby("Barangay")[NOAH].nunique() == 1).all().all()), f"{hh.Barangay.nunique()} barangays")
brgy_utm = brgy.to_crs("EPSG:32651")
brgy_utm["AreaM2"] = brgy_utm.area
barangay_hazard = brgy_utm[["BRGY_NAME"]].rename(columns={"BRGY_NAME": "Barangay"}).copy()
print("Recomputing barangay flood-zone shares from the NOAH maps ...", flush=True)
for rp, col in zip(["5yr", "25yr", "100yr"], NOAH):
    zones = gpd.read_file(ROOT / f"NOAH_Flood_Hazard_Laguna/santa_cruz_flood_hazard_{rp}.geojson")[["HazardClass", "geometry"]]
    inter = gpd.overlay(brgy_utm[["BRGY_NAME", "geometry"]], zones.to_crs("EPSG:32651"), how="intersection")
    share = (inter.assign(A=inter.area).groupby(["BRGY_NAME", "HazardClass"]).A.sum().unstack(fill_value=0)
             .reindex(columns=["Low", "Medium", "High"], fill_value=0)
             .reindex(brgy_utm.BRGY_NAME, fill_value=0).div(brgy_utm.set_index("BRGY_NAME").AreaM2, axis=0) * 100)
    cls = pd.Series(np.select([share.High >= 25, share.Medium + share.High >= 25, share.sum(axis=1) >= 25],
                              ["High", "Medium", "Low"], default="None"), index=share.index)
    for c in ["Low", "Medium", "High"]:
        barangay_hazard[f"Pct{c}_{rp}"] = barangay_hazard.Barangay.map(share[c]).round(2).values
    barangay_hazard[f"PctNone_{rp}"] = (100 - share.sum(axis=1)).clip(lower=0).round(2).values
    barangay_hazard[col] = barangay_hazard.Barangay.map(cls).values
    got = hh.Barangay.map(cls)
    check("Sources", f"{col} equals the barangay's class (at least 25% of its area) on the Project NOAH {rp} map",
          bool((got.values == hh[col].values).all()),
          f"{int((got.values == hh[col].values).sum()):,} of {len(hh):,} households")

sys.path.insert(0, str(ROOT / "flood_risk_system"))
logging.disable(logging.WARNING)
from app.gis import discovery                      # noqa: E402
from app.gis.extraction import extract_features    # noqa: E402
LANDCOVER = {10: "TreeCover", 20: "Shrubland", 30: "Grassland", 40: "Cropland", 50: "BuiltUp",
             60: "BareSparseVegetation", 80: "PermanentWater", 90: "HerbaceousWetland", 95: "Mangroves"}
cov = discovery.get_coverage()
print("Comparing terrain values with the app's own map lookup (a few minutes) ...", flush=True)
worst, lc_bad = 0.0, 0
for h in hh.itertuples():
    f = extract_features(cov, h.Latitude, h.Longitude)["features"]
    for col in ["ElevationMeters", "SlopeDegrees", "DistanceToWaterwayM", "HistoricalWaterOccurrencePct"]:
        worst = max(worst, abs(float(f[col]["value"]) - getattr(h, col)))
    lc_bad += LANDCOVER.get(f["LandCoverCode"]["value"]) != h.LandCoverType
check("Sources", "Terrain values equal the system's map lookup at the pin (Copernicus DEM, OSM, ESA, JRC rasters)",
      worst <= 0.005 + 1e-9 and lc_bad == 0,
      f"{len(hh):,} households; largest numeric difference {worst:.4f} (2-decimal rounding); land cover mismatches {lc_bad}")

# ------------------------------------------------------------------ D. label rule
rain_ok = rain_src.dropna()
amax = rain_ok.groupby(pd.to_datetime(rain_ok.EventDate).dt.year).rainfall_24h_mm.max()
beta = np.sqrt(6) * amax.std() / np.pi
mu = amax.mean() - 0.5772 * beta
R = {T: mu - beta * np.log(-np.log(1 - 1 / T)) for T in (5, 25, 100)}
vul = pd.read_csv(ROOT / "Activities/AGAP_Dataset/label_audit.csv").groupby("HouseholdID").VulnerabilityScore.first() / 100
HV = {"None": 0, "Low": 1, "Medium": 2, "High": 3}
d = full.merge(rain_src, on="EventDate")
rr = d.rainfall_24h_mm + 0.30 * (d.rainfall_3day_mm - d.rainfall_24h_mm)
# The label uses the NOAH class at the house: recompute it with a point-in-polygon lookup on the NOAH maps.
house = {}
for rp in ["5yr", "25yr", "100yr"]:
    zones = gpd.read_file(ROOT / f"NOAH_Flood_Hazard_Laguna/santa_cruz_flood_hazard_{rp}.geojson")[["HazardClass", "geometry"]]
    hit = gpd.sjoin(pts, zones.to_crs("EPSG:4326"), how="left", predicate="within")
    house[rp] = pd.Series(hit.HazardClass.map(HV).fillna(0).groupby(level=0).max().values, index=hh.HouseholdID)
INV = {0: "None", 1: "Low", 2: "Medium", 3: "High"}
for rp, col in zip(["5yr", "25yr", "100yr"], HOUSE):
    got = hh.HouseholdID.map(house[rp]).map(INV)
    check("Sources", f"{col} equals the NOAH {rp} class at the house (point-in-polygon)",
          bool((got.values == hh[col].values).all()), f"{int((got.values == hh[col].values).sum()):,} of {len(hh):,} households")
# MGB at the house, recomputed independently (geopandas, repaired geometries, worst class where polygons overlap).
mgb_zones = gpd.read_file(ROOT / "MGB_Flood_Susceptibility/mgb_flood_susceptibility_sta_cruz_laguna.geojson")
mgb_zones["geometry"] = mgb_zones.geometry.make_valid()
mgb_zones["Level"] = mgb_zones.FloodSusc.map({"MF": 1, "HF": 2, "VHF": 3})
hit = gpd.sjoin(pts, mgb_zones.to_crs("EPSG:4326")[["Level", "geometry"]], how="left", predicate="intersects")
mgb_house = pd.Series(hit.Level.fillna(0).groupby(level=0).max().map({0: "NotMapped", 1: "Moderate", 2: "High", 3: "VeryHigh"}).values,
                      index=hh.HouseholdID)
same = hh.HouseholdID.map(mgb_house).values == hh.MGBFloodSusceptibility.values
check("Sources", "MGBFloodSusceptibility equals the MGB class at the house (point-in-polygon)", bool(same.all()),
      f"{int(same.sum()):,} of {len(hh):,} households")
h5, h25, h100 = (d.HouseholdID.map(house[rp]).astype(float) for rp in ["5yr", "25yr", "100yr"])
c01 = lambda v: np.clip(v, 0, 1)
hz = np.select([rr < R[5], rr < R[25], rr < R[100]],
               [h5 * rr / R[5], h5 + (h25 - h5) * c01((rr - R[5]) / (R[25] - R[5])),
                h25 + (h100 - h25) * c01((rr - R[25]) / (R[100] - R[25]))], default=h100)
hz = np.maximum(hz, d.HouseholdID.map(mgb_house).map(MGB_LEVEL).astype(float) * c01(rr / R[25]))   # worse of NOAH and MGB
# Terrain flood index from the published terrain columns (documented in build_noah_dataset.py / dataset_card.md).
LC = {"BuiltUp": 1.0, "HerbaceousWetland": 1.0, "PermanentWater": 1.0, "Mangroves": 1.0, "Cropland": 0.6,
      "BareSparseVegetation": 0.6, "Grassland": 0.5, "Shrubland": 0.4, "TreeCover": 0.3}
ti = (0.35 * c01((20 - d.ElevationMeters) / 17) + 0.25 * c01((500 - d.DistanceToWaterwayM) / 450)
      + 0.15 * c01((5 - d.SlopeDegrees) / 5) + 0.15 * d.LandCoverType.map(LC) + 0.10 * c01(d.HistoricalWaterOccurrencePct / 20))
hz = np.minimum(3, np.maximum(hz * (0.75 + 0.5 * ti), ti * c01(rr / R[25])))
score = 100 * hz / 3 * (0.40 + 0.60 * d.HouseholdID.map(vul))
lab = pd.cut(score, [-np.inf, 15, 40, np.inf], right=False, labels=["Low", "Medium", "High"]).astype(str)
match = int((lab.values == d.RiskLevel.values).sum())
check("Label", "RiskLevel can be recomputed from the documented rule (NOAH, MGB and terrain index at the house)", match == len(d),
      f"{match:,} of {len(d):,} labels reproduced (return levels 5/25/100-yr: "
      f"{R[5]:.1f} / {R[25]:.1f} / {R[100]:.1f} mm)")

# ------------------------------------------------------------------ outputs
num = train.select_dtypes("number")
stats = num.describe().T[["min", "25%", "50%", "mean", "75%", "max", "std"]].round(2)
freqs = {c: train[c].value_counts().to_dict() for c in CATEGORIES}
hashes = {k: {"file": p.name, "rows": int(sum(1 for _ in open(p, encoding="utf-8")) - 1),
              "bytes": p.stat().st_size, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for k, p in FILES.items()}

# Spot-check samples: the barangay flood-hazard table (with households per barangay) and rain dates of each event type.
barangay_hazard["Households"] = barangay_hazard.Barangay.map(hh.Barangay.value_counts()).fillna(0).astype(int)
spot_rain = (events.groupby("EventType", sort=False).head(2)[["EventDate", "EventType"] + RAIN])
out = {"files": hashes, "checks": checks, "numeric_stats": stats.reset_index().rename(columns={"index": "Variable"}).to_dict("records"),
       "category_counts": freqs, "barangay_hazard": barangay_hazard.sort_values("Barangay").to_dict("records"),
       "spot_rain": spot_rain.to_dict("records"),
       "sample_rows": train.head(8).to_dict("records"),
       "summary": {"passed": sum(c["result"] == "PASS" for c in checks), "total": len(checks)}}
(HERE / "verification_results.json").write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
print(f"\n{out['summary']['passed']} of {out['summary']['total']} checks passed. Saved verification_results.json")
