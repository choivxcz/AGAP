// Model Evaluation Report (dataset v2.8) in exactly the group's section structure (Times New Roman, no letter/header).
// Usage: node build_eval_v26.js <dataset folder> <output.docx>
const fs = require("fs");
const path = require("path");
const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell, WidthType, ShadingType, AlignmentType,
  HeadingLevel, LevelFormat, BorderStyle, Footer, PageNumber, TabStopType, PageBreak, VerticalAlign,
} = require("docx");

const DS = process.argv[2];
const OUT = process.argv[3];
const FONT = "Times New Roman";
const CONTENT = 9360;

// ---------- data
const csv = (f) => {
  const lines = fs.readFileSync(path.join(DS, f), "utf8").trim().split(/\r?\n/);
  const head = lines[0].split(",");
  return lines.slice(1).map((l) => Object.fromEntries(l.split(",").map((v, i) => [head[i], v])));
};
const META = JSON.parse(fs.readFileSync(path.join(DS, "generation_metadata.json"), "utf8"));
const VER = JSON.parse(fs.readFileSync(path.join(DS, "verification_results.json"), "utf8"));
const M = csv("model_metrics.csv").map((r) => Object.fromEntries(Object.entries(r).map(([k, v]) => [k, k === "Model" || k === "Test" ? v : Number(v)])));
const get = (model, test) => M.find((r) => r.Model === model && r.Test === test);
const W = "With class weights", N = "Without class weights", B = "Barangay hazard only";
const w80 = get(W, "80/20 split"), wM = get(W, "5-fold mean"), wS = get(W, "5-fold SD");
const nM = get(N, "5-fold mean"), b80 = get(B, "80/20 split"), bM = get(B, "5-fold mean");
const folds = (model) => M.filter((r) => r.Model === model && r.Test.startsWith("Fold"));
const readCM = (f) => csv(f).map((r) => ["pred Low", "pred Medium", "pred High"].map((k) => Number(r[k])));
const CM = readCM("model_confusion_matrix.csv"), CMB = readCM("model_confusion_matrix_barangay_only.csv");
const BH = csv("barangay_flood_hazard.csv");
const FI = csv("feature_importance.csv").map((r) => ({ Input: r.Input, Drop: Number(r.MacroF1Drop) }));
const HH = csv("agap_noah_dataset.csv").filter((r, i, a) => i === 0 || r.HouseholdID !== a[i - 1].HouseholdID);
const CC = META.clean.class_counts, NREC = META.clean.rows, NHH = META.clean.households;
const L = ["Low", "Medium", "High"];

const n0 = (v) => Math.round(v).toLocaleString("en-US");
const pct = (v, d = 2) => (v * 100).toFixed(d) + "%";
const pm = (m, s) => `${(m * 100).toFixed(2)}% ± ${(s * 100).toFixed(2)}`;
const dec = (v) => v.toFixed(4);
const dpm = (m, s) => `${m.toFixed(4)} ± ${s.toFixed(4)}`;
const share = (k) => (100 * CC[k] / NREC).toFixed(1) + "%";
const sum = (a) => a.reduce((x, y) => x + y, 0);
const testN = w80.Support_Low + w80.Support_Medium + w80.Support_High;
const testHH = Math.round(testN / 30), trainHH = NHH - testHH;
const errs = (cm) => sum(cm.map((r, i) => sum(r.filter((_, j) => j !== i))));
const pLow = w80.Support_Low / testN, baseF1Low = (2 * pLow) / (1 + pLow);
const minFold = (model, k) => Math.min(...folds(model).map((r) => r[k]));
const maxFold = (model, k) => Math.max(...folds(model).map((r) => r[k]));
const missed = (model) => sum(folds(model).map((r) => r.Support_High * (1 - r.Recall_High))) / 5;
const falseHigh = (model) => sum(folds(model).map((r) => r.Support_High * r.Recall_High * (1 / r.Precision_High - 1))) / 5;
const nChecks = VER.checks.length, nPass = VER.checks.filter((c) => c.result === "PASS").length;
const bhCount = (rp, c) => BH.filter((r) => r["FloodHazard" + rp] === c).length;
const houseShare = (rp, c) => (100 * HH.filter((r) => r["HouseFloodHazard" + rp] === c).length / HH.length).toFixed(1) + "%";
const foldsBelow = folds(W).filter((r) => r.F1_High < 0.96);

