"""Raster feature extraction for a household location.

Design rules enforced here:
- Coordinates are stored/validated as (latitude, longitude) in WGS 84.
- The point is reprojected into each raster's own CRS (never assumed 4326).
- Bounds, NoData, NaN/Inf and dtype constraints are all checked before a
  value is accepted; failures produce a missing-data status, never a guess.
- Tile selection is deterministic: municipal raster first, then the
  barangay tile for the containing barangay, then barangay tiles in sorted
  slug order. Municipal and barangay rasters in this dataset share one grid,
  so no mosaicking is needed.
- Layer-specific semantics are documented per layer below.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import rasterio
from pyproj import Transformer
from shapely.geometry import Point

from app.config import EXTRACTION_VERSION, RASTER_LAYERS
from app.gis import discovery
from app.gis.discovery import Coverage, barangay_for_point

logger = logging.getLogger(__name__)

# ESA WorldCover 2021 v200 class legend (https://esa-worldcover.org).
LAND_COVER_LEGEND: dict[int, str] = {
    10: "Tree cover",
    20: "Shrubland",
    30: "Grassland",
    40: "Cropland",
    50: "Built-up",
    60: "Bare / sparse vegetation",
    70: "Snow and ice",
    80: "Permanent water bodies",
    90: "Herbaceous wetland",
    95: "Mangroves",
    100: "Moss and lichen",
}

# output feature name -> (raster layer, unit, value validator)
@dataclass(frozen=True)
class LayerSpec:
    feature: str
    unit: str
    dtype: str


def _unit(layer: str) -> str:
    return {
        "elevation_m": "m",
        "slope_degrees": "degrees",
        "distance_to_osm_waterway_m": "m",
        "land_cover_2021": "class code",
        "historical_surface_water_occurrence_pct_1984_2024": "%",
    }[layer]


def _dtype(layer: str) -> str:
    return "float" if layer != "land_cover_2021" else "integer"


LAYER_SPECS: dict[str, LayerSpec] = {
    layer: LayerSpec(feature=feature, unit=_unit(layer), dtype=_dtype(layer))
    for layer, feature in RASTER_LAYERS.items()
}


class CoordinateError(ValueError):
    """Raised for invalid latitude/longitude input."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def validate_latlon(lat: float, lon: float) -> tuple[float, float]:
    """Validate geographic coordinates. Returns (lat, lon) as floats."""
    try:
        lat_f, lon_f = float(lat), float(lon)
    except (TypeError, ValueError) as exc:
        raise CoordinateError("non_numeric", "latitude and longitude must be numbers") from exc
    if not (math.isfinite(lat_f) and math.isfinite(lon_f)):
        raise CoordinateError("non_finite", "latitude and longitude must be finite numbers")
    if not (-90.0 <= lat_f <= 90.0):
        raise CoordinateError("lat_out_of_range", f"latitude {lat_f} outside [-90, 90]")
    if not (-180.0 <= lon_f <= 180.0):
        raise CoordinateError("lon_out_of_range", f"longitude {lon_f} outside [-180, 180]")
    # Note: a (lon, lat) ordering mistake is already caught by the latitude
    # range check for this study area (longitude ~121 is an invalid latitude);
    # no additional swap heuristic is applied.
    return lat_f, lon_f


@dataclass
class FeatureResult:
    feature: str
    unit: str
    dtype: str
    status: str            # "ok" | "missing" | "error"
    value: float | int | None
    source_raster: str | None = None
    source_crs: str | None = None
    pixel: tuple[int, int] | None = None   # (row, col)
    reason: str | None = None
    note: str | None = None

    def as_dict(self) -> dict:
        return {
            "feature": self.feature, "unit": self.unit, "dtype": self.dtype,
            "status": self.status, "value": self.value,
            "source_raster": self.source_raster, "source_crs": self.source_crs,
            "pixel": list(self.pixel) if self.pixel else None,
            "reason": self.reason, "note": self.note,
        }


