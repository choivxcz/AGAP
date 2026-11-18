# Project AGAP NOAH Flood-Risk Dataset – Santa Cruz, Laguna (v2.8)

A new dataset whose flood hazard comes from **Project NOAH flood hazard maps** (5-, 25- and 100-year rain).
Terrain features are looked up at the household's map pin. The NOAH flood hazard is given two ways: **per barangay**
(every household in a barangay gets the same class, from the NOAH maps over the barangay's whole area) and **at the house**
(the NOAH zone the house's pin falls in). `RiskLevel` uses the hazard at the house. The app can compute all of these when
a user pins their house. Rebuild with:

```powershell
cd C:\Datasets_Pref\Activities\AGAP_NOAH_Dataset
..\.venv\Scripts\python.exe build_noah_dataset.py      # about 4 minutes, same result every run
```

## Files

| File | What it is |
| --- | --- |
| `agap_noah_dataset.csv` | **Ready to train.** 232,110 rows · 7,737 households × 30 rain events · 26 barangays · no duplicates, missing values or flipped labels |
| `agap_noah_training.csv` | **Model inputs + `RiskLevel` only.** Same 232,110 rows as the clean file, in the same order, without `HouseholdID`, `Barangay`, `Latitude`, `Longitude` and `EventDate` (22 features + target). Load with `pd.read_csv(path, keep_default_na=False)` so the NOAH class `None` stays a class instead of becoming blank |
| `agap_noah_dataset_raw.csv` | 241,200 rows with flaws for cleaning practice: 1,200 duplicates, missing Age/HousingMaterial/Slope, 1,225 flipped labels |
| `check_model.py` | Trains and tests a model the recommended way (class weights, households kept apart), the same model without class weights, and a model with the barangay hazard only (no house-level hazard) for comparison |
| `model_metrics.csv`, `model_confusion_matrix.csv`, `model_confusion_matrix_barangay_only.csv` | Every metric from `check_model.py` (accuracy, balanced accuracy, precision/recall/F1 per level, macro and weighted averages, Cohen's kappa, MCC) for the 80/20 split and each fold |
| `verify_dataset.py`, `verification_results.json` | 30 independent checks of the published files against the sources and the label rule (all pass) |
| `AGAP_NOAH_Dataset_Report.docx`, `AGAP_NOAH_Dataset_Verification_Report.docx` | Earlier dataset and verification reports. **Out of date:** they describe v2.3 and older numbers |
| `AGAP_NOAH_Model_Evaluation_Report.docx` | Model evaluation report for the adviser (current results, in the group's layout); rebuilt by `build_evaluation_report.js` |
| `AGAP_NOAH_Model_Evaluation_Report_v2.7.docx` | **Out of date:** v2.7 results (no terrain adjustment) |
| `feature_importance.py`, `feature_importance.csv` | Which inputs the model relies on (drop in macro F1 when each input is shuffled, hold-out households) |
| `AGAP_NOAH_Model_Evaluation_Report_v2.6.docx` | **Out of date:** v2.6 results (no MGB) |
| `AGAP_NOAH_Model_Evaluation_Report.docx` | **Out of date:** v2.4 results |
| `licenses/` | License text and credit lines for every data source. Start with `00_READ_ME_FIRST.txt`. Share this folder with the dataset |
| `label_audit.csv` | Hidden values behind each label (house NOAH classes, effective rain, return period, event hazard, vulnerability, score). **Never use as inputs** |
| `rainfall_events.csv` | The 60 NASA POWER rain events used |
| `data_dictionary.csv`, `generation_metadata.json` | Column list; counts, rain return levels and source hashes |

## Variables

| Group | Columns | Source |
| --- | --- | --- |
| Map pin (not inputs) | `Latitude`, `Longitude` | Real building centroids |
| Household | `Age`, `HousingMaterial`, `HouseMembers`, `ChildrenCount`, `ElderCount`, `PWDCount` | Simulated (from AGAP v1.0) |
| Terrain at the pin | `ElevationMeters`, `SlopeDegrees`, `DistanceToWaterwayM`, `LocationType`, `LandCoverType`, `HistoricalWaterOccurrencePct` | App lookup (`app/gis/extraction.py`) |
| **NOAH flood hazard of the barangay** | `FloodHazard5yr`, `FloodHazard25yr`, `FloodHazard100yr` (None / Low / Medium / High) | Project NOAH |
| **NOAH flood hazard at the house** | `HouseFloodHazard5yr`, `HouseFloodHazard25yr`, `HouseFloodHazard100yr` (point-in-polygon at the pin) | Project NOAH |
| **MGB flood susceptibility at the house** | `MGBFloodSusceptibility` (NotMapped / Moderate / High / VeryHigh) | MGB Detailed Flood Susceptibility (`../../MGB_Flood_Susceptibility/`) |
| Rain event | `Rainfall24hMm`, `Rainfall3DayMm`, `Rainfall7DayMm` | NASA POWER 1991–2025 |
| Target | `RiskLevel` (Low / Medium / High) | Rule below |

**Changes from AGAP v1.0:** `FloodSusceptibility` (assigned from Table 3.12 shares) and `BarangayRiskScore` (Table 3.13) are
removed. The NOAH columns replace them, because NOAH gives a class for the exact spot, while the tables only give barangay
shares. `HistoricalWaterOccurrencePct` is new. `DistanceToWaterway` is renamed `DistanceToWaterwayM` to match the app.

NOAH classes: Low ≈ 0–0.5 m of water, Medium ≈ 0.5–1.5 m, High > 1.5 m (water speed is also considered). None = outside
NOAH's modelled flood zones.

**Barangay class rule:** the highest NOAH class that covers at least 25% of the barangay's area, counting that class or
worse (for example 25% Medium + 5% High = 30% Medium or worse → Medium). None = less than 25% of the barangay is inside
a NOAH flood zone. A bigger storm never gives a lower class. The shares for every barangay are in
`barangay_flood_hazard.csv`.

| Scenario | None | Low | Medium | High (barangays) |
| --- | --- | --- | --- | --- |
| 5-year | 2 | 18 | 5 | 1 |
| 25-year | 0 | 14 | 11 | 1 |
| 100-year | 0 | 6 | 18 | 2 |

## Rain events

NOAH scenarios are defined by 24-hour rain, so events were picked by 24-hour rain (10 days apart at least): 15 extreme (the
wettest days on record), 15 heavy (50 mm and up), 15 moderate (15–50 mm) and 15 light (under 15 mm). Each household gets
30 of them: 9 extreme, 9 heavy, 6 moderate and 6 light (v2.0 used 10 per household; tripling them gives the model
three times as many High examples without changing the shares).

Rain return levels come from a Gumbel fit to the 1991–2025 NASA POWER annual maximum 24 h rain:
**5-year 132.6 mm · 25-year 186.2 mm · 100-year 230.4 mm**. 15 events reach the 5-year level; one (2020-02-20) passes the 100-year level.

**Note on 2020:** rainfall is used exactly as NASA POWER gives it. NASA POWER is a gridded estimate (MERRA-2, 0.5° × 0.625°),
not a rain gauge. Its 2020 values here are far above the ERA5 reanalysis (6,663 mm vs 2,917 mm for the year; ERA5 shows
0.3 mm on 2020-02-20), and 6 of the 15 extreme events are from 2020. These events and the return levels may overstate
actual rainfall. This is stated in the report's limitations.

## RiskLevel rule (risk = flood hazard × vulnerability)

1. **Effective rain** = 24 h rain + 30% of the rain in the two days before (wet ground floods sooner).
2. **Event flood hazard (0–3)** = the higher of NOAH and MGB at the house. NOAH: read from the NOAH class **at the house** (None 0, Low 1, Medium 2, High 3):
   * below the 5-year rain: 5-year class × (rain ÷ 132.6 mm)
   * between the 5- and 25-year rain: blend from the 5-year to the 25-year class
   * between the 25- and 100-year rain: blend from the 25-year to the 100-year class
   * above the 100-year rain: 100-year class
   MGB: Moderate 2, High 2.5, Very High 3 (from MGB's flood heights), reached at the 25-year rain and scaled down
   in proportion for less rain (hazard × min(1, rain ÷ 186.2 mm)). MGB has no rain scenarios, so this is an assumption.
   **Terrain adjustment (v2.8):** a terrain flood index (0–1) = 35% elevation (1 at ≤3 m, 0 at ≥20 m) + 25% distance
   to waterway (1 at ≤50 m, 0 at ≥500 m) + 15% slope (1 at 0°, 0 at ≥5°) + 15% land cover (BuiltUp 1, Cropland/Bare 0.6,
   Grassland 0.5, TreeCover 0.3) + 10% past surface water (1 at ≥20%). Event hazard = min(3, max(map hazard ×
   (0.75 + 0.5 × index), 1.0 × index × min(1, rain ÷ 186.2 mm))). Weights and cut-offs are assumptions, not calibrated.
3. **Vulnerability (0–1)**: the AGAP v1.0 score (housing material 40%, dependents 25%, PWD 15%, household size 10%,
   elderly head 10%).
4. **RiskScore** = 100 × (hazard ÷ 3) × (0.40 + 0.60 × vulnerability) → **Low < 15 ≤ Medium < 40 ≤ High**.

Clean file: Low 156,037 (67.2%) · Medium 56,564 (24.4%) · High 19,509 (8.4%). These shares come from the chosen rain
scenarios (9 extreme, 9 heavy, 6 moderate, 6 light per household, so 60% heavy or extreme, against 1.6% of real days with
50 mm or more). They describe the storm scenarios, not how often households are at risk on an ordinary day.

## Training without bias toward Low

Train with **class weights** (`class_weight="balanced"`): a missed Medium or High case costs the model more than a missed
Low case, so it does not just predict Low. Judge the model by **macro F1 and recall per level**, not accuracy (a model
that always says Low would already be 87% accurate). Keep each household in only one of train/test.

`check_model.py` (gradient boosting, class weights, households kept apart):

| Test | Macro F1 | F1 Low | F1 Medium | F1 High | Recall High |
| --- | --- | --- | --- | --- | --- |
| 80/20 split | 0.976 | 0.996 | 0.974 | 0.957 | 0.958 |
| 5-fold CV, mean | 0.976 | 0.995 | 0.973 | 0.959 | 0.967 |
| 5-fold CV, barangay hazard only (no house-level columns) | 0.820 | 0.917 | 0.758 | 0.787 | 0.843 |

The lowest fold scores 0.956 F1 for High, so the 96% targets for High (average and every fold) are just missed in v2.8;
the terrain adjustment makes the labels depend on gradual terrain differences that are harder to learn. No High-risk
household is predicted as Low.

These scores show the rule is learnable. They are **not** proof of real flood prediction: household details and the label are simulated.

## Correction (November 4, 2026)

`HistoricalWaterOccurrencePct` showed 1% where the JRC data has 0% (GDAL shifted valid zeros because 0 was also the
no-data value). `download_and_prepare.py` was fixed, the layer regenerated and the dataset rebuilt. Labels and model
results did not change.

## Correction (November 5, 2026): flood hazard was almost always None or Low

v2.3 averaged the NOAH classes over each barangay's whole area (None 0, Low 1, Medium 2, High 3) and rounded the result.
Unflooded land counted as 0, so the average hid the Medium and High zones: Oogong, with 24% of its area High in the
100-year map, came out Low. v2.4 uses the 25% rule above instead. The NOAH area shares were recomputed from the
maps and match to 0.005 percentage points. The data did not change, only how it is summarized per barangay.

## Change (November 5, 2026): v2.5 and v2.6, risk label uses the flood hazard at the house

A check against a population exposure report for Santa Cruz showed that the barangay rule overstates flooding: it puts
every household in a flood zone in the 25- and 100-year maps, while the report (and NOAH read at each house) puts about
half the population outside. v2.5 computed `RiskLevel` from the NOAH class at the house but still gave the model only the
barangay class (macro F1 0.61). v2.6 also gives the model the class at the house (`HouseFloodHazard*`), which the system
can read at the user's pin (macro F1 0.98). Both results are reported, as a comparison.

## Change (November 5, 2026): v2.7 adds MGB flood susceptibility

NOAH's rain-based maps leave out flood-prone parts of the town centre: of the households outside every NOAH zone in the
100-year map, 2,056 are Moderate or High on the MGB Detailed Flood Susceptibility map (all of them in Santisima Cruz,
Santo Angel Central and Santo Angel Norte). v2.7 adds `MGBFloodSusceptibility` as an input and makes the event flood
hazard the worse of NOAH and MGB. The MGB file was traced to MGB's Geospatial Data Inventory service (see
`../../MGB_Flood_Susceptibility/README.md`).

## Change (November 5, 2026): v2.8 adds a terrain adjustment

Before v2.8 the label rule used only the flood maps, rainfall and the household, so the model ignored elevation, slope,
distance to waterway, land cover and past surface water (`feature_importance.csv` showed about zero for each). v2.8
adds the terrain flood index above to the rule. The model now uses distance to waterway, elevation, land cover and
slope; past surface water stays near zero because only 0.4% of households have any.

## License

Contains Project NOAH data, licensed under **ODC-ODbL 1.0**. Credit "Project NOAH (UP NOAH Center) and its contributors".
If you share this dataset, share it under ODC-ODbL. See `../../NOAH_Flood_Hazard_Laguna/README.md`.
