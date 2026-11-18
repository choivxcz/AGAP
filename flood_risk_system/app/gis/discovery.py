"""GIS data discovery: barangay folders, raster layers, boundary polygons.

Everything is discovered from disk at runtime; no barangay list is hardcoded.
"""
from __future__ import annotations

import json
import logging
import re
import colorsys
from dataclasses import dataclass, field
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
from shapely.geometry import Point, shape

from app.config import GIS_OUTPUT_DIR, RASTER_LAYERS

logger = logging.getLogger(__name__)


def slug(text: str) -> str:
    """Same slug rule the download pipeline uses for folder names."""
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


@dataclass
class LayerInfo:
    name: str                       # e.g. "elevation_m"
    municipal_path: Path | None
    nodata: float | None
    dtype: str
    crs: str
    resolution: tuple[float, float]
    bounds: tuple[float, float, float, float] | None


@dataclass
class BarangayInfo:
    name: str                       # e.g. "Barangay I (Pob.)"
    slug_name: str                  # folder name
    geometry: object | None = None  # shapely geometry in EPSG:4326, if available
    layers: dict[str, Path] = field(default_factory=dict)


@dataclass
class Coverage:
    root: Path
    barangays: list[BarangayInfo]
    municipal: dict[str, LayerInfo]
    municipality_geometry: object | None   # union of barangay polygons (EPSG:4326)
    crs_consistent: bool
    warnings: list[str]


_coverage_singleton: Coverage | None = None


def get_coverage(gis_root: Path | None = None) -> Coverage:
    """Process-wide Coverage singleton (discovery is not free: it opens rasters).

    Used by both the FastAPI app and the map-data module so the GIS output
    tree is scanned exactly once per server lifetime.
    """
    global _coverage_singleton
    if _coverage_singleton is None or gis_root is not None:
        _coverage_singleton = discover(gis_root)
    return _coverage_singleton


def discover(gis_root: Path | None = None) -> Coverage:
    """Scan the GIS output tree and report what actually exists."""
    root = Path(gis_root) if gis_root else GIS_OUTPUT_DIR
    warnings: list[str] = []
    if not root.exists():
        warnings.append(f"GIS output directory not found: {root}")

    barangay_path = root / "barangays.geojson"
    names: list[str] = []
    geoms: dict[str, object] = {}
    municipality = None
    if barangay_path.exists():
        try:
            gdf = gpd.read_file(barangay_path)
            if gdf.crs is None:
                warnings.append("barangays.geojson has no CRS defined; assuming EPSG:4326 is unsafe — polygons not used")
            else:
                gdf = gdf.to_crs("EPSG:4326")
                name_col = "BRGY_NAME" if "BRGY_NAME" in gdf.columns else gdf.columns[0]
                for row in gdf.itertuples():
                    names.append(str(getattr(row, name_col)))
                    geoms[str(getattr(row, name_col))] = row.geometry
                municipality = gdf.geometry.union_all()
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"Could not read barangays.geojson: {exc!r}")
    else:
        warnings.append("barangays.geojson missing — barangay identification unavailable")

    # Barangay folders: discovered from disk; matched to polygon names by slug.
    folder_root = root / "barangays"
    folder_names = sorted(p.name for p in folder_root.iterdir() if p.is_dir()) if folder_root.exists() else []
    by_slug = {slug(n): n for n in names}
    barangays: list[BarangayInfo] = []
    for folder in folder_names:
        info = BarangayInfo(name=by_slug.get(folder, folder), slug_name=folder,
                            geometry=geoms.get(by_slug.get(folder, folder)))
        for layer in RASTER_LAYERS:
            path = root / "barangays" / folder / f"{layer}.tif"
            if path.exists():
                info.layers[layer] = path
        missing = [l for l in RASTER_LAYERS if l not in info.layers]
        if missing:
            warnings.append(f"barangay '{folder}' missing layers: {', '.join(missing)}")
        barangays.append(info)
    for n in names:
        if slug(n) not in folder_names:
            warnings.append(f"barangay '{n}' has polygon but no raster folder")

    # Municipal rasters.
    municipal: dict[str, LayerInfo] = {}
    for layer in RASTER_LAYERS:
        path = root / f"{layer}.tif"
        if not path.exists():
            warnings.append(f"municipal raster missing: {layer}.tif")
            continue
        try:
            with rasterio.open(path) as src:
                municipal[layer] = LayerInfo(
                    name=layer, municipal_path=path, nodata=src.nodata,
                    dtype=src.dtypes[0], crs=str(src.crs),
                    resolution=(float(src.transform.a), float(-src.transform.e)),
                    bounds=tuple(float(v) for v in src.bounds))
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"municipal raster unreadable: {layer}.tif ({exc!r})")

    # Consistency: same CRS across municipal layers. Resolution may differ by
    # design (10 m land cover vs 30 m terrain); mismatched CRS is the real risk.
    crs_set = {info.crs for info in municipal.values()}
    res_set = {tuple(round(v, 6) for v in info.resolution) for info in municipal.values()}
    crs_consistent = len(crs_set) <= 1
    if len(crs_set) > 1:
        warnings.append(f"municipal layers use mixed CRS: {sorted(crs_set)}")
    if len(res_set) > 1:
        warnings.append(f"note: municipal layers use multiple resolutions (expected: 10 m land cover vs 30 m terrain): {sorted(res_set)}")

    return Coverage(root=root, barangays=barangays, municipal=municipal,
                    municipality_geometry=municipality, crs_consistent=crs_consistent,
                    warnings=warnings)


