"""Build the Project AGAP flood-vulnerability dataset for Santa Cruz, Laguna (new dataset, v1).

Real Santa Cruz inputs (read-only, from this workspace):
  * Barangay boundaries ............ Sta_Cruz_Laguna_GIS_Download/output/barangays.geojson
  * Building footprints ............ flood_risk_system/data/osm/buildings_overture.geojson (Google + Microsoft)
  * Elevation / slope / waterway distance / land cover rasters ... Sta_Cruz_Laguna_GIS_Download/output/*.tif
  * Flood susceptibility per barangay ... sources/flood_susceptibility_exposure_table_3_12.csv
                                       (Table 3.12 "Population Flooding Susceptibility and Exposure", p. 109)
  * MGB flood-susceptibility map ... flood_risk_system/data/assessment_sources/mgb_flood_susceptibility_sta_cruz_laguna.geojson
                                       (only used to decide WHICH households inside a barangay are the exposed ones)
  * NASA POWER daily rainfall ...... nasa_power_daily_rainfall_features_1991_2025.csv (point in Brgy. Calios)

Simulated parts (clearly labelled): household members, children, elders, PWD, head age, housing material,
and the RiskLevel label, which is computed with the documented hazard x vulnerability rule below.

Run:  python build_agap_dataset.py
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
from matplotlib.path import Path as MplPath
from pyproj import Transformer
from rasterio.features import rasterize
from rasterio.warp import transform_geom

SEED = 20261027
N_HOUSEHOLDS = 8000
EVENTS_PER_HOUSEHOLD = 10            # 3 heavy + 3 moderate + 4 light rainfall events per household
LABEL_NOISE = 0.005                  # share of labels moved to a neighbouring class (assessor inconsistency)
MISSING_AGE = 0.015                  # simulated survey non-response (household level)
MISSING_MATERIAL = 0.010
DUPLICATE_ROWS = 0.005               # simulated double-encoded survey rows
LOW_MEDIUM_CUT, MEDIUM_HIGH_CUT = 20.0, 40.0   # RiskScore cut-offs (0-100 scale)

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SRC = {
    "barangays": ROOT / "Sta_Cruz_Laguna_GIS_Download/output/barangays.geojson",
    "buildings": ROOT / "flood_risk_system/data/osm/buildings_overture.geojson",
    "elevation": ROOT / "Sta_Cruz_Laguna_GIS_Download/output/elevation_m.tif",
    "slope": ROOT / "Sta_Cruz_Laguna_GIS_Download/output/slope_degrees.tif",
    "distance": ROOT / "Sta_Cruz_Laguna_GIS_Download/output/distance_to_osm_waterway_m.tif",
    "landcover": ROOT / "Sta_Cruz_Laguna_GIS_Download/output/land_cover_2021.tif",
    "mgb": ROOT / "flood_risk_system/data/assessment_sources/mgb_flood_susceptibility_sta_cruz_laguna.geojson",
    "rainfall": ROOT / "nasa_power_daily_rainfall_features_1991_2025.csv",
    "rain_meta": ROOT / "flood_risk_system/data/assessment_sources/nasa_power_daily_rainfall_features_metadata.json",
    "susceptibility_table": HERE / "sources/flood_susceptibility_exposure_table_3_12.csv",
}
OUT = HERE
LANDCOVER = {10: "TreeCover", 20: "Shrubland", 30: "Grassland", 40: "Cropland", 50: "BuiltUp",
             60: "BareSparseVegetation", 80: "PermanentWater", 90: "HerbaceousWetland", 95: "Mangroves"}
MGB_CODES = {"MF": 1, "HF": 2, "VHF": 3}

rng = np.random.default_rng(SEED)
to_utm = Transformer.from_crs("EPSG:4326", "EPSG:32651", always_xy=True)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def outer_rings(geom):
    return [geom["coordinates"][0]] if geom["type"] == "Polygon" else [p[0] for p in geom["coordinates"]]


# ----------------------------------------------------------------------------- 1. boundaries + buildings
brgy = json.loads(SRC["barangays"].read_text(encoding="utf-8"))
assert all(f["properties"]["CITYMUN"] == "Santa Cruz" and f["properties"]["PROVINCE"] == "Laguna" for f in brgy["features"])
polygons = {f["properties"]["BRGY_NAME"]: [MplPath(np.asarray(r)) for r in outer_rings(f["geometry"])] for f in brgy["features"]}

bld = json.loads(SRC["buildings"].read_text(encoding="utf-8"))["features"]
rows = []
for i, f in enumerate(bld):
    ring = np.asarray(f["geometry"]["coordinates"][0])
    x, y = to_utm.transform(ring[:, 0], ring[:, 1])
    area = 0.5 * abs(np.dot(x, np.roll(y, 1)) - np.dot(y, np.roll(x, 1)))
    rows.append((i, ring[:, 0].mean(), ring[:, 1].mean(), area, f["properties"]["src_dataset"]))
b = pd.DataFrame(rows, columns=["BuildingIndex", "Longitude", "Latitude", "FootprintM2", "BuildingSource"])
b["Barangay"] = None
for name, paths in polygons.items():
    inside = np.zeros(len(b), bool)
    for p in paths:
        inside |= p.contains_points(b[["Longitude", "Latitude"]].to_numpy())
    b.loc[inside & b.Barangay.isna(), "Barangay"] = name
n_buildings_total = len(b)
b = b[b.Barangay.notna() & b.FootprintM2.between(20, 600)].reset_index(drop=True)   # dwelling-sized footprints only

# ----------------------------------------------------------------------------- 2. sample GIS layers at buildings
b["X"], b["Y"] = to_utm.transform(b.Longitude.to_numpy(), b.Latitude.to_numpy())
xy = list(zip(b.X, b.Y))


def sample(path, nodata_values=(0.0, -9999.0)):
    with rasterio.open(path) as src:
        assert src.crs.to_epsg() == 32651
        vals = np.array([v[0] for v in src.sample(xy)], dtype="float64")
    vals[np.isin(vals, nodata_values) | ~np.isfinite(vals)] = np.nan
    return vals


b["ElevationMeters"] = sample(SRC["elevation"])
b["SlopeDegrees"] = sample(SRC["slope"], nodata_values=(-9999.0,))       # 0 deg is a valid slope
b["DistanceToWaterway"] = sample(SRC["distance"], nodata_values=(-9999.0,))
lc_codes = sample(SRC["landcover"])
b["LandCoverType"] = pd.Series(lc_codes).map(LANDCOVER)

with rasterio.open(SRC["landcover"]) as lc:
    shape, transform = lc.shape, lc.transform
mgb = json.loads(SRC["mgb"].read_text(encoding="utf-8"))["features"]
mgb_grid = np.zeros(shape, dtype="uint8")
for f in sorted(mgb, key=lambda f: MGB_CODES[f["properties"]["FloodSusc"]]):   # higher class burned last
    geom = transform_geom("EPSG:4326", "EPSG:32651", f["geometry"])
    burned = rasterize([(geom, MGB_CODES[f["properties"]["FloodSusc"]])], out_shape=shape, transform=transform, fill=0, dtype="uint8")
    mgb_grid = np.maximum(mgb_grid, burned)
rows_i, cols_i = rasterio.transform.rowcol(transform, b.X.to_numpy(), b.Y.to_numpy())
b["MGBClass"] = mgb_grid[np.asarray(rows_i), np.asarray(cols_i)]          # 0 none, 1 MF, 2 HF, 3 VHF

# a dwelling needs an elevation value and must not sit on open water
b = b[b.ElevationMeters.notna() & (b.LandCoverType != "PermanentWater") & b.LandCoverType.notna()].reset_index(drop=True)

# ----------------------------------------------------------------------------- 3. choose households
# Buildings are sampled uniformly, so each barangay gets households in proportion to its dwellings.
chosen = rng.choice(len(b), size=N_HOUSEHOLDS, replace=False)
hh = b.iloc[np.sort(chosen)].reset_index(drop=True)
hh.insert(0, "HouseholdID", [f"AGAP-SCL-{i:05d}" for i in range(1, len(hh) + 1)])
hh["LocationType"] = pd.cut(hh.DistanceToWaterway, [-np.inf, 100, 300, np.inf], right=False,
                            labels=["Riverside", "NearWaterway", "Inland"]).astype("object")

# FloodSusceptibility from Table 3.12: in each barangay, the share of households rated High / Moderate equals the
# table's exposure percentage. The households placed in those classes are the most hazard-prone ones in that
# barangay (MGB class first, then lower ground, then closer to a waterway). Barangays not in the table -> Low.
fs_table = pd.read_csv(SRC["susceptibility_table"])
assert set(fs_table.Barangay) <= set(polygons), "Table 3.12 barangay not found in the boundary file"
share = fs_table.pivot_table(index="Barangay", columns="FloodSusceptibility", values="ExposurePct", aggfunc="sum").fillna(0) / 100
proneness = (2.0 * hh.MGBClass + np.clip((20 - hh.ElevationMeters) / 18, 0, 1)
             + np.exp(-hh.DistanceToWaterway.fillna(hh.DistanceToWaterway.median()) / 250) + rng.uniform(0, 1e-3, len(hh)))
hh["FloodSusceptibility"] = "Low"
calibration = []
for name, grp in hh.groupby("Barangay"):
    order = proneness.loc[grp.index].sort_values(ascending=False).index
    n_high = int(round(share.get("High", pd.Series()).get(name, 0) * len(grp)))
    n_mod = int(round(share.get("Moderate", pd.Series()).get(name, 0) * len(grp)))
    hh.loc[order[:n_high], "FloodSusceptibility"] = "High"
    hh.loc[order[n_high:n_high + n_mod], "FloodSusceptibility"] = "Moderate"
    calibration.append({"Barangay": name, "Households": len(grp),
                        "Table High %": round(100 * share.get("High", pd.Series()).get(name, 0), 2),
                        "Dataset High %": round(100 * n_high / len(grp), 2),
                        "Table Moderate %": round(100 * share.get("Moderate", pd.Series()).get(name, 0), 2),
                        "Dataset Moderate %": round(100 * n_mod / len(grp), 2)})
pd.DataFrame(calibration).to_csv(OUT / "susceptibility_calibration.csv", index=False)

# ----------------------------------------------------------------------------- 4. simulated household profile
n = len(hh)
size_p = np.array([7.5, 13.5, 18.5, 19.0, 15.0, 10.5, 6.5, 4.0, 2.5, 1.5, 1.0, 0.5])  # sizes 1-12, mean ~4.2
hh["HouseMembers"] = rng.choice(np.arange(1, 13), size=n, p=size_p / size_p.sum())
hh["ChildrenCount"] = rng.binomial(hh.HouseMembers - 1, 0.30)
hh["ElderCount"] = rng.binomial(hh.HouseMembers - hh.ChildrenCount, 0.12)
hh["PWDCount"] = rng.binomial(hh.HouseMembers, 0.03)
hh["Age"] = np.clip(np.rint(rng.normal(47, 13, n)), 18, 90).astype(int)          # household-head age
hh.loc[hh.ElderCount > 0, "Age"] = np.maximum(hh.loc[hh.ElderCount > 0, "Age"], rng.integers(60, 86, (hh.ElderCount > 0).sum()))
# housing material: smaller footprints and riverside/non-built-up plots more often light material
logit_light = -1.6 + 1.1 * (hh.FootprintM2 < 45) + 0.8 * (hh.LocationType == "Riverside") + 0.5 * (hh.LandCoverType != "BuiltUp")
logit_semi = -0.4 + 0.6 * (hh.FootprintM2 < 80)
pl, ps = np.exp(logit_light), np.exp(logit_semi)
probs = np.column_stack([pl, ps, np.ones(n)]) / (pl + ps + 1).to_numpy()[:, None]
hh["HousingMaterial"] = [rng.choice(["LightMaterials", "SemiConcrete", "Concrete"], p=p) for p in probs]

# ----------------------------------------------------------------------------- 5. rainfall events (real NASA POWER)
rain = pd.read_csv(SRC["rainfall"]).dropna().reset_index(drop=True)
rain["date"] = pd.to_datetime(rain["date"])


def pick_separated(frame, k, min_gap_days=10):
    picked = []
    for _, r in frame.iterrows():
        if all(abs((r.date - p.date).days) >= min_gap_days for p in picked):
            picked.append(r)
        if len(picked) == k:
            break
    return pd.DataFrame(picked)


q75, q99 = rain.rainfall_3day_mm.quantile([0.75, 0.99])
heavy = pick_separated(rain.sort_values("rainfall_3day_mm", ascending=False), 20)
moderate = pick_separated(rain[rain.rainfall_3day_mm.between(q75, q99)].sample(frac=1, random_state=SEED), 20)
light = pick_separated(rain[rain.rainfall_3day_mm < q75].sample(frac=1, random_state=SEED + 1), 20)
events = pd.concat([heavy.assign(EventType="Heavy"), moderate.assign(EventType="Moderate"),
                    light.assign(EventType="Light")], ignore_index=True)
events = events.rename(columns={"date": "EventDate", "rainfall_24h_mm": "Rainfall24hMm",
                                "rainfall_3day_mm": "Rainfall3DayMm", "rainfall_7day_mm": "Rainfall7DayMm"})

pairs = []
for hid in hh.HouseholdID:
    for etype, k in (("Heavy", 3), ("Moderate", 3), ("Light", 4)):
        idx = events.index[events.EventType == etype]
        pairs += [(hid, j) for j in rng.choice(idx, k, replace=False)]
pairs = pd.DataFrame(pairs, columns=["HouseholdID", "EventIndex"])
df = pairs.merge(hh, on="HouseholdID").merge(events, left_on="EventIndex", right_index=True)

# ----------------------------------------------------------------------------- 6. RiskLevel rule (documented)
def clip01(v):
    return np.clip(v, 0, 1)


rain_term = (0.60 * clip01(df.Rainfall3DayMm / 150) + 0.25 * clip01(df.Rainfall24hMm / 80)
             + 0.15 * clip01(df.Rainfall7DayMm / 250))
fs_term = df.FloodSusceptibility.map({"Low": 0.0, "Moderate": 0.5, "High": 1.0})
low_lying = clip01((20 - df.ElevationMeters) / 18)
near_water = np.exp(-df.DistanceToWaterway.fillna(df.DistanceToWaterway.median()) / 250)
flat = clip01((3 - df.SlopeDegrees.fillna(1.0)) / 3)
lc_term = df.LandCoverType.map({"HerbaceousWetland": 1.0, "BuiltUp": 0.6, "Cropland": 0.5, "Grassland": 0.4,
                                "BareSparseVegetation": 0.3, "Shrubland": 0.25, "TreeCover": 0.2, "Mangroves": 0.8})
susceptibility = 0.30 * fs_term + 0.25 * low_lying + 0.25 * near_water + 0.10 * flat + 0.10 * lc_term
hazard = susceptibility * (0.30 + 0.70 * rain_term)                     # rain triggers the terrain hazard

material = df.HousingMaterial.map({"Concrete": 0.1, "SemiConcrete": 0.5, "LightMaterials": 1.0})
dependents = (df.ChildrenCount + df.ElderCount + df.PWDCount).clip(upper=df.HouseMembers) / df.HouseMembers
vulnerability = (0.40 * material + 0.25 * dependents + 0.15 * clip01(df.PWDCount / 2)
                 + 0.10 * clip01((df.HouseMembers - 1) / 9) + 0.10 * (df.Age >= 60))
df["HazardScore"] = 100 * hazard
df["VulnerabilityScore"] = 100 * vulnerability
df["RiskScore"] = 100 * hazard * (0.45 + 0.55 * vulnerability) / 0.80   # rescaled so the top end nears 100
clean_label = pd.cut(df.RiskScore, [-np.inf, LOW_MEDIUM_CUT, MEDIUM_HIGH_CUT, np.inf], right=False,
                     labels=["Low", "Medium", "High"]).astype(str)
flip = rng.random(len(df)) < LABEL_NOISE
neighbour = {"Low": ["Medium"], "Medium": ["Low", "High"], "High": ["Medium"]}
df["RiskLevel"] = [rng.choice(neighbour[c]) if f else c for c, f in zip(clean_label, flip)]
df["LabelNoiseApplied"] = flip

# ----------------------------------------------------------------------------- 7. survey imperfections
miss_age = set(rng.choice(hh.HouseholdID, int(MISSING_AGE * n), replace=False))
miss_mat = set(rng.choice(hh.HouseholdID, int(MISSING_MATERIAL * n), replace=False))
df.loc[df.HouseholdID.isin(miss_age), "Age"] = np.nan
df.loc[df.HouseholdID.isin(miss_mat), "HousingMaterial"] = np.nan
df["EventDate"] = df.EventDate.dt.strftime("%Y-%m-%d")
df = df.sort_values(["HouseholdID", "EventDate"]).reset_index(drop=True)
dupes = df.sample(frac=DUPLICATE_ROWS, random_state=SEED)
df = pd.concat([df, dupes]).sort_values(["HouseholdID", "EventDate"], kind="stable").reset_index(drop=True)

# ----------------------------------------------------------------------------- 8. write outputs
MAIN = ["HouseholdID", "Barangay", "Latitude", "Longitude", "EventDate",
        "Age", "HousingMaterial", "LocationType", "LandCoverType", "FloodSusceptibility",
        "HouseMembers", "ChildrenCount", "ElderCount", "PWDCount",
        "ElevationMeters", "SlopeDegrees", "DistanceToWaterway",
        "Rainfall24hMm", "Rainfall3DayMm", "Rainfall7DayMm", "RiskLevel"]
main = df[MAIN].copy()
main["Age"] = main.Age.astype("Int64")
for c in ["ElevationMeters", "SlopeDegrees", "DistanceToWaterway"]:
    main[c] = main[c].round(2)
main["Latitude"], main["Longitude"] = main.Latitude.round(6), main.Longitude.round(6)
main.to_csv(OUT / "agap_flood_vulnerability_dataset.csv", index=False)

audit = df[["HouseholdID", "EventDate", "BuildingSource", "FootprintM2", "EventType", "HazardScore",
            "VulnerabilityScore", "RiskScore", "LabelNoiseApplied"]].round(3)
audit.drop_duplicates(["HouseholdID", "EventDate"]).to_csv(OUT / "label_audit.csv", index=False)
events.drop(columns="EventIndex", errors="ignore").assign(EventDate=events.EventDate.dt.strftime("%Y-%m-%d")) \
      .to_csv(OUT / "rainfall_events.csv", index=False)

dictionary = pd.DataFrame([
    ("HouseholdID", "ID", "text", "", "Generated", "Household identifier (one real building footprint per household)"),
    ("Barangay", "ID", "text", "", "Real boundary", "Santa Cruz barangay containing the building"),
    ("Latitude", "ID", "float", "deg", "Real building", "Building-footprint centroid latitude (WGS84)"),
    ("Longitude", "ID", "float", "deg", "Real building", "Building-footprint centroid longitude (WGS84)"),
    ("EventDate", "ID", "date", "", "NASA POWER", "Historical rainfall event date (UTC)"),
    ("Age", "Feature", "int", "years", "Simulated", "Age of household head; at least 60 when an elder lives in the home"),
    ("HousingMaterial", "Feature", "category", "", "Simulated", "Concrete / SemiConcrete / LightMaterials"),
    ("LocationType", "Feature", "category", "", "Derived (real)", "Riverside <100 m, NearWaterway 100-300 m, Inland >=300 m from OSM waterway"),
    ("LandCoverType", "Feature", "category", "", "ESA WorldCover 2021", "Land cover at the building (10 m)"),
    ("FloodSusceptibility", "Feature", "category", "", "Table 3.12 (per-barangay exposure)", "High / Moderate / Low; per-barangay shares follow Table 3.12, Low = not listed as exposed"),
    ("HouseMembers", "Feature", "int", "people", "Simulated", "Household size 1-12 (mean about 4.2)"),
    ("ChildrenCount", "Feature", "int", "people", "Simulated", "Members under 18"),
    ("ElderCount", "Feature", "int", "people", "Simulated", "Members aged 60+"),
    ("PWDCount", "Feature", "int", "people", "Simulated", "Persons with disability (may overlap children/elders)"),
    ("ElevationMeters", "Feature", "float", "m", "Copernicus DEM 30 m", "Ground elevation at the building"),
    ("SlopeDegrees", "Feature", "float", "deg", "Derived from DEM", "Terrain slope at the building"),
    ("DistanceToWaterway", "Feature", "float", "m", "OSM waterways", "Distance from building to nearest mapped waterway"),
    ("Rainfall24hMm", "Feature", "float", "mm", "NASA POWER", "Rainfall on the event date"),
    ("Rainfall3DayMm", "Feature", "float", "mm", "NASA POWER", "3-day total including event date"),
    ("Rainfall7DayMm", "Feature", "float", "mm", "NASA POWER", "7-day total including event date"),
    ("RiskLevel", "Target", "category", "", "Rule-based (simulated)", "Low / Medium / High from RiskScore cut-offs 20 and 40"),
], columns=["Column", "Role", "Type", "Unit", "Origin", "Description"])
dictionary.to_csv(OUT / "data_dictionary.csv", index=False)

meta = {
    "name": "Project AGAP flood vulnerability dataset - Santa Cruz, Laguna", "version": "1.0", "seed": SEED,
    "rows": len(main), "households": int(main.HouseholdID.nunique()), "barangays": int(main.Barangay.nunique()),
    "rainfall_events": int(main.EventDate.nunique()), "buildings_in_source": n_buildings_total,
    "eligible_buildings": len(b), "class_counts": main.RiskLevel.value_counts().to_dict(),
    "flood_susceptibility_households": hh.FloodSusceptibility.value_counts().to_dict(),
    "susceptibility_source": "Table 3.12 Population Flooding Susceptibility and Exposure (p. 109); see susceptibility_calibration.csv",
    "simulated_imperfections": {"label_noise_rate": LABEL_NOISE, "labels_flipped": int(flip.sum()),
                                "missing_age_households": len(miss_age), "missing_material_households": len(miss_mat),
                                "duplicate_rows_added": len(dupes)},
    "risk_rule": {"cutoffs": [LOW_MEDIUM_CUT, MEDIUM_HIGH_CUT],
                  "formula": "RiskScore = 100 * Hazard * (0.45 + 0.55 * Vulnerability) / 0.80; see dataset_card.md"},
    "sources": {k: {"path": str(v.relative_to(ROOT)), "sha256": sha256(v)} for k, v in SRC.items()},
}
(OUT / "generation_metadata.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
print(json.dumps({k: meta[k] for k in ["rows", "households", "barangays", "rainfall_events", "eligible_buildings",
                                       "class_counts", "flood_susceptibility_households", "simulated_imperfections"]}, indent=2))