// ---------- text helpers
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
const caption = (text) => new Paragraph({ children: runs(text, { size: 22 }), spacing: { before: 200, after: 80 }, keepNext: true, keepLines: true });
const note = (text) => new Paragraph({ children: runs(text, { size: 20, italics: true }), spacing: { before: 60, after: 200 } });
const spacer = (after = 120) => new Paragraph({ children: [], spacing: { after } });
const equation = (text, no) => new Paragraph({ spacing: { before: 100, after: 100 }, keepNext: true,
  tabStops: [{ type: TabStopType.CENTER, position: CONTENT / 2 }, { type: TabStopType.RIGHT, position: CONTENT }],
  children: [new TextRun({ text: "\t" + text, italics: true }), new TextRun(`\t(${no})`)] });

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
const cmTable = (cm) => table(["Actual \\ Predicted", "Low", "Medium", "High", "Total"],
  cm.map((r, i) => [`**${L[i]}**`, ...r.map(n0), n0(sum(r))]),
  [2860, 1625, 1625, 1625, 1625], { center: [1, 2, 3, 4], shade: (ri, ci) => (ci === ri + 1 ? "E2EFDA" : null) });

const body = [];
let T = 0; const tn = () => ++T;   // table numbering

// title
body.push(new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 160 },
  children: [new TextRun({ text: "Model Evaluation Report and Request for Dataset Approval", size: 32 })] }));
body.push(new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 360 },
  children: [new TextRun({ text: "Project AGAP: AI-Based Flood Vulnerability Assessment and Evacuation Prioritization System for Santa Cruz, Laguna", size: 24, italics: true })] }));

// 1
body.push(H1("1. Introduction"));
body.push(P("The machine learning component of Project AGAP classifies each household’s flood risk as Low, Medium, or High for a given rainfall event, and the system uses this classification to rank households for evacuation."));

// 2
body.push(H1("2. Dataset Submitted for Review"));
body.push(P("Each record describes one household during one historical rainfall event. The model receives 22 input variables and predicts RiskLevel. Table 1 gives an overview."));
const t1 = tn();
body.push(caption(`**Table ${t1}.** Overview of the training dataset`));
body.push(table(["Item", "Description"], [
  ["File", "agap_noah_training.csv"],
  ["Records", `${n0(NREC)} (${n0(NHH)} households × 30 rainfall events)`],
  ["Coverage", "All 26 barangays of Santa Cruz, Laguna"],
  ["Input variables", "22: six household, six terrain, seven flood hazard (six NOAH, one MGB), three rainfall"],
  ["Target variable", "RiskLevel (Low, Medium, High)"],
  ["Class distribution", `Low ${n0(CC.Low)} (${share("Low")}); Medium ${n0(CC.Medium)} (${share("Medium")}); High ${n0(CC.High)} (${share("High")})`],
  ["Missing values and duplicates", "None"],
], [2800, 6560]));
body.push(spacer());

body.push(H2("2.1 Real and Synthetic Variables"));
body.push(P("Sixteen of the 22 input variables come from real public datasets and were read from maps at the location of real buildings in Santa Cruz. The six household variables and the target are synthetic, because no household survey with recorded flood outcomes is yet available for the municipality. Table 2 lists every variable and its origin; synthetic rows are shaded."));
const Y = "FFF2CC";
const vars = [
  ["Age, HousingMaterial, HouseMembers, ChildrenCount, ElderCount, PWDCount", "Household profile", "**Synthetic** (generated with a fixed seed from design assumptions)"],
  ["ElevationMeters, SlopeDegrees", "Terrain", "Copernicus DEM GLO-30"],
  ["DistanceToWaterwayM, LocationType", "Terrain", "OpenStreetMap waterways"],
  ["LandCoverType", "Terrain", "ESA WorldCover 2021 v200"],
  ["HistoricalWaterOccurrencePct", "Terrain", "JRC Global Surface Water v1.5, 1984 to 2024"],
  ["FloodHazard5yr, FloodHazard25yr, FloodHazard100yr", "Flood hazard (barangay)", "Project NOAH flood hazard maps, summarized per barangay"],
  ["HouseFloodHazard5yr, HouseFloodHazard25yr, HouseFloodHazard100yr", "Flood hazard (house)", "Project NOAH flood hazard maps, read at the house location"],
  ["MGBFloodSusceptibility", "Flood hazard (house)", "MGB Detailed Flood Susceptibility, read at the house location"],
  ["Rainfall24hMm, Rainfall3DayMm, Rainfall7DayMm", "Rainfall", "NASA POWER daily rainfall, 1991 to 2025"],
  ["RiskLevel", "Target", "**Synthetic**"],
];
const t2 = tn();
body.push(caption(`**Table ${t2}.** Variables and their origin`));
body.push(table(["Variables", "Group", "Origin"], vars, [4000, 1700, 3660], { shade: (ri) => (vars[ri][2].includes("Synthetic") ? Y : null) }));
body.push(spacer());