def _choose_raster(cov: Coverage, layer: str, brgy_slug: str | None) -> tuple[Path, str] | None:
    """Deterministic raster choice. Returns (path, selection_reason) or None."""
    mun = cov.municipal.get(layer)
    if mun is not None and mun.municipal_path is not None:
        return mun.municipal_path, "municipal_raster"
    candidates: list[Path] = []
    if brgy_slug:
        p = cov.root / "barangays" / brgy_slug / f"{layer}.tif"
        if p.exists():
            candidates.append(p)
    for b in sorted(cov.barangays, key=lambda x: x.slug_name):
        p = cov.root / "barangays" / b.slug_name / f"{layer}.tif"
        if p.exists() and p not in candidates:
            candidates.append(p)
    if candidates:
        return candidates[0], "barangay_tile_fallback"
    return None


def _sample(path: Path, x: float, y: float, layer: str,
            inside_polygon: bool | None) -> FeatureResult:
    """Sample one layer at a point already transformed into the raster CRS.

    ``inside_polygon`` is the polygon-membership verdict in EPSG:4326 (True /
    False) or None when no boundary data is available; it is used to
    disambiguate fill values that double as legal values (see notes).
    """
    spec = LAYER_SPECS[layer]
    try:
        with rasterio.open(path) as src:
            crs = str(src.crs)
            nodata = src.nodata
            row, col = rasterio.transform.rowcol(src.transform, x, y)
            row, col = int(row), int(col)
            if not (0 <= row < src.height and 0 <= col < src.width):
                return FeatureResult(spec.feature, spec.unit, spec.dtype, "missing", None,
                                     source_raster=str(path), source_crs=crs,
                                     reason="point outside raster bounds")
            window = rasterio.windows.Window(col, row, 1, 1)
            value = src.read(1, window=window)[0, 0]
            value_f = float(value)
            if not math.isfinite(value_f) or (isinstance(value, float) and np.isnan(value)):
                return FeatureResult(spec.feature, spec.unit, spec.dtype, "missing", None,
                                     source_raster=str(path), source_crs=crs, pixel=(row, col),
                                     reason="pixel value is NaN/Inf")
            if nodata is not None and value_f == float(nodata):
                # Layer-specific fill semantics.
                if layer == "historical_surface_water_occurrence_pct_1984_2024":
                    # In this dataset 0 is written both for "0 % occurrence"
                    # (a legal value; the JRC source had no separate 255
                    # NoData inside the municipality) and for pixels outside
                    # the municipal polygon. Resolve using polygon membership.
                    if inside_polygon:
                        return FeatureResult(
                            spec.feature, spec.unit, spec.dtype, "ok", 0.0,
                            source_raster=str(path), source_crs=crs, pixel=(row, col),
                            note="0 % = never surface water observed 1984-2024 "
                                 "(fill value disambiguated by polygon membership)")
                    return FeatureResult(
                        spec.feature, spec.unit, spec.dtype, "missing", None,
                        source_raster=str(path), source_crs=crs, pixel=(row, col),
                        reason="pixel is NoData and point is outside the barangay polygons")
                return FeatureResult(spec.feature, spec.unit, spec.dtype, "missing", None,
                                     source_raster=str(path), source_crs=crs, pixel=(row, col),
                                     reason=f"pixel is NoData ({nodata})")
            # Range sanity for percentage and class layers.
            if layer == "historical_surface_water_occurrence_pct_1984_2024" and not (0.0 <= value_f <= 100.0):
                return FeatureResult(spec.feature, spec.unit, spec.dtype, "missing", None,
                                     source_raster=str(path), source_crs=crs, pixel=(row, col),
                                     reason=f"value {value_f} outside valid 0-100 % range")
            if layer == "land_cover_2021" and value_f not in LAND_COVER_LEGEND:
                return FeatureResult(spec.feature, spec.unit, spec.dtype, "missing", None,
                                     source_raster=str(path), source_crs=crs, pixel=(row, col),
                                     reason=f"class code {value_f} not in ESA WorldCover 2021 legend")
            out: float | int = int(value_f) if spec.dtype == "integer" else value_f
            return FeatureResult(spec.feature, spec.unit, spec.dtype, "ok", out,
                                 source_raster=str(path), source_crs=crs, pixel=(row, col))
    except Exception as exc:  # noqa: BLE001
        logger.exception("raster sampling failed for %s", path)
        return FeatureResult(spec.feature, spec.unit, spec.dtype, "error", None,
                             source_raster=str(path),
                             reason=f"raster sampling error: {exc!r}")


