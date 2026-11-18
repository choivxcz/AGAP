# Santa Cruz Flood Risk & MDRRMO Operations System

| Page | Who | What |
|------|-----|------|
| `/` | everyone | Portal |
| `/report` | residents | Mobile report form (GPS or map pin, water depth, people affected); returns a reference number (`RPT-XXXXXX`) and shows its status |
| `/ops` | MDRRMO staff | Operations console: rain scenario, resident registry and evacuation priority, incident queue, facilities, safest-route planning |
| `/assessment` | anyone | Single-household self-assessment (unchanged; nothing saved) |
| `/explorer` | analysts | GIS explorer (unchanged) |

### Operations module (`app/ops/`)

- **Rain scenario** (`scenario.py`): set by hand (e.g. from a PAGASA advisory), from one of the 60 historical NASA POWER
  events, or from the Open-Meteo forecast (only the town-hall coordinates are sent). Effective rain = 24 h + 30% of the
  two days before. It sets the status (Normal / Watch / Warning / Severe / Extreme, a system heuristic based on the
  2-, 5-, 25- and 100-year rain levels, not an official level) and the NOAH map used for routing (the smallest return
  period at or above the effective rain).
- **Residents**: registering a household extracts and stores its location features once, then predicts its risk
  across the 60 past events (baseline) and under the active scenario (one batch model call for all residents when the
  scenario changes). The **evacuation priority** sorts by predicted risk, then PWD, elders, children, housing and special
  needs. Export: `GET /api/ops/residents/export.csv`.
- **Incidents** (`/api/public/reports`, `/api/ops/incidents`): automatic triage (critical/high/medium/low from type,
  water depth, people and vulnerable residents). Status runs new → verified → dispatched → resolved/dismissed, and every
  change goes in `incident_log`. The public status endpoint never returns the reporter's details or the coordinates.
- **Safest routing** (`roads.py`): the OSM road network is split into pieces of 40 m or less (90,682 nodes, 94,075
  pieces). Each piece carries the NOAH class for 5/25/100-year rain, the MGB class, and its bridge flag; the table is
  cached in `data/ops_cache/`, takes about 15 s to build and rebuilds when a source changes. Dijkstra cost = length ×
  a vehicle-specific multiplier for the water class (standard vehicle, high-clearance truck, team on foot) + an MGB
  penalty that grows with rain. Open, *trusted* reports also apply: staff reports at once, public reports after
  verification. A blocked road blocks pieces within 30 m; a reported depth raises the class within 150 m. Water too
  deep for the vehicle has a very high but finite cost, so a route is always returned, with a **staging point** where
  the vehicle stops and a boat or water-rescue team takes over. Each answer also gives the plain shortest route for
  comparison. The rescue base can be chosen automatically (the cheapest to reach). The evacuation route goes to the
  cheapest-to-reach centre that is active, not in NOAH Medium/High water for the scenario, and not full.
- **Facilities**: seeded once from OSM POIs inside Santa Cruz. Town/barangay halls, fire and police stations become
  rescue bases; halls, schools and community centres become evacuation-centre *candidates* (switched off when inside a
  100-year Medium/High zone). Confirm them against the MDRRMO's designated list and set capacities.
- **Access**: set `FLR_OPS_PIN` to require a PIN for `/api/ops/*` (the console asks for it). Always set it before
  serving beyond localhost (`FLR_HOST=0.0.0.0`), since the console holds personal data. `/api/public/*` stays open.
- **Demo data**: `python scripts/seed_demo_residents.py --n 300` registers simulated households from the AGAP dataset
  (named "Demo household …", flagged `is_demo`); remove them with `--clear`.

Limitations: the risk model was trained on rule-based labels, not observed floods. NOAH maps are hazard scenarios,
not real-time water levels, so field reports are what make routing current. OSM roads may be incomplete, one-way
streets are ignored (emergency vehicles), and evacuation centres from OSM are only candidates.

---

# Household Flood-Risk Location Map — Santa Cruz, Laguna