body.push(H2("2.2 Flood Hazard per Barangay and at the House"));
body.push(P("The dataset describes the Project NOAH flood hazard in two ways. The first three variables describe the household’s barangay. For each rainfall scenario, the NOAH map was overlaid on the barangay boundaries, and the share of each barangay’s area in the Low, Medium, and High hazard zones was measured. The barangay is assigned the highest hazard level that covers at least 25 percent of its area, counting that level and the levels above it. A barangay with less than 25 percent of its area inside any NOAH flood zone is classified as None."));
body.push(P(`Counting the share at each level and above, rather than averaging the levels over the whole barangay, keeps localized deep-water zones visible. An average would treat unflooded land as zero and would classify Oogong, which has ${Number(BH.find((r) => r.Barangay === "Oogong").PctHigh_100yr).toFixed(1)} percent of its area in the High zone of the 100-year map, as Low. Under this rule, the 100-year scenario has ${bhCount("100yr", "Low")} Low, ${bhCount("100yr", "Medium")} Medium, and ${bhCount("100yr", "High")} High barangays. The full table is in Appendix A.`));
body.push(P(`The other three variables give the NOAH hazard level at the house itself. They were obtained through a point-in-polygon overlay of each household’s location on the NOAH maps, which the system can repeat when a user marks a house on the map. The two views differ considerably: although every barangay has some flood-prone area, ${houseShare("100yr", "None")} of the households lie outside all NOAH flood zones in the 100-year scenario (${houseShare("100yr", "Low")} Low, ${houseShare("100yr", "Medium")} Medium, and ${houseShare("100yr", "High")} High). The NOAH hazard levels represent modeled flood depths and do not indicate observed flooding.`));
body.push(P(`The last flood hazard variable is the flood susceptibility class of the Mines and Geosciences Bureau (MGB) at the house: Moderate (expected flood heights of 0.5 to 1 m), High (1 to 2 m), Very High (more than 2 m), or NotMapped. It is included because the NOAH maps, which simulate flooding from rainfall, do not cover some flood-prone areas that appear on the MGB map. Of the households outside all NOAH flood zones in the 100-year scenario, ${n0(HH.filter((r) => r.HouseFloodHazard100yr === "None" && r.MGBFloodSusceptibility !== "NotMapped").length)} are classified by MGB as Moderate or High, including every such household in Santisima Cruz, Santo Angel Central, and Santo Angel Norte.`));

body.push(H2("2.3 Risk Level Rule"));
body.push(P("RiskLevel follows a fixed rule in which risk is the product of flood hazard and household vulnerability. The event flood hazard, on a scale of 0 to 3, is the higher of two values computed at the house. The first is read from the NOAH hazard levels according to the effective rainfall (the 24-hour rainfall plus 30 percent of the two preceding days). The second is the MGB class converted to the same scale from MGB’s flood heights (Moderate 2, High 2.5, Very High 3); because the MGB map has no rainfall scenarios, its level is assumed to be reached at the 25-year rainfall and is scaled down in proportion for less rain. The house-level hazard is used because flood exposure differs between houses in the same barangay. The hazard is then adjusted for the terrain at the house: an index of elevation (35 percent), distance to waterway (25 percent), slope (15 percent), land cover (15 percent), and past surface water (10 percent) raises or lowers it by up to 25 percent, and gives houses outside every mapped flood zone a small hazard of up to the NOAH Low level. Household vulnerability, on a scale of 0 to 1, combines housing material (40 percent), dependents (25 percent), persons with disability (15 percent), household size (10 percent), and an elderly household head (10 percent). The two are combined with Equation 1."));
body.push(equation("RiskScore = 100 × (Hazard ÷ 3) × (0.40 + 0.60 × Vulnerability)", 1));
body.push(P("A score below 15 is Low, a score from 15 to below 40 is Medium, and a score of 40 or above is High. The weights and cut-off values are design choices of the study and are included in this request for review."));

