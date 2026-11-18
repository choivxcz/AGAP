# Project NOAH Flood Hazard Maps – Santa Cruz, Laguna

Downloaded 2026-11-03 from Project NOAH's official open-data folder (linked from https://noah.up.edu.ph):
https://drive.google.com/drive/folders/1ALE4-E9c-4AGjm1fqiPprWHrLUskeY9o → Flood → 5yr / 25yr / 100yr → `Laguna.zip`

## Files

| Path | What it is |
| --- | --- |
| `downloads/Laguna_flood_{5,25,100}yr.zip` | Original NOAH files (whole Laguna province, ESRI shapefile, EPSG:4326) |
| `downloads/NOAH_License.pdf`, `01ReadMe_Flood.pdf`, `metadata_flood.txt` | NOAH's license, method notes and metadata |
| `extracted/<rp>/` | Unzipped shapefiles |
| `santa_cruz_flood_hazard_{5,25,100}yr.geojson` | Hazard polygons clipped to the 26 Santa Cruz barangays |
| `santa_cruz_flood_hazard_by_barangay.csv` | % of each barangay's area in each hazard class, per rain return period |
| `clip_santa_cruz.py` | Rebuilds the clipped files: `..\Activities\.venv\Scripts\python.exe clip_santa_cruz.py` (about 8 minutes) |

## Hazard classes (`Var` field)

| Var | Class | Flood depth (roughly) |
| --- | --- | --- |
| 1 | Low | 0–0.5 m (knee down) |
| 2 | Medium | 0.5–1.5 m (knee to neck) |
| 3 | High | over 1.5 m (above the neck) |

The classes also account for water velocity, so fast, shallow water can be rated higher. The maps show flooding for 5-, 25- and
100-year rain return periods (Flo-2D simulation using PAGASA rainfall). Areas without a polygon have no modelled flood hazard.

## License and attribution

Open Data Commons Open Database License (ODC-ODbL 1.0). Always credit **Project NOAH (UP NOAH Center) and its
contributors**. A dataset built on this data (for example, one with a NOAH hazard column added) may only be shared under ODC-ODbL.