The default homepage provides the resident assessment form: select a map point, load environmental features,
Project NOAH and MGB flood hazard, and enter household details. A gradient boosting classifier trained on the
AGAP NOAH dataset (v2.8) returns Low, Medium or High across 60 historical rain events. Its labels are rule-based,
so the scores show how well that rule is learned; there is no validation against real flood outcomes.
See [ASSESSMENT.md](ASSESSMENT.md) for sources, workflow and limitations.
The original map workflow documented below is available at `/explorer`.

An interactive web map for authorising, selecting and verifying a household's
actual geographical location, and for extracting GIS flood-risk features from
the existing `Sta_Cruz_Laguna_GIS_Download` raster dataset for **all 26
barangays of Santa Cruz, Laguna, Philippines**.

The system never invents coordinates or raster values: every extracted number
carries its source raster, CRS and pixel, and every failure is reported as an
explicit missing-data status.

---

## 1. Repository layout

```
Datasets_Pref/
├── Sta_Cruz_Laguna_GIS_Download/     # existing GIS download pipeline (modified: JRC tile bug fix)
│   ├── download_and_prepare.py       #   original pipeline (URL fixed)
│   ├── regenerate_water_occurrence.py#   regenerates the water-occurrence layers (new)
│   └── output/                       #   municipal + per-barangay GeoTIFFs (EPSG:32651)
└── flood_risk_system/                # NEW: map application
    ├── app/
    │   ├── config.py                 # env-var configuration (FLR_*)
    │   ├── gis/discovery.py          # data-driven barangay/layer discovery + validation
    │   ├── gis/extraction.py         # coordinate validation, CRS transform, raster sampling
    │   ├── database.py               # SQLite: households, locations, extractions, assessments
│   ├── features.py               # ML feature contract (names, units, dtypes, missing policy)
│   ├── main.py                   # FastAPI server
│   └── static/                   # Leaflet (2D) + MapLibre GL (3D) map UI, both bundled locally
    ├── scripts/
    │   ├── fetch_osm_map_features.py # caches OSM buildings/roads/waterways locally
    │   └── validate_gis_coverage.py  # coverage audit for all barangays/layers
    ├── tests/                        # pytest suite (unit + integration)
    └── data/osm/                     # cached OSM GeoJSON (offline map layers)
```

## 2. What was found in the existing project

- `Sta_Cruz_Laguna_GIS_Download/download_and_prepare.py` downloads a Copernicus
  DEM GLO-30 tile, ESA WorldCover 2021 tile, JRC Global Surface Water tile and
  OpenStreetMap waterways, reprojects everything to **EPSG:32651 (UTM 51N)** and
  clips per barangay (26 folders × 5 layers).
- **Bug found and fixed:** the JRC occurrence tile `occurrence_120E_10N_v1_5_2024.tif`
  covers **0–10° N** (JRC tiles are named by their *upper-left* corner). Santa Cruz
  is at ≈14.2° N, so every output occurrence pixel was written as NoData (0) —
  100 % NoData in all 27 occurrence rasters. The URL now points at
  `occurrence_120E_20N_v1_5_2024.tif` (10–20° N), and
  `regenerate_water_occurrence.py` rebuilt the municipal + 26 barangay layers
  from the correct tile. Occurrence values 0–91 % now present; no information
  was lost (the old files contained no data at all).
- There was no existing household database or web app, so this project adds one
  (SQLite + FastAPI). The barangay-level `philippine-disaster-risk-mapping-master`
  notebook was left untouched.

## 3. Install & run

Requires Python 3.10+ with the GIS stack already installed for the download
package (`rasterio`, `geopandas`, `pyproj`, `shapely`, `numpy`). Then:

```powershell
cd C:\Datasets_Pref\flood_risk_system
pip install fastapi uvicorn pytest httpx   # small; requests/pydantic usually present

# one-time: cache OSM map features (needs internet; ~13 MB, already done)
python scripts\fetch_osm_map_features.py

# start the map
python -m app.main
# open http://127.0.0.1:8000
```

Configuration (all optional environment variables):