// 3
body.push(H1("3. Evaluation Method"));
body.push(H2("3.1 Model"));
body.push(P("The model is a gradient boosting classifier (HistGradientBoostingClassifier, scikit-learn) with 500 boosting iterations and native handling of the ten categorical variables. Gradient boosting was chosen because it handles a mix of numeric and categorical inputs without scaling and performs well on tabular data of this size."));
body.push(H2("3.2 Separation of Training and Test Data"));
body.push(P("Every household appears in 30 records, one for each rainfall event, and its household and location values are the same in all of them. If records were split at random, the same household could appear in both the training and the test set, and the model would be tested partly on households it had already seen. To prevent this, all records of a household were always kept together. Two tests were run:"));
body.push(bullet(`**Hold-out test (80/20).** ${n0(trainHH)} households (${n0(NREC - testN)} records) were used for training and ${n0(testHH)} households (${n0(testN)} records) for testing.`));
body.push(bullet("**Five-fold cross-validation.** The households were divided into five groups. The model was trained five times, each time leaving out one group for testing, so every household was tested exactly once."));
body.push(H2("3.3 Handling of Class Imbalance"));
body.push(P(`The class distribution (${share("Low")} Low, ${share("Medium")} Medium, ${share("High")} High) is the result of the selected rainfall scenarios. Each household was paired with 9 extreme, 9 heavy, 6 moderate, and 6 light rainfall events, a set that deliberately includes far more heavy rain than ordinary weather so that the model sees enough high-risk cases. The distribution was not resampled to equal class sizes. To keep the model from favoring Low, each class was weighted in inverse proportion to its frequency during training, so that a mistake on a High record costs the model more than a mistake on a Low record. The same tests were repeated without class weights for comparison (Section 4.5).`));
body.push(H2("3.4 Evaluation Metrics"));
body.push(P(`Accuracy alone is misleading for imbalanced data: a model that always predicts Low would already be ${pct(pLow, 1)} accurate on the test set. The metrics in Table 3 were therefore reported, with the macro F1-score and the recall of the High class treated as the main measures. In the formulas, TP, FP, and FN are the true positives, false positives, and false negatives of a class.`));
const t3 = tn();
body.push(caption(`**Table ${t3}.** Evaluation metrics`));
body.push(table(["Metric", "Definition", "Meaning in this study"], [
  ["Accuracy", "Correct predictions ÷ all predictions", "Overall share of records classified correctly"],
  ["Precision", "TP ÷ (TP + FP)", "Of the households predicted High, the share that are truly High; low precision means false alarms"],
  ["Recall", "TP ÷ (TP + FN)", "Of the truly High households, the share the model found; low recall means at-risk households are missed"],
  ["F1-score", "2 × Precision × Recall ÷ (Precision + Recall)", "Balance of precision and recall for one class"],
  ["Macro F1", "Average F1 of the three classes", "Treats Low, Medium, and High as equally important"],
  ["Weighted F1", "F1 averaged by class size", "Reflects the class proportions of the data"],
  ["Balanced accuracy", "Average recall of the three classes", "Accuracy that is not inflated by the large Low class"],
  ["Cohen’s kappa", "Agreement corrected for chance", "1 is perfect agreement; 0 is no better than chance"],
  ["Matthews correlation coefficient (MCC)", "Correlation between actual and predicted classes", "1 is perfect; 0 is no better than chance"],
], [2300, 3000, 4060], { size: 19 }));
body.push(spacer());
body.push(H2("3.5 Acceptance Criteria"));
body.push(P("The following acceptance criteria were used. The model is considered acceptable if (a) the macro F1-score is at least 96 percent in both tests, (b) the F1-score of every risk level is at least 96 percent, and (c) no High-risk household is classified as Low, since that is the most serious error for an evacuation system."));

