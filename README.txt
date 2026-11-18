PROJECT AGAP - FLOOD RISK ASSESSMENT SYSTEM (FINAL COPY)
Santa Cruz, Laguna | CS 3-B Group 9

This folder is a complete, verified copy of the final system: the app, the trained model, the
final dataset, the data sources it was built from, and the evaluation results. It keeps the same
folder layout as the original project, so the app and the scripts run from here unchanged.


WHERE TO FIND THINGS
--------------------

Final dataset ........ Activities\AGAP_NOAH_Dataset\
    agap_noah_training.csv        The training file: 232,110 records, 22 inputs + RiskLevel
                                  (the same file as "CS 3-B_Group_9_Dataset_CSST 101 & CSST 102.csv")
    agap_noah_dataset.csv         Same records plus HouseholdID, Barangay, Latitude, Longitude, EventDate
    agap_noah_dataset_raw.csv     Raw version before cleaning (duplicates, missing values, wrong labels)
    data_dictionary.csv           Meaning and source of every column
    dataset_card.md               How the dataset was built and the risk rule
    barangay_flood_hazard.csv     NOAH flood class and area shares of each barangay
    rainfall_events.csv           The 60 historical rain events
    label_audit.csv               Values behind each RiskLevel (not model inputs)

Evaluation ........... Activities\AGAP_NOAH_Dataset\
    model_metrics.csv             Accuracy, precision, recall, F1, macro/weighted F1, balanced accuracy,
                                  Cohen's kappa and MCC for the 80/20 test and each of the 5 folds
    model_confusion_matrix.csv    Confusion matrix of the 80/20 test
    feature_importance.csv        How much the model relies on each input
    verification_results.json     The 30 checks of the dataset against its sources (all passed)
    AGAP_NOAH_Model_Evaluation_Report_final.docx / .pdf
    AGAP_Flood_Risk_System_Documentation.docx

Licenses ............. Activities\AGAP_NOAH_Dataset\licenses\   (start with 00_READ_ME_FIRST.txt)

App (main code) ...... flood_risk_system\
    app\main.py                   Web server
    app\assessment.py             Location data, household checks, prediction request
    app\ml_model.py               Loads the model and predicts across the 60 rain events
    app\noah.py, app\mgb.py       NOAH and MGB flood lookups at the pinned point
    app\gis\                      Terrain lookups (elevation, slope, waterway distance, land cover, water)
    app\static\                   Web page (assessment.html, assessment.js, assessment.css)
    data\agap_noah_v2_8\          The trained model (model.joblib) and its metadata
    data\noah_flood_hazard\       NOAH flood maps used by the app
    data\assessment_sources\      Barangay boundaries, rasters, rainfall and MGB map used by the app
    data\osm\                     Map layers for the GIS explorer page
    scripts\train_agap_noah_model.py   Trains the model from the dataset
    tests\                        Automated tests (91 pass)

Data sources ......... (used to build the dataset and by the app)
    Sta_Cruz_Laguna_GIS_Download\output\   Elevation, slope, waterway distance, land cover and
                                           surface-water rasters for Santa Cruz
    Sta_Cruz_Laguna_GIS_Download\raw\      Original downloaded tiles (Copernicus DEM, ESA WorldCover,
                                           JRC Global Surface Water) and barangay boundaries
    NOAH_Flood_Hazard_Laguna\              NOAH flood maps clipped to Santa Cruz, plus the original
                                           NOAH downloads, readme and license
    MGB_Flood_Susceptibility\              MGB flood susceptibility map and its source record
    nasa_power_daily_rainfall_features_1991_2025.csv   NASA POWER daily rainfall
    Activities\AGAP_Dataset\               House locations and household profiles used as the base

Dataset scripts ...... Activities\AGAP_NOAH_Dataset\
    build_noah_dataset.py (builds the dataset), verify_dataset.py (30 checks),
    check_model.py (model evaluation), feature_importance.py


HOW TO RUN THE APP
------------------
Open PowerShell in this folder and run:   python start_flood_system.py
Then open http://127.0.0.1:8000/ in a browser. Press Ctrl + C to stop.
Pages: /ops (MDRRMO operations console), /report (public flood/emergency report), /assessment (self-assessment),
/explorer (GIS explorer). Demo households: python flood_risk_system\scripts\seed_demo_residents.py (--clear removes them).
To let phones on the same network send reports: set FLR_HOST=0.0.0.0 and FLR_OPS_PIN=<a PIN> before starting.
(Only one copy can run at a time. Stop the original project's app first if it is running.)


HOW THIS COPY WAS VERIFIED
--------------------------
- MANIFEST.csv lists every file, where it was copied from, its size and its SHA-256 fingerprint.
  Every copy matched its original exactly.
- The app's 91 automated tests pass when run from this folder. (3 tests about an earlier,
  retired experiment are skipped because that experiment's data was not copied.)
- The app started from this folder and returned predictions with the final model (agap-noah-v2.8).
- verify_dataset.py, run inside this folder, passed all 30 checks against the copied sources.

Not included: earlier model versions and outdated reports, log files, the app's local database
(created automatically when the app starts), and the unzipped NOAH shapefiles (the original
NOAH zip files are included in NOAH_Flood_Hazard_Laguna\downloads).
