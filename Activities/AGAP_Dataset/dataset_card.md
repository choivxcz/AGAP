# Project AGAP Flood Vulnerability Dataset – Santa Cruz, Laguna (v1.0)

New dataset for Project AGAP (CSST102, Group 9). It does **not** reuse the older `synthetic_ml_v1/v2` data or
`synthetic_dataset_tools/build_dataset.py`. Rebuild it at any time with:

```powershell
cd C:\Datasets_Pref\Activities\AGAP_Dataset
..\.venv\Scripts\python.exe build_agap_dataset.py   # dataset (seed 20261027, same result every run)
..\.venv\Scripts\python.exe location_audit.py       # Santa Cruz-only check of every folder
```

## At a glance

| Item | Value |
| --- | --- |
| File | `agap_flood_vulnerability_dataset.csv` |
| Rows | 80,400 (8,000 households × 10 rainfall events, plus 400 duplicated rows left in on purpose for cleaning practice) |
| Barangays | All 26 barangays of Santa Cruz, Laguna |
| Unit (one row) | One household during one historical rainfall event |
| Target | `RiskLevel`: Low 56,526 · Medium 19,061 · High 4,813 |
| Features | The 14 input variables from the Week 1 worksheet (see below) |

## Columns (Week 1 variable list)

| Column | Origin | Notes |
| --- | --- | --- |
| `Age` | Simulated | Age of household head (18–90; ≥ 60 when an elder lives there). 1.5% of households missing |
| `HousingMaterial` | Simulated | Concrete / SemiConcrete / LightMaterials. Light material more likely for small footprints and riverside plots. 1% missing |
| `LocationType` | Real (derived) | Riverside < 100 m, NearWaterway 100–300 m, Inland ≥ 300 m from an OSM waterway |
| `LandCoverType` | Real – ESA WorldCover 2021 (10 m) | Land cover at the building |
| `FloodSusceptibility` | Real – Table 3.12 *Population Flooding Susceptibility and Exposure* (p. 109) | High / Moderate / Low. Each barangay's share of High and Moderate households equals the table's exposure %; barangays not in the table are Low |
| `HouseMembers`, `ChildrenCount`, `ElderCount`, `PWDCount` | Simulated | Household size 1–12 (mean ≈ 4.2), children < 18, elders 60+, persons with disability |
| `ElevationMeters` | Real – Copernicus DEM 30 m | At the building |
| `SlopeDegrees` | Real – from the DEM | Some cells have no value (real raster gaps) |
| `DistanceToWaterway` | Real – OpenStreetMap waterways | Metres to nearest mapped waterway |
| `Rainfall24hMm`, `Rainfall3DayMm`, `Rainfall7DayMm` | Real – NASA POWER (Brgy. Calios point) | 60 historical events 1991–2025: 20 heavy (top storms), 20 moderate, 20 light |
| `RiskLevel` | Rule-based label | See rule below |
| `HouseholdID`, `Barangay`, `Latitude`, `Longitude`, `EventDate` | IDs | Not model inputs. Coordinates are real building-footprint centroids |

`data_dictionary.csv` has the full list. `label_audit.csv` keeps the hidden scores (`HazardScore`,
`VulnerabilityScore`, `RiskScore`) **separately**. Never use them as inputs: they define the label.

## How it was built

1. **Households on real buildings.** Out of 32,544 Google/Microsoft building footprints (Overture), 22,445 are
   dwelling-sized (20–600 m²), lie inside a Santa Cruz barangay, and are not on open water. 8,000 are sampled from them.
2. **Uniform sampling of buildings**, so each barangay gets households in proportion to its dwellings.
3. **Real GIS values.** Elevation, slope, waterway distance, land cover and MGB class are read at each building.
   **FloodSusceptibility follows Table 3.12.** In each barangay, the table's exposure % sets how many households are
   High or Moderate (e.g. Santo Angel Norte 91.14% High; Bagumbayan 4.50% High + 55.92% Moderate). The households
   chosen are the most hazard-prone ones in that barangay: MGB zone first, then lower ground, then nearer a waterway.
   `susceptibility_calibration.csv` compares the table with the dataset; they agree to within about 0.3 points.
   Poblacion IV's 0.41% rounds to 0 of its 56 households.
4. **Real rainfall.** Each household gets 3 heavy, 3 moderate and 4 light events from the NASA POWER record.
5. **Simulated household profile.** Household size, children, elders, PWD, head age and housing material
   (fixed seed; assumptions, not PSA microdata).
6. **RiskLevel rule** (risk = hazard × vulnerability; all terms scaled 0–1):
   * Rain = 0.60·(3-day/150 mm) + 0.25·(24 h/80 mm) + 0.15·(7-day/250 mm)
   * Susceptibility = 0.30·FloodSusceptibility (Low 0, Moderate 0.5, High 1) + 0.25·low-lying (below 20 m) + 0.25·exp(−distance/250 m) + 0.10·flatness + 0.10·land cover
   * Hazard = Susceptibility × (0.30 + 0.70·Rain)
   * Vulnerability = 0.40·housing material + 0.25·dependents share + 0.15·PWD + 0.10·household size + 0.10·elderly head
   * RiskScore = 100 · Hazard · (0.45 + 0.55·Vulnerability) / 0.80 → **Low < 20 ≤ Medium < 40 ≤ High**
7. **Realistic imperfections** for the Week 2 cleaning tasks: 0.5% of labels moved to a neighbouring class
   (assessor inconsistency), missing `Age`/`HousingMaterial` for some households, and 400 duplicated rows.

## Model readiness

With a household-grouped 70/15/15 split, a tuned gradient-boosting model scored **96.6–97.3% test accuracy**
(macro F1 about 0.94–0.95) across three different random splits. The Week 2 notebook shows the full confusion matrix.

## What the accuracy means

Location, terrain, hazard-map and rainfall values are real Santa Cruz data. Household details and `RiskLevel`
are simulated, so **accuracy shows how well a model learns the AGAP risk rule. It is not proof of real-world
flood prediction.** Real validation would need household surveys and recorded flood outcomes, for example from the
Santa Cruz MDRRMO. The rule weights are project design choices and should be reviewed with the instructor.

## Location check (`location_audit.csv`)

Santa Cruz-only and used: barangay boundaries, all GIS rasters, Overture buildings, NASA POWER rainfall (a
grid point in Calios), and Table 3.12. The Population Prone to Flood sheet (64,950 of 123,574) is Santa Cruz but
only context; it is higher than the 38,802 total in Table 3.12. Not Santa Cruz-only:

* `mgb_flood_susceptibility_sta_cruz_laguna.geojson`: despite its name, it covers several provinces (13.5–14.9° N).
  It is only read at Santa Cruz buildings, to rank households inside each barangay.
* `flood_risk_system/data/osm/*` (OSM buildings, roads, POIs, places, addresses, waterways) and `osm/supplemental`:
  these include neighbouring towns. Not used directly.
* `philippine-disaster-risk-mapping-master/`, root `data/`, `charts/`, `maps/`: 12 other cities (Cagayan de Oro, Davao,
  Iloilo, Tacloban, …). No Santa Cruz rows.
* `cv/`: satellite tiles of Cagayan de Oro barangays (PH104305…).
* `urban-flood-modelling/`: a competition benchmark at 300+ m elevation with no location. Not Santa Cruz.
