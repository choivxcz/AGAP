"""The deployed AGAP NOAH risk model (dataset v2.8): artifact contract, single-scenario and 60-event inference."""
from pathlib import Path

import pandas as pd
import pytest

from app.ml_model import CLASSES, HOUSEHOLD, build_frame, load_model, predict, predict_profile, rain_events

DATASET = Path(__file__).resolve().parents[2] / 'Activities' / 'AGAP_NOAH_Dataset'
SITE = {'ElevationMeters': 3.25, 'SlopeDegrees': 0.47, 'DistanceToWaterwayM': 742.77, 'LocationType': 'Inland',
        'LandCoverType': 'Grassland', 'HistoricalWaterOccurrencePct': 0.0,
        'FloodHazard5yr': 'Low', 'FloodHazard25yr': 'Low', 'FloodHazard100yr': 'Medium',
        'HouseFloodHazard5yr': 'None', 'HouseFloodHazard25yr': 'Low', 'HouseFloodHazard100yr': 'Medium',
        'MGBFloodSusceptibility': 'NotMapped'}
STORM = {'Rainfall24hMm': 160.0, 'Rainfall3DayMm': 281.0, 'Rainfall7DayMm': 341.0}
HOUSE = {'Age': 66, 'HousingMaterial': 'LightMaterials', 'HouseMembers': 6, 'ChildrenCount': 2, 'ElderCount': 2,
         'PWDCount': 1}
ZONE = lambda c: {f'HouseFloodHazard{rp}': c for rp in ['5yr', '25yr', '100yr']}


def wrap(values):
    return {k: {'value': v} for k, v in values.items()}


def test_artifact_matches_training_file():
    _, meta = load_model()
    assert len(meta['features']) == 22 and sorted(meta['classes']) == sorted(CLASSES)
    assert set(HOUSEHOLD) <= set(meta['features'])
    assert not {'RiskLevel', 'HouseholdID', 'Barangay', 'Latitude', 'Longitude', 'EventDate'} & set(meta['features'])
    events = rain_events()
    assert len(events) == 60 and {e['EventType'] for e in events} == {'Extreme', 'Heavy', 'Moderate', 'Light'}
    if not (DATASET / 'agap_noah_training.csv').exists():
        pytest.skip('dataset folder not available')
    header = pd.read_csv(DATASET / 'agap_noah_training.csv', nrows=0).columns.tolist()
    assert meta['features'] == [c for c in header if c != 'RiskLevel']
    assert pd.read_csv(DATASET / 'rainfall_events.csv').EventDate.tolist() == [e['EventDate'] for e in events]


def test_single_scenario_and_flood_zone_at_the_house():
    result = predict(wrap({**SITE, **STORM}), HOUSE)
    assert result['RiskLevel'] in CLASSES and abs(sum(result['probabilities'].values()) - 1) < 1e-3
    assert predict(wrap({**SITE, **STORM, **ZONE('None')}), HOUSE)['RiskLevel'] == 'Low'
    assert predict(wrap({**SITE, **STORM, **ZONE('High')}), HOUSE)['RiskLevel'] == 'High'


def test_profile_runs_all_60_events():
    result = predict_profile(wrap(SITE), HOUSE)
    assert result['status'] == 'ml' and result['validated_for_real_world'] is False
    assert len(result['events']) == 60 and sum(result['all_counts'].values()) == 60
    assert sum(result['extreme_counts'].values()) == 15
    top = max(result['extreme_counts'].values())
    assert result['extreme_counts'][result['RiskLevel']] == top
    assert all(result['extreme_counts'][c] < top for c in CLASSES if CLASSES.index(c) > CLASSES.index(result['RiskLevel']))
    assert CLASSES.index(result['worst']['RiskLevel']) == max(CLASSES.index(e['RiskLevel']) for e in result['events'])


def test_profile_follows_the_flood_zone_at_the_house():
    assert predict_profile(wrap({**SITE, **ZONE('None')}), HOUSE)['RiskLevel'] == 'Low'
    assert predict_profile(wrap({**SITE, **ZONE('High')}), HOUSE)['RiskLevel'] == 'High'


def test_missing_and_unseen_inputs_are_reported_not_hidden():
    values = {**SITE, 'ElevationMeters': None, 'LandCoverType': 'Mangroves'}
    _, meta = load_model()
    frame, missing, unseen = build_frame(wrap(values), HOUSE, meta, rain_events())
    assert len(frame) == 60 and missing == ['ElevationMeters'] and unseen == ['LandCoverType=Mangroves']
    assert frame['LandCoverType'].isna().all()
    result = predict_profile(wrap(values), HOUSE)
    assert result['RiskLevel'] in CLASSES
    assert any('Missing inputs: ElevationMeters' in d for d in result['details'])
    assert any('not seen in training' in d for d in result['details'])


def test_terrain_changes_the_assessment():
    """Same household and flood maps; a low, flat, built-up riverside site is riskier than a high inland one."""
    maps = {**SITE, 'HouseFloodHazard5yr': 'Low', 'HouseFloodHazard25yr': 'Low', 'HouseFloodHazard100yr': 'Low'}
    low = {**maps, 'ElevationMeters': 3.0, 'SlopeDegrees': 0.2, 'DistanceToWaterwayM': 30.0, 'LocationType': 'Riverside',
           'LandCoverType': 'BuiltUp'}
    high = {**maps, 'ElevationMeters': 25.0, 'SlopeDegrees': 6.0, 'DistanceToWaterwayM': 900.0, 'LocationType': 'Inland',
            'LandCoverType': 'TreeCover'}
    rank = lambda r: sum(CLASSES.index(e['RiskLevel']) for e in r['events'])
    assert rank(predict_profile(wrap(low), HOUSE)) > rank(predict_profile(wrap(high), HOUSE))
