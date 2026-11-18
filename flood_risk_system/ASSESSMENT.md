# Flood risk assessment: AGAP NOAH model (dataset v2.8)

Run `python .\start_flood_system.py` from C:\Datasets_Pref and open http://127.0.0.1:8000/. Select a Santa Cruz
point, enter household details, then press **Predict flood risk**. No household input or prediction is saved.

There is no rainfall date to choose. The model is run for the household across the dataset's 60 historical rain
events (NASA POWER 1991-2025; 15 extreme, 15 heavy, 15 moderate, 15 light; `data/agap_noah_v2_8/rainfall_events.csv`).
The **risk level** is the most common prediction across the 15 extreme events (ties go to the higher level). The page
also shows the counts across all 60 events, the highest level reached, and the prediction for each event. Six of the
15 extreme events are from 2020, when NASA POWER values for Santa Cruz are much higher than other rainfall records.

## What the model uses

The model (`data/agap_noah_v2_8/model.joblib`) is a HistGradientBoostingClassifier (500 iterations, class weights)
trained on all 232,110 records of `Activities/AGAP_NOAH_Dataset/agap_noah_training.csv`. Its 22 inputs are built
exactly as in that dataset:

| Inputs | Where the app gets them |
| --- | --- |
| Age (household head), HousingMaterial (Concrete / SemiConcrete / LightMaterials), HouseMembers, ChildrenCount, ElderCount, PWDCount | The household form |
| ElevationMeters, SlopeDegrees, DistanceToWaterwayM, LandCoverType, HistoricalWaterOccurrencePct | `app.gis.extraction` at the pin (the lookup used to build the dataset), rounded to 2 decimals |
| LocationType | Riverside < 100 m <= NearWaterway < 300 m <= Inland (unrounded distance) |
| FloodHazard5yr / 25yr / 100yr | `app.noah`: the barangay's NOAH class (highest class covering at least 25% of its area) |
| HouseFloodHazard5yr / 25yr / 100yr | `app.noah`: the NOAH zone at the pin (point-in-polygon; None = outside the flood zones) |
| MGBFloodSusceptibility | `app.mgb`: the MGB Detailed Flood Susceptibility class at the pin (NotMapped / Moderate / High / VeryHigh); the same function builds the dataset column |
| Rainfall24hMm, Rainfall3DayMm, Rainfall7DayMm | Each of the 60 historical rain events in turn |

NOAH data and its license are in `data/noah_flood_hazard/`. `tests/test_assessment.py` checks that, for dataset
records, the app builds exactly the training inputs; `tests/test_noah.py` checks the NOAH classes for all 7,737
dataset households.

The response gives the risk level, the counts per event type and the model's scores for each event. Evaluation (households kept apart,
from the dataset folder, v2.8): macro F1 97.5% on the 80/20 test and 97.6% in 5-fold CV; High-risk recall 95.8%. The
risk label includes a terrain adjustment (elevation, distance to waterway, slope, land cover, past surface water), so the
model uses those inputs. RiskLevel
is a rule-based label, so these numbers show how well the rule is learned, not real flood prediction. The report is
at `/api/assessment/model-report`.

## Retrain

After rebuilding the dataset, run `python scripts/train_agap_noah_model.py` from flood_risk_system and copy the new
`barangay_flood_hazard.csv` into `data/noah_flood_hazard/` (the script copies `rainfall_events.csv` itself). Run `python -m pytest`.

## Earlier prototype: synthetic_ml_v1 (retired)

The text below describes the model the app used before dataset v2.6. Its files remain in data/synthetic_ml_v1 for reference.

Run `python .\start_flood_system.py` from C:\Datasets_Pref and open http://127.0.0.1:8000/. Keep the terminal running; do not start a second server on the same port. Other app files are helper modules. The old GIS explorer remains at /explorer.

Select a Santa Cruz point and historical rainfall date, enter household details, then press **Predict prototype risk**. The server extracts environmental features and calls a saved, fitted histogram gradient boosting classifier. No rule score is computed at inference. No household input or predicted label is saved.

### Scope

The Low / Medium / High target labels are explicitly synthetic and user-authorized. Original datasets remain unchanged and unlabeled. The classifier learns a simulator, not observed flood outcomes. Low does not mean safe. There is no real-world validation, probability calibration or 98% accuracy guarantee.

The report is available at /api/assessment/model-report with raw and normalized confusion matrices, per-class precision, recall, F1, support and majority baseline.

### Training

The separate data/synthetic_ml_v1 directory contains model.joblib, synthetic_labeled_training_data.csv, metrics.json, test_predictions.csv, report.html, confusion_matrix.png and model_card.md. Every generated row includes DataOrigin=Synthetic and TargetOrigin=Synthetic, plus TargetMethod, SyntheticTargetScore and Split.

