// System documentation for the Project AGAP flood risk assessment system (model v2.8).
// Usage: node build_sysdoc.js <flood_risk_system folder> <dataset folder> <figures folder> <output.docx>
const fs = require("fs");
const path = require("path");
const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell, WidthType, ShadingType, AlignmentType,
  HeadingLevel, LevelFormat, BorderStyle, Footer, PageNumber, PageBreak, VerticalAlign, ImageRun,
} = require("docx");

const [APP, DS, FIG, OUT] = process.argv.slice(2);
const FONT = "Times New Roman";
const CONTENT_PX = 624; // 6.5 in at 96 px per inch

// ---------- data
const csv = (f) => {
  const lines = fs.readFileSync(f, "utf8").trim().split(/\r?\n/);
  const head = lines[0].split(",");
  return lines.slice(1).map((l) => Object.fromEntries(l.split(",").map((v, i) => [head[i], v])));
};
const MODEL_DIR = path.join(APP, "data", "agap_noah_v2_8");
const META = JSON.parse(fs.readFileSync(path.join(MODEL_DIR, "metadata.json"), "utf8"));
const EV = META.evaluation;
const EVENTS = csv(path.join(MODEL_DIR, "rainfall_events.csv"));
const FI = csv(path.join(DS, "feature_importance.csv")).map((r) => ({ Input: r.Input, Drop: Number(r.MacroF1Drop) }));
const CM = META.confusion_matrix_holdout.matrix;
const VER = JSON.parse(fs.readFileSync(path.join(DS, "verification_results.json"), "utf8"));
const L = ["Low", "Medium", "High"];
const n0 = (v) => Math.round(v).toLocaleString("en-US");
const pct = (v, d = 1) => (v * 100).toFixed(d) + "%";
const sum = (a) => a.reduce((x, y) => x + y, 0);
const testN = sum(CM.map(sum));
const correct = sum(CM.map((r, i) => r[i]));
const range = (t) => {
  const v = EVENTS.filter((e) => e.EventType === t).map((e) => Number(e.Rainfall24hMm));
  return `${Math.min(...v).toFixed(1)}–${Math.max(...v).toFixed(1)} mm`;
};

// ---------- text helpers (**bold**, ~italic~)
function runs(text, base = {}) {
  const out = [];
  const re = /(\*\*[^*]+\*\*|~[^~]+~)/g;
  let last = 0, m;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(new TextRun({ text: text.slice(last, m.index), ...base }));
    const t = m[0];
    out.push(new TextRun({ ...base, text: t.slice(t.startsWith("**") ? 2 : 1, t.startsWith("**") ? -2 : -1), ...(t.startsWith("**") ? { bold: true } : { italics: true }) }));
    last = m.index + t.length;
  }
  if (last < text.length) out.push(new TextRun({ text: text.slice(last), ...base }));
  return out;
}
const P = (text, opts = {}) => new Paragraph({ children: runs(text), spacing: { after: 160, line: 276 }, indent: { firstLine: 720 }, alignment: AlignmentType.JUSTIFIED, ...opts });
const H1 = (text) => new Paragraph({ heading: HeadingLevel.HEADING_1, children: [new TextRun(text)] });
const H2 = (text) => new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun(text)] });
const bullet = (text) => new Paragraph({ numbering: { reference: "bullets", level: 0 }, children: runs(text), spacing: { after: 80, line: 276 }, alignment: AlignmentType.JUSTIFIED });
const step = (ref, text) => new Paragraph({ numbering: { reference: ref, level: 0 }, children: runs(text), spacing: { after: 80, line: 276 }, alignment: AlignmentType.JUSTIFIED });
const caption = (text) => new Paragraph({ children: runs(text, { size: 22 }), spacing: { before: 200, after: 80 }, keepNext: true, keepLines: true });
const note = (text) => new Paragraph({ children: runs(text, { size: 20, italics: true }), spacing: { before: 60, after: 200 } });
const spacer = (after = 120) => new Paragraph({ children: [], spacing: { after } });

