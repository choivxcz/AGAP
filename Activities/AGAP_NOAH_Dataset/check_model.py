"""Check that a model trained on agap_noah_training.csv is accurate for every risk level, not only for Low.

The data keeps realistic shares (about 87% Low, 11% Medium, 2% High). To stop the model favouring Low, training uses
class weights: a mistake on a rare class costs more. Households are never split between training and testing.
The same tests are also run without class weights, for comparison.

Run:  ..\\.venv\\Scripts\\python.exe check_model.py      (about 10 minutes)
Writes model_metrics.csv (every metric, every test) and prints a summary.
"""
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import (accuracy_score, balanced_accuracy_score, cohen_kappa_score, confusion_matrix,
                             matthews_corrcoef, precision_recall_fscore_support)
from sklearn.model_selection import GroupKFold, GroupShuffleSplit

HERE = Path(__file__).resolve().parent
LEVELS = ["Low", "Medium", "High"]

# keep_default_na=False: the NOAH class "None" is a category, not a missing value.
data = pd.read_csv(HERE / "agap_noah_training.csv", keep_default_na=False)
households = pd.read_csv(HERE / "agap_noah_dataset.csv", usecols=["HouseholdID"]).HouseholdID  # same row order
X, y = data.drop(columns="RiskLevel"), data.RiskLevel
HOUSE = ["HouseFloodHazard5yr", "HouseFloodHazard25yr", "HouseFloodHazard100yr", "MGBFloodSusceptibility"]  # house-level hazard
for col in ["HousingMaterial", "LocationType", "LandCoverType", "FloodHazard5yr", "FloodHazard25yr", "FloodHazard100yr", *HOUSE]:
    X[col] = X[col].astype("category")


def make_model(weights):
    return HistGradientBoostingClassifier(categorical_features="from_dtype", max_iter=500, class_weight=weights,
                                          random_state=0)


def metrics(true, pred):
    p, r, f, s = precision_recall_fscore_support(true, pred, labels=LEVELS, zero_division=0)
    mp, mr, mf, _ = precision_recall_fscore_support(true, pred, labels=LEVELS, average="macro", zero_division=0)
    wp, wr, wf, _ = precision_recall_fscore_support(true, pred, labels=LEVELS, average="weighted", zero_division=0)
    out = {"Accuracy": accuracy_score(true, pred), "BalancedAccuracy": balanced_accuracy_score(true, pred),
           "MacroPrecision": mp, "MacroRecall": mr, "MacroF1": mf,
           "WeightedPrecision": wp, "WeightedRecall": wr, "WeightedF1": wf,
           "CohenKappa": cohen_kappa_score(true, pred), "MCC": matthews_corrcoef(true, pred)}
    for i, c in enumerate(LEVELS):
        out |= {f"Precision_{c}": p[i], f"Recall_{c}": r[i], f"F1_{c}": f[i], f"Support_{c}": int(s[i])}
    return out


split = next(GroupShuffleSplit(n_splits=1, test_size=0.2, random_state=42).split(X, y, households))
folds = list(GroupKFold(n_splits=5).split(X, y, households))
print("Class shares:", (y.value_counts(normalize=True) * 100).round(1).to_dict(), "%")

rows = []
# Third model: same as the first but without the flood hazard at the house (barangay flood hazard only), to show
# how much the house-level hazard contributes.
VARIANTS = [("With class weights", "balanced", X), ("Without class weights", None, X),
            ("Barangay hazard only", "balanced", X.drop(columns=HOUSE))]
for label, weights, X in VARIANTS:
    train, test = split
    pred = make_model(weights).fit(X.iloc[train], y.iloc[train]).predict(X.iloc[test])
    rows.append({"Model": label, "Test": "80/20 split", **metrics(y.iloc[test], pred)})
    if weights:
        name = "model_confusion_matrix.csv" if label == "With class weights" else "model_confusion_matrix_barangay_only.csv"
        cm = pd.DataFrame(confusion_matrix(y.iloc[test], pred, labels=LEVELS),
                          index=[f"true {c}" for c in LEVELS], columns=[f"pred {c}" for c in LEVELS])
        cm.to_csv(HERE / name)
        print(f"\nConfusion matrix, {label}, 80/20 split:\n{cm}")
    for k, (a, b) in enumerate(folds, 1):
        pred = make_model(weights).fit(X.iloc[a], y.iloc[a]).predict(X.iloc[b])
        rows.append({"Model": label, "Test": f"Fold {k}", **metrics(y.iloc[b], pred)})
    print(f"done: {label}", flush=True)

res = pd.DataFrame(rows)
for label in res.Model.unique():
    cv = res[(res.Model == label) & res.Test.str.startswith("Fold")]
    num = cv.select_dtypes("number")
    res = pd.concat([res, pd.DataFrame([{"Model": label, "Test": "5-fold mean", **num.mean().to_dict()},
                                        {"Model": label, "Test": "5-fold SD", **num.std().to_dict()}])],
                    ignore_index=True)
res.to_csv(HERE / "model_metrics.csv", index=False, float_format="%.4f")

show = ["Accuracy", "BalancedAccuracy", "MacroPrecision", "MacroRecall", "MacroF1", "WeightedF1", "CohenKappa", "MCC",
        "Precision_High", "Recall_High", "F1_High"]
print("\n" + res[["Model", "Test", *show]].round(4).to_string(index=False))
