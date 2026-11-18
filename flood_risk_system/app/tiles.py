"""Web-map tiles rendered on demand from the municipal GeoTIFF rasters.

Two tile families are served by app.main under /api/tiles/:

1. Colour overlays (elevation, slope, landcover, wateroccurrence, distwater):
   the requested web-mercator tile bbox is reprojected to the raster's own CRS
   (EPSG:32651), read windowed, resampled to 256x256 and colourised with a
   fixed legend. NoData becomes fully transparent.

2. ``dem``: the same elevation raster re-encoded as Terrarium PNG
   (height_m = R*256 + G + B/256 - 32768) for MapLibre GL terrain.
   Raster NoData (0 in this dataset, i.e. "no valid DEM pixel") is encoded as
   0 m so tile edges do not crater; true 0 m elevations do not occur in this
   study area (observed minimum 1.0 m).

This is display-only tiling. The numerical GIS extraction pipeline
(app/gis/extraction.py) keeps reading the original rasters and is untouched.
Tiles are rendered from static files, so a small LRU cache is safe.
"""
from __future__ import annotations

import io
import threading
from collections import OrderedDict
from pathlib import Path

import numpy as np
import rasterio
from pyproj import CRS, Transformer
from rasterio.enums import Resampling
from rasterio.transform import from_bounds as transform_from_bounds

from app.config import GIS_OUTPUT_DIR

TILE_SIZE = 256
MUNICIPAL_FILES = {
    "elevation": "elevation_m.tif",
    "slope": "slope_degrees.tif",
    "landcover": "land_cover_2021.tif",
    "wateroccurrence": "historical_surface_water_occurrence_pct_1984_2024.tif",
    "distwater": "distance_to_osm_waterway_m.tif",
    "dem": "elevation_m.tif",
}

# Legend definitions shared with the client via /api/tiles/meta. Colour stops
# are (value, "#rrggbb") in the layer's unit; ramps interpolate linearly.
LEGENDS = {
    "elevation": {"title": "Elevation", "unit": "m",
                  "stops": [(0, "#37577e"), (25, "#4a7aa5"), (75, "#7fb08a"),
                            (150, "#d9c978"), (250, "#b3763b"), (400, "#8c4a23")]},
    "slope": {"title": "Slope", "unit": "°",
              "stops": [(0, "#e8f2dc"), (5, "#d3e0a5"), (10, "#e8cf6e"),
                        (20, "#d98d4a"), (30, "#b34a33")]},
    "landcover": {"title": "Land cover (ESA WorldCover 2021)", "unit": "class",
                  "classes": [(10, "Tree", "#006400"), (20, "Shrubland", "#ffbb22"),
                              (30, "Grassland", "#ffff4c"), (40, "Cropland", "#f096ff"),
                              (50, "Built-up", "#fa0000"), (60, "Bare/sparse", "#b4b4b4"),
                              (70, "Snow/ice", "#fa78fa"), (80, "Water", "#0064c8"),
                              (90, "Wetland", "#0096a0"), (95, "Mangrove", "#00cf75"),
                              (100, "Moss/lichen", "#fae6a0")]},
    "wateroccurrence": {"title": "Surface-water occurrence 1984–2024 (JRC)", "unit": "%",
                        "stops": [(0, "#f2f6fa"), (10, "#cfe3f2"), (25, "#8fbfdd"),
                                  (50, "#4a90c4"), (75, "#2b6ca3"), (100, "#173f66")]},
    "distwater": {"title": "Distance to mapped waterway", "unit": "m",
                  "stops": [(0, "#b34a33"), (50, "#d98d4a"), (100, "#e8cf6e"),
                            (250, "#d3e0a5"), (500, "#9cc0e8")]},
}

_tile_cache: "OrderedDict[tuple, bytes]" = OrderedDict()
_tile_cache_max = 600
_cache_lock = threading.Lock()


def _cache_get(key):
    with _cache_lock:
        if key in _tile_cache:
            _tile_cache.move_to_end(key)
            return _tile_cache[key]
    return None


def _cache_put(key, value: bytes) -> None:
    with _cache_lock:
        _tile_cache[key] = value
        _tile_cache.move_to_end(key)
        while len(_tile_cache) > _tile_cache_max:
            _tile_cache.popitem(last=False)


def _hex_rgb(s: str) -> tuple[int, int, int]:
    return tuple(int(s[i:i + 2], 16) for i in (1, 3, 5))  # type: ignore[return-value]


def _interp_ramp(values: np.ndarray, stops: list[tuple[float, str]]) -> np.ndarray:
    """values (2-D float) -> uint8 RGB (3,H,W)."""
    xs = np.array([s[0] for s in stops], dtype="float64")
    cols = np.array([_hex_rgb(s[1]) for s in stops], dtype="float64")
    out = np.empty((3,) + values.shape, dtype="uint8")
    for c in range(3):
        out[c] = np.clip(np.interp(values, xs, cols[:, c]), 0, 255).astype("uint8")
    return out


