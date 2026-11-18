"""Inference with the AGAP NOAH risk model (dataset v2.8).

The artifact is produced by scripts/train_agap_noah_model.py from Activities/AGAP_NOAH_Dataset. The model's 22
inputs are: six household values from the form, six terrain values at the pin, six Project NOAH flood hazard
classes (barangay and house), the MGB flood susceptibility class at the house, and three NASA POWER rainfall totals.

Instead of one chosen date, a household is assessed across the dataset's 60 historical rain events (15 extreme,
15 heavy, 15 moderate, 15 light). Its risk level is the most common predicted level across the 15 extreme events
(ties go to the higher level). RiskLevel was a rule-based (synthetic) target, so the output is a prototype
classification, not a validated real-world risk.
"""
import csv
import json
import os
from collections import Counter
from functools import lru_cache
from pathlib import Path

import joblib
import pandas as pd

MODEL_DIR = Path(os.environ.get('FLR_MODEL_DIR', Path(__file__).resolve().parents[1] / 'data' / 'agap_noah_v2_8'))
HOUSEHOLD = ['Age', 'HousingMaterial', 'HouseMembers', 'ChildrenCount', 'ElderCount', 'PWDCount']
RAIN = ['Rainfall24hMm', 'Rainfall3DayMm', 'Rainfall7DayMm']
CLASSES = ['Low', 'Medium', 'High']
RANK = {c: i for i, c in enumerate(CLASSES)}
EVENT_TYPES = ['Extreme', 'Heavy', 'Moderate', 'Light']


@lru_cache(maxsize=1)
def load_model():
    # Load only the locally produced, trusted artifact; never user uploads.
    return joblib.load(MODEL_DIR / 'model.joblib'), json.loads((MODEL_DIR / 'metadata.json').read_text(encoding='utf-8'))


@lru_cache(maxsize=1)
def rain_events() -> list[dict]:
    """The 60 historical rain events of the training dataset (NASA POWER, 1991-2025)."""
    with (MODEL_DIR / 'rainfall_events.csv').open(newline='', encoding='utf-8') as file:
        return [{'EventDate': r['EventDate'], 'EventType': r['EventType'],
                 **{k: float(r[k]) for k in RAIN}} for r in csv.DictReader(file)]


def build_frame(features: dict, household: dict, meta: dict, rains: list[dict] | None = None):
    """Input frame in the training format (one row per rain scenario), plus missing inputs and unseen categories.

    Rainfall comes from `rains` when given, otherwise from `features`."""
    base = {k: household[k] if k in household else features.get(k, {}).get('value') for k in meta['features']}
    rows = [{**base, **{k: r[k] for k in RAIN}} for r in rains] if rains else [base]
    frame = pd.DataFrame(rows, columns=meta['features'])
    frame[meta['numeric']] = frame[meta['numeric']].apply(pd.to_numeric, errors='coerce')
    unseen = []
    for col in meta['categorical']:
        value = base[col]
        if value is not None and value not in meta['categories'][col]:
            unseen.append(f'{col}={value}')
            frame[col] = None                                                         # unseen -> missing
        frame[col] = pd.Categorical(frame[col], categories=meta['categories'][col])
    missing = [k for k, v in rows[0].items() if v is None]
    return frame, missing, unseen


def _scores(model, frame) -> list[dict]:
    proba = model.predict_proba(frame)
    order = list(model.classes_)
    return [{c: float(p[order.index(c)]) for c in CLASSES} for p in proba]


def _label(scores: dict) -> str:
    return max(CLASSES, key=lambda c: (scores[c], RANK[c]))


def predict(features: dict, household: dict) -> dict:
    """Single scenario: rainfall taken from `features`."""
    model, meta = load_model()
    frame, missing, unseen = build_frame(features, household, meta)
    scores = _scores(model, frame)[0]
    return {'RiskLevel': _label(scores), 'probabilities': {c: round(v, 4) for c, v in scores.items()},
            'missing': missing, 'unseen': unseen}


def predict_profile(features: dict, household: dict) -> dict:
    """Assess the household across all 60 historical rain events."""
    try:
        model, meta = load_model()
        events = rain_events()
    except (OSError, ValueError, KeyError):
        return {'status': 'unavailable', 'RiskLevel': None, 'events': [],
                'message': 'Risk model artifact is unavailable. Run scripts/train_agap_noah_model.py.', 'details': []}
    frame, missing, unseen = build_frame(features, household, meta, events)
    results = [{**e, 'RiskLevel': _label(s), 'probabilities': {c: round(v, 4) for c, v in s.items()}}
               for e, s in zip(events, _scores(model, frame))]
    count = lambda rows: {c: sum(r['RiskLevel'] == c for r in rows) for c in CLASSES}
    extreme = [r for r in results if r['EventType'] == 'Extreme']
    ext_counts = count(extreme)
    headline = max(CLASSES, key=lambda c: (ext_counts[c], RANK[c]))                # most common; ties -> higher
    worst = max(results, key=lambda r: (RANK[r['RiskLevel']], r['Rainfall24hMm']))
    by_type = {t: count([r for r in results if r['EventType'] == t]) for t in EVENT_TYPES}
    from_2020 = sum(r['EventDate'].startswith('2020') for r in extreme)
    ev = meta['evaluation']
    details = [
        f"Risk level = the most common prediction across the {len(extreme)} extreme rain events (the wettest days "
        'in the 1991-2025 NASA POWER record, at least 10 days apart). Ties go to the higher level.',
        f"Model: {meta['model_type']} ({meta['version']}), trained on {meta['dataset']['rows']:,} records; "
        f"hold-out macro F1 {ev['holdout_80_20']['MacroF1']:.1%}, High-risk recall {ev['holdout_80_20']['Recall_High']:.1%} "
        '(agreement with rule-based labels, not real flood outcomes).',
        f'{from_2020} of the {len(extreme)} extreme events are from 2020, when NASA POWER values for Santa Cruz are '
        'much higher than other rainfall records; they may overstate actual rainfall.',
        ('Missing inputs: ' + ', '.join(missing) + '. The model treats them as unknown, which adds uncertainty.')
        if missing else 'All household, terrain and flood hazard (NOAH and MGB) inputs are available.',
    ]
    if unseen:
        details.append('Values not seen in training (treated as unknown): ' + ', '.join(unseen) + '.')
    return {'status': 'ml', 'RiskLevel': headline, 'basis': 'Most common level across the extreme rain events',
            'extreme_counts': ext_counts, 'counts_by_event_type': by_type, 'all_counts': count(results),
            'worst': {k: worst[k] for k in ['EventDate', 'EventType', 'Rainfall24hMm', 'RiskLevel']},
            'events': results, 'method': meta['model_type'], 'version': meta['version'],
            'validated_for_real_world': False,
            'message': 'Prototype prediction from a model trained on rule-based risk labels. '
                       'It is not validated against real flood outcomes.',
            'details': details, 'report_url': '/api/assessment/model-report'}