// 4
body.push(H1("4. Results"));
body.push(H2("4.1 Overall Performance"));
body.push(P(`Table 4 summarizes the performance of the model trained with all 22 variables and class weights. On the hold-out test, the model classified ${n0(testN - errs(CM))} of ${n0(testN)} records correctly (${pct(w80.Accuracy)}) and reached a macro F1-score of ${pct(w80.MacroF1)}. Cross-validation gave similar results, with a mean macro F1-score of ${pct(wM.MacroF1)} and a standard deviation of ${(wS.MacroF1 * 100).toFixed(2)} percentage points. The last column shows a baseline that always predicts Low, for comparison.`));
const t4 = tn();
body.push(caption(`**Table ${t4}.** Overall performance of the model trained with class weights`));
body.push(table(["Metric", "Hold-out test (80/20)", "Five-fold CV (mean ± SD)", "Always-Low baseline"], [
  ["Accuracy", pct(w80.Accuracy), pm(wM.Accuracy, wS.Accuracy), pct(pLow)],
  ["Balanced accuracy", pct(w80.BalancedAccuracy), pm(wM.BalancedAccuracy, wS.BalancedAccuracy), "33.33%"],
  ["Macro precision", pct(w80.MacroPrecision), pm(wM.MacroPrecision, wS.MacroPrecision), pct(pLow / 3)],
  ["Macro recall", pct(w80.MacroRecall), pm(wM.MacroRecall, wS.MacroRecall), "33.33%"],
  ["**Macro F1-score**", `**${pct(w80.MacroF1)}**`, `**${pm(wM.MacroF1, wS.MacroF1)}**`, pct(baseF1Low / 3)],
  ["Weighted F1-score", pct(w80.WeightedF1), pm(wM.WeightedF1, wS.WeightedF1), pct(baseF1Low * pLow)],
  ["Cohen’s kappa", dec(w80.CohenKappa), dpm(wM.CohenKappa, wS.CohenKappa), "0.0000"],
  ["MCC", dec(w80.MCC), dpm(wM.MCC, wS.MCC), "0.0000"],
], [2160, 2100, 2700, 2400], { center: [1, 2, 3] }));
body.push(note("SD is the standard deviation across the five folds. The baseline is computed on the same hold-out test set."));

body.push(H2("4.2 Performance per Risk Level"));
body.push(P(`Tables 5 and 6 break the results down by risk level. Low was classified almost perfectly and Medium nearly so. High, the smallest class, reached an F1-score of ${pct(w80.F1_High)} on the hold-out test and ${pct(wM.F1_High)} on average across the folds. Its recall on the hold-out test was ${pct(w80.Recall_High)}: of the ${n0(w80.Support_High)} High-risk records, ${n0(CM[2][2])} were identified.`));
const t5 = tn();
body.push(caption(`**Table ${t5}.** Results per risk level, hold-out test`));
body.push(table(["Risk level", "Precision", "Recall", "F1-score", "Support"], [
  ...L.map((c) => [c, pct(w80["Precision_" + c]), pct(w80["Recall_" + c]), pct(w80["F1_" + c]), n0(w80["Support_" + c])]),
  ["**Macro average**", pct(w80.MacroPrecision), pct(w80.MacroRecall), pct(w80.MacroF1), n0(testN)],
  ["**Weighted average**", pct(w80.WeightedPrecision), pct(w80.WeightedRecall), pct(w80.WeightedF1), n0(testN)],
], [2560, 1700, 1700, 1700, 1700], { center: [1, 2, 3, 4], shade: (ri) => (ri >= 3 ? "F2F2F2" : null) }));
body.push(note("Support is the number of test records in each risk level."));
const t6 = tn();
body.push(caption(`**Table ${t6}.** Results per risk level, five-fold cross-validation (mean ± SD)`));
body.push(table(["Risk level", "Precision", "Recall", "F1-score", "Lowest fold F1"], [
  ...L.map((c) => [c, pm(wM["Precision_" + c], wS["Precision_" + c]), pm(wM["Recall_" + c], wS["Recall_" + c]), pm(wM["F1_" + c], wS["F1_" + c]), pct(minFold(W, "F1_" + c))]),
  ["**Macro average**", pm(wM.MacroPrecision, wS.MacroPrecision), pm(wM.MacroRecall, wS.MacroRecall), pm(wM.MacroF1, wS.MacroF1), pct(minFold(W, "MacroF1"))],
], [1960, 1850, 1850, 1850, 1850], { center: [1, 2, 3, 4], shade: (ri) => (ri >= 3 ? "F2F2F2" : null) }));
body.push(spacer());