def raster_meta() -> dict:
    """Legend/range metadata for the client (legends + diagnostics)."""
    out = {}
    for kind, legend in LEGENDS.items():
        info = _raster_info(kind)
        out[kind] = {**legend, "available": info.get("exists", False),
                     "min": info.get("min"), "max": info.get("max"),
                     "file": Path(info["path"]).name if info.get("exists") else None}
    dem = _raster_info("dem")
    out["dem"] = {"available": dem.get("exists", False),
                  "title": "Terrain (DEM)", "unit": "m", "encoding": "terrarium",
                  "min": dem.get("min"), "max": dem.get("max"),
                  "note": "display-only tiling of elevation_m.tif; extraction reads the original raster"}
    return out


def _raster_info(kind: str) -> dict:
    """Static raster facts (bounds, nodata, valid range) per layer."""
    path = GIS_OUTPUT_DIR / MUNICIPAL_FILES[kind]
    if not path.exists():
        return {"exists": False}
    with rasterio.open(path) as src:
        data = src.read(1)
        nodata = src.nodata
        mask = np.isfinite(data)
        if nodata is not None:
            mask &= data != nodata
        lo = float(data[mask].min()) if mask.any() else 0.0
        hi = float(data[mask].max()) if mask.any() else 1.0
        return {"exists": True, "path": str(path), "bounds": tuple(float(v) for v in src.bounds),
                "crs": str(src.crs), "nodata": None if nodata is None else float(nodata),
                "min": lo, "max": hi, "shape": (int(src.height), int(src.width))}


def _tile_bounds(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    """Web-mercator tile -> (west, south, east, north) in EPSG:4326 degrees."""
    n = 2.0 ** z

    def lat_of(yv: float) -> float:
        return float(np.degrees(np.arctan(np.sinh(np.pi * (1 - 2 * yv / n)))))

    west = x / n * 360.0 - 180.0
    east = (x + 1) / n * 360.0 - 180.0
    return west, lat_of(y + 1), east, lat_of(y)


def _pixel_rgba(raster: np.ndarray, valid: np.ndarray, kind: str) -> np.ndarray:
    h, w = raster.shape
    rgba = np.zeros((4, h, w), dtype="uint8")
    if kind == "landcover":
        classes = {v: _hex_rgb(s) for v, _n, s in LEGENDS["landcover"]["classes"]}
        for v, col in classes.items():
            m = valid & (np.rint(raster) == v)
            for c in range(3):
                rgba[c][m] = col[c]
    else:
        rgba[:3] = _interp_ramp(raster, LEGENDS[kind]["stops"])
    rgba[3] = np.where(valid, 235, 0).astype("uint8")   # slight transparency
    return rgba


def _terrarium(elev: np.ndarray, valid: np.ndarray) -> np.ndarray:
    h, w = elev.shape
    rgba = np.zeros((4, h, w), dtype="uint8")
    vals = np.where(valid, elev, 0.0) + 32768.0
    r = np.floor(vals / 256.0)
    g = np.floor(vals - r * 256.0)
    b = np.floor((vals - np.floor(vals)) * 256.0)
    rgba[0], rgba[1], rgba[2] = r.astype("uint8"), g.astype("uint8"), b.astype("uint8")
    rgba[3] = 255
    return rgba


def _write_png(rgba: np.ndarray) -> bytes:
    h, w = rgba.shape[1], rgba.shape[2]
    buf = io.BytesIO()
    with rasterio.open(
        buf, "w", driver="PNG", width=w, height=h, count=4, dtype="uint8",
        transform=transform_from_bounds(0, 0, 1, 1, w, h), crs=CRS.from_epsg(3857),
    ) as dst:
        dst.write(rgba)
    return buf.getvalue()


def _transparent_png() -> bytes:
    return _write_png(np.zeros((4, 1, 1), dtype="uint8"))


def render_tile(kind: str, z: int, x: int, y: int) -> bytes:
    """PNG bytes for a tile (transparent when outside raster coverage)."""
    key = (kind, z, x, y)
    hit = _cache_get(key)
    if hit is not None:
        return hit

    info = _raster_info(kind)
    if not info.get("exists"):
        png = _transparent_png()
        _cache_put(key, png)
        return png

    west4326, south, east4326, north = _tile_bounds(z, x, y)
    b = info["bounds"]
    transformer = Transformer.from_crs(4326, info["crs"], always_xy=True)
    xs, ys = transformer.transform(
        (west4326, east4326, west4326, east4326), (north, north, south, south))
    rx0, rx1, ry0, ry1 = min(xs), max(xs), min(ys), max(ys)
    if rx1 < b[0] or rx0 > b[2] or ry1 < b[1] or ry0 > b[3]:
        png = _transparent_png()
    else:
        with rasterio.open(info["path"]) as src:
            win = rasterio.windows.from_bounds(rx0, ry0, rx1, ry1, transform=src.transform)
            data = src.read(1, window=win, boundless=True,
                            fill_value=src.nodata if src.nodata is not None else 0,
                            out_shape=(TILE_SIZE, TILE_SIZE),
                            resampling=Resampling.bilinear).astype("float64")
            nodata = src.nodata
        valid = np.isfinite(data)
        if nodata is not None:
            valid &= data != nodata
        valid &= data > -1e30
        rgb = _terrarium(data, valid) if kind == "dem" else _pixel_rgba(data, valid, kind)
        png = _write_png(rgb)
    _cache_put(key, png)
    return png