const border = { style: BorderStyle.SINGLE, size: 4, color: "8EAADB" };
const borders = { top: border, bottom: border, left: border, right: border };
function table(headers, rows, widths, { shade = () => null, size = 20, center = [] } = {}) {
  const cell = (text, w, fill, b, colIdx) => new TableCell({
    width: { size: w, type: WidthType.DXA }, borders, verticalAlign: VerticalAlign.CENTER,
    margins: { top: 60, bottom: 60, left: 100, right: 100 },
    shading: fill ? { type: ShadingType.CLEAR, color: "auto", fill } : undefined,
    children: [new Paragraph({ alignment: center.includes(colIdx) ? AlignmentType.CENTER : AlignmentType.LEFT,
      children: runs(String(text), { size, bold: b || undefined }) })],
  });
  return new Table({
    width: { size: sum(widths), type: WidthType.DXA }, columnWidths: widths,
    rows: [
      new TableRow({ tableHeader: true, cantSplit: true, children: headers.map((h, i) => cell(h, widths[i], "D9E2F3", true, i)) }),
      ...rows.map((r, ri) => new TableRow({ cantSplit: true, children: r.map((c, i) => cell(c, widths[i], shade(ri, i), false, i)) })),
    ],
  });
}
let T = 0, F = 0;
const tn = () => ++T;
function figure(file, widthPx, text) {
  const buf = fs.readFileSync(path.join(FIG, file));
  const w = buf.readUInt32BE(16), h = buf.readUInt32BE(20);
  const width = Math.min(widthPx, CONTENT_PX), height = Math.round(width * h / w);
  const no = ++F;
  return [
    new Paragraph({ alignment: AlignmentType.CENTER, spacing: { before: 160, after: 60 }, keepNext: true,
      children: [new ImageRun({ type: "png", data: buf, transformation: { width, height },
        altText: { title: `Figure ${no}`, description: text, name: `Figure ${no}` } })] }),
    new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 220 }, children: runs(`**Figure ${no}.** ${text}`, { size: 22 }) }),
  ];
}

const body = [];
body.push(new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 160 },
  children: [new TextRun({ text: "System Documentation: Flood Risk Assessment System", size: 32 })] }));
body.push(new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 360 },
  children: [new TextRun({ text: "Project AGAP: AI-Based Flood Vulnerability Assessment and Evacuation Prioritization System for Santa Cruz, Laguna", size: 24, italics: true })] }));

// 1
body.push(H1("1. Introduction"));
body.push(P("This document describes how the flood risk assessment system of Project AGAP works. It explains what the user does, what data the system gathers for a household, how the machine learning model turns that data into a risk level, and how the system is run and maintained. It accompanies the model evaluation report, which presents the training dataset and the test results in detail."));
body.push(P(`The version described here uses the risk model trained on the AGAP NOAH dataset, version ${META.dataset.version} (${META.version}). The system is a research prototype: it classifies households using official flood maps, terrain data, historical rainfall, and household characteristics, but its risk levels were learned from rule-based labels and have not yet been validated against recorded flood outcomes.`));

