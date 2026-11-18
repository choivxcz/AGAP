"""Road network graph with flood hazard on every segment, and safest-route search.

The graph is built from the cached OpenStreetMap roads (data/osm/roads.geojson). Each OSM way is cut into pieces of
at most SEGMENT_M metres so that the flood hazard of a piece describes that piece and not a whole block. Every piece
stores:

  * its length, road class and whether it is a bridge,
  * the Project NOAH flood hazard class it touches for the 5-, 25- and 100-year rain (0 None .. 3 High),
  * the MGB flood susceptibility class it touches (0 NotMapped .. 3 VeryHigh).

"Touches" (line intersects polygon) is deliberately conservative: a piece that grazes a deep-water zone counts as
deep water. The table is computed once and cached in data/ops_cache (rebuilt automatically when a source changes).

Routing is a Dijkstra search (scipy) where the cost of a piece is its length times a safety multiplier that depends
on the vehicle, the water depth class for the active rain scenario, MGB susceptibility and live incident reports
(blocked roads, reported flood depth). Water that the vehicle cannot cross gets a very large but finite cost, so a
route is always returned and the impassable stretches are reported (typically the last stretch to a flooded house,
which needs a rubber boat or wading team).
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import threading
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import connected_components, dijkstra
from scipy.spatial import cKDTree
from shapely import STRtree, linestrings, make_valid
from shapely.geometry import shape

from app.config import OSM_DATA_DIR, PROJECT_ROOT
from app.mgb import CODES as MGB_CODES, MGB_PATH, ORDER as MGB_ORDER
from app.noah import LEVEL as NOAH_LEVEL, NOAH_DIR, RETURN_PERIODS

logger = logging.getLogger("flood-risk-map.roads")

ROADS_PATH = OSM_DATA_DIR / "roads.geojson"
CACHE_DIR = PROJECT_ROOT / "data" / "ops_cache"
CACHE_VERSION = "roads-v1"
SEGMENT_M = 40.0

# Local metric projection (equirectangular around Santa Cruz); errors < 0.1% over the study area.
LAT0 = 14.27
M_PER_DEG_LAT = 110_574.0
M_PER_DEG_LON = 111_320.0 * math.cos(math.radians(LAT0))

ROAD_KINDS = ["primary", "primary_link", "secondary", "secondary_link", "tertiary", "unclassified", "residential",
              "service", "track", "pedestrian", "footway", "path", "steps", "cycleway"]
KIND_INDEX = {k: i for i, k in enumerate(ROAD_KINDS)}
FOOT_ONLY = {"pedestrian", "footway", "path", "steps", "cycleway"}
MAJOR = {"primary", "primary_link", "secondary", "secondary_link", "tertiary"}

# Soft costs: high enough that any dry detour of reasonable length wins, finite so a route always exists.
IMPASSABLE = 1_000.0
BLOCKED = 20_000.0

HAZARD_NAMES = ["None", "Low", "Medium", "High"]
DEPTH_TEXT = {0: "dry (outside NOAH flood zones)", 1: "Low (< 0.5 m)", 2: "Medium (0.5-1.5 m)", 3: "High (> 1.5 m)"}


@dataclass(frozen=True)
class Vehicle:
    key: str
    label: str
    multipliers: tuple[float, float, float, float]  # cost per metre for NOAH class 0..3
    speed_kmh: tuple[float, float, float, float]    # planning speed per class (None/Low/Medium/High)
    foot_ways: bool                                 # may use footways/paths
    note: str


VEHICLES = {
    "standard": Vehicle("standard", "Ambulance / standard vehicle", (1.0, 3.0, IMPASSABLE, IMPASSABLE),
                        (30, 10, 0, 0), False, "Avoids any flooded road where possible; cannot cross 0.5 m or deeper."),
    "high_clearance": Vehicle("high_clearance", "High-clearance rescue truck", (1.0, 1.4, 6.0, IMPASSABLE),
                              (25, 15, 6, 0), False, "Can cross up to about 1.5 m with care; avoids deep water."),
    "foot": Vehicle("foot", "Rescue team on foot", (1.0, 2.0, 12.0, IMPASSABLE),
                    (4, 2.5, 1, 0), True, "May use footpaths; wading over 0.5 m is slow and risky."),
}


def to_xy(lon, lat):
    return (np.asarray(lon) - 121.4) * M_PER_DEG_LON, (np.asarray(lat) - LAT0) * M_PER_DEG_LAT


def to_lonlat(x, y):
    return np.asarray(x) / M_PER_DEG_LON + 121.4, np.asarray(y) / M_PER_DEG_LAT + LAT0


def _source_key() -> str:
    parts = [CACHE_VERSION, str(SEGMENT_M)]
    for path in [ROADS_PATH, MGB_PATH, *[NOAH_DIR / f"santa_cruz_flood_hazard_{rp}.geojson" for rp in RETURN_PERIODS]]:
        st = path.stat()
        parts.append(f"{path.name}:{st.st_size}:{int(st.st_mtime)}")
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


def _polygons(features, level_of) -> tuple[list, np.ndarray]:
    """Explode (multi)polygons into single polygons so an STRtree can index them."""
    geoms, levels = [], []
    for feature in features:
        geom = make_valid(shape(feature["geometry"]))
        level = level_of(feature["properties"])
        parts = getattr(geom, "geoms", [geom])
        for part in parts:
            if part.geom_type == "Polygon" and not part.is_empty:
                geoms.append(part)
                levels.append(level)
            elif hasattr(part, "geoms"):
                for sub in part.geoms:
                    if sub.geom_type == "Polygon" and not sub.is_empty:
                        geoms.append(sub)
                        levels.append(level)
    return geoms, np.asarray(levels, dtype=np.int8)


def _max_level(lines, geoms, levels) -> np.ndarray:
    """Highest polygon level each line touches (0 when it touches none)."""
    out = np.zeros(len(lines), dtype=np.int8)
    if not geoms:
        return out
    # Index the many small lines and query with the few large polygons: each polygon is then prepared once.
    tree = STRtree(lines)
    poly_idx, line_idx = tree.query(geoms, predicate="intersects")
    np.maximum.at(out, line_idx, levels[poly_idx])
    return out


def _build() -> dict[str, np.ndarray]:
    data = json.loads(ROADS_PATH.read_text(encoding="utf-8"))
    node_ids: dict[tuple[int, int], int] = {}
    node_lon: list[float] = []
    node_lat: list[float] = []

    def node(lon: float, lat: float) -> int:
        key = (round(lon * 1e7), round(lat * 1e7))
        idx = node_ids.get(key)
        if idx is None:
            idx = node_ids[key] = len(node_lon)
            node_lon.append(lon)
            node_lat.append(lat)
        return idx

    names: list[str] = [""]
    name_index = {"": 0}
    u, v, kind, bridge, name = [], [], [], [], []
    for feature in data["features"]:
        props = feature["properties"]
        k = KIND_INDEX.get(props.get("kind"), KIND_INDEX["unclassified"])
        b = bool(props.get("bridge"))
        nm = props.get("name") or ""
        n = name_index.setdefault(nm, len(names))
        if n == len(names):
            names.append(nm)
        coords = feature["geometry"]["coordinates"]
        for (lon1, lat1), (lon2, lat2) in zip(coords[:-1], coords[1:]):
            x1, y1 = to_xy(lon1, lat1)
            x2, y2 = to_xy(lon2, lat2)
            pieces = max(1, math.ceil(math.hypot(x2 - x1, y2 - y1) / SEGMENT_M))
            prev = node(lon1, lat1)
            for i in range(1, pieces + 1):
                t = i / pieces
                cur = node(lon2, lat2) if i == pieces else node(lon1 + (lon2 - lon1) * t, lat1 + (lat2 - lat1) * t)
                if cur != prev:
                    u.append(prev); v.append(cur); kind.append(k); bridge.append(b); name.append(n)
                prev = cur

    lon = np.asarray(node_lon); lat = np.asarray(node_lat)
    u = np.asarray(u, dtype=np.int32); v = np.asarray(v, dtype=np.int32)
    x, y = to_xy(lon, lat)
    length = np.hypot(x[u] - x[v], y[u] - y[v]).astype(np.float32)
    lines = linestrings(np.stack([np.stack([lon[u], lat[u]], 1), np.stack([lon[v], lat[v]], 1)], 1))

    arrays = {"lon": lon, "lat": lat, "u": u, "v": v, "length": length,
              "kind": np.asarray(kind, dtype=np.int8), "bridge": np.asarray(bridge, dtype=bool),
              "name": np.asarray(name, dtype=np.int32), "names": np.asarray(names, dtype=object)}
    for rp in RETURN_PERIODS:
        noah = json.loads((NOAH_DIR / f"santa_cruz_flood_hazard_{rp}.geojson").read_text(encoding="utf-8"))
        geoms, levels = _polygons(noah["features"], lambda p: NOAH_LEVEL[p["HazardClass"]])
        arrays[f"noah_{rp}"] = _max_level(lines, geoms, levels)
    mgb = json.loads(MGB_PATH.read_text(encoding="utf-8"))
    geoms, levels = _polygons(mgb["features"], lambda p: MGB_ORDER.index(MGB_CODES[p["FloodSusc"]]))
    arrays["mgb"] = _max_level(lines, geoms, levels)
    return arrays


class RoadNetwork:
    def __init__(self, a: dict[str, np.ndarray]):
        self.lon, self.lat = a["lon"], a["lat"]
        self.u, self.v = a["u"], a["v"]
        self.length = a["length"].astype(np.float64)
        self.kind, self.bridge, self.name = a["kind"], a["bridge"], a["name"]
        self.names = [str(s) for s in a["names"]]
        self.noah = {rp: a[f"noah_{rp}"] for rp in RETURN_PERIODS}
        self.mgb = a["mgb"]
        self.n_nodes = len(self.lon)
        x, y = to_xy(self.lon, self.lat)
        self.node_xy = np.stack([x, y], 1)
        self.mid_xy = (self.node_xy[self.u] + self.node_xy[self.v]) / 2
        self.mid_tree = cKDTree(self.mid_xy)
        foot_only = np.isin(self.kind, [KIND_INDEX[k] for k in FOOT_ONLY])
        self.allowed = {"vehicle": ~foot_only, "foot": np.ones(len(self.u), dtype=bool)}
        self.major = np.isin(self.kind, [KIND_INDEX[k] for k in MAJOR])
        self.track = self.kind == KIND_INDEX["track"]
        # Snap only to the largest connected part of each network, so a route can always be found.
        self._snap = {}
        for mode, ok in self.allowed.items():
            g = csr_matrix((np.ones(ok.sum()), (self.u[ok], self.v[ok])), shape=(self.n_nodes, self.n_nodes))
            _, labels = connected_components(g, directed=False)
            touched = np.zeros(self.n_nodes, dtype=bool)
            touched[self.u[ok]] = True
            touched[self.v[ok]] = True
            main = np.bincount(labels[touched]).argmax()
            nodes = np.flatnonzero(touched & (labels == main))
            self._snap[mode] = (nodes, cKDTree(self.node_xy[nodes]))

    # ------------------------------------------------------------------ helpers
    def snap(self, lat: float, lon: float, mode: str) -> tuple[int, float]:
        nodes, tree = self._snap[mode]
        dist, i = tree.query(np.array(to_xy(lon, lat)))
        return int(nodes[i]), float(dist)

    def edges_near(self, lat: float, lon: float, radius_m: float) -> np.ndarray:
        return np.asarray(self.mid_tree.query_ball_point(np.array(to_xy(lon, lat)), radius_m), dtype=np.int64)

    def hazard(self, scenario_rp: str, flood_reports: list[dict]) -> np.ndarray:
        """NOAH water class per edge for the scenario, raised where residents/teams report deeper water.

        Bridges are raised above the water, so NOAH's river/flood polygons under them are not counted."""
        h = self.noah[scenario_rp].copy()
        h[self.bridge] = 0
        for rep in flood_reports:
            idx = self.edges_near(rep["latitude"], rep["longitude"], rep.get("radius_m", 150))
            if len(idx):
                h[idx] = np.maximum(h[idx], rep["depth_class"])
        return h

    # ------------------------------------------------------------------ routing
    def weights(self, vehicle: Vehicle, hazard: np.ndarray, rain_factor: float, blocked: np.ndarray,
                safest: bool = True) -> np.ndarray:
        ok = self.allowed["foot" if vehicle.foot_ways else "vehicle"]
        if not safest:
            w = self.length.copy()
        else:
            mult = np.asarray(vehicle.multipliers)[hazard]
            # MGB susceptibility: soft penalty growing with the rain (MGB has no rain scenarios).
            mult = mult + np.asarray([0.0, 0.3, 0.6, 1.0])[self.mgb] * rain_factor
            mult = np.where(self.major, mult * 0.9, mult)       # wider, usually higher, better known roads
            mult = np.where(self.track, mult * 1.3, mult)       # unpaved tracks are slow and slippery when wet
            w = self.length * mult
        w = np.where(blocked, self.length * BLOCKED, w)
        return np.where(ok, w, np.inf)

    def _graph(self, w: np.ndarray) -> tuple[csr_matrix, tuple[np.ndarray, np.ndarray]]:
        ok = np.isfinite(w)
        idx = np.flatnonzero(ok)
        rows = np.concatenate([self.u[idx], self.v[idx]])
        cols = np.concatenate([self.v[idx], self.u[idx]])
        ww = np.concatenate([w[idx], w[idx]])
        eid = np.concatenate([idx, idx])
        # Keep only the cheapest edge between each pair of nodes (csr_matrix would otherwise sum duplicates).
        key = rows.astype(np.int64) * self.n_nodes + cols
        order = np.lexsort((ww, key))
        key, first = key[order], np.ones(len(order), dtype=bool)
        first[1:] = key[1:] != key[:-1]
        order = order[first]
        g = csr_matrix((ww[order], (rows[order], cols[order])), shape=(self.n_nodes, self.n_nodes))
        # (pair key -> edge id) lookup, returned rather than stored: requests run concurrently on threads.
        return g, (key[first], eid[order])

    def _edge(self, lookup, a: int, b: int) -> int:
        keys, eids = lookup
        return int(eids[np.searchsorted(keys, a * self.n_nodes + b)])

    def search(self, w: np.ndarray, source: int):
        """Dijkstra from one node: (cost to every node, predecessors, edge lookup for describe())."""
        g, lookup = self._graph(w)
        dist, pred = dijkstra(g, directed=True, indices=source, return_predecessors=True)
        return dist, pred, lookup

    def path(self, pred: np.ndarray, source: int, target: int) -> list[int]:
        nodes = [target]
        while nodes[-1] != source:
            p = pred[nodes[-1]]
            if p < 0:
                return []
            nodes.append(int(p))
        return nodes[::-1]

    def describe(self, nodes: list[int], vehicle: Vehicle, hazard: np.ndarray, blocked: np.ndarray, lookup) -> dict:
        """Geometry and safety statistics of a node path."""
        edges = [self._edge(lookup, a, b) for a, b in zip(nodes[:-1], nodes[1:])]
        length = float(sum(self.length[e] for e in edges))
        by_class = [0.0, 0.0, 0.0, 0.0]
        minutes = 0.0
        impassable, blocked_m, via = [], 0.0, []
        segments = []   # [[lon,lat],...] runs with the same hazard class, for drawing
        for (a, b), e in zip(zip(nodes[:-1], nodes[1:]), edges):
            h = int(hazard[e])
            seg_len = float(self.length[e])
            by_class[h] += seg_len
            speed = vehicle.speed_kmh[h]
            cannot = vehicle.multipliers[h] >= IMPASSABLE
            if blocked[e]:
                blocked_m += seg_len
                cannot = True
            minutes += seg_len / 1000 / (speed if speed > 0 else 2.0) * 60   # 2 km/h ~ boat/wading
            point_a = [round(float(self.lon[a]), 6), round(float(self.lat[a]), 6)]
            point_b = [round(float(self.lon[b]), 6), round(float(self.lat[b]), 6)]
            status = "blocked" if blocked[e] else ("impassable" if cannot else HAZARD_NAMES[h])
            if segments and segments[-1]["status"] == status:
                segments[-1]["coordinates"].append(point_b)
            else:
                segments.append({"status": status, "coordinates": [point_a, point_b]})
            if cannot:
                impassable.append(e)
            nm = self.names[self.name[e]]
            if nm and (not via or via[-1]["name"] != nm):
                via.append({"name": nm, "length_m": 0.0})
            if nm and via:
                via[-1]["length_m"] += seg_len
        # Staging point: where the vehicle has to stop before the impassable stretch at the end of the route.
        staging = None
        if edges and impassable:
            bad = set(impassable)
            last_ok = 0
            for i, e in enumerate(edges):
                if e not in bad:
                    last_ok = i + 1
            first_bad = next(i for i, e in enumerate(edges) if e in bad)
            node = nodes[min(first_bad, len(nodes) - 1)]
            staging = {"latitude": float(self.lat[node]), "longitude": float(self.lon[node]),
                       "remaining_m": round(float(sum(self.length[e] for e in edges[first_bad:])), 0),
                       "continues_after": last_ok > first_bad}
        return {"length_m": round(length), "eta_min": round(minutes, 1),
                "length_by_depth_m": {HAZARD_NAMES[i]: round(by_class[i]) for i in range(4)},
                "flooded_m": round(sum(by_class[1:])), "impassable_m": round(sum(float(self.length[e]) for e in impassable)),
                "blocked_m": round(blocked_m), "segments": segments,
                "via": [{"name": v["name"], "length_m": round(v["length_m"])} for v in via if v["length_m"] >= 60][:12],
                "staging_point": staging}


