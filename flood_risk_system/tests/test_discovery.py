"""Tests for GIS discovery: folder layout, missing layers, grid consistency.

Uses the real dataset when available (integration) plus SYNTHETIC trees for
the missing-file paths (clearly labelled).
"""
from __future__ import annotations

import numpy as np
import rasterio
from rasterio.transform import from_bounds

from app.gis import discovery


def test_real_discovery_counts(gis_root):
    cov = discovery.discover(gis_root)
    assert len(cov.barangays) == 26
    assert len(cov.municipal) == 5
    # Slugs must map back to polygon names.
    assert all(discovery.slug(b.name) == b.slug_name for b in cov.barangays)


def test_real_barangay_layers_complete(gis_root):
    cov = discovery.discover(gis_root)
    expected = {"elevation_m", "slope_degrees", "land_cover_2021",
                "distance_to_osm_waterway_m",
                "historical_surface_water_occurrence_pct_1984_2024"}
    for b in cov.barangays:
        assert set(b.layers) == expected, f"{b.slug_name}: {set(b.layers)}"


def test_real_polygons_and_municipality(gis_root):
    cov = discovery.discover(gis_root)
    assert cov.municipality_geometry is not None
    # Municipality centroid must be inside its own coverage.
    c = cov.municipality_geometry.centroid
    assert discovery.barangay_for_point(cov, c.x, c.y) is not None


def test_real_bounds_near_laguna(gis_root):
    cov = discovery.discover(gis_root)
    info = cov.municipal["elevation_m"]
    # Raster bounds are in EPSG:32651 (metres) — sanity-check magnitude.
    west, south, east, north = info.bounds
    assert 300_000 < west < 350_000 and 1_500_000 < south < 1_600_000
    assert info.resolution == (30.0, 30.0)


def test_synthetic_missing_root(tmp_path):
    """SYNTHETIC: a non-existent root must be reported, not crash."""
    cov = discovery.discover(tmp_path / "does_not_exist")
    assert cov.barangays == []
    assert cov.municipal == {}
    assert any("not found" in w for w in cov.warnings)


def test_synthetic_broken_geojson(tmp_path):
    """SYNTHETIC: corrupt barangays.geojson -> warning, no crash."""
    (tmp_path / "barangays.geojson").write_text("{ not json", encoding="utf-8")
    cov = discovery.discover(tmp_path)
    assert cov.municipality_geometry is None
    assert any("barangays.geojson" in w for w in cov.warnings)


def test_synthetic_unreadable_raster(tmp_path):
    """SYNTHETIC: a corrupt raster is reported as a warning and excluded."""
    (tmp_path / "elevation_m.tif").write_bytes(b"not a tiff")
    cov = discovery.discover(tmp_path)
    assert "elevation_m" not in cov.municipal
    assert any("unreadable" in w for w in cov.warnings)


def test_grid_alignment_detects_mismatch(tmp_path):
    """SYNTHETIC: rasters on different grids must fail grid_aligned."""
    def w(p, west):
        data = np.zeros((4, 4), dtype="float32")
        with rasterio.open(p, "w", driver="GTiff", height=4, width=4, count=1,
                           dtype="float32", crs="EPSG:32651", nodata=0.0,
                           transform=from_bounds(west, 1550000, west + 400, 1550400, 4, 4)) as dst:
            dst.write(data, 1)
    a = tmp_path / "a.tif"
    b = tmp_path / "b.tif"
    w(a, 500000)
    w(b, 500015)  # 15 m offset: not on the same grid
    assert discovery.grid_aligned([a])
    assert not discovery.grid_aligned([a, b])
