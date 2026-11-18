"""FastAPI application: interactive household location map + GIS extraction."""
from __future__ import annotations

import json
import logging
import sys
import threading
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI, HTTPException
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import database as db
from app import mapdata, tiles
from app.assessment import router as assessment_router
from app.config import (GIS_OUTPUT_DIR, HOST, OSM_DATA_DIR, OSM_TILE_URL, PORT,
                        PROJECT_ROOT, SATELLITE_TILE_URL)
from app.features import FEATURE_CONTRACT, FEATURE_NAMES, TARGET_NAME, join_with_labels
from app.gis import discovery
from app.gis.extraction import CoordinateError, extract_features, extract_with_labels, validate_latlon
from app.ops import api as ops_api

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("flood-risk-map")

app = FastAPI(title="Santa Cruz Flood Risk & MDRRMO Operations System", version="2.0.0")
app.include_router(assessment_router)
app.include_router(ops_api.router)
app.include_router(ops_api.public)

# The vector map layers are multi-MB GeoJSON documents; gzip cuts the wire
# size roughly 8-10x and keeps cold loads responsive on ordinary connections.
app.add_middleware(GZipMiddleware, minimum_size=2048)

# Coverage is discovered once at startup (shared singleton with app.mapdata);
# the download pipeline owns the files while the app is running.
COVERAGE = discovery.get_coverage()
for w in COVERAGE.warnings:
    log = logger.info if w.startswith("note:") else logger.warning
    log("GIS coverage: %s", w)

# Vector map layers (multi-MB GeoJSON) are parsed and serialized once in a
# background thread; requests before that fall through to on-demand loading.
threading.Thread(target=mapdata.preload, daemon=True, name="mapdata-preload").start()
# Operations module: candidate facilities, road network flood table and hazard layers, loaded in the background.
ops_api.warm_up()
if HOST not in ("127.0.0.1", "localhost", "::1") and not ops_api.OPS_PIN:
    logger.warning("Serving on %s without FLR_OPS_PIN: anyone on the network can open resident records. "
                   "Set FLR_OPS_PIN to protect the operations console.", HOST)

STATIC_DIR = PROJECT_ROOT / "app" / "static"


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "portal.html")


@app.get("/assessment")
def assessment_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "assessment.html")


@app.get("/ops")
def ops_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "ops.html")


@app.get("/report")
def report_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "report.html")


@app.get("/explorer")
def explorer() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> FileResponse:
    return FileResponse(STATIC_DIR / "favicon.svg", media_type="image/svg+xml")


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "gis_root": str(COVERAGE.root),
        "gis_root_exists": COVERAGE.root.exists(),
        "municipal_layers": sorted(COVERAGE.municipal),
        "barangays_with_polygons": sum(b.geometry is not None for b in COVERAGE.barangays),
        "warnings": COVERAGE.warnings,
    }


@app.get("/api/coverage")
def coverage() -> dict:
    return {
        "gis_root": str(COVERAGE.root),
        "crs_consistent": COVERAGE.crs_consistent,
        "municipal_layers": {
            name: {"nodata": info.nodata, "dtype": info.dtype, "crs": info.crs,
                   "resolution": info.resolution, "bounds": info.bounds}
            for name, info in COVERAGE.municipal.items()},
        "barangays": [
            {"name": b.name, "slug": b.slug_name, "has_polygon": b.geometry is not None,
             "layers": sorted(b.layers)}
            for b in COVERAGE.barangays],
        "warnings": COVERAGE.warnings,
    }


@app.get("/api/barangays")
def barangays() -> JSONResponse:
    return JSONResponse(discovery.coverage_geojson(COVERAGE))


@app.get("/api/municipality")
def municipality() -> JSONResponse:
    """Verified Santa Cruz municipal study-area boundary in EPSG:4326."""
    return JSONResponse(discovery.municipality_geojson(COVERAGE))