body.push(H2("4.3 Confusion Matrix"));
body.push(P("Table 7 compares the actual and predicted risk levels on the hold-out test. Correct predictions lie on the shaded diagonal."));
const t7 = tn();
body.push(caption(`**Table ${t7}.** Confusion matrix, hold-out test`));
body.push(cmTable(CM));
body.push(spacer());
body.push(P(`The model made ${n0(errs(CM))} errors out of ${n0(testN)} records (${pct(errs(CM) / testN)}). Every error was off by one level. No High-risk record was predicted as Low, and no Low-risk record was predicted as High. Among the High-risk records, ${n0(CM[2][1])} were predicted as Medium; these are the cases the evacuation list would rank lower than it should. In the other direction, ${n0(CM[1][2])} Medium-risk records were predicted as High, which would add households to the priority list that do not strictly need to be there.`));

body.push(H2("4.4 Consistency across Folds"));
body.push(P(`Across the five folds, the macro F1-score ranged from ${pct(minFold(W, "MacroF1"))} to ${pct(maxFold(W, "MacroF1"))}, and the F1-score of the High class from ${pct(minFold(W, "F1_High"))} to ${pct(maxFold(W, "F1_High"))}. The High class varies more than the others because each fold contains only about ${n0(wM.Support_High)} High-risk records, so a few errors change its score noticeably.`));

body.push(H2("4.5 Effect of Class Weights"));
body.push(P("Table 8 compares the model trained with and without class weights, using the cross-validation averages."));
const t8 = tn();
body.push(caption(`**Table ${t8}.** Model trained with and without class weights (five-fold CV mean)`));
body.push(table(["Metric", "Without class weights", "With class weights"], [
  ["Accuracy", pct(nM.Accuracy), pct(wM.Accuracy)],
  ["Macro F1-score", pct(nM.MacroF1), pct(wM.MacroF1)],
  ["Precision, High", pct(nM.Precision_High), pct(wM.Precision_High)],
  ["Recall, High", pct(nM.Recall_High), `**${pct(wM.Recall_High)}**`],
  ["F1-score, High", pct(nM.F1_High), pct(wM.F1_High)],
  ["High-risk records missed per fold", n0(missed(N)), `**${n0(missed(W))}**`],
  ["Non-High records flagged as High per fold", n0(falseHigh(N)), n0(falseHigh(W))],
], [4160, 2600, 2600], { center: [1, 2] }));
body.push(spacer());
body.push(P(`Both models performed similarly overall, and the model without class weights was slightly more precise. The difference lies in the High class. With class weights, recall for High rose from ${pct(nM.Recall_High)} to ${pct(wM.Recall_High)}, and the number of High-risk records missed in each fold fell from about ${n0(missed(N))} to about ${n0(missed(W))}, at the cost of about ${n0(falseHigh(W) - falseHigh(N))} more false alarms per fold. For an evacuation system, missing a household that needs to leave is more serious than including one that may not, so the model trained with class weights is the one proposed for the system.`));

body.push(H2("4.6 Summary against the Acceptance Criteria"));
const t10 = tn();
const met = (ok) => (ok ? "Met" : "Not met");
const everyFold = foldsBelow.length === 0;
const critOK = [w80.MacroF1 >= 0.96, wM.MacroF1 >= 0.96, L.every((c) => wM["F1_" + c] >= 0.96), everyFold, CM[2][0] === 0];
body.push(caption(`**Table ${t10}.** Results against the acceptance criteria`));
body.push(table(["Criterion", "Target", "Result", "Status"], [
  ["Macro F1-score, hold-out test", "≥ 96%", pct(w80.MacroF1), met(w80.MacroF1 >= 0.96)],
  ["Macro F1-score, five-fold CV mean", "≥ 96%", pct(wM.MacroF1), met(wM.MacroF1 >= 0.96)],
  ["F1-score of each risk level, CV mean", "≥ 96%", `Low ${pct(wM.F1_Low)}; Medium ${pct(wM.F1_Medium)}; High ${pct(wM.F1_High)}`, met(L.every((c) => wM["F1_" + c] >= 0.96))],
  ["F1-score of High in every fold", "≥ 96%", `Lowest fold ${pct(minFold(W, "F1_High"))}`, met(everyFold)],
  ["High-risk households predicted as Low", "0", n0(CM[2][0]), met(CM[2][0] === 0)],
], [3300, 1200, 3660, 1200], { center: [1, 3], shade: (ri, ci) => (ci === 3 ? (critOK[ri] ? "E2EFDA" : "FCE4D6") : null) }));
body.push(spacer());
const nMet = critOK.filter(Boolean).length;
const WORD = ["none", "one", "two", "three", "four", "all five"];
if (nMet < 5) body.push(P(`The model met ${WORD[nMet]} of the five criteria. The F1-score of the High class was ${pct(w80.F1_High)} on the hold-out test and ${pct(wM.F1_High)} on average across the folds (lowest fold ${pct(minFold(W, "F1_High"))}), ${wM.F1_High < 0.96 ? "slightly below" : "above"} the 96 percent target. The terrain adjustment makes the risk levels depend on gradual differences in elevation, distance to waterways, and slope, which are harder for the model to learn than the map classes. ${CM[2][0] === 0 ? "Every error was still off by one level, and no High-risk household was predicted as Low. " : ""}`));