| Variable             | Default                                   | Purpose                     |
|----------------------|-------------------------------------------|-----------------------------|
| `FLR_GIS_OUTPUT_DIR` | `../Sta_Cruz_Laguna_GIS_Download/output`  | raster + boundary location  |
| `FLR_DB_PATH`        | `flood_risk_system/data/flood_risk.db`    | SQLite database             |
| `FLR_OSM_DATA_DIR`   | `flood_risk_system/data/osm`              | cached OSM GeoJSON          |
| `FLR_HOST` / `FLR_PORT` | `127.0.0.1` / `8000`                   | server bind                 |
| `FLR_OSM_TILE_URL`   | OpenStreetMap tile server                 | optional online basemap     |

No absolute paths are hardcoded; no API keys are required.

## 4. Using the map

1. **Select a location** — click the map, drag the marker, or type
   latitude/longitude (WGS 84, latitude first) and press *Show on map*.
2. The panel immediately shows the **barangay** (point-in-polygon against
   `barangays.geojson`) or a clear *outside GIS coverage* message.
3. Press **Extract GIS features** — elevation, slope, distance to mapped
   waterway, land-cover code/class and historical water occurrence appear with
   per-feature status and the source raster of each value.
4. **Households** — create a household (socio-economic attributes only), select
   it, then *Save selected location to household*. Saving over a **verified**
   location requires explicit confirmation (HTTP 409 otherwise).
5. *Extract & save for household* stores a full extraction run (audit trail,
   never overwritten). *Mark location verified* records independent
   verification explicitly — the system never self-verifies.
6. **Map appearance** — the 2-D view renders a real-map style from the local
   vector data (land-coloured barangay polygons with dashed boundaries,
   OSM-Carto-style roads by class, blue waterways, buildings) and every
   barangay name is permanently visible at its centroid. Boundaries, waterways,
   roads, buildings and name labels can each be toggled.
7. **Basemap choice** — *Local vector (offline)* by default; optional online
   *OSM streets* or *Satellite* (Esri World Imagery) tiles. Online tiles are
   never required; if they fail to load the app shows a notice and keeps
   working (local street layers are auto-hidden while an online basemap is
   active, then restored).
8. **3-D exploration** — press the **3D** switch (top-right) for a
   Google-Maps-style tilted view built on MapLibre GL (bundled locally):
   right-drag or Ctrl+drag rotates/tilts, drag pans, scroll zooms, the compass
   control resets north/pitch, the municipal DEM is served as a Terrarium
   raster-dem source with hillshade, roads include source-backed labels, and
   buildings are extruded in 3-D. Clicking a building opens its attributes
   and shares the selected footprint with the 2-D workflow. Building heights are **illustrative**
   (OSM has no heights for most footprints here) and labelled as such in the
   UI; they never enter the GIS feature data. Switching 2-D ↔ 3-D keeps the
   camera position and the selected marker in sync.

The `/api/map-coverage` endpoint is the authoritative local vector audit. In
the checked dataset it reports all 26 barangays with road geometry, 2,169 OSM
road features (208 named), 4,205 OSM building footprints, 25,171 supplementary
Overture footprints, 691 POIs, and 75 waterways. Five barangays have no OSM
building footprints; their supplementary Overture counts are reported
separately and the application does not present either source as a complete
building inventory. A footprint is never treated as proof of a household
location.

## 5. How extraction works

1. Input `(latitude, longitude)` is validated (numeric, finite, range-checked;
   a (lon, lat) swap fails the latitude range check for this study area).
2. The point is transformed from **EPSG:4326** into each raster's **own CRS**
   via pyproj (`always_xy=True`) — never assumed to be 4326.
3. **Tile selection is deterministic**: the municipal raster first (single
   aligned 30 m grid; barangay tiles are exact grid subsets of it); if absent,
   the containing barangay's tile; else barangay tiles in sorted slug order.
   No mosaicking is performed.
4. The pixel is read at the transformed location; bounds, NoData, NaN/Inf and
   layer-specific value ranges are checked. Failures produce
   `status: missing/error` with a reason — never a fabricated value.