@app.get("/api/osm/{layer}")
def osm_layer(layer: str) -> Response:
    """Locally cached OSM vector layers (EPSG:4326 GeoJSON, [lon, lat]).

    Parsed and serialized once per server lifetime (see app/mapdata.py).
    """
    if layer not in mapdata.LAYER_FILES:
        raise HTTPException(404, f"unknown layer '{layer}'; expected one of {sorted(mapdata.LAYER_FILES)}")
    path = OSM_DATA_DIR / mapdata.LAYER_FILES[layer]
    supplemental = mapdata.SUPPLEMENTAL_DATA_DIR / mapdata.LAYER_FILES[layer]
    if mapdata.USE_SUPPLEMENTAL_DATA and not path.exists():
        path = supplemental
    if not path.exists():
        if layer == "waterways":
            legacy = GIS_OUTPUT_DIR / "rivers_osm.geojson"
            if legacy.exists():
                path = legacy
        else:
            raise HTTPException(404, f"local OSM layer '{layer}' not available; run "
                                     f"scripts/fetch_osm_map_features.py to cache it")
    _, blob = mapdata.layer(layer)
    if not blob:
        raise HTTPException(404, f"local OSM layer '{layer}' not available; run "
                                 f"scripts/fetch_osm_map_features.py to cache it")
    return Response(content=blob, media_type="application/json",
                    headers={"Cache-Control": "public, max-age=3600"})


@app.get("/api/search")
def search(q: str, limit: int = 25) -> dict:
    """Local search over cached street/POI/locality/address names + barangays.

    Accepts "lat, lon" (WGS 84, latitude first) as a coordinate passthrough.
    Also matches household codes/labels already shown in the app's household
    panel (this application has no separate auth layer; search exposes only
    what the UI already lists). Only names present in the source data are
    returned; nothing is invented.
    """
    limit = max(1, min(limit, 50))
    results = mapdata.search(q, limit)
    # Household identifier search (code/label), merged after geographic hits.
    needle = q.strip().lower()
    if needle:
        with db.connect() as conn:
            for h in db.list_households(conn):
                hay = [s for s in (h.get("code"), h.get("label")) if s]
                if any(needle in s.lower() for s in hay):
                    detail = h.get("barangay_name") or "Santa Cruz, Laguna"
                    loc = h.get("latitude"), h.get("longitude")
                    results.append({"type": "household", "name": h.get("code") or f"HH-{h['household_id']}",
                                    "kind": h.get("label") or "household",
                                    "detail": detail,
                                    "latitude": loc[0], "longitude": loc[1]})
                if len([r for r in results if r["type"] == "household"]) >= 10:
                    break
    results.sort(key=lambda r: ({"barangay": 0, "street": 1, "poi": 2, "locality": 3,
                                 "address": 4, "household": 5, "coordinates": 6}.get(r["type"], 9),
                                r.get("rank", 9), r["name"]))
    return {"query": q, "results": results[:limit]}


@app.get("/api/map-coverage")
def map_coverage() -> dict:
    """Per-barangay vector coverage audit (roads/buildings/POIs/waterways)."""
    return mapdata.map_coverage()


@app.get("/api/tiles/meta")
def tiles_meta() -> dict:
    """Raster overlay legends, units and valid ranges for the map client."""
    return tiles.raster_meta()


@app.get("/api/tiles/{kind}/{z}/{x}/{y}.png")
def tile_png(kind: str, z: int, x: int, y: int) -> Response:
    """256px PNG tile: colour overlays or Terrarium DEM for 3-D terrain."""
    if kind not in tiles.MUNICIPAL_FILES:
        raise HTTPException(404, f"unknown tile layer '{kind}'; expected one of {sorted(tiles.MUNICIPAL_FILES)}")
    if not (0 <= z <= 18):
        raise HTTPException(400, "zoom z must be 0..18")
    n = 2 ** z
    if not (0 <= x < n and 0 <= y < n):
        raise HTTPException(400, "tile x/y out of range for zoom")
    png = tiles.render_tile(kind, z, x, y)
    return Response(content=png, media_type="image/png",
                    headers={"Cache-Control": "public, max-age=86400, immutable"})


