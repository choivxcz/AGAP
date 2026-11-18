"""API integration tests using FastAPI TestClient against the real GIS data.

Uses a temporary database via env var before the app is imported.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

_TEMP_DB = Path(tempfile.mkdtemp(prefix="flr_api_")) / "api_test.db"
os.environ["FLR_DB_PATH"] = str(_TEMP_DB)

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

client = TestClient(app)


@pytest.fixture(scope="module", autouse=True)
def clean_db():
    if _TEMP_DB.exists():
        _TEMP_DB.unlink()
    yield
    if _TEMP_DB.exists():
        _TEMP_DB.unlink()


def test_health_and_contract():
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    ml = client.get("/api/ml/features").json()
    assert "RiskLevel" not in ml["feature_names"]
    assert ml["target"]["name"] == "RiskLevel"


def test_index_and_static_assets():
    assert client.get("/").status_code == 200
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/vendor/leaflet.js").status_code == 200
    assert client.get("/static/vendor/leaflet.css").status_code == 200


def test_barangays_geojson():
    r = client.get("/api/barangays")
    assert r.status_code == 200
    fc = r.json()
    assert fc["type"] == "FeatureCollection"
    assert len(fc["features"]) == 26
    for f in fc["features"]:
        ring = f["geometry"]["coordinates"][0]
        # GeoJSON order is [lon, lat].
        lon, lat = ring[0]
        assert -180 <= lon <= 180 and -90 <= lat <= 90


def test_osm_layers_available():
    for layer in ["buildings", "roads", "waterways"]:
        r = client.get(f"/api/osm/{layer}")
        assert r.status_code == 200, layer
        fc = r.json()
        assert fc["type"] == "FeatureCollection"
        assert len(fc["features"]) > 0
    assert client.get("/api/osm/nope").status_code == 404


def test_extract_endpoint_valid_and_invalid():
    r = client.post("/api/extract", json={"latitude": 14.2822, "longitude": 121.4171})
    assert r.status_code == 200
    body = r.json()
    assert body["extraction_status"] in {"ok", "partial"}
    r2 = client.post("/api/extract", json={"latitude": 121.4171, "longitude": 14.2822})
    assert r2.status_code == 422
    assert r2.json()["detail"]["code"] == "lat_out_of_range"


def test_household_flow_create_location_extract_verify():
    r = client.post("/api/households", json={
        "code": "HH-API-1", "label": "api test", "household_size": 4,
        "children_count": 1, "elderly_count": 1, "pwd_count": 0,
        "house_material": "concrete"})
    assert r.status_code == 201
    hh_id = r.json()["household_id"]

    # Extraction before location -> 400
    assert client.post(f"/api/households/{hh_id}/extract").status_code == 400

    # Bad coordinates -> 422
    r = client.put(f"/api/households/{hh_id}/location",
                   json={"latitude": 500, "longitude": 121.4})
    assert r.status_code == 422

    # Set location inside Santisima Cruz centroid area.
    brgys = client.get("/api/barangays").json()
    sc = next(f for f in brgys["features"] if f["properties"]["name"] == "Santisima Cruz")
    ring = sc["geometry"]["coordinates"][0]
    lat = sum(p[1] for p in ring) / len(ring)
    lon = sum(p[0] for p in ring) / len(ring)
    r = client.put(f"/api/households/{hh_id}/location",
                   json={"latitude": lat, "longitude": lon, "coordinate_source": "map_click"})
    assert r.status_code == 200
    assert r.json()["verification_status"] == "unverified"
    assert r.json()["barangay_name"]
    household = client.get(f"/api/households/{hh_id}")
    assert household.status_code == 200
    assert household.json()["barangay_name"] == r.json()["barangay_name"]

    r = client.post(f"/api/households/{hh_id}/extract")
    assert r.status_code == 200
    saved = r.json()
    assert saved["extraction_status"] in {"ok", "partial"}
    assert saved["household_id"] == hh_id
    assert saved["result"]["barangay"]["name"] == "Santisima Cruz"

    # Verify then attempt overwrite without confirmation -> 409
    assert client.post(f"/api/households/{hh_id}/verify").status_code == 200
    r = client.put(f"/api/households/{hh_id}/location",
                   json={"latitude": lat + 0.001, "longitude": lon,
                         "coordinate_source": "map_click", "confirm_overwrite": False})
    assert r.status_code == 409
    r = client.put(f"/api/households/{hh_id}/location",
                   json={"latitude": lat + 0.001, "longitude": lon,
                         "coordinate_source": "map_click", "confirm_overwrite": True})
    assert r.status_code == 200
    assert r.json()["verification_status"] == "unverified"

    # Assessment + training rows.
    r = client.post(f"/api/households/{hh_id}/assessment",
                    json={"collected_at": "2026-10-25T00:00:00+00:00", "risk_level": "moderate"})
    assert r.status_code == 201
    rows = client.get("/api/ml/training-rows").json()
    assert rows["count"] >= 1
    mine = [x for x in rows["rows"] if x["household_id"] == hh_id]
    assert mine[-1]["label"] == "moderate"
    assert "RiskLevel" not in rows["feature_names"]


def test_household_barangay_can_be_recorded_from_map_selection():
    created = client.post("/api/households", json={"code": "HH-MAP-BRGY", "label": "map test"})
    assert created.status_code == 201
    household_id = created.json()["household_id"]
    updated = client.put(
        f"/api/households/{household_id}",
        json={"barangay_name": "Bagumbayan"},
    )
    assert updated.status_code == 200
    assert updated.json()["barangay_name"] == "Bagumbayan"


def test_training_rows_exclude_risk_from_features():
    rows = client.get("/api/ml/training-rows").json()
    assert "RiskLevel" not in rows["feature_names"]
    for row in rows["rows"]:
        assert set(row["features"].keys()) == set(rows["feature_names"])
        assert "RiskLevel" not in row["features"]


def test_unknown_household_404():
    assert client.get("/api/households/99999").status_code == 404
    assert client.post("/api/households/99999/extract").status_code == 404
