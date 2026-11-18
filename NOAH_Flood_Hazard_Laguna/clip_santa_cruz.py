"""Clip the Project NOAH Laguna flood hazard maps to Santa Cruz, Laguna.

Input  : extracted/<5yr|25yr|100yr>/PH043400000_FH_<rp>.shp  (NOAH open data, ODC-ODbL, EPSG:4326)
Output : santa_cruz_flood_hazard_<rp>.geojson    hazard polygons inside Santa Cruz (Var 1/2/3 -> Low/Medium/High)
         santa_cruz_flood_hazard_by_barangay.csv % of each barangay's area in each hazard class

Run:  ..\\Activities\\.venv\\Scripts\\python.exe clip_santa_cruz.py
"""
from pathlib import Path

import geopandas as gpd
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
BOUNDARY = ROOT / "Sta_Cruz_Laguna_GIS_Download/output/barangays.geojson"
CLASSES = {1: "Low", 2: "Medium", 3: "High"}
UTM = "EPSG:32651"

brgy = gpd.read_file(BOUNDARY).to_crs("EPSG:4326")
assert (brgy.CITYMUN == "Santa Cruz").all() and len(brgy) == 26
town = brgy.dissolve()[["geometry"]]
brgy_utm = brgy[["BRGY_NAME", "geometry"]].to_crs(UTM)
brgy_utm["BarangayAreaM2"] = brgy_utm.area

summary = []
for rp in ["5yr", "25yr", "100yr"]:
    shp = HERE / f"extracted/{rp}/PH043400000_FH_{rp}.shp"
    hz = gpd.read_file(shp, bbox=tuple(town.total_bounds))
    hz = hz.set_crs("EPSG:4326") if hz.crs is None else hz.to_crs("EPSG:4326")
    assert set(hz.Var.unique()) <= set(CLASSES), f"unexpected Var values {hz.Var.unique()}"
    hz["geometry"] = hz.geometry.make_valid()
    clipped = gpd.clip(hz, town)
    clipped = clipped[~clipped.geometry.is_empty]
    clipped = clipped.dissolve(by="Var", as_index=False)          # one (multi)polygon per class
    clipped["HazardClass"] = clipped.Var.map(CLASSES)
    clipped["ReturnPeriod"] = rp
    clipped[["Var", "HazardClass", "ReturnPeriod", "geometry"]].to_file(
        HERE / f"santa_cruz_flood_hazard_{rp}.geojson", driver="GeoJSON")

    inter = gpd.overlay(brgy_utm, clipped.to_crs(UTM)[["HazardClass", "geometry"]], how="intersection")
    inter["AreaM2"] = inter.area
    pct = (inter.groupby(["BRGY_NAME", "HazardClass"]).AreaM2.sum().unstack(fill_value=0)
           .reindex(columns=["Low", "Medium", "High"], fill_value=0))
    pct = pct.reindex(brgy_utm.BRGY_NAME, fill_value=0).div(brgy_utm.set_index("BRGY_NAME").BarangayAreaM2, axis=0) * 100
    pct.columns = [f"Pct{c}_{rp}" for c in pct.columns]
    summary.append(pct)
    print(rp, "classes in Santa Cruz:", clipped.HazardClass.tolist(),
          "| % of town flooded:", round(float(inter.AreaM2.sum() / brgy_utm.BarangayAreaM2.sum() * 100), 2))

out = pd.concat(summary, axis=1).round(2).rename_axis("Barangay").reset_index()
out.to_csv(HERE / "santa_cruz_flood_hazard_by_barangay.csv", index=False)
print(out.to_string(index=False))