// 2
body.push(H1("2. System Overview"));
body.push(P("The system runs as a small web application on a local computer. The user works in a web browser; a Python server gathers the data for the selected location, runs the trained model, and returns the result. Figure 1 shows how data moves through the system."));
body.push(...figure("fig_flow.png", 320, "Flow of data through the system"));
body.push(P("Table 1 lists the main parts of the system and the files that implement them."));
const t1 = tn();
body.push(caption(`**Table ${t1}.** Main parts of the system`));
body.push(table(["Part", "What it does", "Files"], [
  ["Web page", "Map for choosing a location, display of the environmental data, household form, and result", "app/static/assessment.html, assessment.js, assessment.css"],
  ["Server", "Receives requests, validates inputs, and returns results", "app/main.py, app/assessment.py"],
  ["Terrain lookup", "Reads elevation, slope, distance to waterway, land cover, and past surface water at a point", "app/gis/extraction.py, app/gis/discovery.py"],
  ["Flood map lookups", "Reads the Project NOAH classes (barangay and point) and the MGB flood susceptibility class", "app/noah.py, app/mgb.py"],
  ["Risk model", "Loads the trained model and predicts the risk level for each rain event", "app/ml_model.py, data/agap_noah_v2_8/"],
  ["Training script", "Trains the model from the dataset and saves it for the system", "scripts/train_agap_noah_model.py"],
  ["Tests", "Automated checks of the data lookups, the model, and the web interface", "tests/, scripts/check_assessment_browser.py"],
], [1700, 4260, 3400], { size: 19 }));
body.push(spacer());

// 3
body.push(H1("3. Using the System"));
body.push(H2("3.1 Choosing a Location"));
body.push(P("The user clicks the house on the map or types its latitude and longitude. The system first checks that the point lies inside one of the 26 barangays of Santa Cruz and that it is on land rather than water. It then reads the environmental data for that exact point and displays it, as in Figure 2."));
body.push(...figure("shot_location.png", 600, "Location step: the pinned house in Santisima Cruz and its environmental data"));
body.push(H2("3.2 Environmental Data"));
body.push(P("Thirteen values are read at the selected point. None of them is entered by the user. Table 2 lists them with their sources."));
const t2 = tn();
body.push(caption(`**Table ${t2}.** Environmental data read at the selected point`));
body.push(table(["Value", "Source", "How it is read"], [
  ["Elevation, slope", "Copernicus DEM GLO-30 (slope derived from it)", "Raster cell containing the point"],
  ["Distance to waterway", "OpenStreetMap rivers, streams, canals, drains, and ditches", "Raster of distances to the nearest waterway"],
  ["Location type", "Derived from the distance", "Riverside under 100 m, NearWaterway 100–300 m, Inland 300 m or more"],
  ["Land cover", "ESA WorldCover 2021", "Raster cell containing the point"],
  ["Past surface water", "JRC Global Surface Water, 1984–2024", "Share of satellite observations in which the point was under water"],
  ["NOAH class at the point (5-, 25-, 100-year)", "Project NOAH flood hazard maps", "Flood zone that contains the point; None if outside all zones"],
  ["NOAH class of the barangay (5-, 25-, 100-year)", "Project NOAH maps and barangay boundaries", "Highest class covering at least 25 percent of the barangay"],
  ["MGB flood susceptibility", "MGB Detailed Flood Susceptibility", "Susceptibility area that contains the point; NotMapped if outside"],
], [2700, 3360, 3300], { size: 19 }));
body.push(note("“None” and “NotMapped” mean the point is outside the mapped flood areas. They do not mean the place cannot flood."));

body.push(H2("3.3 Household Details"));
body.push(P("The user enters six details about the household, shown in Figure 3. The server checks each value before the model is run, using the rules in Table 3, so that the inputs stay within the range the model was trained on."));
body.push(...figure("shot_household.png", 600, "Household details form"));
const t3 = tn();
body.push(caption(`**Table ${t3}.** Household details and validation rules`));
body.push(table(["Field", "Allowed values", "Rule"], [
  ["Age of household head", "Whole number, 18–100", "A head aged 60 or older must be counted in Elders"],
  ["Housing material", "Concrete, Semi-concrete (mixed), Light materials", "Must be one of the three"],
  ["Household members", "1–12", "Includes the household head"],
  ["Children (under 18)", "0–12", "Children + elders cannot exceed members; a head under 60 must leave room for the head"],
  ["Elders (60 and older)", "0–12", "Same as above"],
  ["Persons with disability", "0–12", "Cannot exceed members; may overlap with children or elders"],
], [2500, 3060, 3800], { size: 19 }));
body.push(spacer());

