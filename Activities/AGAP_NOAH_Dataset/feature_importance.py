"""Which inputs does the model rely on? Permutation importance on the household-grouped 80/20 test.

The model is trained on the 80% of households (same split, model and settings as check_model.py). Then, one input at a
time, the test values of that input are shuffled and the drop in macro F1 is measured: a large drop means the model
relies on that input; about zero means it ignores it.

Run:  ..\\.venv\\Scripts\\python.exe feature_importance.py      (a few minutes)
Writes feature_importance.csv.
"""
from pathlib import Path

import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.inspection import permutation_importance
from sklearn.metrics import f1_score
from sklearn.model_selection import GroupShuffleSplit

HERE = Path(__file__).resolve().parent
data = pd.read_csv(HERE / "agap_noah_training.csv", keep_default_na=False)
households = pd.read_csv(HERE / "agap_noah_dataset.csv", usecols=["HouseholdID"]).HouseholdID
X, y = data.drop(columns="RiskLevel"), data.RiskLevel
for col in X.columns:
    if not pd.api.types.is_numeric_dtype(X[col]):
        X[col] = X[col].astype("category")

train, test = next(GroupShuffleSplit(n_splits=1, test_size=0.2, random_state=42).split(X, y, households))
model = HistGradientBoostingClassifier(categorical_features="from_dtype", max_iter=500, class_weight="balanced",
                                       random_state=0).fit(X.iloc[train], y.iloc[train])
sample = pd.Series(test).sample(20000, random_state=0).values
macro_f1 = lambda m, a, b: f1_score(b, m.predict(a), average="macro")
result = permutation_importance(model, X.iloc[sample], y.iloc[sample], scoring=macro_f1, n_repeats=5, random_state=0)
out = (pd.DataFrame({"Input": X.columns, "MacroF1Drop": result.importances_mean, "SD": result.importances_std})
       .sort_values("MacroF1Drop", ascending=False))
out.round(4).to_csv(HERE / "feature_importance.csv", index=False)
print((out.set_index("Input").MacroF1Drop * 100).round(1).to_string())
