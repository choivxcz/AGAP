"""MDRRMO operations module: scenario, triage, resident risk, public reports, safest routing.

Integration tests use the real road network, NOAH/MGB maps, model and GIS rasters, with a temporary database.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import config
from app.main import app
from app.ops import api as ops_api, roads, scenario as scen

client = TestClient(app)

TOWN_HALL = (14.281776, 121.414396)
FLOODED = (14.2833919, 121.4073882)   # road piece inside a NOAH 100-year High zone near the lakeshore

RESIDENT = {"full_name": "Test Resident", "contact_number": "0917-000-0000", "address_text": "Test street",
            "latitude": FLOODED[0], "longitude": FLOODED[1], "age": 67, "housing_material": "LightMaterials",
            "house_members": 5, "children_count": 2, "elder_count": 2, "pwd_count": 1,
            "special_needs": "wheelchair user"}


@pytest.fixture(scope="module", autouse=True)
def temp_db():
    old = config.DB_PATH
    config.DB_PATH = Path(tempfile.mkdtemp(prefix="flr_ops_")) / "ops.db"
    ops_api.seed_facilities()
    yield
    config.DB_PATH = old


# ------------------------------------------------------------------ pure rules

def test_scenario_maps_rain_to_conservative_noah_map_and_status():
    assert scen.make(40, 60, 90, "manual", "x")["hazard_map"] == "5yr"
    s = scen.make(150, 160, 200, "manual", "x")
    assert s["EffectiveRainMm"] == pytest.approx(153.0)
    assert s["hazard_map"] == "25yr" and s["status"] == "warning"
    assert scen.make(240, 300, 400, "manual", "x")["hazard_map"] == "100yr"
    assert scen.make(240, 300, 400, "manual", "x")["status"] == "extreme"
    with pytest.raises(ValueError):
        scen.make(100, 50, 200, "manual", "x")       # 3-day total below 24-hour total


def test_forecast_picks_wettest_of_next_three_days():
    from datetime import datetime, timedelta, timezone
    today = (datetime.now(timezone.utc) + timedelta(hours=8)).date()      # Manila date, as the module uses
    days = [(today + timedelta(days=i)).isoformat() for i in range(-7, 7)]
    rain = [0] * 7 + [10, 120, 30, 200, 0, 0, 0]     # day +3 (200 mm) is beyond the 3-day window
    s = scen.forecast(fetch=lambda url: {"daily": {"time": days, "precipitation_sum": rain}})
    assert s["scenario"]["Rainfall24hMm"] == 120.0
    assert s["scenario"]["Rainfall3DayMm"] == 130.0


def test_incident_triage():
    p = ops_api.incident_priority
    assert p("rescue", "chest", 3) == "critical"
    assert p("rescue", "ankle", 1) == "high"
    assert p("flooding", "knee", 2) == "high"
    assert p("flooding", "ankle", 2) == "medium"
    assert p("flooding", "ankle", 2, vulnerable=True) == "high"
    assert p("road_blocked", None, None) == "medium"
    assert p("power_water", None, 50) == "medium"


def test_priority_ranks_risk_before_vulnerability():
    base = {"pwd_count": 0, "elder_count": 0, "children_count": 0, "housing_material": "Concrete"}
    vulnerable = {"pwd_count": 3, "elder_count": 3, "children_count": 4, "housing_material": "LightMaterials",
                  "special_needs": "bedridden"}
    assert scen.priority("High", {"High": .6}, base) > scen.priority("Medium", {"Medium": .9}, vulnerable)
    assert scen.priority("Medium", None, vulnerable) > scen.priority("Medium", None, base)


# ------------------------------------------------------------------ road network

def test_safest_route_is_never_more_exposed_than_shortest():
    net = roads.network()
    veh = roads.VEHICLES["high_clearance"]
    hz = net.hazard("100yr", [])
    blocked = np.zeros(len(net.u), dtype=bool)
    src, _ = net.snap(*TOWN_HALL, "vehicle")
    dst, _ = net.snap(*FLOODED, "vehicle")
    safe_w = net.weights(veh, hz, 1.0, blocked, True)
    _, pred, lk = net.search(safe_w, src)
    safe = net.describe(net.path(pred, src, dst), veh, hz, blocked, lk)
    _, pred2, lk2 = net.search(net.weights(veh, hz, 1.0, blocked, False), src)
    short = net.describe(net.path(pred2, src, dst), veh, hz, blocked, lk2)
    assert short["length_m"] <= safe["length_m"]
    assert safe["impassable_m"] <= short["impassable_m"]
    assert safe["length_by_depth_m"]["High"] < short["length_by_depth_m"]["High"]


def test_bridges_are_not_counted_as_flooded():
    net = roads.network()
    hz = net.hazard("100yr", [])
    assert net.bridge.any() and not hz[net.bridge].any()


def test_reported_flood_raises_hazard_nearby_only():
    net = roads.network()
    before = net.hazard("5yr", [])
    after = net.hazard("5yr", [{"latitude": TOWN_HALL[0], "longitude": TOWN_HALL[1], "depth_class": 3,
                                "radius_m": 100}])
    changed = np.flatnonzero(after != before)
    assert len(changed) > 0
    d = np.hypot(*(net.mid_xy[changed] - np.array(roads.to_xy(TOWN_HALL[1], TOWN_HALL[0]))).T)
    assert d.max() <= 100.5


# ------------------------------------------------------------------ API workflow

def test_facilities_seeded_from_osm_inside_santa_cruz():
    facilities = client.get("/api/ops/facilities").json()
    types = {f["type"] for f in facilities}
    assert {"rescue_base", "evacuation_center"} <= types
    assert any(f["name"].startswith("MDRRMO") for f in facilities)
    assert all(f["barangay"] for f in facilities)


def test_resident_registration_scenario_and_priority_list():
    r = client.post("/api/ops/residents", json=RESIDENT)
    assert r.status_code == 201, r.text
    res = r.json()
    assert res["ref_code"].startswith("RES-") and res["barangay"]
    assert res["baseline_risk"] in ("Low", "Medium", "High")
    dry = client.put("/api/ops/scenario", json={"Rainfall24hMm": 0, "Rainfall3DayMm": 0, "Rainfall7DayMm": 5}).json()
    low_risk = client.get(f"/api/ops/residents/{res['resident_id']}").json()["current_risk"]
    s = client.put("/api/ops/scenario", json={"Rainfall24hMm": 226.1, "Rainfall3DayMm": 357.5,
                                              "Rainfall7DayMm": 362.8, "source": "historical",
                                              "label": "2020-02-20"}).json()
    assert s["residents_updated"] >= 1 and s["hazard_map"] == "100yr" and dry["hazard_map"] == "5yr"
    storm = client.get(f"/api/ops/residents/{res['resident_id']}").json()
    assert storm["current_risk"] == "High" and low_risk != "High"
    assert storm["priority_score"] > 200
    csv_text = client.get("/api/ops/residents/export.csv").text
    assert "Test Resident" in csv_text and "Evacuation priority list" in csv_text
    bad = client.post("/api/ops/residents", json={**RESIDENT, "children_count": 4, "elder_count": 3})
    assert bad.status_code == 422


def test_public_report_lifecycle_and_privacy():
    r = client.post("/api/public/reports", json={"type": "rescue", "latitude": FLOODED[0], "longitude": FLOODED[1],
                                                 "water_depth": "chest", "people_affected": 4,
                                                 "reporter_name": "Juan", "reporter_contact": "0917"})
    assert r.status_code == 201, r.text
    ref = r.json()["ref_code"]
    status = client.get(f"/api/public/reports/{ref.lower()}").json()
    assert status["status"] == "new" and "reporter_contact" not in status and "latitude" not in status
    queue = client.get("/api/ops/incidents?open_only=true").json()
    inc = next(i for i in queue if i["ref_code"] == ref)
    assert inc["priority"] == "critical"
    upd = client.patch(f"/api/ops/incidents/{inc['incident_id']}",
                       json={"status": "dispatched", "assigned_team": "WASAR Team 1", "note": "boat sent"}).json()
    assert upd["status"] == "dispatched" and len(upd["log"]) == 2
    assert [h["status"] for h in client.get(f"/api/public/reports/{ref}").json()["history"]] == ["new", "dispatched"]
    outside = client.post("/api/public/reports", json={"type": "flooding", "latitude": 14.60, "longitude": 121.00})
    assert outside.status_code == 422
    assert client.post("/api/public/reports", json={"type": "flooding", "latitude": FLOODED[0],
                                                    "longitude": FLOODED[1], "resident_id": 1}).status_code == 422


def test_rescue_route_from_nearest_base_and_blocked_road_detour():
    rid = client.get("/api/ops/residents").json()[0]["resident_id"]
    out = client.post("/api/ops/route", json={"destination": {"resident_id": rid}, "vehicle": "standard"})
    assert out.status_code == 200, out.text
    route = out.json()
    assert route["origin"]["auto_selected"] and route["safest"]["segments"]
    assert route["safest"]["impassable_m"] <= route["shortest"]["impassable_m"]
    assert route["safest"]["staging_point"] is not None    # deep water at the house: last stretch by boat
    # Block a dry road piece in the middle of the safest route: the next route must go around it.
    seg = max(route["safest"]["segments"], key=lambda s: len(s["coordinates"]))
    lon, lat = seg["coordinates"][len(seg["coordinates"]) // 2]
    client.post("/api/ops/incidents", json={"type": "road_blocked", "latitude": lat, "longitude": lon})
    again = client.post("/api/ops/route", json={"origin": {"facility_id": route["origin"]["id"]},
                                                "destination": {"resident_id": rid}, "vehicle": "standard"}).json()
    assert again["safest"]["blocked_m"] == 0
    assert any(c.get("type") == "road_blocked" for c in again["conditions_used"])


def test_evacuation_route_goes_to_a_dry_active_centre():
    rid = client.get("/api/ops/residents").json()[0]["resident_id"]
    out = client.post("/api/ops/evacuation-route", json={"origin": {"resident_id": rid}})
    assert out.status_code == 200, out.text
    fid = out.json()["destination"]["id"]
    centre = next(f for f in client.get("/api/ops/facilities").json() if f["facility_id"] == fid)
    assert centre["is_active"] and centre["type"] == "evacuation_center"
    assert centre["hazard"]["noah"]["100yr"] not in ("Medium", "High")
    moved = client.post(f"/api/ops/residents/{rid}/status", json={"evac_status": "evacuated",
                                                                   "evac_facility_id": fid}).json()
    assert moved["evac_status"] == "evacuated"
    occupancy = next(f for f in client.get("/api/ops/facilities").json() if f["facility_id"] == fid)["occupancy"]
    assert occupancy == RESIDENT["house_members"]


def test_summary_and_pages():
    s = client.get("/api/ops/summary").json()
    assert s["residents"]["total"] >= 1 and s["incidents"]["open"] >= 1 and s["barangays"]
    for page in ("/", "/ops", "/report", "/assessment", "/explorer"):
        assert client.get(page).status_code == 200
    assert client.get("/api/ops/hazard/25yr.geojson").json()["type"] == "FeatureCollection"


def test_staff_pin(monkeypatch):
    monkeypatch.setattr(ops_api, "OPS_PIN", "4821")
    assert client.get("/api/ops/summary").status_code == 401
    assert client.get("/api/ops/summary", headers={"X-Ops-Pin": "0000"}).status_code == 401
    assert client.get("/api/ops/summary", headers={"X-Ops-Pin": "4821"}).status_code == 200
    assert client.get("/api/public/report-options").status_code == 200    # public side stays open
