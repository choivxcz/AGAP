"""Central configuration.

All paths are configurable through environment variables so nothing is
hardcoded to one machine. Defaults are relative to this repository.
"""
from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Root of the GIS download package output (municipal rasters + barangays/).
GIS_OUTPUT_DIR = Path(os.environ.get("FLR_GIS_OUTPUT_DIR", PROJECT_ROOT.parent / "Sta_Cruz_Laguna_GIS_Download" / "output"))

# SQLite database file.
DB_PATH = Path(os.environ.get("FLR_DB_PATH", PROJECT_ROOT / "data" / "flood_risk.db"))

# Locally cached OSM map features (GeoJSON in EPSG:4326).
OSM_DATA_DIR = Path(os.environ.get("FLR_OSM_DATA_DIR", PROJECT_ROOT / "data" / "osm"))

# Host / port for the development server.
HOST = os.environ.get("FLR_HOST", "127.0.0.1")
PORT = int(os.environ.get("FLR_PORT", "8000"))

# Raster layer file names (without .tif). Discovery is still data-driven: a
# layer is only reported available when the file actually exists and opens.
RASTER_LAYERS: dict[str, str] = {
    "elevation_m": "ElevationMeters",
    "slope_degrees": "SlopeDegrees",
    "distance_to_osm_waterway_m": "DistanceToWaterwayM",
    "land_cover_2021": "LandCoverCode",
    "historical_surface_water_occurrence_pct_1984_2024": "HistoricalWaterOccurrencePct",
}

# Online basemaps (optional; the local vector map works without internet).
OSM_TILE_URL = os.environ.get("FLR_OSM_TILE_URL", "https://tile.openstreetmap.org/{z}/{x}/{y}.png")

# Keyless satellite imagery (Esri World Imagery), used by the optional
# "Satellite" basemap style in 2D and 3D.
SATELLITE_TILE_URL = os.environ.get(
    "FLR_SATELLITE_TILE_URL",
    "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}")

EXTRACTION_VERSION = "1.0.0"
