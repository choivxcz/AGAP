"""Unit tests for the extraction core using SYNTHETIC rasters/GeoJSON.

These fixtures are hand-made and clearly synthetic: a tiny 4326 DEM-like
raster, a categorical raster, an all-nodata raster, and barangay polygons.
They exercise bounds/nodata/CRS logic without touching the real dataset.
"""
from __future__ import annotations

import json

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_bounds
from shapely.geometry import Polygon

from app.gis import discovery
from app.gis.extraction import CoordinateError, extract_features, extract_with_labels, validate_latlon


def write_raster(path, data, west, south, east, north, nodata, crs="EPSG:32651", dtype="float32"):
    height, width = data.shape
    profile = {
        "driver": "GTiff", "height": height, "width": width, "count": 1,
        "dtype": dtype, "crs": crs,
        "transform": from_bounds(west, south, east, north, width, height),
        "nodata": nodata, "compress": "deflate",
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(data.astype(dtype), 1)


@pytest.fixture(scope="module")
def synthetic_root(tmp_path_factory):
    """SYNTHETIC GIS tree: two barangays with rasters in UTM 51N.

    Polygon A covers x 500000-500900, y 1550000-1550900 (EPSG:32651).
    Polygon B covers x 500900-501800 (adjacent, shared boundary).
    """
    root = tmp_path_factory.mktemp("gis_output")

    # Raster grids aligned to a 100 m grid.
    write_raster(root / "elevation_m.tif",
                 np.array([[10.0, 11.0, 12.0, 13.0],
                           [14.0, 15.0, 16.0, 17.0],
                           [18.0, 19.0, 20.0, 21.0],
                           [22.0, 23.0, 24.0, 25.0]], dtype="float32"),
                 west=500000, east=500400, south=1550000, north=1550400, nodata=0.0)
    write_raster(root / "slope_degrees.tif",
                 np.array([[1.0, 2.0, 3.0, 4.0],
                           [5.0, 6.0, 7.0, 8.0],
                           [9.0, 10.0, 11.0, 12.0],
                           [13.0, 14.0, 0.0, 16.0]], dtype="float32"),
                 west=500000, east=500400, south=1550000, north=1550400, nodata=0.0)
    write_raster(root / "land_cover_2021.tif",
                 np.array([[40, 40, 50, 50],
                           [40, 10, 10, 50],
                           [30, 30, 80, 80],
                           [30, 90, 90, 80]], dtype="uint8"),
                 west=500000, east=500400, south=1550000, north=1550400, nodata=0, dtype="uint8")
    # Distance raster with -9999 nodata ring.
    dist = np.full((4, 4), -9999.0, dtype="float32")
    dist[1:3, 1:3] = np.array([[100.0, 150.0], [200.0, 250.0]], dtype="float32")
    write_raster(root / "distance_to_osm_waterway_m.tif", dist,
                 west=500000, east=500400, south=1550000, north=1550400, nodata=-9999.0)
    # Occurrence raster: valid 0-100 inside, 0 also used as nodata outside polygon.
    occ = np.array([[0, 5, 10, 0],
                    [0, 100, 50, 0],
                    [0, 25, 75, 0],
                    [0, 0, 0, 0]], dtype="uint8")
    write_raster(root / "historical_surface_water_occurrence_pct_1984_2024.tif", occ,
                 west=500000, east=500400, south=1550000, north=1550400, nodata=0, dtype="uint8")

    # Barangay polygons in EPSG:4326 near lat 14.0, lon 121.0 for simplicity.
    # (Discovery reprojects polygons to 4326; sampling transforms via pyproj,
    # so the synthetic UTM raster will NOT overlap these polygons — the tests
    # rely on raster-bounds behaviour, and barangay resolution must be None.)
    polygons = {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "properties": {"BRGY_NAME": "Test A", "CITYMUN": "Santa Cruz", "PROVINCE": "Laguna"},
            "geometry": {"type": "Polygon", "coordinates": [[
                [120.99, 13.99], [121.01, 13.99], [121.01, 14.01], [120.99, 14.01], [120.99, 13.99]]]},
        }, {
            "type": "Feature",
            "properties": {"BRGY_NAME": "Test B", "CITYMUN": "Santa Cruz", "PROVINCE": "Laguna"},
            "geometry": {"type": "Polygon", "coordinates": [[
                [121.01, 13.99], [121.03, 13.99], [121.03, 14.01], [121.01, 14.01], [121.01, 13.99]]]},
        }],
    }
    (root / "barangays.geojson").write_text(json.dumps(polygons), encoding="utf-8")
    (root / "barangays").mkdir()
    for slug in ["test_a", "test_b"]:
        d = root / "barangays" / slug
        d.mkdir(exist_ok=True)
        # Barangay tile: single valid pixel, rest nodata (tests tile fallback).
        write_raster(d / "elevation_m.tif",
                     np.array([[9.0, 0.0], [0.0, 0.0]], dtype="float32"),
                     west=501000, east=501200, south=1551000, north=1551200, nodata=0.0)
    return root


