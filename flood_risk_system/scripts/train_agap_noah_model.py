"""Train the deployed risk model on the AGAP NOAH dataset and save it for the app.

Same model and settings as the evaluation in Activities/AGAP_NOAH_Dataset/check_model.py (gradient boosting,
500 iterations, class weights), fitted once on all records. The evaluation results (household-grouped
80/20 test and 5-fold cross-validation) are copied from that folder; this script does not re-evaluate.

Run (from flood_risk_system):  python scripts/train_agap_noah_model.py
Writes data/agap_noah_v<dataset version>/ (for example data/agap_noah_v2_7/): model.joblib, metadata.json, confusion_matrix.png, report.html, rainfall_events.csv
(the 60 historical rain events of the dataset; the app predicts each household across all of them)
"""
from __future__ import annotations

import csv
import hashlib
import html
import json
import sys
from datetime import date
from pathlib import Path

import shutil

import joblib
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier

ROOT = Path(__file__).resolve().parents[1]
DATASET = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT.parent / "Activities" / "AGAP_NOAH_Dataset"
OUT = ROOT / "data" / f"agap_noah_v{json.loads((DATASET / 'generation_metadata.json').read_text(encoding='utf-8'))['version'].replace('.', '_')}"
TARGET = "RiskLevel"
CLASSES = ["Low", "Medium", "High"]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    train_file = DATASET / "agap_noah_training.csv"
    meta_in = json.loads((DATASET / "generation_metadata.json").read_text(encoding="utf-8"))
    data = pd.read_csv(train_file, keep_default_na=False)       # "None" is a NOAH class, not a missing value
    features = [c for c in data.columns if c != TARGET]
    categorical = [c for c in features if not pd.api.types.is_numeric_dtype(data[c])]
    numeric = [c for c in features if c not in categorical]
    categories = {c: sorted(data[c].unique().tolist()) for c in categorical}
    X = data[features].copy()
    for c in categorical:
        X[c] = pd.Categorical(X[c], categories=categories[c])

    print(f"Training on {len(X):,} records, {len(features)} inputs ...", flush=True)
    model = HistGradientBoostingClassifier(categorical_features="from_dtype", max_iter=500,
                                           class_weight="balanced", random_state=0)
    model.fit(X, data[TARGET])

    # Evaluation results from the dataset folder (household-grouped tests).
    with (DATASET / "model_metrics.csv").open(newline="", encoding="utf-8") as file:
        rows = [r for r in csv.DictReader(file)]
    pick = lambda m, t: {k: float(v) for k, v in next(r for r in rows if r["Model"] == m and r["Test"] == t).items()
                         if k not in ("Model", "Test")}
    evaluation = {"holdout_80_20": pick("With class weights", "80/20 split"),
                  "cv_5fold_mean": pick("With class weights", "5-fold mean"),
                  "cv_5fold_sd": pick("With class weights", "5-fold SD"),
                  "comparison_barangay_hazard_only_cv_mean": pick("Barangay hazard only", "5-fold mean")}
    with (DATASET / "model_confusion_matrix.csv").open(newline="", encoding="utf-8") as file:
        cm = [[int(v) for v in r[1:]] for r in list(csv.reader(file))[1:]]

    OUT.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, OUT / "model.joblib")
    shutil.copyfile(DATASET / "rainfall_events.csv", OUT / "rainfall_events.csv")
    metadata = {
        "version": f"agap-noah-v{meta_in['version']}",
        "dataset": {"name": meta_in["name"], "version": meta_in["version"], "file": train_file.name,
                    "rows": len(data), "sha256": sha256(train_file),
                    "class_counts": data[TARGET].value_counts().to_dict()},
        "trained_on": date.today().isoformat(),
        "model_type": "HistGradientBoostingClassifier",
        "params": {"max_iter": 500, "class_weight": "balanced", "categorical_features": "from_dtype",
                   "random_state": 0},
        "features": features, "numeric": numeric, "categorical": categorical, "categories": categories,
        "classes": [str(c) for c in model.classes_],
        "evaluation": evaluation,
        "confusion_matrix_holdout": {"labels": CLASSES, "matrix": cm},
        "label_note": "RiskLevel is a rule-based (synthetic) target; scores measure how well the rule is learned, "
                      "not real-world flood prediction.",
    }
    (OUT / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    write_confusion_png(cm)
    write_report(metadata)
    print(json.dumps({"saved": str(OUT), "classes": metadata["classes"],
                      "holdout_macro_f1": evaluation["holdout_80_20"]["MacroF1"]}, indent=2))


def write_confusion_png(cm) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(4.6, 3.9), dpi=150)
    ax.imshow(cm, cmap="Blues")
    for i, row in enumerate(cm):
        for j, v in enumerate(row):
            ax.text(j, i, f"{v:,}", ha="center", va="center", color="white" if v > max(map(max, cm)) / 2 else "black")
    ax.set_xticks(range(3), CLASSES)
    ax.set_yticks(range(3), CLASSES)
    ax.set_xlabel("Predicted")
    ax.set_ylabel("Actual")
    ax.set_title("Hold-out test (80/20, households kept apart)", fontsize=9)
    fig.tight_layout()
    fig.savefig(OUT / "confusion_matrix.png")
    plt.close(fig)


def write_report(meta: dict) -> None:
    ev = meta["evaluation"]
    p = lambda v: f"{v * 100:.2f}%"
    rows = [("Accuracy", "Accuracy"), ("Balanced accuracy", "BalancedAccuracy"), ("Macro F1", "MacroF1"),
            ("F1 Low", "F1_Low"), ("F1 Medium", "F1_Medium"), ("F1 High", "F1_High"), ("Recall High", "Recall_High")]
    body = "".join(f"<tr><td>{n}</td><td>{p(ev['holdout_80_20'][k])}</td><td>{p(ev['cv_5fold_mean'][k])}</td>"
                   f"<td>{p(ev['comparison_barangay_hazard_only_cv_mean'][k])}</td></tr>" for n, k in rows)
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Risk model evaluation</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>body{{font-family:system-ui,sans-serif;max-width:760px;margin:2rem auto;padding:0 1rem;color:#1d2a26;background:#fff}}
table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #c8d5d0;padding:.4rem .6rem;text-align:right}}
td:first-child,th:first-child{{text-align:left}}th{{background:#eef4f1}}.note{{color:#5a6b65}}img{{max-width:100%}}</style></head><body>
<h1>Flood risk model ({html.escape(meta['version'])})</h1>
<p>{html.escape(meta['model_type'])} trained on {meta['dataset']['rows']:,} records of the {html.escape(meta['dataset']['name'])},
version {html.escape(meta['dataset']['version'])}. {len(meta['features'])} inputs: household profile, terrain at the pin,
Project NOAH flood hazard for the barangay and at the house, and NASA POWER rainfall.</p>
<table><tr><th>Measure</th><th>Hold-out test</th><th>5-fold CV mean</th><th>Comparison: barangay hazard only</th></tr>{body}</table>
<p><img src="/api/assessment/confusion_matrix.png" alt="Confusion matrix of the hold-out test"></p>
<p class="note">{html.escape(meta['label_note'])} Households were never split between training and testing.</p>
</body></html>"""
    (OUT / "report.html").write_text(page, encoding="utf-8")


if __name__ == "__main__":
    main()