def extract_features(cov: Coverage, lat: float, lon: float) -> dict:
    """Extract all configured features for one (lat, lon) point.

    Returns a JSON-serialisable dictionary. This function never raises for
    data problems: failures are reported per feature and through
    ``extraction_status``.
    """
    lat_f, lon_f = validate_latlon(lat, lon)
    brgy = barangay_for_point(cov, lon_f, lat_f)
    brgy_slug = brgy.slug_name if brgy else None
    inside_polygon: bool | None = (brgy is not None) if cov.municipality_geometry is not None else None

    notes: list[str] = []
    if cov.municipality_geometry is None:
        notes.append("barangay boundary file unavailable; barangay not verified and "
                     "NoData/0 disambiguation for water occurrence is conservative")

    # Transform once per distinct raster CRS.
    transformers: dict[str, Transformer] = {}

    def to_raster_xy(crs: str) -> tuple[float, float]:
        if crs not in transformers:
            transformers[crs] = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
        return transformers[crs].transform(lon_f, lat_f)

    features: dict[str, dict] = {}
    n_ok = 0
    for layer in RASTER_LAYERS:
        choice = _choose_raster(cov, layer, brgy_slug)
        if choice is None:
            spec = LAYER_SPECS[layer]
            features[spec.feature] = FeatureResult(
                spec.feature, spec.unit, spec.dtype, "missing", None,
                reason=f"no raster available for layer '{layer}'").as_dict()
            continue
        path, selection = choice
        with rasterio.open(path) as src:
            crs = str(src.crs)
        x, y = to_raster_xy(crs)
        result = _sample(path, x, y, layer, inside_polygon)
        if selection == "barangay_tile_fallback":
            result.note = ((result.note + " ") if result.note else "") + \
                "sampled from a barangay tile fallback (municipal raster unavailable)"
        if result.status == "ok":
            n_ok += 1
        features[result.feature] = result.as_dict()

    if inside_polygon is False:
        notes.append("selected point is outside the available GIS coverage "
                     "(outside all Santa Cruz barangay polygons)")
    coverage_status = ("outside" if inside_polygon is False
                       else "inside" if inside_polygon is True else "unknown")

    if n_ok == len(RASTER_LAYERS):
        extraction_status = "ok"
    elif n_ok == 0:
        extraction_status = "failed"
    else:
        extraction_status = "partial"

    return {
        "input": {"latitude": lat_f, "longitude": lon_f},
        "coordinate_reference": "EPSG:4326 (WGS 84), stored as latitude, longitude",
        "inside_gis_coverage": coverage_status,
        "barangay": ({"name": brgy.name, "slug": brgy.slug_name, "matched_by": "point_in_polygon"}
                     if brgy else None),
        "extraction_status": extraction_status,
        "extraction_version": EXTRACTION_VERSION,
        "features": features,
        "notes": notes,
    }


def land_cover_label(code: int | None) -> str | None:
    if code is None:
        return None
    return LAND_COVER_LEGEND.get(int(code))


def extract_with_labels(cov: Coverage, lat: float, lon: float) -> dict:
    """extract_features + readable land-cover label."""
    result = extract_features(cov, lat, lon)
    lc = result["features"].get("LandCoverCode")
    result["features"]["LandCoverClass"] = {
        "feature": "LandCoverClass", "unit": "label", "dtype": "string",
        "status": lc["status"] if lc else "missing",
        "value": land_cover_label(lc["value"]) if lc and lc["value"] is not None else None,
        "reason": None if lc and lc["status"] == "ok" else "land cover code unavailable",
        "note": "ESA WorldCover 2021 v200 legend",
    }
    return result