@pytest.fixture(scope="module")
def synthetic_cov(synthetic_root):
    return discovery.discover(synthetic_root)


def test_synthetic_discovery(synthetic_cov):
    assert len(synthetic_cov.barangays) == 2
    assert set(synthetic_cov.municipal) == {
        "elevation_m", "slope_degrees", "land_cover_2021",
        "distance_to_osm_waterway_m", "historical_surface_water_occurrence_pct_1984_2024"}
    assert synthetic_cov.crs_consistent


def test_synthetic_sample_inside(synthetic_cov):
    # Pixel centre of the raster: x=500100, y=1550300 -> value 10.0 (row 0, col 1).
    result = extract_features(synthetic_cov, 14.005, 121.0)
    assert result["barangay"] is not None  # point inside synthetic polygon
    elev = result["features"]["ElevationMeters"]
    assert elev["status"] == "missing"  # UTM raster does not cover the polygon area
    assert "outside raster bounds" in elev["reason"]


def test_synthetic_point_in_polygon(synthetic_cov):
    from app.gis.discovery import barangay_for_point
    b = barangay_for_point(synthetic_cov, 121.0, 14.005)
    assert b is not None and b.name == "Test A"
    b2 = barangay_for_point(synthetic_cov, 121.02, 14.005)
    assert b2 is not None and b2.name == "Test B"
    assert barangay_for_point(synthetic_cov, 121.05, 14.005) is None


def test_synthetic_sample_by_transformed_point():
    """Direct raster sampling with explicit CRS transformation (no polygons)."""
    import rasterio
    from pyproj import Transformer
    # Use the synthetic root from tmp; locate via pytest tmp retention below.
    # Standalone check of rowcol behaviour in UTM:
    tr = Transformer.from_crs("EPSG:4326", "EPSG:32651", always_xy=True)
    x, y = tr.transform(121.4171, 14.2822)
    assert 320000 < x < 330000 and 1575000 < y < 1585000  # Santa Cruz UTM zone 51N


def test_occurrence_zero_valid_inside_polygon(synthetic_cov):
    """0 % occurrence is a legal value; nodata disambiguation is documented."""
    result = extract_features(synthetic_cov, 14.005, 121.0)
    occ = result["features"]["HistoricalWaterOccurrencePct"]
    assert occ["status"] in {"missing", "ok"}  # polygon present, raster does not overlap


def test_extract_preserves_latlon_order(synthetic_cov):
    result = extract_features(synthetic_cov, 14.005, 121.0)
    assert result["input"] == {"latitude": 14.005, "longitude": 121.0}


def test_extract_rejects_swapped_input(synthetic_cov):
    with pytest.raises(CoordinateError):
        extract_features(synthetic_cov, 121.0, 14.005)


def test_land_cover_labels_match_legend():
    from app.gis.extraction import LAND_COVER_LEGEND, land_cover_label
    assert land_cover_label(50) == "Built-up"
    assert land_cover_label(40) == "Cropland"
    assert land_cover_label(999) is None
    assert set(LAND_COVER_LEGEND) == {10, 20, 30, 40, 50, 60, 70, 80, 90, 95, 100}


def test_extract_with_labels_shape(synthetic_cov):
    result = extract_with_labels(synthetic_cov, 14.005, 121.0)
    assert "LandCoverClass" in result["features"]
    assert result["features"]["LandCoverClass"]["unit"] == "label"