Training-only median imputation with missing indicators and one-hot categories feeds HistGradientBoostingClassifier: 160 iterations, 31 maximum leaf nodes, learning rate 0.1, L2=1, early stopping off, seed 20261026. Fixed hyperparameters; no tuning toward 98%.

Training: 116,480 rows from 2,080 households with dates through 2018. Test: 7,280 rows from 520 different households with dates in 2019-2025. Both household IDs and event dates are disjoint. Cross-group combinations are excluded. Synthetic accuracy 81.65%, macro F1 0.734, balanced accuracy 71.31%. These measure agreement with synthetic labels only. Repeated household and weather patterns induce dependence; no independent-observation confidence interval is claimed.

Inputs: Age, HouseMaterial, LocationType, LandCoverType, FloodSusceptibility, HouseMembers, ChildrenCount, ElderCount, PWDCount, ElevationMeters, SlopeDegrees, DistanceToWaterwayM, Rainfall24hMm, Rainfall3DayMm, Rainfall7DayMm. IDs, coordinates, dates, target scores, RiskLevel and provenance are excluded from predictors.

### Synthetic targets

Age is generated within an age band represented in the household: children 0-17, adults 18-59, elders 60-95. No demographic calibration is claimed.

Labels use the earlier demo-1.0 simulator: MGB 0-4 points, distance 0-3, elevation 0-2, slope 0-1, maximum rainfall-window score 0-3, wetland/mangroves 0-1, material 0-2, composition 0-3, age 0-1, size 0-1. Low 0-5; Medium 6-11; High 12-21. Full thresholds are in app/demo_rules.py and scripts/train_synthetic_model.py. These are arbitrary assumptions, not official standards. LocationType is not counted again in target generation.

Missing numeric or unmapped susceptibility contributions are simulated as uniform integers from zero to the factor maximum, constant per household/factor. These latent contributions do not overwrite missing environmental observations. Inference uses training medians for missing numbers and NotMapped as a category. The UI discloses missing/unmapped inputs. Synthetic predictions must not become observed training labels.

### Sources and validation

data/assessment_sources holds unmodified original GIS/rainfall files and source_manifest.json with original paths and hashes. Override with FLR_ASSESSMENT_SOURCE_DIR before startup. The old explorer separately uses FLR_GIS_OUTPUT_DIR. Restart after source/model changes.

Raster sampling transforms WGS84 points into each raster CRS and reads the containing cell. Masked/non-finite values remain missing. Permanent-water and unknown-land points are rejected. LocationType: <=100m Riverside, >100-300m NearWaterway, >300m Inland. MGB geometries are repaired in memory; overlap priority is VHF > HF > MF. No polygon match means NotMapped, not low risk.

Rainfall is historical NASA POWER MERRA-2 at 14.281N,121.398E, shared across Santa Cruz. It is not live weather or a household gauge. Latest complete date: 2025-12-31; rolling windows include the selected UTC day. Static layers do not reconstruct historical household conditions.

Server validation covers boundaries, rainfall availability, household constraints and age-group consistency. Disability may overlap age groups in reality; the requested disjoint-count constraint remains a prototype simplification. No names or addresses are requested.

### Reproduction

Run `python scripts/train_synthetic_model.py` from flood_risk_system. It reads synthetic_dataset_tools/validated_dataset/synthetic_households_event_features.csv and refuses existing output directories. Use a separate output for future experiments. metrics.json records source SHA256, seed, features and sklearn version. Load the trusted local artifact with matching sklearn versions; never load arbitrary uploaded joblib files.

Run `python -m pytest` from flood_risk_system. Tests verify disjoint splits, provenance, saved-model outputs and reported metrics, excluded target-derived predictors, missing-value inference and API validation. The browser acceptance script checks the form and synthetic-model disclosures on desktop/mobile.


### Version 2 experiment (not promoted)

The new 364,000-row dataset, model, frozen experiment plan, validation comparison and final report are in data/synthetic_ml_v2. Four model configurations were selected using a separate validation set; one final test was used. Final synthetic accuracy: 84.08%; macro F1: 0.712; balanced accuracy: 67.28%; High recall: 36.31%. The 98% goal was not met. These scores are not directly comparable to v1 because the dataset changed. Due to the unmet target and weak High recall, v1 remains the app default.

Open /api/assessment/experiments/v2/report.html for the separate report. If deliberately experimenting with v2, set FLR_MODEL_DIR to its absolute directory before restarting; this does not constitute real-world validation. Reproduce with scripts/train_synthetic_model_v2.py; existing experiment folders are never overwritten.
