# MGB Detailed Flood Susceptibility – Santa Cruz, Laguna

| File | Content |
| --- | --- |
| `mgb_flood_susceptibility_sta_cruz_laguna.geojson` | MGB flood susceptibility polygons for Santa Cruz (field `FloodSusc`: `MF` Moderate, `HF` High, `VHF` Very High). Downloaded on October 25, 2026 |
| `service_layer_info_2026-11-05.json` | The service's own layer description, saved when the source was re-checked |

**Source:** Mines and Geosciences Bureau (MGB), Geospatial Data Inventory, *Detailed Flood Susceptibility*
https://controlmap.mgb.gov.ph/arcgis/rest/services/GeospatialDataInventory/GDI_Detailed_Flood_Susceptibility/MapServer/0

The origin was recorded in the earlier project copy
`OneDrive\Documents\ChatGPT\dataset\Sta_Cruz_Laguna_GIS_Download\README.md`. On November 5, 2026 the service was
online (Map, Query, Data enabled), used the same `FloodSusc` field and codes, and returned `HF` at a test point in
Santisima Cruz (14.293 N, 121.4077 E), matching this file. The same file is used by the app
(`flood_risk_system/data/assessment_sources/`).

**Meaning of the classes** (MGB's description on its public GeoRisk service): Moderate = flood heights above 0.5 m up
to 1 m for 1–3 days; High = above 1 m up to 2 m for more than 3 days; Very High = above 2 m for more than 3 days.
Areas outside the polygons are "NotMapped" in the dataset, which is not the same as "no risk".

**License:** the service states no license or terms of use. It is public government data. Credit "Mines and
Geosciences Bureau (MGB)". Confirm reuse with MGB before publishing a dataset that contains it.

**Geometry:** two of the three polygons are invalid as stored; they are repaired in memory (`shapely.make_valid`)
before use. Where classes overlap, the worse class is used (Very High > High > Moderate).
