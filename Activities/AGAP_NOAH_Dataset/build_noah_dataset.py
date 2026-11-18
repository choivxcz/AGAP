"""Build the Project AGAP NOAH flood-risk dataset for Santa Cruz, Laguna (v2.8).

Hazard comes from Project NOAH flood hazard maps (5-, 25-, 100-year rain), summarised per barangay (area-based).
Every location feature is a lookup at Latitude/Longitude, so the app can compute the same values for a user's pin:
  * Elevation, slope, waterway distance, land cover, historical water ... flood_risk_system app/gis/extraction.py
  * FloodHazard5yr / 25yr / 100yr ........ the barangay containing the pin -> its NOAH class covering at least 25% of the barangay
  * HouseFloodHazard5yr / 25yr / 100yr ... the NOAH class at the house itself (point-in-polygon on the NOAH maps)
  * MGBFloodSusceptibility ............... the MGB flood susceptibility class at the house (app/mgb.py)
The RiskLevel label uses the NOAH class at the house. v2.5 gave the model only the barangay class (macro F1 0.61);
v2.6 also gives it the class at the house, which the system can read at the user's pin. v2.7 adds MGB flood
susceptibility, which marks flood-prone areas in the town centre that NOAH's rain simulations leave out; the event
flood hazard is the worse of NOAH and MGB. v2.8 adds a terrain flood index (elevation, distance to waterway, slope,
land cover, past surface water) that adjusts the mapped hazard and gives unmapped low-lying houses a small hazard.
Households (real building locations + simulated profiles) are reused from AGAP v1.0. Rainfall events are re-selected
from NASA POWER by 24-hour rain, because NOAH's scenarios are defined by 24-hour rainfall.

Outputs: agap_noah_dataset.csv (clean, ready to train), agap_noah_dataset_raw.csv (with flaws for cleaning practice),
         label_audit.csv, rainfall_events.csv, data_dictionary.csv, generation_metadata.json

Run:  ..\\.venv\\Scripts\\python.exe build_noah_dataset.py      (about 4 minutes)
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

SEED = 20261103
EVENTS = {"Extreme": 15, "Heavy": 15, "Moderate": 15, "Light": 15}   # rainfall events in the pool
PER_HOUSEHOLD = {"Extreme": 9, "Heavy": 9, "Moderate": 6, "Light": 6}   # 30 of the 60 events per household
LABEL_NOISE, DUPLICATE_ROWS = 0.005, 0.005
LOW_MEDIUM_CUT, MEDIUM_HIGH_CUT = 15.0, 40.0
HAZARD_VALUE = {"None": 0, "Low": 1, "Medium": 2, "High": 3}

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SRC = {
    "agap_households": ROOT / "Activities/AGAP_Dataset/agap_flood_vulnerability_dataset.csv",
    "agap_label_audit": ROOT / "Activities/AGAP_Dataset/label_audit.csv",
    "rainfall": ROOT / "nasa_power_daily_rainfall_features_1991_2025.csv",
    "noah_barangay_area": ROOT / "NOAH_Flood_Hazard_Laguna/santa_cruz_flood_hazard_by_barangay.csv",
    **{f"noah_zones_{rp}": ROOT / f"NOAH_Flood_Hazard_Laguna/santa_cruz_flood_hazard_{rp}.geojson"
       for rp in ["5yr", "25yr", "100yr"]},
    "mgb": ROOT / "MGB_Flood_Susceptibility/mgb_flood_susceptibility_sta_cruz_laguna.geojson",
}
MGB_ANCHOR_YEARS = 25    # MGB depths are taken as reached in the 25-year rain and scaled down linearly below it
# Terrain flood index (0 = least, 1 = most flood-prone setting). Weights and cut-offs are documented assumptions.
TERRAIN_WEIGHTS = {"Elevation": 0.35, "Distance": 0.25, "Slope": 0.15, "LandCover": 0.15, "PastWater": 0.10}
TERRAIN_LANDCOVER = {"BuiltUp": 1.0, "HerbaceousWetland": 1.0, "PermanentWater": 1.0, "Mangroves": 1.0, "Cropland": 0.6,
                     "BareSparseVegetation": 0.6, "Grassland": 0.5, "Shrubland": 0.4, "TreeCover": 0.3}
TERRAIN_ADJUST = 0.25    # mapped hazard x (1 - 0.25 .. 1 + 0.25) for index 0 .. 1
TERRAIN_ONLY_MAX = 1.0   # hazard from terrain alone (NOAH "Low" level) for index 1 at the 25-year rain
rng = np.random.default_rng(SEED)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def clip01(v):
    return np.clip(v, 0, 1)


# ----------------------------------------------------------------------------- 1. households (from AGAP v1.0)
agap = pd.read_csv(SRC["agap_households"]).drop_duplicates("HouseholdID")
PROFILE = ["Age", "HousingMaterial", "HouseMembers", "ChildrenCount", "ElderCount", "PWDCount"]
hh = agap[["HouseholdID", "Barangay", "Latitude", "Longitude"] + PROFILE].reset_index(drop=True)
# Vulnerability per household comes from the v1.0 audit (computed before Age/HousingMaterial were blanked).
vuln = pd.read_csv(SRC["agap_label_audit"]).groupby("HouseholdID").VulnerabilityScore.first() / 100
hh["Vulnerability"] = hh.HouseholdID.map(vuln)
assert hh.Vulnerability.notna().all()

# ----------------------------------------------------------------------------- 2. GIS values at the pin (app lookup)
sys.path.insert(0, str(ROOT / "flood_risk_system"))
logging.disable(logging.WARNING)
from app.gis import discovery                      # noqa: E402
from app.gis.extraction import extract_features    # noqa: E402
os.environ["FLR_MGB_PATH"] = str(SRC["mgb"])
from app import mgb                                 # noqa: E402  (same lookup as the app)

LANDCOVER = {10: "TreeCover", 20: "Shrubland", 30: "Grassland", 40: "Cropland", 50: "BuiltUp",
             60: "BareSparseVegetation", 80: "PermanentWater", 90: "HerbaceousWetland", 95: "Mangroves"}
cov = discovery.get_coverage()
print("Reading GIS values with the app's extraction (a few minutes) ...", flush=True)
gis = []
for h in hh.itertuples():
    res = extract_features(cov, h.Latitude, h.Longitude)
    f = res["features"]
    assert res["barangay"] and res["barangay"]["name"] == h.Barangay
    gis.append({"ElevationMeters": f["ElevationMeters"]["value"], "SlopeDegrees": f["SlopeDegrees"]["value"],
                "DistanceToWaterwayM": f["DistanceToWaterwayM"]["value"],
                "LandCoverType": LANDCOVER.get(f["LandCoverCode"]["value"]),
                "HistoricalWaterOccurrencePct": f["HistoricalWaterOccurrencePct"]["value"]})
gis = pd.DataFrame(gis).astype({"ElevationMeters": float, "SlopeDegrees": float, "DistanceToWaterwayM": float,
                                "HistoricalWaterOccurrencePct": float})
hh = pd.concat([hh, gis], axis=1)
hh["LocationType"] = pd.cut(hh.DistanceToWaterwayM, [-np.inf, 100, 300, np.inf], right=False,
                            labels=["Riverside", "NearWaterway", "Inland"]).astype("object")

# ----------------------------------------------------------------------------- 3. NOAH flood hazard per barangay
# One class per barangay and rain scenario, read from the NOAH maps over the barangay's whole area.
# Class = the highest NOAH level that covers at least a quarter (25%) of the barangay, counting that level or worse
# (the upper-quartile hazard of the barangay). None = less than 25% of the barangay is inside NOAH's flood zones.
# Example: 25% Medium + 5% High -> 30% is Medium or worse -> Medium.
# (v2.3 averaged the classes over the whole barangay; unflooded land counted as 0, so the average hid the Medium and
# High zones, e.g. Oogong with 24% High area came out Low.)
# Every household in a barangay gets the same class. Area shares come from NOAH_Flood_Hazard_Laguna/clip_santa_cruz.py.
AREA_SHARE = 25.0
area = pd.read_csv(SRC["noah_barangay_area"])
assert set(hh.Barangay) <= set(area.Barangay), "a household barangay is missing from the NOAH area table"
for rp in ["5yr", "25yr", "100yr"]:
    low, med, high = (area[f"Pct{c}_{rp}"] for c in ["Low", "Medium", "High"])
    area[f"PctNone_{rp}"] = (100 - (low + med + high)).clip(lower=0)
    area[f"PctLowOrWorse_{rp}"] = low + med + high
    area[f"PctMediumOrWorse_{rp}"] = med + high
    area[f"FloodHazard{rp}"] = np.select([high >= AREA_SHARE, med + high >= AREA_SHARE, low + med + high >= AREA_SHARE],
                                         ["High", "Medium", "Low"], default="None")
    hh[f"FloodHazard{rp}"] = hh.Barangay.map(area.set_index("Barangay")[f"FloodHazard{rp}"])
for a, b in [("5yr", "25yr"), ("25yr", "100yr")]:   # a bigger storm never lowers a barangay's class
    assert (area[f"FloodHazard{b}"].map(HAZARD_VALUE) >= area[f"FloodHazard{a}"].map(HAZARD_VALUE)).all()
area_cols = ["Barangay"] + [f"{p}_{rp}" for rp in ["5yr", "25yr", "100yr"]
                            for p in ["PctNone", "PctLow", "PctMedium", "PctHigh", "PctLowOrWorse", "PctMediumOrWorse"]]
(area[area_cols].round(2).assign(**{f"FloodHazard{rp}": area[f"FloodHazard{rp}"] for rp in ["5yr", "25yr", "100yr"]})
 .to_csv(HERE / "barangay_flood_hazard.csv", index=False))

# NOAH class at the house itself (point-in-polygon on the NOAH maps). Used for the label and, from v2.6, as an input
# (the app can read it at the user's pin, like the terrain values).
pins = gpd.GeoDataFrame(hh[["HouseholdID"]], geometry=gpd.points_from_xy(hh.Longitude, hh.Latitude), crs="EPSG:4326")
for rp in ["5yr", "25yr", "100yr"]:
    zones = gpd.read_file(SRC[f"noah_zones_{rp}"])[["HazardClass", "geometry"]].to_crs("EPSG:4326")
    hit = gpd.sjoin(pins, zones, how="left", predicate="within")
    level = hit.HazardClass.map(HAZARD_VALUE).fillna(0).groupby(level=0).max()     # worst class if zones touch
    hh[f"HouseFloodHazard{rp}"] = level.reindex(hh.index).map({0: "None", 1: "Low", 2: "Medium", 3: "High"})
# MGB flood susceptibility at the house (NotMapped / Moderate / High / VeryHigh), same function as the app.
hh["MGBFloodSusceptibility"] = [mgb.susceptibility(h.Latitude, h.Longitude) for h in hh.itertuples()]
assert hh[[f"HouseFloodHazard{rp}" for rp in ["5yr", "25yr", "100yr"]]].notna().all().all()

# ----------------------------------------------------------------------------- 4. rainfall events (NASA POWER, by 24 h)
rain = pd.read_csv(SRC["rainfall"]).dropna().reset_index(drop=True)
rain["date"] = pd.to_datetime(rain.date)
annual_max = rain.groupby(rain.date.dt.year).rainfall_24h_mm.max()
beta = np.sqrt(6) * annual_max.std() / np.pi                         # Gumbel fit (method of moments)
mu = annual_max.mean() - 0.5772 * beta
R = {T: float(mu - beta * np.log(-np.log(1 - 1 / T))) for T in (2, 5, 25, 100)}


def pick_separated(frame, k, min_gap_days=10):
    picked = []
    for _, r in frame.iterrows():
        if all(abs((r.date - p.date).days) >= min_gap_days for p in picked):
            picked.append(r)
        if len(picked) == k:
            break
    return pd.DataFrame(picked)


by_24h = rain.sort_values("rainfall_24h_mm", ascending=False)
extreme = pick_separated(by_24h, EVENTS["Extreme"])                           # the wettest days on record
taken = set(extreme.date)
heavy_pool = rain[rain.rainfall_24h_mm.between(50, extreme.rainfall_24h_mm.min(), inclusive="left")
                  & ~rain.date.isin(taken)]
heavy = pick_separated(heavy_pool.sample(frac=1, random_state=SEED), EVENTS["Heavy"])
moderate = pick_separated(rain[rain.rainfall_24h_mm.between(15, 50, inclusive="left")]
                          .sample(frac=1, random_state=SEED + 1), EVENTS["Moderate"])
light = pick_separated(rain[rain.rainfall_24h_mm < 15].sample(frac=1, random_state=SEED + 2), EVENTS["Light"])
events = pd.concat([extreme.assign(EventType="Extreme"), heavy.assign(EventType="Heavy"),
                    moderate.assign(EventType="Moderate"), light.assign(EventType="Light")], ignore_index=True)
events = events.rename(columns={"date": "EventDate", "rainfall_24h_mm": "Rainfall24hMm",
                                "rainfall_3day_mm": "Rainfall3DayMm", "rainfall_7day_mm": "Rainfall7DayMm"})

# Effective 24 h rain: the day's rain plus 30% of the two days before it (wet ground floods sooner).
events["EffectiveRainMm"] = events.Rainfall24hMm + 0.30 * (events.Rainfall3DayMm - events.Rainfall24hMm)
events["RainReturnPeriodYears"] = 1 / (1 - np.exp(-np.exp(-(events.EffectiveRainMm - mu) / beta)))

pairs = []
for hid in hh.HouseholdID:
    for etype, k in PER_HOUSEHOLD.items():
        pairs += [(hid, j) for j in rng.choice(events.index[events.EventType == etype], k, replace=False)]
pairs = pd.DataFrame(pairs, columns=["HouseholdID", "EventIndex"])
df = pairs.merge(hh, on="HouseholdID").merge(events, left_on="EventIndex", right_index=True)

# ----------------------------------------------------------------------------- 5. RiskLevel rule (documented)
# Flood hazard during the event = the worse of NOAH and MGB at the house.
# NOAH: follow NOAH's maps AT THE HOUSE along the rain scale.
#   rain < R5         -> 5-year class x (rain / R5)          (less rain than the 5-year storm, shallower water)
#   R5 <= rain < R25  -> blend 5-year -> 25-year class
#   R25 <= rain < R100-> blend 25-year -> 100-year class
#   rain >= R100      -> 100-year class
h5, h25, h100 = (df[f"HouseFloodHazard{rp}"].map(HAZARD_VALUE).astype(float) for rp in ["5yr", "25yr", "100yr"])
r = df.EffectiveRainMm
w1 = clip01((r - R[5]) / (R[25] - R[5]))
w2 = clip01((r - R[25]) / (R[100] - R[25]))
event_hazard = np.select([r < R[5], r < R[25], r < R[100]],
                         [h5 * r / R[5], h5 + (h25 - h5) * w1, h25 + (h100 - h25) * w2], default=h100)
# MGB: one class without return periods. Its depth (Moderate 2, High 2.5, VeryHigh 3 on the same scale) is taken as
# reached in the 25-year rain and scaled down linearly for less rain: hazard x min(1, rain / R25).
mgb_hazard = df.MGBFloodSusceptibility.map(mgb.HAZARD).astype(float) * clip01(r / R[MGB_ANCHOR_YEARS])
df["NOAHEventHazard"], df["MGBEventHazard"] = event_hazard, mgb_hazard
map_hazard = np.maximum(event_hazard, mgb_hazard)
# Terrain flood index from the published (2-decimal) terrain values, so it can be recomputed from the files.
elev, dist, slope, water = (df[c].round(2) for c in ["ElevationMeters", "DistanceToWaterwayM", "SlopeDegrees",
                                                     "HistoricalWaterOccurrencePct"])
parts = {"Elevation": clip01((20 - elev) / (20 - 3)), "Distance": clip01((500 - dist) / (500 - 50)),
         "Slope": clip01((5 - slope) / 5), "LandCover": df.LandCoverType.map(TERRAIN_LANDCOVER).astype(float),
         "PastWater": clip01(water / 20)}
# A missing terrain value (only in the 263 incomplete households, which stay out of the clean file) counts as neutral 0.5.
df["TerrainIndex"] = sum(TERRAIN_WEIGHTS[k] * v.fillna(0.5) for k, v in parts.items())
terrain_only = TERRAIN_ONLY_MAX * df.TerrainIndex * clip01(r / R[MGB_ANCHOR_YEARS])
event_hazard = np.minimum(3, np.maximum(map_hazard * (1 - TERRAIN_ADJUST + 2 * TERRAIN_ADJUST * df.TerrainIndex),
                                        terrain_only))
df["MapEventHazard"], df["TerrainOnlyHazard"] = map_hazard, terrain_only
df["EventFloodHazard"] = event_hazard                                         # 0 (none) .. 3 (high)
df["RiskScore"] = 100 * (event_hazard / 3) * (0.40 + 0.60 * df.Vulnerability)
clean_label = pd.cut(df.RiskScore, [-np.inf, LOW_MEDIUM_CUT, MEDIUM_HIGH_CUT, np.inf], right=False,
                     labels=["Low", "Medium", "High"]).astype(str)
flip = rng.random(len(df)) < LABEL_NOISE
neighbour = {"Low": ["Medium"], "Medium": ["Low", "High"], "High": ["Medium"]}
df["TrueRiskLevel"] = clean_label
df["RiskLevel"] = [rng.choice(neighbour[c]) if f else c for c, f in zip(clean_label, flip)]
df["LabelNoiseApplied"] = flip
df["EventDate"] = df.EventDate.dt.strftime("%Y-%m-%d")
df = df.sort_values(["HouseholdID", "EventDate"]).reset_index(drop=True)

# ----------------------------------------------------------------------------- 6. outputs
COLS = ["HouseholdID", "Barangay", "Latitude", "Longitude", "EventDate",
        "Age", "HousingMaterial", "HouseMembers", "ChildrenCount", "ElderCount", "PWDCount",
        "ElevationMeters", "SlopeDegrees", "DistanceToWaterwayM", "LocationType", "LandCoverType",
        "HistoricalWaterOccurrencePct", "FloodHazard5yr", "FloodHazard25yr", "FloodHazard100yr",
        "HouseFloodHazard5yr", "HouseFloodHazard25yr", "HouseFloodHazard100yr", "MGBFloodSusceptibility",
        "Rainfall24hMm", "Rainfall3DayMm", "Rainfall7DayMm", "RiskLevel"]
for c in ["ElevationMeters", "SlopeDegrees", "DistanceToWaterwayM", "HistoricalWaterOccurrencePct"]:
    df[c] = df[c].round(2)
df["Age"] = df.Age.astype("Int64")

dupes = df.sample(frac=DUPLICATE_ROWS, random_state=SEED)
raw = pd.concat([df, dupes]).sort_values(["HouseholdID", "EventDate"], kind="stable")[COLS].reset_index(drop=True)
raw.to_csv(HERE / "agap_noah_dataset_raw.csv", index=False)

incomplete = df.loc[df[["Age", "HousingMaterial", "SlopeDegrees", "HistoricalWaterOccurrencePct"]].isna().any(axis=1),
                    "HouseholdID"].unique()
clean = df[~df.HouseholdID.isin(incomplete)].copy()
clean["RiskLevel"] = clean.TrueRiskLevel
clean = clean[COLS].reset_index(drop=True)
assert clean.notna().all().all() and not clean.duplicated(["HouseholdID", "EventDate"]).any()
clean["Age"] = clean.Age.astype(int)
clean.to_csv(HERE / "agap_noah_dataset.csv", index=False)

# Training file: model inputs + target only (IDs, map pin and date removed). Same rows and order as the clean file.
NOT_FOR_TRAINING = ["HouseholdID", "Barangay", "Latitude", "Longitude", "EventDate"]
training = clean.drop(columns=NOT_FOR_TRAINING)
training.to_csv(HERE / "agap_noah_training.csv", index=False)

df[["HouseholdID", "EventDate", "EventType", "HouseFloodHazard5yr", "HouseFloodHazard25yr", "HouseFloodHazard100yr",
    "MGBFloodSusceptibility", "EffectiveRainMm", "RainReturnPeriodYears", "NOAHEventHazard", "MGBEventHazard",
    "MapEventHazard", "TerrainIndex", "TerrainOnlyHazard", "EventFloodHazard",
    "Vulnerability", "RiskScore", "LabelNoiseApplied"]].round(4).to_csv(HERE / "label_audit.csv", index=False)
ev = events.drop(columns="EventIndex", errors="ignore").assign(EventDate=events.EventDate.dt.strftime("%Y-%m-%d"))
ev.round(2).to_csv(HERE / "rainfall_events.csv", index=False)

PIN = "looked up at the household's map pin"
dictionary = pd.DataFrame([
    ("HouseholdID", "ID", "text", "", "Generated (AGAP v1.0)", "Household identifier (one real building per household)"),
    ("Barangay", "ID", "text", "", "Barangay boundary", "Santa Cruz barangay containing the pin"),
    ("Latitude", "Location (map pin)", "float", "deg", "Real building", "Map pin only (WGS84), not a model input"),
    ("Longitude", "Location (map pin)", "float", "deg", "Real building", "Map pin only (WGS84), not a model input"),
    ("EventDate", "ID", "date", "", "NASA POWER", "Historical rainfall event date"),
    ("Age", "Feature", "int", "years", "Simulated", "Age of household head; at least 60 when an elder lives there"),
    ("HousingMaterial", "Feature", "category", "", "Simulated", "Concrete / SemiConcrete / LightMaterials"),
    ("HouseMembers", "Feature", "int", "people", "Simulated", "Household size 1-12"),
    ("ChildrenCount", "Feature", "int", "people", "Simulated", "Members under 18"),
    ("ElderCount", "Feature", "int", "people", "Simulated", "Members aged 60+"),
    ("PWDCount", "Feature", "int", "people", "Simulated", "Persons with disability"),
    ("ElevationMeters", "Feature", "float", "m", "Copernicus DEM 30 m", f"Ground elevation, {PIN} (app extraction)"),
    ("SlopeDegrees", "Feature", "float", "deg", "Derived from DEM", f"Terrain slope, {PIN} (app extraction)"),
    ("DistanceToWaterwayM", "Feature", "float", "m", "OSM waterways", f"Distance to nearest waterway, {PIN} (app extraction)"),
    ("LocationType", "Feature", "category", "", "Derived", "Riverside <100 m, NearWaterway 100-300 m, Inland >=300 m"),
    ("LandCoverType", "Feature", "category", "", "ESA WorldCover 2021", f"Land cover, {PIN} (app extraction)"),
    ("HistoricalWaterOccurrencePct", "Feature", "float", "%", "JRC Global Surface Water 1984-2024",
     f"How often the spot was under water in satellite images, {PIN} (app extraction)"),
    ("FloodHazard5yr", "Feature", "category", "", "Project NOAH (ODbL)", "None/Low/Medium/High flood hazard of the barangay in 5-year rain (highest NOAH class covering at least 25% of the barangay area; see barangay_flood_hazard.csv)"),
    ("FloodHazard25yr", "Feature", "category", "", "Project NOAH (ODbL)", "None/Low/Medium/High flood hazard of the barangay in 25-year rain (highest NOAH class covering at least 25% of the barangay area; see barangay_flood_hazard.csv)"),
    ("FloodHazard100yr", "Feature", "category", "", "Project NOAH (ODbL)", "None/Low/Medium/High flood hazard of the barangay in 100-year rain (highest NOAH class covering at least 25% of the barangay area; see barangay_flood_hazard.csv)"),
    *[(f"HouseFloodHazard{rp}", "Feature", "category", "", "Project NOAH (ODbL)",
       f"None/Low/Medium/High NOAH flood hazard at the house itself in {rp[:-2]}-year rain (point-in-polygon at the map pin)")
      for rp in ["5yr", "25yr", "100yr"]],
    ("MGBFloodSusceptibility", "Feature", "category", "", "MGB Detailed Flood Susceptibility",
     "NotMapped/Moderate/High/VeryHigh MGB flood susceptibility at the house (point-in-polygon at the map pin)"),
    ("Rainfall24hMm", "Feature", "float", "mm", "NASA POWER", "Rainfall on the event date"),
    ("Rainfall3DayMm", "Feature", "float", "mm", "NASA POWER", "3-day total including the event date"),
    ("Rainfall7DayMm", "Feature", "float", "mm", "NASA POWER", "7-day total including the event date"),
    ("RiskLevel", "Target", "category", "", "Rule-based (simulated)",
     f"Low / Medium / High from RiskScore cut-offs {LOW_MEDIUM_CUT:g} and {MEDIUM_HIGH_CUT:g}, using the NOAH class at the house (see dataset_card.md)"),
], columns=["Column", "Role", "Type", "Unit", "Origin", "Description"])
dictionary.to_csv(HERE / "data_dictionary.csv", index=False)

hh_clean = clean.drop_duplicates("HouseholdID")
meta = {
    "name": "Project AGAP NOAH flood-risk dataset - Santa Cruz, Laguna", "version": "2.8", "seed": SEED,
    "license_note": "Contains Project NOAH data (ODC-ODbL 1.0); share derived datasets under ODC-ODbL with attribution",
    "rain_return_levels_24h_mm": {f"{k}yr": round(v, 1) for k, v in R.items()},
    "rain_return_fit": "Gumbel (method of moments) on 1991-2025 NASA POWER annual maximum 24 h rain",
    "label_cutoffs": [LOW_MEDIUM_CUT, MEDIUM_HIGH_CUT],
    "clean": {"file": "agap_noah_dataset.csv", "rows": len(clean), "households": int(clean.HouseholdID.nunique()),
              "class_counts": clean.RiskLevel.value_counts().to_dict(),
              "households_removed_incomplete": int(len(incomplete))},
    "training": {"file": "agap_noah_training.csv", "rows": len(training), "columns": list(training.columns),
                 "removed_columns": NOT_FOR_TRAINING},
    "raw": {"file": "agap_noah_dataset_raw.csv", "rows": len(raw), "households": int(raw.HouseholdID.nunique()),
            "class_counts": raw.RiskLevel.value_counts().to_dict(), "duplicate_rows_added": len(dupes),
            "labels_flipped": int(flip.sum())},
    "households_by_noah_100yr": hh_clean.FloodHazard100yr.value_counts().to_dict(),
    "flood_hazard_method": "per barangay: the highest NOAH class (Low/Medium/High) whose area, counting that class or worse, covers at least 25% of the barangay; None if less than 25% of the barangay is inside NOAH flood zones",
    "label_hazard_method": "Event flood hazard = max(NOAH class at the house along the rain scale, MGB class at the house x min(1, rain / 25-year rain)); MGB Moderate 2, High 2.5, VeryHigh 3. Inputs: FloodHazard* = barangay NOAH class; HouseFloodHazard* = NOAH class at the house; MGBFloodSusceptibility = MGB class at the house",
    "terrain_index": {"weights": TERRAIN_WEIGHTS, "elevation_m": [3, 20], "distance_m": [50, 500], "slope_deg": [0, 5],
                      "past_water_pct": [0, 20], "land_cover": TERRAIN_LANDCOVER, "adjust": TERRAIN_ADJUST,
                      "terrain_only_max": TERRAIN_ONLY_MAX,
                      "rule": "event hazard = min(3, max(map hazard x (0.75 + 0.5 x index), 1.0 x index x min(1, rain / 25-year rain)))"},
    "households_by_mgb": hh_clean.HouseholdID.map(hh.set_index("HouseholdID").MGBFloodSusceptibility).value_counts().to_dict(),
    "households_by_house_noah_100yr": hh_clean.HouseholdID.map(hh.set_index("HouseholdID").HouseFloodHazard100yr).value_counts().to_dict(),
    "events_by_type": events.EventType.value_counts().to_dict(),
    "events_reaching_5yr_rain": int((events.EffectiveRainMm >= R[5]).sum()),
    "sources": {k: {"path": str(v.relative_to(ROOT)), "sha256": sha256(v)} for k, v in SRC.items()},
}
(HERE / "generation_metadata.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
print(json.dumps({k: meta[k] for k in ["rain_return_levels_24h_mm", "clean", "raw", "households_by_noah_100yr",
                                       "events_reaching_5yr_rain"]}, indent=2))
