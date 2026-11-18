"""Fetch OSM map features (buildings, roads, waterways, POIs, places, addresses).

Downloads OpenStreetMap data for the Santa Cruz, Laguna study area using the
standard OSM API 0.6 ``map`` endpoint in small tiles (same approach as
``Sta_Cruz_Laguna_GIS_Download/download_and_prepare.py``), then writes local
GeoJSON files in EPSG:4326 (GeoJSON [lon, lat] order):

    data/osm/buildings.geojson   polygons (osm_id, name, kind, levels, height_m)
    data/osm/roads.geojson       lines   (osm_id, kind, name, bridge)
    data/osm/waterways.geojson   lines   (osm_id, kind, name)
    data/osm/pois.geojson        points  (osm_id, kind, name)  named POIs only
    data/osm/place.geojson       points  (osm_id, kind, name)  localities/sitios
    data/osm/addresses.geojson   points  (street, housenumber) both required

These are cached once so the web map has locally available vector layers that
work without internet. Re-running skips tiles already present in the cache.
No attributes are invented: name/kind/levels/height/bridge are carried through
only when the OSM source actually contains them.

Usage:
    python scripts/fetch_osm_map_features.py [--force]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from xml.etree import ElementTree

import requests
from shapely.geometry import LineString, Point, Polygon

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OSM_DIR = PROJECT_ROOT / "data" / "osm"
TILE_CACHE = OSM_DIR / "tiles"
OSM_MAP_URL = "https://api.openstreetmap.org/api/0.6/map"

# Study-area bbox (west, south, east, north) covering all 26 barangays with a
# small buffer. Matches the extent of the downloaded rasters.
BBOX = (121.27, 14.12, 121.55, 14.42)

BUILDING_TAGS = {"building", "building:part"}
# Every mapped route a user may walk or drive, including minor residential
# streets, service alleys, tracks and foot paths. `construction` is excluded
# (not yet a real road).
ROAD_HIGHWAYS = {
    "motorway", "trunk", "primary", "secondary", "tertiary", "unclassified",
    "residential", "service", "living_street", "pedestrian", "track",
    "motorway_link", "trunk_link", "primary_link", "secondary_link", "tertiary_link",
    "footway", "path", "steps", "cycleway",
}
WATERWAYS = {"river", "stream", "canal", "drain", "ditch"}

# Landmarks / public facilities: named nodes and closed ways only. Tag lookup
# is in priority order (first match wins for the `kind` property).
POI_TAG_PRIORITY = ("healthcare", "amenity", "tourism", "leisure", "shop", "office")
LOCALITY_PLACES = {"village", "hamlet", "town", "neighbourhood", "quarter",
                   "suburb", "city_block", "locality"}
USER_AGENT = "StaCruzFloodRiskMap/1.0 (household GIS feature extraction)"


def fetch_tile(west: float, south: float, east: float, north: float, force: bool,
               tile_cache: Path = TILE_CACHE) -> Path:
    tile_cache.mkdir(parents=True, exist_ok=True)
    name = f"map_{west:.4f}_{south:.4f}_{east:.4f}_{north:.4f}.osm"
    path = tile_cache / name
    if path.exists() and path.stat().st_size > 0 and not force:
        return path
    for attempt in range(3):
        try:
            response = requests.get(
                OSM_MAP_URL, params={"bbox": f"{west},{south},{east},{north}"},
                timeout=180, headers={"User-Agent": USER_AGENT})
            if response.status_code == 504 and attempt < 2:
                time.sleep(10)
                continue
            response.raise_for_status()
            path.write_bytes(response.content)
            return path
        except requests.RequestException as exc:
            if attempt == 2:
                raise RuntimeError(f"OSM request failed for bbox {west},{south},{east},{north}: {exc}") from exc
            time.sleep(15)
    raise RuntimeError("unreachable")


def parse_tile(path: Path, node_coords: dict[str, tuple[float, float]],
               ways: dict[str, tuple[list[str], dict[str, str]]],
               node_tags: dict[str, dict[str, str]]) -> None:
    root = ElementTree.fromstring(path.read_bytes())
    for node in root.findall("node"):
        node_coords[node.attrib["id"]] = (float(node.attrib["lon"]), float(node.attrib["lat"]))
        tags = {tag.attrib["k"]: tag.attrib["v"] for tag in node.findall("tag")}
        if tags:
            node_tags[node.attrib["id"]] = tags
    for way in root.findall("way"):
        tags = {tag.attrib["k"]: tag.attrib["v"] for tag in way.findall("tag")}
        ways[way.attrib["id"]] = ([nd.attrib["ref"] for nd in way.findall("nd")], tags)


def line_for(refs: list[str], node_coords: dict[str, tuple[float, float]]) -> LineString | None:
    coords = [node_coords[r] for r in refs if r in node_coords]
    return LineString(coords) if len(coords) >= 2 else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="re-download cached tiles")
    parser.add_argument("--output-dir", type=Path, default=OSM_DIR,
                        help="write GeoJSON and tile cache here; defaults to the existing local source")
    args = parser.parse_args()

    west, south, east, north = BBOX
    step = 0.05
    node_coords: dict[str, tuple[float, float]] = {}
    node_tags: dict[str, dict[str, str]] = {}
    ways: dict[str, tuple[list[str], dict[str, str]]] = {}
    n_tiles = 0
    for y in [round(south + i * step, 4) for i in range(int((north - south) / step) + 1) if south + i * step < north]:
        for x in [round(west + i * step, 4) for i in range(int((east - west) / step) + 1) if west + i * step < east]:
            tile_east, tile_north = min(x + step, east), min(y + step, north)
            path = fetch_tile(x, y, tile_east, tile_north, args.force,
                              args.output_dir / "tiles")
            parse_tile(path, node_coords, ways, node_tags)
            n_tiles += 1
            time.sleep(0.5)
    print(f"Processed {n_tiles} tiles, {len(ways)} ways, {len(node_coords)} nodes.")

    buildings, roads, waterways = [], [], []
    for way_id, (refs, tags) in ways.items():
        line = line_for(refs, node_coords)
        if line is None:
            continue
        if any(t in tags for t in BUILDING_TAGS):
            # Closed building ways become polygons; open ones are skipped
            # (building:part outlines of relations are not reconstructed here).
            if refs and refs[0] == refs[-1]:
                buildings.append({"type": "Feature",
                                  "properties": building_properties(way_id, tags),
                                  "geometry": json.loads(json.dumps({
                                      "type": "Polygon",
                                      "coordinates": [list(line.coords)]}))})
        elif tags.get("highway") in ROAD_HIGHWAYS:
            roads.append({"type": "Feature",
                          "properties": {"osm_id": way_id, "kind": tags["highway"],
                                         "name": tags.get("name"),
                                         "bridge": tags.get("bridge") not in (None, "no")},
                          "geometry": json.loads(json.dumps({"type": "LineString",
                                                             "coordinates": list(line.coords)}))})
        elif tags.get("waterway") in WATERWAYS:
            waterways.append({"type": "Feature",
                              "properties": {"osm_id": way_id, "kind": tags["waterway"],
                                             "name": tags.get("name")},
                              "geometry": json.loads(json.dumps({"type": "LineString",
                                                                 "coordinates": list(line.coords)}))})

    pois, places, addresses = [], [], []
    for node_id, tags in node_tags.items():
        if node_id not in node_coords:
            continue
        lon, lat = node_coords[node_id]
        place = tags.get("place")
        if place in LOCALITY_PLACES and tags.get("name"):
            places.append(point_feature(f"node-{node_id}", place, tags["name"], lon, lat))
            continue
        if tags.get("addr:housenumber") and tags.get("addr:street"):
            addresses.append({"type": "Feature",
                              "properties": {"street": tags["addr:street"],
                                             "housenumber": tags["addr:housenumber"]},
                              "geometry": {"type": "Point", "coordinates": [lon, lat]}})
            continue
        kind = next((t for t in POI_TAG_PRIORITY if tags.get(t)), None)
        if kind and tags.get("name"):
            pois.append(point_feature(f"node-{node_id}", tags[kind], tags["name"], lon, lat))
    for way_id, (refs, tags) in ways.items():
        # Named facility areas (school grounds, church lots, parks, ...) become
        # a landmark point at the polygon centroid.
        kind = next((t for t in POI_TAG_PRIORITY if tags.get(t)), None)
        if not (kind and tags.get("name")) or len(refs) < 4 or refs[0] != refs[-1]:
            continue
        try:
            poly = Polygon([node_coords[r] for r in refs if r in node_coords])
            if not poly.is_valid or poly.area <= 0:
                continue
            c = poly.centroid
            pois.append(point_feature(f"way-{way_id}", tags[kind], tags["name"], c.x, c.y))
        except (KeyError, ValueError):
            continue

    args.output_dir.mkdir(parents=True, exist_ok=True)
    layers = [("buildings", buildings), ("roads", roads), ("waterways", waterways),
              ("pois", pois), ("place", places), ("addresses", addresses)]
    for name, feats in layers:
        fc = {"type": "FeatureCollection", "features": feats}
        out = args.output_dir / f"{name}.geojson"
        out.write_text(json.dumps(fc, ensure_ascii=False), encoding="utf-8")
        print(f"{out}: {len(feats)} features ({out.stat().st_size / 1e6:.1f} MB)")
    return 0


def building_properties(way_id: str, tags: dict[str, str]) -> dict:
    """Carry through only attributes present in OSM; never invent values."""
    return {"osm_id": way_id,
            "name": tags.get("name"),
            "kind": tags.get("building") or tags.get("building:part") or "yes",
            "levels": int(tags["building:levels"]) if _is_int(tags.get("building:levels")) else None,
            "height_m": float(tags["height"].rstrip("m ")) if _is_float(tags.get("height")) else None}


def _is_int(v) -> bool:
    if v is None:
        return False
    try:
        int(v)
        return True
    except ValueError:
        return False


def _is_float(v) -> bool:
    if v is None:
        return False
    try:
        float(str(v).rstrip("m "))
        return True
    except ValueError:
        return False


def point_feature(osm_id: str, kind: str, name: str, lon: float, lat: float) -> dict:
    return {"type": "Feature",
            "properties": {"osm_id": osm_id, "kind": kind, "name": name},
            "geometry": {"type": "Point", "coordinates": [lon, lat]}}


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print("The map still works with existing local layers; re-run later to retry.", file=sys.stderr)
        raise SystemExit(1)
