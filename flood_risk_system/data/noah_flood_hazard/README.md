# Project NOAH flood hazard (Santa Cruz, Laguna)

Used by `app/noah.py` to give the risk model its six flood hazard inputs.

| File | Content |
| --- | --- |
| `santa_cruz_flood_hazard_5yr.geojson`, `_25yr`, `_100yr` | NOAH hazard zones (Low / Medium / High) clipped to Santa Cruz. Copied from `NOAH_Flood_Hazard_Laguna/` |
| `barangay_flood_hazard.csv` | Each barangay's NOAH area shares and class (highest class covering at least 25% of the barangay). Copied from `Activities/AGAP_NOAH_Dataset/` (dataset v2.6) |
| `NOAH_License.pdf` | Project NOAH open data license |

The lookups match how the training dataset was built: `HouseFloodHazard*` is a point-in-polygon test on the
zones (worst class if zones touch, `None` outside all zones), and `FloodHazard*` is the barangay's row in the CSV.
If the dataset is rebuilt, copy the new `barangay_flood_hazard.csv` here.

**License:** Contains Project NOAH data, Open Data Commons Open Database License (ODbL) 1.0.
Credit: "Project NOAH (UP NOAH Center) and its contributors".