5. Layer semantics (documented in code):
   - `elevation_m` — nodata 0 (Copernicus GLO-30 surface model, ~30 m, metres).
   - `slope_degrees` — nodata 0; 3×3 finite-difference derivative of that DEM,
     degrees. (Exactly-flat grid cells are indistinguishable from nodata in
     this dataset — a known limitation of the original pipeline.)
   - `distance_to_osm_waterway_m` — nodata −9999; distance to **mapped OSM
     waterways** (`river|stream|canal|drain|ditch`) on the 30 m grid, metres —
     not necessarily the nearest natural river.
   - `land_cover_2021` — nodata 0; ESA WorldCover 2021 v200 legend
     (10 Tree … 50 Built-up, 80 Water, 90 Wetland …), ~10 m.
   - `historical_surface_water_occurrence_pct_1984_2024` — JRC GSW v1.5
     occurrence, % of valid Landsat observations classified as water,
     1984–2024, ~30 m. In this dataset the fill value 0 doubles as both
     "0 % occurrence" and "outside coverage"; disambiguated by polygon
     membership and reported with an explanatory note on the value.

## 6. Data validation results (2026-10-25)

`scripts/validate_gis_coverage.py` output: **all 26 barangays × 5 layers
present with valid data**, all EPSG:32651, all 30 m terrain/10 m land cover
rasters on one aligned grid. Valid-pixel percentages (36–78 %) reflect
NoData outside each irregular barangay polygon — expected. Municipal
occurrence raster: 0–91 %, previously 100 % NoData (see bug fix above).

Remaining layer caveats:
- **Slope**: exactly 0 is written where the DEM was invalid, so genuine
  0.000° flat cells cannot be distinguished from nodata by the original
  pipeline; such cells are reported as missing.
- **Elevation**: nodata=0 means true 0 m elevations would be reported as
  NoData; observed minimum in Santa Cruz is 1.0 m, so this did not occur.
- **Historical water occurrence**: regenerate from JRC *change/seasonality*
  products if flood-season behaviour matters; occurrence is not flood depth.
- Barangay boundaries are from a public GEODATA service (26 features), not an
  official PSA boundary set — verify with the LGU before high-stakes use.

## 7. Database & ML integration

SQLite tables: `household` (socio-economic attributes only), `household_location`
(append-only; active flag; `coordinate_source` ∈ map_click|manual_entry|gps|
address_geocode; `verification_status`), `gis_extraction` (one row per run with
per-feature values, statuses, source rasters, version, timestamps),
`assessment` (survey-observed `risk_level` labels only — never generated).

`app/features.py` is the single source of truth for the ML contract:
`ElevationMeters (m, float32)`, `SlopeDegrees (degrees, float32)`,
`DistanceToWaterwayM (m, float32)`, `LandCoverCode (int8)`,
`LandCoverClass (string)`, `HistoricalWaterOccurrencePct (%, float32)`.
`GET /api/ml/training-rows` returns feature rows with a `missing` mask and the
survey label; `RiskLevel` is never an input feature. Numerical encoding of
missing values (imputation etc.) must be decided once in the training pipeline
and reused at inference to avoid skew.

## 8. Tests

```powershell
cd C:\Datasets_Pref\flood_risk_system
python -m pytest tests -v
```

67 tests, all passing (2026-10-25): coordinate validation & ordering, CRS
transformation, sampling at known real points (cross-checked against direct
raster reads and the raw Copernicus DEM), outside-coverage points, NoData/NaN
handling, barangay polygon selection, missing-file/inconsistent-grid cases,
database round-trips incl. verified-location overwrite guard, API integration
(TestClient), and the feature-name/unit/dtype contract. Synthetic fixtures
(tiny hand-made rasters/GeoJSON) are used only for unit tests and are labelled
as such in the test file docstrings.

## 9. Known limitations

- 30 m terrain resolution: a household's pixel may not match its exact lot;
  positional accuracy is limited by source resolution (~30 m, ~10 m land cover)
  and the DEM's horizontal accuracy.
- OSM completeness varies; distance-to-water is only as good as mapped
  waterways.
- Building footprints are displayed for reference only; no automatic
  household↔building assignment is performed.
- Online basemaps (OSM streets / satellite tiles) require internet; everything
  else — including the whole 2-D and 3-D vector map — works offline.
- 3-D building heights are deterministic but illustrative (display only,
  labelled in the UI); they are never written to the database or used as
  features. The 3-D view requires a browser with WebGL.