def barangay_for_point(cov: Coverage, lon: float, lat: float) -> BarangayInfo | None:
    """Point-in-polygon lookup in EPSG:4326. Returns None when outside or no polygons."""
    if cov.municipality_geometry is None:
        return None
    pt = Point(lon, lat)
    if not cov.municipality_geometry.covers(pt):
        return None
    for b in cov.barangays:
        if b.geometry is not None and b.geometry.covers(pt):
            return b
    return None


def coverage_geojson(cov: Coverage) -> dict:
    """Barangay polygons as GeoJSON (EPSG:4326, [lon, lat] order)."""
    if cov.municipality_geometry is None:
        return {"type": "FeatureCollection", "features": []}
    ordered_barangays = sorted(
        (b for b in cov.barangays if b.geometry is not None),
        key=lambda item: slug(item.slug_name),
    )
    colors = {}
    for index, barangay in enumerate(ordered_barangays):
        red, green, blue = colorsys.hls_to_rgb((index * 137.508) % 360 / 360, 0.48, 0.58)
        colors[slug(barangay.slug_name)] = "#{:02x}{:02x}{:02x}".format(
            round(red * 255), round(green * 255), round(blue * 255))
    features = []
    for b in cov.barangays:
        if b.geometry is None:
            continue
        color = colors[slug(b.slug_name)]
        features.append({
            "type": "Feature",
            "properties": {"name": b.name, "slug": b.slug_name,
                           "layers": sorted(b.layers), "color": color},
            "geometry": json.loads(gpd.GeoSeries([b.geometry]).to_json())["features"][0]["geometry"],
        })
    return {"type": "FeatureCollection", "features": features}


def municipality_geojson(cov: Coverage) -> dict:
    """Municipal study-area boundary as GeoJSON in EPSG:4326."""
    if cov.municipality_geometry is None:
        return {"type": "FeatureCollection", "features": []}
    geometry = json.loads(
        gpd.GeoSeries([cov.municipality_geometry], crs="EPSG:4326").to_json()
    )["features"][0]["geometry"]
    return {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "properties": {"name": "Santa Cruz Municipality", "boundary": "municipality"},
            "geometry": geometry,
        }],
    }


def raster_valid_stats(path: Path) -> dict:
    """Basic validity report for one raster (used by the validation script)."""
    with rasterio.open(path) as src:
        data = src.read(1)
        nodata = src.nodata
        valid = data[np.isfinite(data)] if nodata is None else data[np.isfinite(data) & (data != nodata)]
        return {
            "path": str(path),
            "crs": str(src.crs),
            "width": int(src.width),
            "height": int(src.height),
            "resolution": [float(src.transform.a), float(-src.transform.e)],
            "nodata": None if nodata is None else float(nodata),
            "dtype": str(src.dtypes[0]),
            "pct_valid": round(100.0 * valid.size / data.size, 2),
            "min": None if valid.size == 0 else float(valid.min()),
            "max": None if valid.size == 0 else float(valid.max()),
        }


def grid_aligned(paths: list[Path]) -> bool:
    """True when all rasters share CRS, resolution and 30 m grid origin parity."""
    keys = set()
    for p in paths:
        with rasterio.open(p) as src:
            a, _, _, _, e, _ = src.transform[:6]
            res = (abs(a), abs(e))
            key = (str(src.crs), tuple(round(v, 6) for v in res),
                   round(src.bounds[0] % max(res), 6), round(src.bounds[3] % max(res), 6))
            keys.add(key)
    return len(keys) == 1