// 6
body.push(H1("5. Data Sources and Licenses"));
body.push(P("The license texts and the exact credit lines for every source are provided in the licenses folder that accompanies the dataset. The table below summarizes them."));
const t11 = tn();
body.push(caption(`**Table ${t11}.** Data sources and licenses`));
body.push(table(["Source", "Used for", "License"], [
  ["Project NOAH flood hazard maps (UP NOAH Center)", "Flood hazard variables (barangay and house)", "ODbL 1.0"],
  ["Copernicus DEM GLO-30 (EU and ESA)", "Elevation, slope", "Copernicus WorldDEM-30 Licence"],
  ["OpenStreetMap", "Distance to waterway, location type", "ODbL 1.0"],
  ["ESA WorldCover 2021 v200", "Land cover", "CC BY 4.0"],
  ["JRC Global Surface Water v1.5", "Historical water occurrence", "Free, no restriction of use"],
  ["NASA POWER", "Rainfall variables", "Free; acknowledgment requested"],
  ["Overture Maps buildings (Google, Microsoft)", "Household locations (reference only)", "ODbL 1.0; Google footprints CC BY 4.0"],
  ["MGB Detailed Flood Susceptibility (Geospatial Data Inventory)", "MGB flood susceptibility variable", "No license stated; public government data"],
  ["GEODATA Systems barangay boundaries", "Barangay assignment and areas", "Proprietary"],
], [3500, 3060, 2800]));
body.push(spacer());
body.push(H2("5.1 Data Credits"));
body.push(P("The researchers acknowledge the following organizations and contributors, whose data made this dataset possible. The credits are given in the form each provider requires and will also appear in the thesis and on the credits page of the system."));
for (const t of [
  "**Flood hazard maps:** Project NOAH (UP NOAH Center) and its contributors, licensed under the Open Data Commons Open Database License (ODbL) 1.0.",
  "**Elevation and slope:** Produced using Copernicus WorldDEM-30 © DLR e.V. 2010-2014 and © Airbus Defence and Space GmbH 2014-2018 provided under COPERNICUS by the European Union and ESA; all rights reserved. The organisations in charge of the Copernicus programme by law or by delegation do not incur any liability for any use of the Copernicus WorldDEM-30.",
  "**Waterways:** © OpenStreetMap contributors, available under the Open Database License (https://www.openstreetmap.org/copyright).",
  "**Land cover:** © ESA WorldCover project 2021 / Contains modified Copernicus Sentinel data (2021) processed by ESA WorldCover consortium (Zanaga et al., 2022).",
  "**Historical surface water:** Source: EC JRC/Google (Pekel et al., 2016).",
  "**Rainfall:** The data was obtained from National Aeronautics and Space Administration (NASA) Langley Research Center’s Prediction Of Worldwide Energy Resources (POWER) project funded through the NASA Earth Science Division. POWER Daily 2.x, accessed October 26, 2026.",
  "**Building locations:** © Overture Maps Foundation; Microsoft Global ML Building Footprints (ODbL 1.0); Google Open Buildings (CC BY 4.0).",
  "**Flood susceptibility:** Mines and Geosciences Bureau (MGB), Detailed Flood Susceptibility, MGB Geospatial Data Inventory.",
  "**Barangay boundaries:** GEODATA Systems Technologies, Inc.",
]) body.push(bullet(t));
body.push(P("This dataset contains information from Project NOAH, OpenStreetMap, and Overture Maps, which is made available under the Open Database License (ODbL) 1.0.", { spacing: { before: 120, after: 160 } }));

