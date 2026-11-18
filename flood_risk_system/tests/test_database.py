"""Database tests using a temporary SQLite file (no real data touched)."""
from __future__ import annotations

import pytest

from app import database as db


@pytest.fixture()
def conn(tmp_path):
    with db.connect(tmp_path / "test.db") as c:
        yield c


def _make_household(conn, code="HH-0001"):
    return db.create_household(conn, code=code, label="test", barangay_name="Jasaan",
                               household_size=4, children_count=1, elderly_count=1,
                               pwd_count=0, house_material="wood", notes=None)


def _extraction_result(lat=14.29, lon=121.42, brgy="Jasaan", status="ok"):
    return {
        "input": {"latitude": lat, "longitude": lon},
        "inside_gis_coverage": "inside",
        "barangay": {"name": brgy, "slug": "jasaan", "matched_by": "point_in_polygon"},
        "extraction_status": status,
        "extraction_version": "1.0.0",
        "features": {
            "ElevationMeters": {"feature": "ElevationMeters", "status": "ok", "value": 12.5,
                                "source_raster": "/x/elevation_m.tif"},
            "SlopeDegrees": {"feature": "SlopeDegrees", "status": "ok", "value": 1.5,
                             "source_raster": "/x/slope_degrees.tif"},
            "DistanceToWaterwayM": {"feature": "DistanceToWaterwayM", "status": "ok", "value": 300.0,
                                    "source_raster": "/x/distance.tif"},
            "LandCoverCode": {"feature": "LandCoverCode", "status": "ok", "value": 50,
                              "source_raster": "/x/land_cover.tif"},
            "LandCoverClass": {"feature": "LandCoverClass", "status": "ok", "value": "Built-up"},
            "HistoricalWaterOccurrencePct": {"feature": "HistoricalWaterOccurrencePct",
                                             "status": "ok", "value": 3.0,
                                             "source_raster": "/x/occ.tif"},
        },
        "notes": [],
    }


def test_create_and_get_household(conn):
    h = _make_household(conn)
    assert h["household_id"] == 1
    assert h["household_size"] == 4
    got = db.get_household(conn, h["household_id"])
    assert got["code"] == "HH-0001"


def test_unique_household_code(conn):
    _make_household(conn, "HH-DUP")
    with pytest.raises(Exception):
        _make_household(conn, "HH-DUP")


def test_update_attributes_does_not_duplicate(conn):
    h = _make_household(conn)
    updated = db.update_household_attributes(conn, h["household_id"],
                                             {"household_size": 6, "house_material": "concrete"})
    assert updated["household_size"] == 6
    assert updated["household_id"] == h["household_id"]
    assert updated["updated_at"] >= h["updated_at"]


def test_location_set_and_active_flag(conn):
    h = _make_household(conn)
    db.set_location(conn, h["household_id"], 14.29, 121.42, "map_click")
    db.set_location(conn, h["household_id"], 14.30, 121.43, "manual_entry")
    loc = db.get_active_location(conn, h["household_id"])
    assert (loc["latitude"], loc["longitude"]) == (14.30, 121.43)
    assert loc["is_active"] == 1


def test_verified_location_overwrite_guard(conn):
    h = _make_household(conn)
    db.set_location(conn, h["household_id"], 14.29, 121.42, "map_click")
    db.verify_location(conn, h["household_id"])
    with pytest.raises(ValueError, match="confirm_overwrite"):
        db.set_location(conn, h["household_id"], 14.30, 121.43, "map_click")
    loc = db.set_location(conn, h["household_id"], 14.30, 121.43, "map_click",
                          confirm_overwrite=True)
    assert loc["verification_status"] == "unverified"  # new point starts unverified


def test_unverified_location_can_be_replaced(conn):
    h = _make_household(conn)
    db.set_location(conn, h["household_id"], 14.29, 121.42, "map_click")
    db.set_location(conn, h["household_id"], 14.31, 121.44, "map_click")  # no guard
    assert db.get_active_location(conn, h["household_id"])["latitude"] == 14.31


def test_invalid_coordinate_source_rejected(conn):
    h = _make_household(conn)
    with pytest.raises(ValueError, match="coordinate_source"):
        db.set_location(conn, h["household_id"], 14.29, 121.42, "telepathy")


def test_save_and_load_extraction_roundtrip(conn):
    h = _make_household(conn)
    db.set_location(conn, h["household_id"], 14.29, 121.42, "map_click")
    loc = db.get_active_location(conn, h["household_id"])
    saved = db.save_extraction(conn, _extraction_result(), household_id=h["household_id"],
                               location_id=loc["location_id"])
    assert saved["extraction_status"] == "ok"
    assert saved["elevation_m"] == 12.5
    assert saved["land_cover_class"] == "Built-up"
    assert saved["source_rasters"]["ElevationMeters"] == "/x/elevation_m.tif"
    loaded = db.latest_extraction(conn, h["household_id"])
    assert loaded["extraction_id"] == saved["extraction_id"]
    assert loaded["feature_statuses"]["SlopeDegrees"]["value"] == 1.5


def test_extraction_history_preserved(conn):
    h = _make_household(conn)
    db.set_location(conn, h["household_id"], 14.29, 121.42, "map_click")
    loc = db.get_active_location(conn, h["household_id"])
    db.save_extraction(conn, _extraction_result(status="failed"), household_id=h["household_id"],
                       location_id=loc["location_id"])
    db.save_extraction(conn, _extraction_result(), household_id=h["household_id"],
                       location_id=loc["location_id"])
    with conn as c:
        rows = c.execute("SELECT extraction_id, extraction_status FROM gis_extraction "
                         "ORDER BY extraction_id").fetchall()
    assert [r["extraction_status"] for r in rows] == ["failed", "ok"]


def test_assessment_stores_observed_label_only(conn):
    h = _make_household(conn)
    a = db.save_assessment(conn, h["household_id"], "2026-10-25T00:00:00+00:00",
                           risk_level="high", notes="survey")
    assert a["risk_level"] == "high"
    assert a["extraction_id"] is None


def test_list_households_includes_location(conn):
    h = _make_household(conn)
    db.set_location(conn, h["household_id"], 14.29, 121.42, "gps")
    rows = db.list_households(conn)
    assert len(rows) == 1
    assert rows[0]["latitude"] == 14.29
    assert rows[0]["coordinate_source"] == "gps"
