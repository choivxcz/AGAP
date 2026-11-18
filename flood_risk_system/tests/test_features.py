"""ML feature contract tests."""
from __future__ import annotations

from app import database as db
from app.features import (FEATURE_CONTRACT, FEATURE_NAMES, NUMERIC_FEATURE_NAMES,
                          TARGET_NAME, build_feature_row, join_with_labels)


def test_contract_names_and_units():
    names = [f["name"] for f in FEATURE_CONTRACT]
    assert names == ["ElevationMeters", "SlopeDegrees", "DistanceToWaterwayM",
                     "LandCoverCode", "LandCoverClass", "HistoricalWaterOccurrencePct"]
    units = {f["name"]: f["unit"] for f in FEATURE_CONTRACT}
    assert units["ElevationMeters"] == "m"
    assert units["SlopeDegrees"] == "degrees"
    assert units["DistanceToWaterwayM"] == "m"
    assert units["HistoricalWaterOccurrencePct"] == "%"


def test_target_is_not_an_input():
    assert TARGET_NAME == "RiskLevel"
    assert TARGET_NAME not in FEATURE_NAMES


def test_numeric_features_exclude_string_label():
    assert "LandCoverClass" not in NUMERIC_FEATURE_NAMES
    assert "LandCoverCode" in NUMERIC_FEATURE_NAMES


def test_build_feature_row_from_extraction_dict():
    extraction = {
        "extraction_id": 7, "household_id": 3, "latitude": 14.29, "longitude": 121.42,
        "barangay_name": "Jasaan", "extraction_status": "partial",
        "feature_statuses": {
            "ElevationMeters": {"status": "ok", "value": 10.0},
            "SlopeDegrees": {"status": "ok", "value": 2.0},
            "DistanceToWaterwayM": {"status": "ok", "value": 100.0},
            "LandCoverCode": {"status": "ok", "value": 40},
            "LandCoverClass": {"status": "ok", "value": "Cropland"},
            "HistoricalWaterOccurrencePct": {"status": "missing", "value": None,
                                             "reason": "pixel is NoData (0.0)"},
        },
    }
    row = build_feature_row(extraction)
    assert row["features"]["ElevationMeters"] == 10.0
    assert row["features"]["HistoricalWaterOccurrencePct"] is None
    assert row["missing"]["HistoricalWaterOccurrencePct"] is True
    assert row["missing"]["ElevationMeters"] is False
    assert row["extraction_id"] == 7


def test_build_feature_row_falls_back_to_columns():
    extraction = {
        "extraction_id": 1, "household_id": 1, "latitude": 14.29, "longitude": 121.42,
        "barangay_name": None, "extraction_status": "ok",
        "elevation_m": 5.5, "slope_degrees": 0.5, "distance_waterway_m": 25.0,
        "land_cover_code": 50, "land_cover_class": "Built-up", "water_occurrence_pct": 2.0,
    }
    row = build_feature_row(extraction)
    assert row["features"]["LandCoverClass"] == "Built-up"
    assert all(v is not None for v in row["features"].values())


def test_join_labels_never_invented():
    extractions = [{"extraction_id": 1, "household_id": 1, "latitude": 1.0,
                    "longitude": 2.0, "barangay_name": "X", "extraction_status": "ok"}]
    rows = join_with_labels(extractions, {1: [{"risk_level": "low"}]})
    assert rows[0]["label"] == "low"
    rows_no_label = join_with_labels(extractions, {})
    assert rows_no_label[0]["label"] is None
