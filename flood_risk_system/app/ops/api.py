"""HTTP API of the MDRRMO operations module.

/api/ops/...     staff endpoints (operations console). Protected by a PIN when FLR_OPS_PIN is set.
/api/public/...  resident-facing endpoints: submit a report, check its status (no personal data returned).
"""
from __future__ import annotations

import csv
import hmac
import io
import json
import logging
import os
import threading
from typing import Literal

import numpy as np
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app import mgb, noah
from app.assessment import Location, extract_location
from app.ops import roads, scenario as scen, store

logger = logging.getLogger("flood-risk-map.ops")

OPS_PIN = os.environ.get("FLR_OPS_PIN", "").strip()


def require_staff(x_ops_pin: str | None = Header(default=None)) -> None:
    """Staff endpoints need the PIN when FLR_OPS_PIN is configured (always set it when serving beyond localhost)."""
    if OPS_PIN and not hmac.compare_digest((x_ops_pin or "").encode(), OPS_PIN.encode()):
        raise HTTPException(401, "Operations PIN required.")


router = APIRouter(prefix="/api/ops", tags=["MDRRMO operations"], dependencies=[Depends(require_staff)])
public = APIRouter(prefix="/api/public", tags=["Public reporting"])

INCIDENT_TYPES = {"flooding": "Flooding", "rescue": "Trapped / needs rescue", "medical": "Medical emergency",
                  "road_blocked": "Road blocked / impassable", "evacuation_request": "Needs help to evacuate",
                  "damaged_infrastructure": "Damaged bridge, dike or road", "power_water": "Power or water outage",
                  "other": "Other"}
DEPTHS = {"none": 0, "ankle": 1, "knee": 2, "waist": 2, "chest": 3, "above_head": 3}
DEPTH_TEXT = {"none": "No standing water", "ankle": "Ankle-deep (~0.2 m)", "knee": "Knee-deep (~0.5 m)",
              "waist": "Waist-deep (~1 m)", "chest": "Chest-deep (~1.3 m)", "above_head": "Above head / roof level"}
STATUSES = ["new", "verified", "dispatched", "resolved", "dismissed"]
EVAC_STATUSES = ["at_home", "advised", "evacuated", "rescued", "unaccounted"]
OPEN = ("new", "verified", "dispatched")


# ------------------------------------------------------------------ helpers

def active_scenario(conn) -> dict:
    s = store.get_setting(conn, "scenario")
    return s if s else scen.derive(dict(scen.DEFAULT))


def barangay_of(lat: float, lon: float) -> str | None:
    from app.gis import discovery
    b = discovery.barangay_for_point(discovery.get_coverage(), lon, lat)
    return b.name if b else None


def point_hazard(lat: float, lon: float) -> dict:
    try:
        return {"noah": noah.house_hazard(lat, lon), "mgb": mgb.susceptibility(lat, lon)}
    except (OSError, ValueError, KeyError):
        return {"noah": None, "mgb": None}


def incident_priority(type_: str, depth: str | None, people: int | None, vulnerable: bool = False) -> str:
    """Triage rule: life safety first, then water depth, then number of people."""
    d = DEPTHS.get(depth or "none", 0)
    people = people or 0
    if type_ in ("rescue", "medical"):
        level = 3 if (d >= 2 or type_ == "medical" or people >= 5) else 2
    elif type_ == "evacuation_request":
        level = 2 + (d >= 2)
    elif type_ == "flooding":
        level = {0: 0, 1: 1, 2: 2, 3: 3}[d]
    elif type_ in ("road_blocked", "damaged_infrastructure"):
        level = 1 + (d >= 2)
    else:
        level = 0
    if people >= 10 or vulnerable:
        level = min(3, level + 1)
    return ["low", "medium", "high", "critical"][level]