body.push(H2("3.4 Prediction across Rain Events"));
body.push(P(`The user does not choose a rainfall date. Instead, the system runs the model for the household under each of the ${EVENTS.length} historical rain events that the model was trained on, taken from the NASA POWER daily rainfall record for Santa Cruz (1991–2025). The events cover a wide range of rainfall, as shown in Table 4. Each run uses the same household and location values and changes only the rainfall.`));
const t4 = tn();
body.push(caption(`**Table ${t4}.** Rain events used for each assessment`));
body.push(table(["Event group", "Number", "24-hour rainfall", "How they were chosen"], [
  ["Extreme", "15", range("Extreme"), "The 15 wettest days on record, at least 10 days apart"],
  ["Heavy", "15", range("Heavy"), "Days with 50 mm or more, chosen at random"],
  ["Moderate", "15", range("Moderate"), "Days with 15–50 mm"],
  ["Light", "15", range("Light"), "Days with less than 15 mm"],
], [1500, 1100, 2300, 4460], { size: 19, center: [1, 2] }));
body.push(spacer());

body.push(H2("3.5 Reading the Result"));
body.push(P("The result, shown in Figure 4, gives the household’s risk level together with the details behind it:"));
for (const t of [
  "**Risk level in major storms.** The most common predicted level across the 15 extreme rain events. If two levels are tied, the higher one is shown, which is the cautious choice for evacuation planning.",
  "**Counts.** How many of the extreme events, and of all 60 events, gave Low, Medium, and High.",
  "**Highest level reached.** The worst predicted level and the rain event that produced it.",
  "**Table of rain events.** The predicted level for each of the 60 events, with its date and rainfall.",
  "**Model and evaluation details.** The model version, its test scores, and any missing or unusual inputs.",
]) body.push(bullet(t));
body.push(...figure("shot_result_top.png", 600, "Result for a light-material household in Santisima Cruz (first rows of the rain event table shown)"));

// 4
body.push(H1("4. The Machine Learning Component"));
body.push(H2("4.1 Model and Training"));
body.push(P(`The model is a gradient boosting classifier. It consists of 500 small decision trees, each a short series of yes-or-no questions about the inputs, such as whether the house lies in an MGB High area or whether the 24-hour rainfall is above a certain amount. Each new tree concentrates on the cases that the earlier trees classified wrongly, and together the trees produce a score for Low, Medium, and High. The questions are not written by hand; the model learns them from the training examples. Table 5 summarizes the model.`));
const t5 = tn();
body.push(caption(`**Table ${t5}.** The trained model`));
body.push(table(["Item", "Value"], [
  ["Algorithm", "HistGradientBoostingClassifier (scikit-learn), 500 iterations"],
  ["Training data", `${n0(META.dataset.rows)} records of the AGAP NOAH dataset, version ${META.dataset.version}`],
  ["Inputs", `${META.features.length}: six household values, six terrain values, seven flood hazard classes (six NOAH, one MGB), three rainfall totals`],
  ["Output", "Low, Medium, or High, with a score for each"],
  ["Class imbalance", "Class weights, so that mistakes on the rarer Medium and High classes count more"],
  ["Saved model", "data/agap_noah_v2_8/model.joblib, with metadata.json describing its inputs and test results"],
], [2600, 6760], { size: 19 }));
body.push(spacer());
body.push(P("The training labels follow the study’s documented risk rule: the flood hazard at the house (the worse of the NOAH and MGB maps, adjusted by a terrain index of elevation, distance to waterway, slope, land cover, and past surface water) multiplied by household vulnerability. The model therefore learns how these factors combine. It does not observe floods itself; its knowledge of where flooding occurs comes from the flood maps read at the house."));

