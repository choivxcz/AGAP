"""Project NOAH lookups give the same classes as the training dataset (v2.6)."""
from pathlib import Path

import pandas as pd
import pytest

from app import noah

DATASET = Path(__file__).resolve().parents[2] / 'Activities' / 'AGAP_NOAH_Dataset' / 'agap_noah_dataset.csv'


@pytest.fixture(scope='module')
def households():
    if not DATASET.exists():
        pytest.skip('dataset folder not available')
    return pd.read_csv(DATASET, keep_default_na=False).drop_duplicates('HouseholdID')


def test_house_and_barangay_classes_match_dataset(households):
    for r in households.itertuples():
        house, area = noah.house_hazard(r.Latitude, r.Longitude), noah.barangay_hazard(r.Barangay)
        for rp in noah.RETURN_PERIODS:
            assert house[rp] == getattr(r, f'HouseFloodHazard{rp}'), (r.HouseholdID, rp)
            assert area[rp] == getattr(r, f'FloodHazard{rp}'), (r.HouseholdID, rp)


def test_outside_zones_and_unknown_barangay():
    assert noah.house_hazard(0.0, 0.0) == {rp: 'None' for rp in noah.RETURN_PERIODS}
    assert noah.barangay_hazard('Not a barangay') is None
    assert len(noah._barangay_table()) == 26
