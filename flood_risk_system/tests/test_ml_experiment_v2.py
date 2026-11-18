"""Audit a completed v2 experiment without rerunning model selection."""
import hashlib
import json
from pathlib import Path
import numpy as np
import pandas as pd
import pytest
from sklearn.metrics import accuracy_score, confusion_matrix

ROOT=Path(__file__).resolve().parents[1]/'data/synthetic_ml_v2'

@pytest.fixture(scope='module')
def experiment():
    if not (ROOT/'metrics.json').exists(): pytest.skip('No completed v2 experiment')
    return pd.read_csv(ROOT/'synthetic_labeled_training_data.csv'),json.loads((ROOT/'metrics.json').read_text())

def test_provenance_and_quality(experiment):
    df,m=experiment
    assert len(df)==364000
    assert df.DataOrigin.eq('Synthetic').all() and df.TargetOrigin.eq('Synthetic').all()
    for v in m['source_manifest'].values():
        assert hashlib.sha256(Path(v['path']).read_bytes()).hexdigest()==v['sha256']
    assert (df.ChildrenCount+df.ElderCount+df.PWDCount<=df.HouseMembers).all()
    assert ((df.Age>=18)|(df.ChildrenCount>0)).all()
    assert ((df.Age<60)|(df.ElderCount>0)).all()
    assert ((~df.Age.between(18,59))|(df.HouseMembers>df.ChildrenCount+df.ElderCount)).all()
    assert not {'RiskLevel','SyntheticTargetScore','UnknownTargetContributions','Split'}&set(m['features'])

def test_disjoint_validation_and_test(experiment):
    df,_=experiment
    for a,b in [('Train','Validation'),('Train','Test'),('Validation','Test')]:
        x,y=df[df.Split==a],df[df.Split==b]
        assert not set(x.SyntheticHouseholdID)&set(y.SyntheticHouseholdID)
        assert not set(x.EventDate)&set(y.EventDate)
    counts=df.drop_duplicates('SyntheticHouseholdID').groupby(['Barangay','HouseholdGroup']).size()
    assert set(counts.xs('Train',level=1))=={60}
    assert set(counts.xs('Validation',level=1))=={20}
    assert set(counts.xs('Test',level=1))=={20}

def test_validation_selection_and_honest_accuracy(experiment):
    df,m=experiment
    candidates=json.loads((ROOT/'validation_comparison.json').read_text())
    best=max(candidates,key=lambda c:(c['macro_f1'],c['balanced_accuracy']))
    assert m['model_name']==best['name']
    p=pd.read_csv(ROOT/'test_predictions.csv')
    assert len(p)==len(df[df.Split=='Test'])
    assert np.isclose(accuracy_score(p.RiskLevel,p.PredictedRiskLevel),m['accuracy'])
    assert confusion_matrix(p.RiskLevel,p.PredictedRiskLevel,labels=m['labels']).tolist()==m['confusion_matrix']
    assert m['accuracy_target_met']==(m['accuracy']>=.98)
    assert m['target_origin']=='Synthetic'

# The live inference code now serves the AGAP NOAH v2.6 model (tests/test_ml_model.py); this archived
# experiment is audited above and its report routes below.

def test_versioned_report_routes():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.assessment import router
    app=FastAPI();app.include_router(router)
    client=TestClient(app)
    assert client.get('/api/assessment/experiments/v2/report.html').status_code==200
    assert client.get('/api/assessment/experiments/v2/confusion_matrix.png').status_code==200
    assert client.get('/api/assessment/experiments/v2/model.joblib').status_code==422