body.push(H2("4.2 Inputs the Model Relies On"));
body.push(P("Table 6 shows how much the model depends on each input, measured on households it had not seen in training: each input was shuffled in turn and the drop in macro F1-score was recorded. The ten inputs with the largest effect are listed."));
const t6 = tn();
body.push(caption(`**Table ${t6}.** The ten inputs the model relies on most`));
body.push(table(["Input", "Drop in macro F1 (points)"], FI.slice(0, 10).map((r) => [r.Input, (r.Drop * 100).toFixed(1)]),
  [6000, 3360], { size: 19, center: [1] }));
body.push(spacer());
body.push(P("Rainfall, MGB flood susceptibility, housing material, and the NOAH class at the house carry most of the prediction. Distance to waterway and elevation also contribute through the terrain index. The barangay NOAH classes, location type, and past surface water have almost no effect: the first two add nothing beyond the house-level class and the exact distance, and very few households have any recorded past surface water."));

body.push(H2("4.3 Performance"));
body.push(P(`The model was tested on 1,548 households that were kept out of training. It classified ${n0(correct)} of ${n0(testN)} test records correctly (${pct(correct / testN, 2)}), with a macro F1-score of ${pct(EV.holdout_80_20.MacroF1, 2)}; five-fold cross-validation gave a mean macro F1-score of ${pct(EV.cv_5fold_mean.MacroF1, 2)}. Table 7 shows the confusion matrix.`));
const t7 = tn();
body.push(caption(`**Table ${t7}.** Confusion matrix, hold-out test`));
body.push(table(["Actual \\ Predicted", "Low", "Medium", "High", "Total"],
  CM.map((r, i) => [`**${L[i]}**`, ...r.map(n0), n0(sum(r))]),
  [2860, 1625, 1625, 1625, 1625], { center: [1, 2, 3, 4], shade: (ri, ci) => (ci === ri + 1 ? "E2EFDA" : null) }));
body.push(spacer());
body.push(P(`All errors were one level apart, and no High-risk record was predicted as Low. The F1-score of the High class was ${pct(EV.holdout_80_20.F1_High, 2)} on the hold-out test and ${pct(EV.cv_5fold_mean.F1_High, 2)} across the folds, slightly below the study’s 96 percent target. The full results are in the model evaluation report. Because the labels follow a rule, these scores show how well the model learned that rule, not how accurately it predicts real floods.`));

body.push(H2("4.4 Consistency between Training and the System"));
body.push(P("A model gives reliable results only if the system prepares its inputs exactly as they were prepared for training. The system therefore uses the same code that built the training dataset: the terrain values come from the same lookup module and the same raster files, and the NOAH and MGB classes from the same point lookups. This was checked directly:"));
for (const t of [
  "For all 7,737 households in the dataset, the system’s NOAH and MGB lookups return exactly the dataset values (no mismatches).",
  "For sampled dataset records, all 22 inputs that the system builds equal the training values.",
  `The dataset itself passed all ${VER.checks.length} verification checks against its sources.`,
  "Values the model has never seen, such as an unusual land cover class, are treated as unknown and reported in the result instead of being guessed.",
]) body.push(bullet(t));

// 5
body.push(H1("5. Data Sources and Licenses"));
const t8 = tn();
body.push(caption(`**Table ${t8}.** Data used by the system`));
body.push(table(["Source", "Used for", "License or terms"], [
  ["Project NOAH flood hazard maps (UP NOAH Center)", "NOAH classes", "ODbL 1.0"],
  ["MGB Detailed Flood Susceptibility", "MGB class", "No license stated; public government data"],
  ["Copernicus DEM GLO-30 (EU and ESA)", "Elevation, slope", "Copernicus WorldDEM-30 Licence"],
  ["OpenStreetMap", "Distance to waterway", "ODbL 1.0"],
  ["ESA WorldCover 2021 v200", "Land cover", "CC BY 4.0"],
  ["JRC Global Surface Water v1.5", "Past surface water", "Free, no restriction of use"],
  ["NASA POWER", "Rain events", "Free; acknowledgment requested"],
  ["GEODATA Systems barangay boundaries", "Barangay check and barangay NOAH classes", "Proprietary"],
], [3700, 2660, 3000], { size: 19 }));
body.push(spacer());
body.push(P("The license texts and the required credit lines are in the licenses folder that accompanies the dataset. Before the system is published or deployed publicly, the barangay boundaries should be replaced with an openly licensed set, and reuse of the MGB data should be confirmed with MGB."));