// references
body.push(H1("References"));
const ref = (text) => new Paragraph({ children: runs(text), indent: { left: 720, hanging: 720 }, spacing: { after: 160, line: 276 } });
for (const t of [
  "European Space Agency. (n.d.). ~Copernicus global digital elevation model~ [Data set]. https://doi.org/10.5270/ESA-c5d3d65",
  "GEODATA Systems Technologies, Inc. (n.d.). ~Philippine administrative boundaries~ [Data set]. ArcGIS Online.",
  "Mines and Geosciences Bureau. (n.d.). ~Detailed flood susceptibility~ [Data set]. MGB Geospatial Data Inventory. Retrieved October 25, 2026, from https://controlmap.mgb.gov.ph/arcgis/rest/services/GeospatialDataInventory/GDI_Detailed_Flood_Susceptibility/MapServer/0",
  "NASA Langley Research Center. (n.d.). ~Prediction Of Worldwide Energy Resources (POWER) project: Daily data~ [Data set]. Retrieved October 26, 2026, from https://power.larc.nasa.gov/",
  "OpenStreetMap contributors. (n.d.). ~OpenStreetMap~ [Data set]. https://www.openstreetmap.org/copyright",
  "Overture Maps Foundation. (n.d.). ~Overture Maps buildings theme~ [Data set]. https://docs.overturemaps.org/",
  "Pedregosa, F., Varoquaux, G., Gramfort, A., Michel, V., Thirion, B., Grisel, O., Blondel, M., Prettenhofer, P., Weiss, R., Dubourg, V., Vanderplas, J., Passos, A., Cournapeau, D., Brucher, M., Perrot, M., & Duchesnay, É. (2011). Scikit-learn: Machine learning in Python. ~Journal of Machine Learning Research, 12~, 2825–2830.",
  "Pekel, J.-F., Cottam, A., Gorelick, N., & Belward, A. S. (2016). High-resolution mapping of global surface water and its long-term changes. ~Nature, 540~, 418–422. https://doi.org/10.1038/nature20584",
  "UP NOAH Center. (n.d.). ~Project NOAH flood hazard maps: Laguna, 5-, 25-, and 100-year rainfall return periods~ [Data set]. Retrieved November 3, 2026, from https://noah.up.edu.ph/",
  "Zanaga, D., Van De Kerchove, R., Daems, D., De Keersmaecker, W., Brockmann, C., Kirches, G., Wevers, J., Cartus, O., Santoro, M., Fritz, S., Lesiv, M., Herold, M., Tsendbazar, N. E., Xu, P., Ramoino, F., & Arino, O. (2022). ~ESA WorldCover 10 m 2021 v200~ [Data set]. Zenodo. https://doi.org/10.5281/zenodo.7254221",
]) body.push(ref(t));

// appendix A
body.push(new Paragraph({ children: [new PageBreak()] }));
body.push(H1("Appendix A. Flood Hazard Class per Barangay"));
body.push(P("The table gives, for the 100-year scenario, the share of each barangay’s area inside any NOAH flood zone, the share at Medium or worse, and the share at High, followed by the class assigned for each scenario. A class is assigned when its share, counting worse levels, reaches 25 percent."));
body.push(caption("**Table A1.** NOAH flood hazard by barangay"));
const f1 = (v) => Number(v).toFixed(1) + "%";
body.push(table(["Barangay", "Flooded (100-yr)", "Medium or worse", "High", "5-yr", "25-yr", "100-yr"],
  [...BH].sort((a, b) => a.Barangay.localeCompare(b.Barangay)).map((r) => [r.Barangay, f1(r.PctLowOrWorse_100yr), f1(r.PctMediumOrWorse_100yr), f1(r.PctHigh_100yr), r.FloodHazard5yr, r.FloodHazard25yr, r.FloodHazard100yr]),
  [2360, 1300, 1300, 1000, 1100, 1100, 1200], { size: 18, center: [1, 2, 3, 4, 5, 6] }));

// ---------- document
const doc = new Document({
  creator: "Group 9",
  title: "Model Evaluation Report and Request for Dataset Approval",
  styles: {
    default: { document: { run: { font: FONT, size: 24 } } },
    paragraphStyles: [
      { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true, run: { font: FONT, size: 30 }, paragraph: { spacing: { before: 360, after: 120 }, keepNext: true, keepLines: true, outlineLevel: 0 } },
      { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true, run: { font: FONT, size: 26 }, paragraph: { spacing: { before: 240, after: 100 }, keepNext: true, keepLines: true, outlineLevel: 1 } },
    ],
  },
  numbering: { config: [
    { reference: "bullets", levels: [{ level: 0, format: LevelFormat.BULLET, text: "•", alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 720, hanging: 360 } } } }] },
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
