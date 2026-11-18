"""INTEGRATION tests against the real Santa Cruz GIS files (skipped when the
dataset is absent). Values are cross-checked against direct raster reads."""
from __future__ import annotations

import math

import numpy as np
import pytest
import rasterio
from pyproj import Transformer

from app.gis import discovery
from app.gis.extraction import extract_features, extract_with_labels

# Known reference points (WGS 84 lat, lon) inside the municipality.
SANTA_CRUZ_MUNICIPAL_HALL = (14.2822, 121.4171)


@pytest.fixture(scope="module")
def cov(gis_root):
    return discovery.discover(gis_root)


def test_all_26_barangay_folders_present(cov):
    assert len(cov.barangays) == 26
    assert all(b.layers for b in cov.barangays)


def test_all_municipal_layers_present_and_consistent(cov):
    assert set(cov.municipal) == {
        "elevation_m", "slope_degrees", "land_cover_2021",
        "distance_to_osm_waterway_m", "historical_surface_water_occurrence_pct_1984_2024"}
    assert cov.crs_consistent
    assert all(info.crs == "EPSG:32651" for info in cov.municipal.values())


def test_municipal_layers_share_one_30m_grid(gis_root):
    paths = [gis_root / f"{n}.tif" for n in
             ["elevation_m", "slope_degrees", "distance_to_osm_waterway_m",
              "historical_surface_water_occurrence_pct_1984_2024"]]
    assert discovery.grid_aligned(paths)


def test_water_occurrence_layers_regenerated(cov):
    """The JRC tile bug previously made these 100% NoData; they must now hold
    real values inside the municipality."""
    info = cov.municipal["historical_surface_water_occurrence_pct_1984_2024"]
    with rasterio.open(info.municipal_path) as src:
        data = src.read(1)
        valid = data[data != src.nodata]
    assert valid.size > 0, "occurrence raster still entirely NoData"
    assert valid.min() >= 0 and valid.max() <= 100


def test_municipal_hall_extraction_matches_direct_sampling(cov):
    lat, lon = SANTA_CRUZ_MUNICIPAL_HALL
    result = extract_features(cov, lat, lon)
    assert result["extraction_status"] == "ok"
    assert result["barangay"]["matched_by"] == "point_in_polygon"

    tr = Transformer.from_crs("EPSG:4326", "EPSG:32651", always_xy=True)
    x, y = tr.transform(lon, lat)

    with rasterio.open(cov.municipal["elevation_m"].municipal_path) as src:
        row, col = src.index(x, y)
        expected = float(src.read(1)[row, col])
    got = result["features"]["ElevationMeters"]["value"]
    assert got == pytest.approx(expected, abs=1e-6)


def test_plausible_value_ranges_municipal_hall(cov):
    lat, lon = SANTA_CRUZ_MUNICIPAL_HALL
    r = extract_with_labels(cov, lat, lon)["features"]
    elev = r["ElevationMeters"]["value"]
    assert 0.0 < elev < 100.0          # metres, low-lying lakeshore town
    slope = r["SlopeDegrees"]["value"]
    assert 0.0 <= slope < 45.0          # degrees
    dist = r["DistanceToWaterwayM"]["value"]
    assert 0.0 <= dist < 5000.0         # metres on a 30 m grid
    occ = r["HistoricalWaterOccurrencePct"]["value"]
    assert 0.0 <= occ <= 100.0
    code = r["LandCoverCode"]["value"]
    assert code in {10, 20, 30, 40, 50, 60, 70, 80, 90, 95, 100}
    assert r["LandCoverClass"]["value"]


def test_point_outside_coverage_reports_missing(cov):
    # Middle of Laguna de Bay, well outside the barangay polygons.
    result = extract_features(cov, 14.15, 121.25)
    assert result["inside_gis_coverage"] == "outside"
    assert result["barangay"] is None
    assert result["extraction_status"] == "failed"
    for f in result["features"].values():
        assert f["status"] in {"missing", "error"}
        assert f["value"] is None
    assert any("outside" in n for n in result["notes"])


def test_point_far_outside_rejected_gracefully(cov):
    # Manila Bay area: inside lat/lon ranges but nowhere near the rasters.
    result = extract_features(cov, 14.60, 120.97)
    assert result["inside_gis_coverage"] == "outside"
    assert result["extraction_status"] == "failed"


def test_invalid_coordinates_never_sample(cov):
    from app.gis.extraction import CoordinateError
    with pytest.raises(CoordinateError):
        extract_features(cov, 121.4171, 14.2822)  # swapped


def test_occurrence_zero_disambiguation_inside(cov):
    """A 0 % pixel inside the municipality must read as valid 0.0, not nodata."""
    result = extract_features(cov, *SANTA_CRUZ_MUNICIPAL_HALL)
    occ = result["features"]["HistoricalWaterOccurrencePct"]
    if occ["status"] == "ok" and occ["value"] == 0.0:
        assert "never surface water" in occ["note"]
    else:
        assert occ["status"] == "ok"


def test_all_26_barangay_centroids_extract(cov):
    """Every barangay centroid must be inside coverage and yield >= 3 features."""
    for b in cov.barangays:
        assert b.geometry is not None, f"missing polygon for {b.name}"
        c = b.geometry.centroid
        result = extract_features(cov, c.y, c.x)
        n_ok = sum(1 for f in result["features"].values() if f["status"] == "ok")
        assert n_ok >= 3, f"{b.name}: only {n_ok} features extracted at centroid"
        assert result["extraction_status"] in {"ok", "partial"}


def test_elevation_matches_raw_dem_within_tolerance(gis_root):
    """Elevation in the output raster must be close to the raw Copernicus DEM
    sampled at the same location (bilinear reprojection to a 30 m grid)."""
    from app.gis.extraction import LAYER_SPECS  # noqa: F401  (import sanity)
    lat, lon = SANTA_CRUZ_MUNICIPAL_HALL
    out_path = gis_root / "elevation_m.tif"
    with rasterio.open(out_path) as src:
        tr = Transformer.from_crs("EPSG:4326", src.crs, always_xy=True)
        x, y = tr.transform(lon, lat)
        row, col = src.index(x, y)
        out_value = float(src.read(1)[row, col])
    raw_path = gis_root.parent / "raw" / "copernicus_dem_glo30_N14E121.tif"
    with rasterio.open(raw_path) as src:
        row, col = src.index(lon, lat)
        raw_value = float(src.read(1)[row, col])
    # Bilinear resampling of a 30 m source should stay within a few metres.
    assert abs(out_value - raw_value) < 5.0, (out_value, raw_value)


def test_feature_names_units_dtypes_contract(cov):
    lat, lon = SANTA_CRUZ_MUNICIPAL_HALL
    r = extract_with_labels(cov, lat, lon)
    f = r["features"]
    assert f["ElevationMeters"]["unit"] == "m" and f["ElevationMeters"]["dtype"] == "float"
    assert f["SlopeDegrees"]["unit"] == "degrees"
    assert f["DistanceToWaterwayM"]["unit"] == "m"
    assert f["LandCoverCode"]["dtype"] == "integer"
    assert f["HistoricalWaterOccurrencePct"]["unit"] == "%"
    assert isinstance(f["LandCoverCode"]["value"], int)
    assert isinstance(f["LandCoverClass"]["value"], str)