// 6
body.push(H1("6. Running and Maintaining the System"));
body.push(H2("6.1 Requirements"));
for (const t of [
  "Windows with Python 3.10 or later and the packages listed in flood_risk_system/requirements.txt (FastAPI, Uvicorn, scikit-learn 1.8, pandas, rasterio, shapely, pyproj, joblib).",
  "The project folder C:\\Datasets_Pref with the GIS rasters in Sta_Cruz_Laguna_GIS_Download/output and the system data in flood_risk_system/data.",
  "A web browser. The map background needs an internet connection; everything else works offline.",
]) body.push(bullet(t));
body.push(H2("6.2 Starting and Stopping"));
for (const t of [
  "Open PowerShell in C:\\Datasets_Pref and run **python start_flood_system.py**.",
  "Wait for the message that the server is running, then open **http://127.0.0.1:8000/** in the browser.",
  "Keep the PowerShell window open while using the system. Press **Ctrl + C** in it to stop the system.",
  "Run only one copy at a time. If the message “only one usage of each socket address” appears, another copy is already using port 8000 and must be stopped first.",
]) body.push(step("start", t));
body.push(H2("6.3 Updating the Model"));
body.push(P("When the dataset is rebuilt, the model is updated in four steps:"));
for (const t of [
  "Rebuild and check the dataset in Activities/AGAP_NOAH_Dataset (build_noah_dataset.py, verify_dataset.py, check_model.py).",
  "Run **python scripts/train_agap_noah_model.py** in flood_risk_system. It trains the model on the whole dataset and saves it, its metadata, its report, and the rain events in a new data/agap_noah_v<version> folder.",
  "Point MODEL_DIR in app/ml_model.py to the new folder, and copy the new barangay_flood_hazard.csv into data/noah_flood_hazard.",
  "Run **python -m pytest** and restart the system.",
]) body.push(step("update", t));
body.push(H2("6.4 Testing"));
body.push(P("The system has 94 automated tests, run with python -m pytest in the flood_risk_system folder. They check the coordinate and household validation, the terrain, NOAH, and MGB lookups against the dataset, the saved model and its inputs, the 60-event prediction, and the effect of terrain on the result. A separate browser test (scripts/check_assessment_browser.py) opens the page, places a pin, submits a household, and checks the result table and the mobile layout."));

// 7
body.push(H1("7. Privacy, Validation, and Safety"));
for (const t of [
  "**No household data is stored.** The household details are used only to produce the response and are not saved.",
  "**Map values cannot be overridden.** The server rejects any request that tries to supply environmental values or a risk level; these always come from the system’s own lookups.",
  "**Inputs are checked.** Locations outside Santa Cruz or on water, and household values outside the allowed ranges, are rejected with a message.",
  "**Only the system’s own model is loaded.** The model file is produced locally by the training script; the system never loads uploaded files.",
  "**Results are labelled as prototype output.** Every result states that the model was trained on rule-based labels and is not validated for real residents, and that Low does not mean safe.",
]) body.push(bullet(t));