def repredict(conn, resident_ids: list[int] | None = None) -> int:
    s = active_scenario(conn)
    rows = store.list_residents(conn) if resident_ids is None else [store.get_resident(conn, i) for i in resident_ids]
    rows = [r for r in rows if r]
    results = scen.predict_residents(rows, s)
    stamp = s.get("set_at") or "default"
    conn.executemany("UPDATE resident SET current_risk = ?, current_probs = ?, priority_score = ?, scenario_stamp = ? "
                     "WHERE resident_id = ?",
                     [(r["current_risk"], json.dumps(r["current_probs"]), r["priority_score"], stamp, r["resident_id"])
                      for r in results])
    conn.commit()
    return len(results)


# ------------------------------------------------------------------ scenario

class ScenarioIn(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    Rainfall24hMm: float = Field(ge=0, le=1000)
    Rainfall3DayMm: float = Field(ge=0, le=1500)
    Rainfall7DayMm: float = Field(ge=0, le=2000)
    source: Literal["manual", "historical", "forecast"] = "manual"
    label: str = Field(default="Manual entry", max_length=200)


@router.get("/scenario")
def get_scenario():
    with store.connect() as conn:
        return active_scenario(conn)


@router.put("/scenario")
def set_scenario(req: ScenarioIn):
    try:
        s = scen.make(req.Rainfall24hMm, req.Rainfall3DayMm, req.Rainfall7DayMm, req.source, req.label)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    with store.connect() as conn:
        store.set_setting(conn, "scenario", s)
        n = repredict(conn)
    return {**s, "residents_updated": n}


@router.get("/scenario/events")
def scenario_events():
    return scen.historical_events()


@router.get("/scenario/forecast")
def scenario_forecast():
    try:
        return scen.forecast()
    except Exception as exc:  # network down, API change: the console falls back to manual entry
        logger.warning("forecast fetch failed: %s", exc)
        raise HTTPException(503, "Forecast service unreachable. Enter rain totals manually.") from exc


# ------------------------------------------------------------------ residents

class ResidentIn(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, str_strip_whitespace=True)
    full_name: str = Field(min_length=2, max_length=120)
    contact_number: str | None = Field(default=None, max_length=40)
    address_text: str | None = Field(default=None, max_length=200)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    location_source: Literal["map_click", "gps", "manual_entry", "survey"] = "map_click"
    age: int = Field(ge=18, le=100)
    housing_material: Literal["Concrete", "SemiConcrete", "LightMaterials"]
    house_members: int = Field(ge=1, le=30)
    children_count: int = Field(ge=0, le=30)
    elder_count: int = Field(ge=0, le=30)
    pwd_count: int = Field(ge=0, le=30)
    special_needs: str | None = Field(default=None, max_length=300)
    notes: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def counts(self):
        if self.children_count + self.elder_count > self.house_members:
            raise ValueError("Children + elders cannot exceed household members.")
        if self.pwd_count > self.house_members:
            raise ValueError("PWD count cannot exceed household members.")
        return self


def _register(conn, req: ResidentIn, is_demo: bool = False) -> dict:
    loc = extract_location(Location(latitude=req.latitude, longitude=req.longitude))
    data = req.model_dump()
    household = scen.household_of(data)
    risk, base = scen.baseline(loc["features"], household)
    created = store.insert_resident(conn, {**data, "barangay": loc["barangay"], "features": loc["features"],
                                           "baseline_risk": risk, "baseline": base, "is_demo": is_demo})
    repredict(conn, [created["resident_id"]])
    out = store.get_resident(conn, created["resident_id"])
    out["warnings"] = loc["warnings"]
    return out


@router.post("/residents", status_code=201)
def create_resident(req: ResidentIn):
    with store.connect() as conn:
        return _register(conn, req)


@router.get("/residents")
def list_residents(barangay: str | None = None, risk: Literal["Low", "Medium", "High"] | None = None,
                   q: str | None = Query(default=None, max_length=80), evac_status: str | None = None,
                   limit: int = Query(default=5000, ge=1, le=20000), compact: bool = False):
    with store.connect() as conn:
        rows = store.list_residents(conn, barangay, risk, q, evac_status, limit)
    if compact:   # map layer: no features/baseline payload
        keep = ("resident_id", "ref_code", "full_name", "barangay", "latitude", "longitude", "current_risk",
                "priority_score", "evac_status", "house_members", "pwd_count", "elder_count", "children_count",
                "address_text", "contact_number")
        rows = [{k: r[k] for k in keep} for r in rows]
    return rows


@router.get("/residents/export.csv")
def export_residents(barangay: str | None = None, risk: str | None = None):
    with store.connect() as conn:
        rows = store.list_residents(conn, barangay, risk)
        s = active_scenario(conn)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([f"Evacuation priority list - scenario: {s['label']} ({s['EffectiveRainMm']} mm effective rain, "
                f"status {s['status_label']})"])
    w.writerow(["Rank", "Ref", "Name", "Contact", "Barangay", "Address", "Predicted risk", "Baseline risk",
                "Priority", "Members", "Children", "Elders", "PWD", "Housing", "Special needs", "Evacuation status",
                "Latitude", "Longitude"])
    for i, r in enumerate(rows, 1):
        w.writerow([i, r["ref_code"], r["full_name"], r["contact_number"], r["barangay"], r["address_text"],
                    r["current_risk"], r["baseline_risk"], r["priority_score"], r["house_members"],
                    r["children_count"], r["elder_count"], r["pwd_count"], r["housing_material"], r["special_needs"],
                    r["evac_status"], r["latitude"], r["longitude"]])
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": "attachment; filename=evacuation_priority_list.csv"})


@router.get("/residents/{rid}")
def get_resident(rid: int):
    with store.connect() as conn:
        r = store.get_resident(conn, rid)
        if r is None:
            raise HTTPException(404, "Resident not found.")
        r["incidents"] = [i for i in store.list_incidents(conn) if i["resident_id"] == rid]
        return r


@router.put("/residents/{rid}")
def update_resident(rid: int, req: ResidentIn):
    with store.connect() as conn:
        before = store.get_resident(conn, rid)
        if before is None:
            raise HTTPException(404, "Resident not found.")
        data = req.model_dump()
        moved = (data["latitude"], data["longitude"]) != (before["latitude"], before["longitude"])
        if moved:
            loc = extract_location(Location(latitude=req.latitude, longitude=req.longitude))
            data["barangay"] = loc["barangay"]
            features = loc["features"]
            data["features_json"] = json.dumps(features)
        else:
            features = before["features"]
        risk, base = scen.baseline(features, scen.household_of(data))
        data.update(baseline_risk=risk, baseline_json=json.dumps(base))
        store.update_resident(conn, rid, data)
        repredict(conn, [rid])
        return store.get_resident(conn, rid)


class EvacStatusIn(BaseModel):
    evac_status: Literal["at_home", "advised", "evacuated", "rescued", "unaccounted"]
    evac_facility_id: int | None = None


@router.post("/residents/{rid}/status")
def set_evac_status(rid: int, req: EvacStatusIn):
    with store.connect() as conn:
        if store.get_resident(conn, rid) is None:
            raise HTTPException(404, "Resident not found.")
        if req.evac_facility_id is not None and store.get_facility(conn, req.evac_facility_id) is None:
            raise HTTPException(422, "Unknown evacuation centre.")
        fid = req.evac_facility_id if req.evac_status in ("evacuated", "rescued") else None
        return store.update_resident(conn, rid, {"evac_status": req.evac_status, "evac_facility_id": fid})


@router.delete("/residents/{rid}", status_code=204)
def delete_resident(rid: int):
    with store.connect() as conn:
        if not store.delete_resident(conn, rid):
            raise HTTPException(404, "Resident not found.")
    return Response(status_code=204)


@router.post("/residents/predict-all")
def predict_all():
    with store.connect() as conn:
        return {"residents_updated": repredict(conn)}


# ------------------------------------------------------------------ incidents

class IncidentIn(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, str_strip_whitespace=True)
    type: Literal[tuple(INCIDENT_TYPES)]  # type: ignore[valid-type]
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    water_depth: Literal[tuple(DEPTHS)] | None = None  # type: ignore[valid-type]
    people_affected: int | None = Field(default=None, ge=0, le=10000)
    description: str | None = Field(default=None, max_length=1000)
    reporter_name: str | None = Field(default=None, max_length=120)
    reporter_contact: str | None = Field(default=None, max_length=40)
    resident_id: int | None = None


def _create_incident(req: IncidentIn, source: str) -> dict:
    barangay = barangay_of(req.latitude, req.longitude)
    if barangay is None:
        raise HTTPException(422, "The location is outside Santa Cruz, Laguna. Move the pin inside the municipality.")
    with store.connect() as conn:
        vulnerable = False
        if req.resident_id is not None:
            r = store.get_resident(conn, req.resident_id)
            if r is None:
                raise HTTPException(422, "Unknown resident.")
            vulnerable = r["pwd_count"] > 0 or r["elder_count"] > 0
        data = req.model_dump()
        data.update(barangay=barangay, source=source, status="new",
                    priority=incident_priority(req.type, req.water_depth, req.people_affected, vulnerable))
        return store.insert_incident(conn, data)


@router.post("/incidents", status_code=201)
def staff_incident(req: IncidentIn):
    return _create_incident(req, "staff")


@router.get("/incidents")
def list_incidents(status: str | None = None, open_only: bool = False):
    with store.connect() as conn:
        return store.list_incidents(conn, status, open_only)


@router.get("/incidents/{iid}")
def get_incident(iid: int):
    with store.connect() as conn:
        i = store.get_incident(conn, iid, with_log=True)
    if i is None:
        raise HTTPException(404, "Incident not found.")
    return i


class IncidentUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    status: Literal["new", "verified", "dispatched", "resolved", "dismissed"] | None = None
    priority: Literal["critical", "high", "medium", "low"] | None = None
    assigned_team: str | None = Field(default=None, max_length=120)
    water_depth: Literal[tuple(DEPTHS)] | None = None  # type: ignore[valid-type]
    people_affected: int | None = Field(default=None, ge=0, le=10000)
    note: str | None = Field(default=None, max_length=1000)


@router.patch("/incidents/{iid}")
def update_incident(iid: int, req: IncidentUpdate):
    with store.connect() as conn:
        out = store.update_incident(conn, iid, req.model_dump(exclude={"note"}), req.note)
    if out is None:
        raise HTTPException(404, "Incident not found.")
    return out


@public.get("/report-options")
def report_options():
    return {"types": INCIDENT_TYPES, "depths": DEPTH_TEXT}


@public.post("/reports", status_code=201)
def public_report(req: IncidentIn):
    if req.resident_id is not None:
        raise HTTPException(422, "resident_id cannot be set on public reports.")
    i = _create_incident(req, "public")
    return {"ref_code": i["ref_code"], "status": i["status"], "barangay": i["barangay"], "created_at": i["created_at"],
            "message": "Report received. Keep your reference number. For life-threatening emergencies also call 911."}


@public.get("/reports/{ref}")
def public_report_status(ref: str):
    with store.connect() as conn:
        i = store.get_incident_by_ref(conn, ref)
    if i is None:
        raise HTTPException(404, "No report with that reference number.")
    return {"ref_code": i["ref_code"], "type": INCIDENT_TYPES.get(i["type"], i["type"]), "status": i["status"],
            "barangay": i["barangay"], "created_at": i["created_at"], "updated_at": i["updated_at"],
            "history": [{"at": h["at"], "status": "new" if h["action"] == "reported"
                         else h["action"].split("status: ", 1)[1].split(" -> ", 1)[1].split(";", 1)[0]}
                        for h in i["log"] if h["action"] == "reported" or h["action"].startswith("status: ")]}


# ------------------------------------------------------------------ facilities

FACILITY_SEED = {  # OSM POI kind -> facility type
    "townhall": "rescue_base", "fire_station": "rescue_base", "police": "rescue_base",
    "hospital": "hospital", "sports_hall": "evacuation_center", "community_centre": "evacuation_center",
    "school": "evacuation_center",
}


def seed_facilities() -> int:
    """Create candidate facilities from OpenStreetMap POIs inside Santa Cruz (once; idempotent)."""
    from app.config import OSM_DATA_DIR
    path = OSM_DATA_DIR / "pois.geojson"
    if not path.exists():
        return 0
    with store.connect() as conn:
        if conn.execute("SELECT COUNT(*) FROM facility").fetchone()[0]:
            return 0
    # The hazard lookups are slow on first use (map loading): do them before opening the database.
    rows = []
    for f in json.loads(path.read_text(encoding="utf-8"))["features"]:
        p = f["properties"]
        ftype = FACILITY_SEED.get(p.get("kind"))
        if not ftype or not p.get("name") or f["geometry"]["type"] != "Point":
            continue
        lon, lat = f["geometry"]["coordinates"]
        brgy = barangay_of(lat, lon)
        if brgy is None:
            continue
        hz = point_hazard(lat, lon)
        flooded = bool(hz["noah"]) and hz["noah"].get("100yr") in ("Medium", "High")
        name = p["name"]
        if p.get("kind") == "townhall" and "Santa Cruz" in name:
            name = f"MDRRMO - {name}"
        notes = ("Candidate from OpenStreetMap; confirm against the MDRRMO's designated list."
                 if ftype == "evacuation_center" else "From OpenStreetMap.")
        if ftype == "evacuation_center" and flooded:
            notes += " Inactive by default: inside a NOAH 100-year Medium/High flood zone."
        rows.append({"name": name, "type": ftype, "latitude": lat, "longitude": lon, "barangay": brgy,
                     "source": "osm", "osm_id": p.get("osm_id"), "hazard": hz, "notes": notes,
                     "is_active": 0 if (ftype == "evacuation_center" and flooded) else 1})
    with store.connect() as conn:
        if conn.execute("SELECT COUNT(*) FROM facility").fetchone()[0]:
            return 0
        for r in rows:
            store.insert_facility(conn, r)
    return len(rows)


@router.get("/facilities")
def list_facilities(type: str | None = None, active_only: bool = False):  # noqa: A002
    with store.connect() as conn:
        return store.list_facilities(conn, type, active_only)


class FacilityIn(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, str_strip_whitespace=True)
    name: str = Field(min_length=2, max_length=160)
    type: Literal["rescue_base", "evacuation_center", "hospital"]
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    capacity: int | None = Field(default=None, ge=0, le=100000)
    notes: str | None = Field(default=None, max_length=500)


@router.post("/facilities", status_code=201)
def create_facility(req: FacilityIn):
    brgy = barangay_of(req.latitude, req.longitude)
    with store.connect() as conn:
        return store.insert_facility(conn, {**req.model_dump(), "barangay": brgy, "source": "manual",
                                            "hazard": point_hazard(req.latitude, req.longitude)})


class FacilityUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str | None = Field(default=None, min_length=2, max_length=160)
    capacity: int | None = Field(default=None, ge=0, le=100000)
    is_active: bool | None = None
    notes: str | None = Field(default=None, max_length=500)


@router.patch("/facilities/{fid}")
def update_facility(fid: int, req: FacilityUpdate):
    with store.connect() as conn:
        if store.get_facility(conn, fid) is None:
            raise HTTPException(404, "Facility not found.")
        data = req.model_dump(exclude_unset=True)
        if "is_active" in data:
            data["is_active"] = int(data["is_active"])
        return store.update_facility(conn, fid, data)


# ------------------------------------------------------------------ routing

class Place(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    facility_id: int | None = None
    resident_id: int | None = None
    incident_id: int | None = None
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)


class RouteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    origin: Place | Literal["nearest_base"] = "nearest_base"
    destination: Place
    vehicle: Literal["standard", "high_clearance", "foot"] = "high_clearance"


class EvacRouteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    origin: Place
    vehicle: Literal["standard", "high_clearance", "foot"] = "foot"


def resolve(conn, place: Place) -> dict:
    if place.facility_id is not None:
        f = store.get_facility(conn, place.facility_id)
        if f is None:
            raise HTTPException(404, "Facility not found.")
        return {"kind": "facility", "id": f["facility_id"], "name": f["name"], "latitude": f["latitude"],
                "longitude": f["longitude"]}
    if place.resident_id is not None:
        r = store.get_resident(conn, place.resident_id)
        if r is None:
            raise HTTPException(404, "Resident not found.")
        return {"kind": "resident", "id": r["resident_id"], "name": f"{r['ref_code']} {r['full_name']}",
                "latitude": r["latitude"], "longitude": r["longitude"]}
    if place.incident_id is not None:
        i = store.get_incident(conn, place.incident_id)
        if i is None:
            raise HTTPException(404, "Incident not found.")
        return {"kind": "incident", "id": i["incident_id"], "name": f"{i['ref_code']} {INCIDENT_TYPES[i['type']]}",
                "latitude": i["latitude"], "longitude": i["longitude"]}
    if place.latitude is None or place.longitude is None:
        raise HTTPException(422, "Give a facility, resident, incident or latitude/longitude.")
    return {"kind": "point", "id": None, "name": f"{place.latitude:.5f}, {place.longitude:.5f}",
            "latitude": place.latitude, "longitude": place.longitude}


def applies_to_routing(i: dict) -> bool:
    """Open reports change routing once trusted: staff reports at once, public reports after a staff member verifies
    them (so one false report cannot divert rescue teams)."""
    return i["status"] in OPEN and (i["source"] == "staff" or i["status"] != "new")


def conditions(conn, net: roads.RoadNetwork) -> tuple[dict, np.ndarray, np.ndarray, list[dict]]:
    """Scenario hazard per road piece, raised by reported flood depth; blocked pieces from blocked-road reports."""
    s = active_scenario(conn)
    trusted = [i for i in store.list_incidents(conn, open_only=True) if applies_to_routing(i)]
    floods = [{"latitude": i["latitude"], "longitude": i["longitude"], "depth_class": DEPTHS[i["water_depth"]],
               "radius_m": 150, "ref_code": i["ref_code"]}
              for i in trusted if DEPTHS.get(i["water_depth"] or "none", 0) > 0]
    hazard = net.hazard(s["hazard_map"], floods)
    blocked = np.zeros(len(net.u), dtype=bool)
    used = []
    for i in trusted:
        if i["type"] in ("road_blocked", "damaged_infrastructure"):
            idx = net.edges_near(i["latitude"], i["longitude"], 30)
            blocked[idx] = True
            used.append({"ref_code": i["ref_code"], "type": i["type"], "latitude": i["latitude"],
                         "longitude": i["longitude"]})
    used += [{"flood_report": True, **f} for f in floods]
    return s, hazard, blocked, used


def _network() -> roads.RoadNetwork:
    try:
        return roads.network()
    except (OSError, ValueError, KeyError) as exc:
        raise HTTPException(503, "Road network data unavailable.") from exc


def _plan(net, s, hazard, blocked, vehicle, src_node, dst_node, src, dst, extra_warnings):
    mode = "foot" if vehicle.foot_ways else "vehicle"
    w_safe = net.weights(vehicle, hazard, s["rain_factor"], blocked, safest=True)
    dist, pred, lookup = net.search(w_safe, src_node)
    nodes = net.path(pred, src_node, dst_node)
    if not nodes:
        raise HTTPException(422, "No road connection between these points.")
    safest = net.describe(nodes, vehicle, hazard, blocked, lookup)
    w_short = net.weights(vehicle, hazard, s["rain_factor"], blocked, safest=False)
    _, pred2, lookup2 = net.search(w_short, src_node)
    shortest = net.describe(net.path(pred2, src_node, dst_node), vehicle, hazard, blocked, lookup2)
    warnings = list(extra_warnings)
    if safest["blocked_m"]:
        warnings.append("No way around a reported blocked road: the route crosses it. Confirm on the ground.")
    if safest["staging_point"]:
        sp = safest["staging_point"]
        warnings.append(f"Water along the last {sp['remaining_m']:.0f} m is too deep for a {vehicle.label.lower()}. "
                        "Stop at the staging point and continue by rubber boat or with a water rescue team.")
    if s["source"] == "default":
        warnings.append("No rain scenario is active: routing uses the NOAH 5-year map as a baseline.")
    return {"vehicle": {"key": vehicle.key, "label": vehicle.label, "note": vehicle.note},
            "origin": src, "destination": dst, "safest": safest, "shortest": shortest,
            "comparison": {"extra_distance_m": safest["length_m"] - shortest["length_m"],
                           "flood_exposure_avoided_m": shortest["flooded_m"] - safest["flooded_m"],
                           "deep_water_avoided_m": shortest["impassable_m"] - safest["impassable_m"]},
            "scenario": {k: s[k] for k in ("label", "hazard_map", "EffectiveRainMm", "status_label")},
            "warnings": warnings, "mode": mode}


def _snap(net, place: dict, mode: str, what: str, warnings: list[str]) -> int:
    node, d = net.snap(place["latitude"], place["longitude"], mode)
    if d > 250:
        warnings.append(f"The {what} is {d:.0f} m from the nearest mapped road; the route ends at that road.")
    return node


@router.post("/route")
def route(req: RouteIn):
    """Safest route for a rescue team (and the plain shortest route for comparison)."""
    net = _network()
    vehicle = roads.VEHICLES[req.vehicle]
    mode = "foot" if vehicle.foot_ways else "vehicle"
    with store.connect() as conn:
        s, hazard, blocked, used = conditions(conn, net)
        dst = resolve(conn, req.destination)
        warnings: list[str] = []
        dst_node = _snap(net, dst, mode, "destination", warnings)
        if req.origin == "nearest_base":
            bases = store.list_facilities(conn, "rescue_base", active_only=True)
            if not bases:
                raise HTTPException(422, "No active rescue base. Add one under Facilities.")
            # Undirected costs: search once from the destination and take the cheapest base.
            w = net.weights(vehicle, hazard, s["rain_factor"], blocked, safest=True)
            dist, _, _ = net.search(w, dst_node)
            scored = [(dist[net.snap(b["latitude"], b["longitude"], mode)[0]], b) for b in bases]
            cost, base = min(scored, key=lambda t: t[0])
            src = {"kind": "facility", "id": base["facility_id"], "name": base["name"],
                   "latitude": base["latitude"], "longitude": base["longitude"], "auto_selected": True}
        else:
            src = resolve(conn, req.origin)
    src_node = _snap(net, src, mode, "origin", warnings)
    out = _plan(net, s, hazard, blocked, vehicle, src_node, dst_node, src, dst, warnings)
    out["conditions_used"] = used
    return out


@router.post("/evacuation-route")
def evacuation_route(req: EvacRouteIn):
    """Route from a resident/point to the evacuation centre that is safest to reach (active, dry at the scenario)."""
    net = _network()
    vehicle = roads.VEHICLES[req.vehicle]
    mode = "foot" if vehicle.foot_ways else "vehicle"
    with store.connect() as conn:
        s, hazard, blocked, used = conditions(conn, net)
        src = resolve(conn, req.origin)
        centres = store.list_facilities(conn, "evacuation_center", active_only=True)
    rp = s["hazard_map"]
    safe = [c for c in centres if not (c.get("hazard") and c["hazard"].get("noah")
                                       and c["hazard"]["noah"].get(rp) in ("Medium", "High"))
            and not (c.get("capacity") and c["occupancy"] >= c["capacity"])]
    if not safe:
        raise HTTPException(422, "No active evacuation centre outside the flood zone with free capacity.")
    warnings: list[str] = []
    src_node = _snap(net, src, mode, "origin", warnings)
    w = net.weights(vehicle, hazard, s["rain_factor"], blocked, safest=True)
    dist, _, _ = net.search(w, src_node)
    scored = sorted(((dist[net.snap(c["latitude"], c["longitude"], mode)[0]], c) for c in safe), key=lambda t: t[0])
    cost, centre = scored[0]
    dst = {"kind": "facility", "id": centre["facility_id"], "name": centre["name"], "latitude": centre["latitude"],
           "longitude": centre["longitude"], "capacity": centre["capacity"], "occupancy": centre["occupancy"]}
    dst_node = _snap(net, dst, mode, "evacuation centre", warnings)
    out = _plan(net, s, hazard, blocked, vehicle, src_node, dst_node, src, dst, warnings)
    out["alternatives"] = [{"facility_id": c["facility_id"], "name": c["name"]} for _, c in scored[1:4]]
    out["conditions_used"] = used
    return out


@router.get("/hazard/{rp}.geojson")
def hazard_layer(rp: Literal["5yr", "25yr", "100yr"]):
    return Response(roads.hazard_geojson(rp), media_type="application/json",
                    headers={"Cache-Control": "public, max-age=86400"})


# ------------------------------------------------------------------ dashboard

@router.get("/summary")
def summary():
    with store.connect() as conn:
        s = active_scenario(conn)
        residents = store.list_residents(conn)
        incidents = store.list_incidents(conn)
        facilities = store.list_facilities(conn)
    by_brgy: dict[str, dict] = {}
    for r in residents:
        b = by_brgy.setdefault(r["barangay"] or "Unknown", {"barangay": r["barangay"], "residents": 0, "people": 0,
                                                            "High": 0, "Medium": 0, "Low": 0, "evacuated_people": 0,
                                                            "open_incidents": 0})
        b["residents"] += 1
        b["people"] += r["house_members"]
        if r["current_risk"]:
            b[r["current_risk"]] += 1
        if r["evac_status"] in ("evacuated", "rescued"):
            b["evacuated_people"] += r["house_members"]
    open_inc = [i for i in incidents if i["status"] in OPEN]
    for i in open_inc:
        by_brgy.setdefault(i["barangay"] or "Unknown", {"barangay": i["barangay"], "residents": 0, "people": 0,
                                                        "High": 0, "Medium": 0, "Low": 0, "evacuated_people": 0,
                                                        "open_incidents": 0})["open_incidents"] += 1
    count = lambda rows, key: {k: sum(1 for r in rows if r[key] == k) for k in {r[key] for r in rows}}
    high = [r for r in residents if r["current_risk"] == "High"]
    centres = [f for f in facilities if f["type"] == "evacuation_center" and f["is_active"]]
    return {
        "scenario": s,
        "residents": {"total": len(residents), "people": sum(r["house_members"] for r in residents),
                      "by_risk": count(residents, "current_risk"), "by_evac_status": count(residents, "evac_status"),
                      "high_risk_people": sum(r["house_members"] for r in high),
                      "high_risk_vulnerable": sum(r["pwd_count"] + r["elder_count"] + r["children_count"]
                                                  for r in high),
                      "high_risk_not_evacuated": sum(1 for r in high if r["evac_status"] not in ("evacuated", "rescued"))},
        "incidents": {"open": len(open_inc), "by_priority": count(open_inc, "priority"),
                      "by_status": count(incidents, "status"), "by_type": count(open_inc, "type")},
        "facilities": {"evacuation_centers_active": len(centres),
                       "capacity": sum(f["capacity"] or 0 for f in centres),
                       "occupancy": sum(f["occupancy"] for f in centres),
                       "rescue_bases": sum(1 for f in facilities if f["type"] == "rescue_base" and f["is_active"])},
        "barangays": sorted(by_brgy.values(), key=lambda b: (-b["High"], -b["open_incidents"], b["barangay"] or "")),
        "network_ready": roads.ready(),
    }


@router.get("/meta")
def meta():
    return {"incident_types": INCIDENT_TYPES, "depths": DEPTH_TEXT, "statuses": STATUSES,
            "evac_statuses": EVAC_STATUSES,
            "vehicles": {k: {"label": v.label, "note": v.note} for k, v in roads.VEHICLES.items()},
            "pin_required": bool(OPS_PIN)}


def warm_up() -> None:
    """Background start-up: facilities from OSM, road network, hazard maps (FLR_OPS_WARMUP=0 disables it)."""
    if os.environ.get("FLR_OPS_WARMUP", "1") == "0":
        return

    def run():
        try:
            n = seed_facilities()
            if n:
                logger.info("Seeded %d candidate facilities from OpenStreetMap", n)
            roads.network()
            for rp in ("5yr", "25yr", "100yr"):
                roads.hazard_geojson(rp)
        except Exception:   # never take the server down; endpoints report their own errors
            logger.exception("operations warm-up failed")
    threading.Thread(target=run, daemon=True, name="ops-warmup").start()
