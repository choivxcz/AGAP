"""Pytest configuration.

Integration tests use the real GIS files under Sta_Cruz_Laguna_GIS_Download
when present and are skipped otherwise. Unit tests use small synthetic
rasters/GeoJSON written to a temporary directory and are clearly labelled.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

# The app's background warm-up (facility seeding, road network) would write to whichever database a test module
# configures while the test runs; tests that need it call it explicitly.
os.environ.setdefault("FLR_OPS_WARMUP", "0")

WORKSPACE = Path(__file__).resolve().parent.parent.parent
DEFAULT_GIS_ROOT = WORKSPACE / "Sta_Cruz_Laguna_GIS_Download" / "output"


def real_gis_root() -> Path | None:
    root = DEFAULT_GIS_ROOT
    if (root / "elevation_m.tif").exists() and (root / "barangays").exists():
        return root
    return None


@pytest.fixture(scope="session")
def gis_root() -> Path:
    root = real_gis_root()
    if root is None:
        pytest.skip("real GIS data not available")
    return root