_lock = threading.Lock()
_network: RoadNetwork | None = None


def network() -> RoadNetwork:
    """The road network (built on first use, then cached on disk and in memory)."""
    global _network
    with _lock:
        if _network is None:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            key = _source_key()
            path = CACHE_DIR / f"road_network_{key}.npz"
            if path.exists():
                arrays = dict(np.load(path, allow_pickle=True))
            else:
                logger.info("Building road network flood-hazard table (one time, about a minute)...")
                arrays = _build()
                np.savez_compressed(path, **arrays)
                for old in CACHE_DIR.glob("road_network_*.npz"):
                    if old != path:
                        old.unlink(missing_ok=True)
            _network = RoadNetwork(arrays)
            logger.info("Road network ready: %d nodes, %d segments", _network.n_nodes, len(_network.u))
        return _network


def ready() -> bool:
    return _network is not None


@lru_cache(maxsize=4)
def hazard_geojson(rp: str, tolerance: float = 0.00008) -> bytes:
    """Simplified NOAH polygons (about 9 m tolerance) for display on the operations map."""
    from shapely import set_precision, simplify
    from shapely.geometry import mapping
    data = json.loads((NOAH_DIR / f"santa_cruz_flood_hazard_{rp}.geojson").read_text(encoding="utf-8"))
    features = []
    for f in data["features"]:
        geom = simplify(make_valid(shape(f["geometry"])), tolerance, preserve_topology=True)
        geom = set_precision(geom, 1e-6)
        features.append({"type": "Feature", "properties": {"HazardClass": f["properties"]["HazardClass"]},
                         "geometry": mapping(geom)})
    return json.dumps({"type": "FeatureCollection", "features": features}, separators=(",", ":")).encode()
