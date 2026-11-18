"""MGB flood susceptibility lookup gives the same class as the training dataset (v2.7+)."""
from pathlib import Path

import pandas as pd
import pytest

from app import mgb

DATASET = Path(__file__).resolve().parents[2] / 'Activities' / 'AGAP_NOAH_Dataset' / 'agap_noah_dataset.csv'


def test_mgb_class_matches_dataset():
    if not DATASET.exists():
        pytest.skip('dataset folder not available')
    hh = pd.read_csv(DATASET, keep_default_na=False).drop_duplicates('HouseholdID')
    for r in hh.itertuples():
        assert mgb.susceptibility(r.Latitude, r.Longitude) == r.MGBFloodSusceptibility, r.HouseholdID


def test_outside_and_known_point():
    assert mgb.susceptibility(0.0, 0.0) == 'NotMapped'
    assert mgb.susceptibility(14.293, 121.407725) == 'High'   # Santisima Cruz; MGB's live service returned HF here
    assert set(mgb.HAZARD) == set(mgb.ORDER)
