"""Check whether each data source in C:\\Datasets_Pref covers Santa Cruz, Laguna only.

Writes location_audit.csv. Santa Cruz extent comes from the official barangay boundary file.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
from rasterio.warp import transform_bounds

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
brgy = json.loads((ROOT / "Sta_Cruz_Laguna_GIS_Download/output/barangays.geojson").read_text(encoding="utf-8"))
pts = np.array([p for f in brgy["features"] for poly in ([f["geometry"]["coordinates"]] if f["geometry"]["type"] == "Polygon"
                else f["geometry"]["coordinates"]) for ring in poly for p in ring])
W, S_, E, N = *pts.min(0), *pts.max(0)
PAD = 0.005                                   # ~500 m tolerance for raster/edge cells
SC_BOX = f"{S_:.3f}-{N:.3f} N, {W:.3f}-{E:.3f} E"


def within(w, s, e, n):
    return w >= W - PAD and s >= S_ - PAD and e <= E + PAD and n <= N + PAD


def geo_bounds(path):
    feats = json.loads(path.read_text(encoding="utf-8"))["features"]
    xy = []

    def walk(c):
        if isinstance(c[0], (int, float)):
            xy.append(c[:2])
        else:
            for x in c:
                walk(x)
    for f in feats:
        if f.get("geometry"):
            walk(f["geometry"]["coordinates"])
    a = np.asarray(xy)
    return (*a.min(0), *a.max(0)), len(feats)


rows = []


def add(source, path, verdict, evidence, use):
    rows.append({"Source": source, "Path": path, "Santa Cruz only?": verdict, "Evidence": evidence, "Used in AGAP dataset": use})


# barangay boundaries
props = pd.DataFrame([f["properties"] for f in brgy["features"]])
add("Barangay boundaries", "Sta_Cruz_Laguna_GIS_Download/output/barangays.geojson", "YES",
    f"{len(props)} barangays, all CITYMUN=Santa Cruz, PROVINCE=Laguna; extent {SC_BOX}", "Yes - barangay assignment")

# rasters
for name in ["elevation_m", "slope_degrees", "distance_to_osm_waterway_m", "land_cover_2021",
             "historical_surface_water_occurrence_pct_1984_2024"]:
    with rasterio.open(ROOT / f"Sta_Cruz_Laguna_GIS_Download/output/{name}.tif") as r:
        b = transform_bounds(r.crs, "EPSG:4326", *r.bounds)
    used = "No - not one of the listed variables" if "water_occurrence" in name else "Yes"
    add(f"Raster {name}", f"Sta_Cruz_Laguna_GIS_Download/output/{name}.tif", "YES" if within(*b) else "NO",
        f"bounds {b[1]:.3f}-{b[3]:.3f} N, {b[0]:.3f}-{b[2]:.3f} E", used)
add("Per-barangay raster clips and ZIP copy", "Sta_Cruz_Laguna_GIS_Download/output/barangays/, Sta_Cruz_Laguna_GIS_Download.zip",
    "YES", "Clips/copies of the Santa Cruz rasters above", "No - duplicates of the full rasters")

# vector layers
for label, rel, use in [
    ("MGB flood susceptibility", "flood_risk_system/data/assessment_sources/mgb_flood_susceptibility_sta_cruz_laguna.geojson", "Partly - only to choose which households in a barangay are the exposed ones (shares come from Table 3.12)"),
    ("Overture building footprints", "flood_risk_system/data/osm/buildings_overture.geojson", "Yes - household locations (only buildings inside Santa Cruz)"),
    ("OSM buildings", "flood_risk_system/data/osm/buildings.geojson", "No - Overture layer used instead"),
    ("OSM roads", "flood_risk_system/data/osm/roads.geojson", "No"),
    ("OSM waterways", "flood_risk_system/data/osm/waterways.geojson", "Indirectly - waterway-distance raster"),
    ("OSM points of interest", "flood_risk_system/data/osm/pois.geojson", "No"),
    ("OSM places", "flood_risk_system/data/osm/place.geojson", "No"),
    ("OSM addresses", "flood_risk_system/data/osm/addresses.geojson", "No"),
]:
    (w, s, e, n), count = geo_bounds(ROOT / rel)
    ok = within(w, s, e, n)
    note = ""
    if not ok:
        note = (" - covers a much wider region (several provinces); not clipped to Santa Cruz" if (e - w) > 0.5
                else " - extends beyond Santa Cruz into neighbouring towns; must be clipped")
    add(label, rel, "YES" if ok else "NO", f"{count:,} features; bounds {s:.3f}-{n:.3f} N, {w:.3f}-{e:.3f} E{note}", use)
add("OSM supplemental copies", "flood_risk_system/data/osm/supplemental/", "NO",
    "Byte copies of the OSM layers above (same wider extent)", "No")

# rainfall + population
meta = json.loads((ROOT / "flood_risk_system/data/assessment_sources/nasa_power_daily_rainfall_features_metadata.json").read_text(encoding="utf-8"))
add("NASA POWER daily rainfall 1991-2025", "nasa_power_daily_rainfall_features_1991_2025.csv", "YES (gridded)",
    f"Point {meta['coordinates']['latitude']}, {meta['coordinates']['longitude']} in Brgy. Calios; MERRA-2 cell ~50 km covers Santa Cruz and nearby towns",
    "Yes - rainfall features")
pop = pd.read_excel(ROOT / "Population Prone to Flood - Santa Cruz, Laguna, Region IV-A (CALABARZON).xlsx", header=None)
add("Population Prone to Flood sheet", "Population Prone to Flood - Santa Cruz, Laguna, Region IV-A (CALABARZON).xlsx", "YES",
    f"Location: {pop.iloc[2, 1]}; {pop.iloc[3, 1]}", "No - municipal context only (Table 3.12 used instead)")

# app database
db = sqlite3.connect(f"file:{ROOT / 'flood_risk_system/data/flood_risk.db'}?mode=ro", uri=True)
loc = pd.read_sql("select latitude, longitude from household_location where latitude is not null", db)
ok = within(loc.longitude.min(), loc.latitude.min(), loc.longitude.max(), loc.latitude.max())
add("Flood-risk app database (test entries)", "flood_risk_system/data/flood_risk.db", "YES" if ok else "NO",
    f"{len(loc)} map-click test locations, all inside the Santa Cruz extent", "No - a few manual test records")
add("Earlier synthetic datasets v1/v2 and synthetic_dataset_tools outputs",
    "flood_risk_system/data/synthetic_ml_v1, synthetic_ml_v2, synthetic_dataset_tools/", "YES",
    "Synthetic points inside Santa Cruz barangays", "No - replaced by this new dataset (as requested)")

# non-Santa Cruz sources
locs = pd.read_csv(ROOT / "philippine-disaster-risk-mapping-master/data/location.csv")
cities = sorted(locs.adm3_en.unique())
add("Philippine disaster-risk-mapping project", "philippine-disaster-risk-mapping-master/", "NO",
    f"{len(locs)} barangays in {len(cities)} other cities: {', '.join(cities)}; no Santa Cruz, Laguna rows", "No")
top = pd.read_csv(ROOT / "data/top_10_priorities.csv")
add("Root data/ folder", "data/*.csv", "NO",
    f"Copies of that project's outputs (e.g. {top.City.iloc[0]}); no Santa Cruz rows", "No")
add("Root charts/ folder", "charts/*.png", "NO", "Charts of the same other-city findings", "No")
add("Root maps/ folder", "maps/risk_map.html", "NO", "Risk map of the other cities (Cagayan de Oro, Davao, Iloilo, ...)", "No")
add("Root cv/ folder", "cv/*.png", "NO",
    "Satellite tiles named PH104305011/013/045 = barangay codes in Cagayan de Oro City, Misamis Oriental", "No")
nodes = pd.read_csv(ROOT / "urban-flood-modelling/2d_nodes_static.csv", nrows=2000)
add("Urban flood modelling benchmark", "urban-flood-modelling/", "NO",
    f"Projected x/y grid with ground elevation {nodes.elevation.min():.0f}-{nodes.elevation.max():.0f} m; "
    "Santa Cruz is 1-40 m above sea level; no location/CRS supplied (competition benchmark)", "No")

audit = pd.DataFrame(rows)
audit.to_csv(HERE / "location_audit.csv", index=False)
print(f"Santa Cruz extent: {SC_BOX}")
print(audit[["Source", "Santa Cruz only?", "Used in AGAP dataset"]].to_string(index=False))