// 8
body.push(H1("8. Limitations"));
for (const t of [
  "**Rule-based labels.** The model reproduces the study’s risk rule; it has not been validated against recorded floods or evacuations.",
  "**Flood maps are models.** NOAH and MGB show expected flooding, not observed flooding, and may miss local problems such as blocked drains.",
  "**Historical rainfall only.** The system replays past rain events; it does not use live weather or forecasts.",
  "**One rainfall point.** All rain events come from a single NASA POWER grid point for the whole municipality, and the 2020 values appear much higher than other records.",
  "**Assumptions in the risk rule.** The terrain index weights, the MGB conversion, and the vulnerability weights are design choices that are not yet calibrated on local data.",
  "**Evacuation priority not yet included.** The system gives each household’s risk level; ranking households and assigning evacuation centers are planned next.",
]) body.push(bullet(t));

// Appendix A
body.push(new Paragraph({ children: [new PageBreak()] }));
body.push(H1("Appendix A. Server Requests Used by the Assessment Page"));
body.push(table(["Request", "Purpose"], [
  ["GET /", "Assessment page"],
  ["GET /api/assessment/config", "Model availability and the rain event summary"],
  ["GET /api/assessment/barangays", "Barangay boundaries for the map"],
  ["POST /api/assessment/location", "Environmental data for a latitude and longitude"],
  ["POST /api/assessment/prepare", "Environmental data, household validation, and the 60-event prediction"],
  ["GET /api/assessment/model-report", "Model evaluation summary page"],
  ["GET /api/assessment/confusion_matrix.png", "Confusion matrix image"],
], [4200, 5160], { size: 19 }));

// Appendix B
body.push(H1("Appendix B. Model Inputs"));
body.push(table(["No.", "Input", "Group"], META.features.map((f, i) => [String(i + 1), f,
  ["Age", "HousingMaterial", "HouseMembers", "ChildrenCount", "ElderCount", "PWDCount"].includes(f) ? "Household (form)"
    : f.startsWith("Rainfall") ? "Rainfall (rain event)" : f.startsWith("HouseFlood") ? "NOAH at the point"
    : f.startsWith("FloodHazard") ? "NOAH barangay class" : f === "MGBFloodSusceptibility" ? "MGB at the point" : "Terrain at the point"]),
  [800, 4400, 4160], { size: 19, center: [0] }));

// ---------- document
const doc = new Document({
  creator: "Group 9",
  title: "System Documentation: Flood Risk Assessment System",
  styles: {
    default: { document: { run: { font: FONT, size: 24 } } },
    paragraphStyles: [
      { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true, run: { font: FONT, size: 30 }, paragraph: { spacing: { before: 360, after: 120 }, keepNext: true, keepLines: true, outlineLevel: 0 } },
      { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true, run: { font: FONT, size: 26 }, paragraph: { spacing: { before: 240, after: 100 }, keepNext: true, keepLines: true, outlineLevel: 1 } },
    ],
  },
  numbering: { config: [
    { reference: "bullets", levels: [{ level: 0, format: LevelFormat.BULLET, text: "•", alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 720, hanging: 360 } } } }] },
    { reference: "start", levels: [{ level: 0, format: LevelFormat.DECIMAL, text: "%1.", alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 720, hanging: 360 } } } }] },
    { reference: "update", levels: [{ level: 0, format: LevelFormat.DECIMAL, text: "%1.", alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 720, hanging: 360 } } } }] },
  ] },
  sections: [{
    properties: { page: { size: { width: 12240, height: 15840 }, margin: { top: 1440, right: 1440, bottom: 1440, left: 1440 } } },
    footers: { default: new Footer({ children: [new Paragraph({ alignment: AlignmentType.CENTER,
      children: [new TextRun({ text: "Page ", size: 18, color: "7F7F7F" }), new TextRun({ children: [PageNumber.CURRENT], size: 18, color: "7F7F7F" }),
                 new TextRun({ text: " of ", size: 18, color: "7F7F7F" }), new TextRun({ children: [PageNumber.TOTAL_PAGES], size: 18, color: "7F7F7F" })] })] }) },
    children: body,
  }],
});
Packer.toBuffer(doc).then((buf) => { fs.writeFileSync(OUT, buf); console.log("wrote", OUT); });