@app.get("/api/config")
def config() -> dict:
    """Client configuration: basemap tile styles, glyphs, attribution strings."""
    return {"online_tile_url": OSM_TILE_URL,
            "satellite_tile_url": SATELLITE_TILE_URL,
            "glyphs_url": "/static/fonts/{fontstack}/{range}.pbf",
            "font_stack": ["Noto Sans Regular"],
            "attribution": {
                "osm": '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
                "satellite": "Imagery &copy; Esri, Maxar, Earthstar Geographics",
                "overture": "Building footprints (supplementary): Overture Maps Foundation "
                            "(Google Open Buildings &copy; Google, Microsoft ML Buildings, OpenStreetMap)",
            },
            "note": "online basemaps are optional; the local vector map works without internet"}


@app.get("/api/buildings/overture")
def overture_buildings() -> Response:
    """Supplementary building footprints from Overture Maps Foundation.

    A local cache produced by scripts/fetch_overture_buildings.py. Present in
    the file only when the download was run; the map degrades gracefully when
    absent. Each feature carries its source dataset for attribution.
    """
    path = OSM_DATA_DIR / "buildings_overture.geojson"
    if not path.exists():
        raise HTTPException(404, "supplementary Overture buildings not cached; run "
                                 "scripts/fetch_overture_buildings.py to download them "
                                 "(optional — OSM footprints remain available)")
    blob = path.read_bytes()
    return Response(content=blob, media_type="application/json",
                    headers={"Cache-Control": "public, max-age=86400"})


class ExtractRequest(BaseModel):
    latitude: float = Field(..., description="WGS 84 latitude")
    longitude: float = Field(..., description="WGS 84 longitude")


@app.post("/api/extract")
def extract_point(req: ExtractRequest) -> dict:
    """Preview extraction for an arbitrary point (not saved)."""
    try:
        return extract_with_labels(COVERAGE, req.latitude, req.longitude)
    except CoordinateError as exc:
        raise HTTPException(422, {"code": exc.code, "detail": str(exc)}) from exc


class HouseholdCreate(BaseModel):
    code: str | None = None
    label: str | None = None
    barangay_name: str | None = None
    household_size: int | None = Field(default=None, ge=0)
    children_count: int | None = Field(default=None, ge=0)
    elderly_count: int | None = Field(default=None, ge=0)
    pwd_count: int | None = Field(default=None, ge=0)
    house_material: str | None = None
    notes: str | None = None


@app.post("/api/households", status_code=201)
def create_household(req: HouseholdCreate) -> dict:
    with db.connect() as conn:
        return db.create_household(
            conn, code=req.code, label=req.label, barangay_name=req.barangay_name,
            household_size=req.household_size, children_count=req.children_count,
            elderly_count=req.elderly_count, pwd_count=req.pwd_count,
            house_material=req.house_material, notes=req.notes)


@app.get("/api/households")
def list_households() -> list[dict]:
    with db.connect() as conn:
        return db.list_households(conn)


@app.get("/api/households/{household_id}")
def get_household(household_id: int) -> dict:
    with db.connect() as conn:
        household = db.get_household(conn, household_id)
        if household is None:
            raise HTTPException(404, "household not found")
        household["location"] = db.get_active_location(conn, household_id)
        household["latest_extraction"] = db.latest_extraction(conn, household_id)
        return household


@app.put("/api/households/{household_id}")
def update_household(household_id: int, req: HouseholdCreate) -> dict:
    with db.connect() as conn:
        if db.get_household(conn, household_id) is None:
            raise HTTPException(404, "household not found")
        return db.update_household_attributes(conn, household_id, req.model_dump(exclude_unset=True))


class LocationSet(BaseModel):
    latitude: float
    longitude: float
    coordinate_source: str = "map_click"
    confirm_overwrite: bool = False


