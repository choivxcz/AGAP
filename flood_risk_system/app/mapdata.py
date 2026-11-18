"""In-memory map data: cached OSM/GeoJSON layers, search index, coverage stats.

The previous implementation re-read and re-parsed the multi-megabyte GeoJSON
files on every request. Layers here are parsed once per server lifetime and
served as pre-serialized JSON bytes.

Search is strictly local: it indexes names that actually exist in the cached
OSM extracts and the barangay boundary file. Nothing is invented; unnamed
roads/buildings cannot be found by name.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

from shapely.geometry import Point, shape
from shapely.strtree import STRtree

from app.config import OSM_DATA_DIR
from app.gis import discovery
from app.gis.discovery import barangay_for_point, get_coverage

COVERAGE = get_coverage()

LAYER_FILES = {
    "buildings": "buildings.geojson",
    "roads": "roads.geojson",
    "waterways": "waterways.geojson",
    "pois": "pois.geojson",
    "place": "place.geojson",
    "addresses": "addresses.geojson",
}

# Optional supplementary layer (Overture Maps footprints); served separately
# from the OSM buildings so each feature keeps its own provenance.
OVERTURE_BUILDINGS_FILE = "buildings_overture.geojson"
SUPPLEMENTAL_DATA_DIR = OSM_DATA_DIR / "supplemental"
USE_SUPPLEMENTAL_DATA = False

_lock = threading.Lock()
_features: dict[str, list[dict]] = {}
_serialized: dict[str, bytes] = {}
_brgy_tree = None            # STRtree over barangay polygons
_brgy_by_idx: list[discovery.BarangayInfo] = []
_coverage_cache: dict | None = None


def _parse_fc(path: Path) -> tuple[list[dict], bytes]:
    fc = json.loads(path.read_text(encoding="utf-8"))
    feats = fc.get("features", [])
    return feats, json.dumps(fc, ensure_ascii=False).encode("utf-8")


def _source_features(name: str) -> list[dict]:
    """Load supplemental features first, then retain unmatched legacy features."""
    filename = LAYER_FILES[name]
    paths = ([SUPPLEMENTAL_DATA_DIR / filename, OSM_DATA_DIR / filename]
             if USE_SUPPLEMENTAL_DATA else [OSM_DATA_DIR / filename])
    merged: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for path in paths:
        if not path.exists():
            continue
        features, _ = _parse_fc(path)
        for feature in features:
            props = feature.get("properties", {})
            identity = (str(props["osm_id"]), "") if props.get("osm_id") else (
                "", json.dumps(feature.get("geometry"), sort_keys=True))
            if identity in seen:
                continue
            seen.add(identity)
            merged.append(feature)
    return merged


def _with_barangay_context(features: list[dict]) -> list[dict]:
    """Attach source-derived containing barangay names to map features."""
    if _brgy_tree is None:
        _build_brgy_index()
    if _brgy_tree is None:
        return features
    enriched = []
    for feature in features:
        geometry = feature.get("geometry")
        if not geometry:
            enriched.append(feature)
            continue
        try:
            point = shape(geometry).representative_point()
            barangay = barangay_at(point.x, point.y)
        except Exception:  # noqa: BLE001
            barangay = None
        if barangay is None:
            enriched.append(feature)
            continue
        enriched.append({
            **feature,
            "properties": {
                **feature.get("properties", {}),
                "barangay_name": barangay.name,
            },
        })
    return enriched


def _build_brgy_index() -> None:
    global _brgy_tree, _brgy_by_idx
    geoms, infos = [], []
    for b in COVERAGE.barangays:
        if b.geometry is not None:
            geoms.append(b.geometry)
            infos.append(b)
    _brgy_by_idx = infos
    _brgy_tree = STRtree(geoms) if geoms else None


def barangay_at(lon: float, lat: float) -> discovery.BarangayInfo | None:
    """Fast point-in-polygon via STRtree (falls back to linear scan)."""
    if _brgy_tree is None:
        return barangay_for_point(COVERAGE, lon, lat)
    idx = _brgy_tree.query(Point(lon, lat))
    for i in idx:
        if _brgy_by_idx[i].geometry.covers(Point(lon, lat)):
            return _brgy_by_idx[i]
    return None


def layer(name: str) -> tuple[list[dict], bytes | None]:
    """(features, serialized_bytes) for a cached layer; bytes None if missing."""
    with _lock:
        if name in _features:
            return _features[name], _serialized.get(name)
    source_paths = (SUPPLEMENTAL_DATA_DIR / LAYER_FILES[name],
                    OSM_DATA_DIR / LAYER_FILES[name])
    if not any(path.exists() for path in (source_paths if USE_SUPPLEMENTAL_DATA
                                          else source_paths[1:])):
        with _lock:
            _features[name] = []
            _serialized[name] = b""
        return [], None
    feats = _source_features(name)
    blob = json.dumps({"type": "FeatureCollection", "features": feats},
                      ensure_ascii=False).encode("utf-8")
    if name in {"roads", "buildings", "pois", "waterways"}:
        feats = _with_barangay_context(feats)
        blob = json.dumps({"type": "FeatureCollection", "features": feats},
                          ensure_ascii=False).encode("utf-8")
    with _lock:
        _features[name] = feats
        _serialized[name] = blob
    return feats, blob


def preload() -> None:
    """Parse all layers once (called at startup in a background thread)."""
    _build_brgy_index()
    for name in LAYER_FILES:
        layer(name)


# ------------------------------------------------------------------- search

def _norm(s: str) -> str:
    return " ".join(str(s).lower().split())


def _rank(name: str, q: str) -> int:
    """0 best (prefix / word-start) … 3 weakest (substring)."""
    n, qq = _norm(name), _norm(q)
    if n == qq:
        return 0
    if n.startswith(qq):
        return 1
    if f" {qq}" in f" {n}":
        return 2
    return 3


def search(query: str, limit: int = 25) -> list[dict]:
    """Local name search over roads, POIs, localities, addresses, barangays.

    Accepts "lat, lon" or "lat lon" coordinates as a passthrough result type.
    """
    q = (query or "").strip()
    if not q:
        return []
    results: list[dict] = []

    # Coordinate passthrough (WGS 84, latitude first).
    parts = q.replace(",", " ").split()
    if len(parts) == 2:
        try:
            lat, lon = float(parts[0]), float(parts[1])
            if -90 <= lat <= 90 and -180 <= lon <= 180:
                b = barangay_at(lon, lat)
                results.append({"type": "coordinates", "name": f"{lat:.6f}, {lon:.6f}",
                                "kind": "lat, lon — WGS 84",
                                "detail": b.name if b else "outside Santa Cruz barangay boundaries",
                                "latitude": lat, "longitude": lon})
        except ValueError:
            pass

    for b in COVERAGE.barangays:
        if _rank(b.name, q) <= 3:
            results.append({"type": "barangay", "name": b.name, "kind": "barangay",
                            "detail": "Santa Cruz, Laguna", "latitude": None,
                            "longitude": None, "_geom": b.geometry})
    for lname, rtype, detail in (("roads", "street", "OSM street"),
                                 ("pois", "poi", "landmark / POI"),
                                 ("place", "locality", "sitio / locality"),
                                 ("addresses", "address", "OSM address point")):
        feats, _ = layer(lname)
        for f in feats:
            p = f.get("properties", {})
            name = p.get("name") or (f"{p.get('housenumber')} {p.get('street')}"
                                     if lname == "addresses" and p.get("street") else None)
            if not name:
                continue
            rank = _rank(name, q)
            if rank > 3:
                continue
            results.append({"type": rtype, "name": name, "kind": p.get("kind") or "",
                            "detail": detail, "rank": rank, "_geom": f.get("geometry")})
    _finalize(results, limit)
    return results


def _finalize(results: list[dict], limit: int) -> None:
    for r in results:
        geom = r.pop("_geom", None)
        lat, lng = r.get("latitude"), r.get("longitude")
        if (lat is None or lng is None) and geom is not None:
            if geom["type"] == "Point":
                lng, lat = geom["coordinates"][0], geom["coordinates"][1]
            else:
                c = shape(geom).centroid
                lng, lat = c.x, c.y
        r["latitude"], r["longitude"] = (None if lat is None else round(float(lat), 6),
                                         None if lng is None else round(float(lng), 6))
        if lat is not None:
            b = barangay_at(lng, lat)
            if b:
                r["detail"] = f"{b.name}, Santa Cruz, Laguna"
    results.sort(key=lambda r: ({"barangay": 0, "street": 1, "poi": 2, "locality": 3,
                                 "address": 4, "coordinates": 5}.get(r["type"], 9),
                                r.get("rank", 9), r["name"]))
    del results[limit:]


def map_coverage() -> dict:
    """Per-barangay vector-coverage audit (computed once, cached)."""
    global _coverage_cache
    with _lock:
        if _coverage_cache is not None:
            return _coverage_cache
    stats = {b.slug_name: {"name": b.name, "roads": 0, "named_roads": 0,
                           "buildings": 0, "buildings_overture": 0,
                           "pois": 0, "waterways": 0}
             for b in COVERAGE.barangays}
    tree = _brgy_tree
    if tree is None:
        _build_brgy_index()
        tree = _brgy_tree
    counts = {"roads": 0, "named_roads": 0, "buildings": 0,
              "buildings_overture": 0, "pois": 0, "waterways": 0}

    def bump(feature: dict, key: str) -> None:
        geom = feature.get("geometry")
        if not geom:
            return
        try:
            pt = shape(geom).representative_point()
        except Exception:  # noqa: BLE001
            return
        idx = tree.query(pt) if tree is not None else []
        for i in idx:
            if _brgy_by_idx[i].geometry.covers(pt):
                slug = _brgy_by_idx[i].slug_name
                if slug in stats:
                    stats[slug][key] += 1
                    counts[key] += 1
                return

    feats_roads, _ = layer("roads")
    for f in feats_roads:
        bump(f, "roads")
        if f["properties"].get("name"):
            # named flag follows the road's barangay assignment
            geom = f.get("geometry")
            try:
                pt = shape(geom).representative_point()
            except Exception:  # noqa: BLE001
                continue
            idx = tree.query(pt) if tree is not None else []
            for i in idx:
                if _brgy_by_idx[i].geometry.covers(pt):
                    slug = _brgy_by_idx[i].slug_name
                    if slug in stats:
                        stats[slug]["named_roads"] += 1
                        counts["named_roads"] += 1
                    break
    for f in layer("buildings")[0]:
        bump(f, "buildings")
    overture = OSM_DATA_DIR / OVERTURE_BUILDINGS_FILE
    if overture.exists():
        for f in _parse_fc(overture)[0]:
            bump(f, "buildings_overture")
    for f in layer("pois")[0]:
        bump(f, "pois")
    for f in layer("waterways")[0]:
        bump(f, "waterways")

    out = {
        "summary": {"total_roads": counts["roads"], "named_roads": counts["named_roads"],
                    "named_road_pct": round(100 * counts["named_roads"] / counts["roads"], 1)
                    if counts["roads"] else 0.0,
                    "total_buildings": counts["buildings"],
                    "total_buildings_overture": counts["buildings_overture"],
                    "total_pois": counts["pois"],
                    "total_waterways": counts["waterways"],
                    "note": "counts assign each feature to its containing barangay polygon "
                            "(features outside boundaries are not counted). 'buildings' = OSM "
                            "footprints; 'buildings_overture' = supplementary Overture (Google/"
                            "Microsoft ML) footprints cached by scripts/fetch_overture_buildings.py"},
        "barangays": sorted(stats.values(), key=lambda s: s["name"]),
    }
    with _lock:
        _coverage_cache = out
    return out
