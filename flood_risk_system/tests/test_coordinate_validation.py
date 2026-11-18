"""Coordinate validation tests (pure unit tests, no GIS files)."""
from __future__ import annotations

import math

import pytest

from app.gis.extraction import CoordinateError, validate_latlon


def test_valid_santa_cruz_point():
    lat, lon = validate_latlon(14.2822, 121.4171)
    assert lat == 14.2822 and lon == 121.4171


@pytest.mark.parametrize("lat,lon,code", [
    (121.4, 14.2, "lat_out_of_range"),      # (lon, lat) swap -> invalid latitude
    (14.2, -200.0, "lon_out_of_range"),
    (95.0, 121.4, "lat_out_of_range"),
    (float("nan"), 121.4, "non_finite"),
    (14.2, float("inf"), "non_finite"),
    ("abc", 121.4, "non_numeric"),
    (None, 121.4, "non_numeric"),
    (float("nan"), float("nan"), "non_finite"),
])
def test_invalid_coordinates_rejected(lat, lon, code):
    with pytest.raises(CoordinateError) as excinfo:
        validate_latlon(lat, lon)
    assert excinfo.value.code == code


def test_lat_lon_order_preserved():
    """Returned tuple must keep (latitude, longitude) order."""
    lat, lon = validate_latlon("14.5", "121.5")
    assert (lat, lon) == (14.5, 121.5)
    assert -90 <= lat <= 90 and -180 <= lon <= 180


def test_exact_boundaries_accepted():
    assert validate_latlon(90.0, 180.0) == (90.0, 180.0)
    assert validate_latlon(-90.0, -180.0) == (-90.0, -180.0)


def test_equatorial_point_not_rejected():
    # Both in range near the equator; no heuristic swap rejection.
    lat, lon = validate_latlon(1.35, 103.8)
    assert math.isfinite(lat) and math.isfinite(lon)