@app.put("/api/households/{household_id}/location")
def set_location(household_id: int, req: LocationSet) -> dict:
    try:
        lat, lon = validate_latlon(req.latitude, req.longitude)
    except CoordinateError as exc:
        raise HTTPException(422, {"code": exc.code, "detail": str(exc)}) from exc
    with db.connect() as conn:
        if db.get_household(conn, household_id) is None:
            raise HTTPException(404, "household not found")
        try:
            loc = db.set_location(conn, household_id, lat, lon, req.coordinate_source,
                                  confirm_overwrite=req.confirm_overwrite)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        barangay = discovery.barangay_for_point(COVERAGE, lon, lat)
        barangay_name = barangay.name if barangay else None
        db.update_household_attributes(
            conn, household_id, {"barangay_name": barangay_name}
        )
        loc["barangay_name"] = barangay_name
        return loc


@app.post("/api/households/{household_id}/extract")
def extract_for_household(household_id: int) -> dict:
    with db.connect() as conn:
        household = db.get_household(conn, household_id)
        if household is None:
            raise HTTPException(404, "household not found")
        loc = db.get_active_location(conn, household_id)
        if loc is None:
            raise HTTPException(400, "household has no location yet; set a location first")
        result = extract_with_labels(COVERAGE, loc["latitude"], loc["longitude"])
        saved = db.save_extraction(conn, result, household_id=household_id,
                                   location_id=loc["location_id"])
    saved["result"] = result
    return saved


@app.post("/api/households/{household_id}/verify")
def verify_household_location(household_id: int) -> dict:
    with db.connect() as conn:
        if db.get_household(conn, household_id) is None:
            raise HTTPException(404, "household not found")
        loc = db.verify_location(conn, household_id)
    if loc is None:
        raise HTTPException(400, "household has no active location to verify")
    return loc


class AssessmentCreate(BaseModel):
    collected_at: str
    risk_level: str | None = None
    notes: str | None = None


@app.post("/api/households/{household_id}/assessment", status_code=201)
def add_assessment(household_id: int, req: AssessmentCreate) -> dict:
    """Record a survey-observed label. Never generated by the system."""
    with db.connect() as conn:
        if db.get_household(conn, household_id) is None:
            raise HTTPException(404, "household not found")
        latest = db.latest_extraction(conn, household_id)
        return db.save_assessment(conn, household_id, req.collected_at, req.risk_level,
                                  req.notes, extraction_id=latest["extraction_id"] if latest else None)


@app.get("/api/ml/features")
def ml_feature_contract() -> dict:
    return {
        "features": FEATURE_CONTRACT,
        "feature_names": FEATURE_NAMES,
        "target": {"name": TARGET_NAME,
                   "note": "survey-observed label only; never used as an input feature"},
        "missing_policy": "null value plus missing flag; numerical encoding (e.g. imputation) "
                          "must match the trained model's preprocessing",
    }


@app.get("/api/ml/training-rows")
def ml_training_rows() -> dict:
    """Feature rows joined with survey labels (label null when absent)."""
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT * FROM gis_extraction ORDER BY extraction_id").fetchall()
        rows = [{k: r[k] for k in r.keys()} for r in rows]
        for r in rows:
            r["source_rasters"] = json.loads(r["source_rasters"]) if r["source_rasters"] else None
            r["feature_statuses"] = json.loads(r["feature_statuses"]) if r["feature_statuses"] else None
        assessments = conn.execute("SELECT * FROM assessment ORDER BY assessment_id").fetchall()
        assessments = [{k: a[k] for k in a.keys()} for a in assessments]
    by_household: dict[int, list[dict]] = {}
    for a in assessments:
        by_household.setdefault(a["household_id"], []).append(a)
    joined = join_with_labels(rows, by_household)
    return {"count": len(joined), "feature_names": FEATURE_NAMES, "rows": joined}


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def main() -> None:
    import uvicorn
    # Reuse this instance: importing app.main again when run as a script
    # duplicates startup logging and the map preloading thread.
    uvicorn.run(app, host=HOST, port=PORT, reload=False)


if __name__ == "__main__":
    main()
