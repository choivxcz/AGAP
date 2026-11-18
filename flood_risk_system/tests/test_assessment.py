"""Assessment API: real-source features, model prediction and input safety."""
from pathlib import Path

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import assessment
from app.ml_model import build_frame, load_model

app = FastAPI()
app.include_router(assessment.router)
client = TestClient(app)
LOCATION = {'latitude': 14.281, 'longitude': 121.398}
HOUSE = {'Age': 35, 'HousingMaterial': 'Concrete', 'HouseMembers': 4, 'ChildrenCount': 1, 'ElderCount': 1, 'PWDCount': 0}
NOAH_KEYS = [f'{p}{rp}' for p in ['FloodHazard', 'HouseFloodHazard'] for rp in ['5yr', '25yr', '100yr']]
MGB_VALUES = ['NotMapped', 'Moderate', 'High', 'VeryHigh']
DATASET = Path(__file__).resolve().parents[2] / 'Activities' / 'AGAP_NOAH_Dataset' / 'agap_noah_dataset.csv'


def test_config_and_location_features():
    config = client.get('/api/assessment/config').json()
    assert config['prediction_available'] and config['prediction_mode'] == 'ml_profile'
    assert config['rain_events'] == {'Extreme': 15, 'Heavy': 15, 'Moderate': 15, 'Light': 15}
    response = client.post('/api/assessment/location', json=LOCATION)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data['barangay'] == 'Calios'
    for key in NOAH_KEYS:
        assert data['features'][key]['value'] in ['None', 'Low', 'Medium', 'High']
    assert data['features']['HistoricalWaterOccurrencePct']['status'] == 'ok'
    assert data['features']['MGBFloodSusceptibility']['value'] in MGB_VALUES
    assert 'FloodSusceptibility' not in data['features'] and 'Rainfall24hMm' not in data['features']


def test_outside_points_and_extra_fields_are_rejected():
    for patch in [{'latitude': 0, 'longitude': 0}, {'event_date': '2025-12-31'}]:
        assert client.post('/api/assessment/location', json={**LOCATION, **patch}).status_code == 422


def test_prediction_and_rejected_feature_overrides():
    response = client.post('/api/assessment/prepare', json={**LOCATION, **HOUSE})
    assert response.status_code == 200, response.text
    prediction = response.json()['prediction']
    assert prediction['RiskLevel'] in ['Low', 'Medium', 'High']
    assert prediction['status'] == 'ml' and prediction['validated_for_real_world'] is False
    assert len(prediction['events']) == 60 and sum(prediction['extreme_counts'].values()) == 15
    for patch in [{'RiskLevel': 'Low'}, {'ElevationMeters': 999}, {'HouseFloodHazard100yr': 'None'},
                  {'Rainfall24hMm': 500}]:
        assert client.post('/api/assessment/prepare', json={**LOCATION, **HOUSE, **patch}).status_code == 422


def test_household_constraints():
    bad = [{'ChildrenCount': 4, 'ElderCount': 1}, {'Age': 17}, {'Age': 70, 'ElderCount': 0}, {'HouseMembers': 2.5},
           {'Age': 35, 'ChildrenCount': 2, 'ElderCount': 2}, {'PWDCount': 5}, {'HousingMaterial': 'MixedMaterials'}]
    for patch in bad:
        response = client.post('/api/assessment/prepare', json={**LOCATION, **HOUSE, **patch})
        assert response.status_code == 422, (patch, response.text)
    ok = {'Age': 70, 'ElderCount': 1, 'PWDCount': 1, 'HousingMaterial': 'SemiConcrete'}
    assert client.post('/api/assessment/prepare', json={**LOCATION, **HOUSE, **ok}).status_code == 200


def test_missing_noah_source_is_not_classified_as_none(monkeypatch):
    def absent(*_):
        raise FileNotFoundError()
    monkeypatch.setattr(assessment.noah, 'house_hazard', absent)
    data = client.post('/api/assessment/location', json=LOCATION).json()
    for key in NOAH_KEYS:
        assert data['features'][key]['value'] is None and data['features'][key]['status'] == 'missing'


def test_water_location_cannot_prepare(monkeypatch):
    monkeypatch.setattr(assessment, 'extract_location', lambda req: {'valid_land': False})
    assert client.post('/api/assessment/prepare', json={**LOCATION, **HOUSE}).status_code == 422


def test_app_inputs_equal_training_rows():
    """For dataset records, the app builds exactly the inputs the model was trained on."""
    if not DATASET.exists():
        pytest.skip('dataset folder not available')
    _, meta = load_model()
    sample = pd.read_csv(DATASET, keep_default_na=False).sample(150, random_state=7)
    for r in sample.itertuples(index=False):
        loc = assessment.Location(latitude=r.Latitude, longitude=r.Longitude)
        features = assessment.extract_location(loc)['features']
        features.update({k: {'value': getattr(r, k)} for k in ['Rainfall24hMm', 'Rainfall3DayMm', 'Rainfall7DayMm']})
        frame, missing, unseen = build_frame(features, {k: getattr(r, k) for k in
                                                        ['Age', 'HousingMaterial', 'HouseMembers', 'ChildrenCount',
                                                         'ElderCount', 'PWDCount']}, meta)
        assert not missing and not unseen
        for col in meta['features']:
            expected, got = getattr(r, col), frame.iloc[0][col]
            if isinstance(expected, float):
                assert abs(float(got) - expected) <= 0.0051, (r.HouseholdID, col)
            else:
                assert str(got) == str(expected), (r.HouseholdID, col)


def test_model_report_routes():
    assert client.get('/api/assessment/model-report').status_code == 200
    assert client.get('/api/assessment/confusion_matrix.png').status_code == 200
