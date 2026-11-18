"""Machine-learning feature contract.

A single source of truth for the geographic feature names, units, dtypes and
missing-value policy shared by the training and serving paths, so there is no
training-serving skew.

Rules:
- ``RiskLevel`` is a survey-observed *label*, never an input feature.
- Missing features are exported as JSON ``null`` plus a boolean ``missing``
  mask; the numerical encoding used by a specific model (e.g. NaN vs median
  imputation) must be the same at training and inference time and is decided
  by the training pipeline, not improvised here.
- Units are fixed and documented; no rescaling happens in this module.
"""
from __future__ import annotations

from typing import Any

FEATURE_CONTRACT: list[dict[str, str]] = [
    {"name": "ElevationMeters", "unit": "m", "dtype": "float32",
     "source": "Copernicus DEM GLO-30 (surface model, ~30 m)"},
    {"name": "SlopeDegrees", "unit": "degrees", "dtype": "float32",
     "source": "finite-difference derivative of Copernicus DEM GLO-30 (~30 m)"},
    {"name": "DistanceToWaterwayM", "unit": "m", "dtype": "float32",
     "source": "distance to mapped OSM waterway (river/stream/canal/drain/ditch) on the 30 m grid"},
    {"name": "LandCoverCode", "unit": "class code", "dtype": "int8",
     "source": "ESA WorldCover 2021 v200 (~10 m)"},
    {"name": "LandCoverClass", "unit": "label", "dtype": "string",
     "source": "ESA WorldCover 2021 legend label for LandCoverCode"},
    {"name": "HistoricalWaterOccurrencePct", "unit": "%", "dtype": "float32",
     "source": "JRC Global Surface Water v1.5 occurrence 1984-2024 (~30 m)"},
]

FEATURE_NAMES: list[str] = [f["name"] for f in FEATURE_CONTRACT]
NUMERIC_FEATURE_NAMES: list[str] = [f["name"] for f in FEATURE_CONTRACT if f["dtype"] != "string"]

# The prediction target (kept here only as documentation; never an input).
TARGET_NAME = "RiskLevel"


def build_feature_row(extraction: dict[str, Any]) -> dict[str, Any]:
    """Convert a stored gis_extraction dict (as returned by the DB) into a
    named feature row with a missing mask.

    Returns
    -------
    dict with keys:
        features: {feature_name: value-or-None}
        missing:  {feature_name: bool}
        extraction_id, household_id, latitude, longitude, barangay_name
    """
    source = extraction.get("feature_statuses") or {}
    # Fallback for rows stored before feature_statuses existed: rebuild from columns.
    column_map = {
        "ElevationMeters": extraction.get("elevation_m"),
        "SlopeDegrees": extraction.get("slope_degrees"),
        "DistanceToWaterwayM": extraction.get("distance_waterway_m"),
        "LandCoverCode": extraction.get("land_cover_code"),
        "LandCoverClass": extraction.get("land_cover_class"),
        "HistoricalWaterOccurrencePct": extraction.get("water_occurrence_pct"),
    }
    features: dict[str, Any] = {}
    missing: dict[str, bool] = {}
    for name in FEATURE_NAMES:
        status = (source.get(name) or {}).get("status")
        value = (source.get(name) or {}).get("value")
        if value is None:
            value = column_map.get(name)
        features[name] = value
        missing[name] = value is None or status == "missing" or status == "error"
    return {
        "features": features,
        "missing": missing,
        "extraction_id": extraction.get("extraction_id"),
        "household_id": extraction.get("household_id"),
        "latitude": extraction.get("latitude"),
        "longitude": extraction.get("longitude"),
        "barangay_name": extraction.get("barangay_name"),
        "extraction_status": extraction.get("extraction_status"),
    }


def join_with_labels(conn_rows: list[dict], assessments_by_household: dict[int, list[dict]]) -> list[dict]:
    """Attach survey labels to feature rows. Labels are never invented: a row
    without an assessment simply has ``label: null`` and is excluded from
    supervised training by the caller."""
    rows = []
    for extraction in conn_rows:
        row = build_feature_row(extraction)
        household_assessments = assessments_by_household.get(extraction.get("household_id")) or []
        labels = [a.get("risk_level") for a in household_assessments if a.get("risk_level")]
        row["label"] = labels[-1] if labels else None
        rows.append(row)
    return rows
