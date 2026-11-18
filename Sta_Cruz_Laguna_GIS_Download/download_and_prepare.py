"""Download and prepare barangay-level GIS inputs for Santa Cruz, Laguna, Philippines."""
from __future__ import annotations

import json
import math
import re
import time
from datetime import date
from pathlib import Path
from xml.etree import ElementTree

import geopandas as gpd
import numpy as np
import rasterio
import requests
from rasterio.enums import Resampling
from rasterio.features import geometry_mask, rasterize
from rasterio.mask import mask
from rasterio.transform import Affine
from rasterio.warp import calculate_default_transform, reproject
from scipy.ndimage import distance_transform_edt
from shapely.geometry import LineString, shape

ROOT = Path(__file__).resolve().parent
RAW = ROOT / "raw"
OUT = ROOT / "output"
CRS_UTM = "EPSG:32651"
BARANGAY_URL = (
    "https://services7.arcgis.com/poQdgvLD6DHnbpsT/ArcGIS/rest/services/"
    "Philippine_Administrative_Boundaries/FeatureServer/3/query"
)
DEM_URL = (
    "https://copernicus-dem-30m.s3.amazonaws.com/"
    "Copernicus_DSM_COG_10_N14_00_E121_00_DEM/"
    "Copernicus_DSM_COG_10_N14_00_E121_00_DEM.tif"
)
LAND_COVER_URL = (
    "https://esa-worldcover.s3.amazonaws.com/v200/2021/map/"
    "ESA_WorldCover_10m_2021_v200_N12E120_Map.tif"
)
# JRC GSW tiles are named by their upper-left corner: the "120E_10N" tile
# covers 0-10 deg N, so Santa Cruz (about 14.2 deg N) needs the "120E_20N"
# tile, which covers 10-20 deg N. (Bug fix: the 10N tile yielded 100% NoData.)
WATER_URL = (
    "https://s3.waw4-1.cloudferro.com/swift/v1/global-surface-water/"
    "download2024/Aggregated/VER1-5/occurrence/"
    "occurrence_120E_20N_v1_5_2024.tif"
)
OSM_MAP_URL = "https://api.openstreetmap.org/api/0.6/map"


def download(url: str, path: Path) -> None:
    if path.exists() and path.stat().st_size > 0:
        print(f"Already downloaded: {path.name}")
        return
    print(f"Downloading {path.name} …")
    with requests.get(url, stream=True, timeout=180) as response:
        response.raise_for_status()
        with path.open("wb") as file:
            for chunk in response.iter_content(1024 * 1024):
                if chunk:
                    file.write(chunk)


def write_raster(path: Path, data: np.ndarray, profile: dict, nodata: float) -> None:
    profile = profile.copy()
    profile.update(driver="GTiff", count=1, dtype=data.dtype, nodata=nodata, compress="deflate")
    with rasterio.open(path, "w", **profile) as dataset:
        dataset.write(data, 1)


def reproject_clip(source: Path, geometry, resolution: float, categorical: bool, destination: Path) -> tuple[np.ndarray, dict]:
    """Reproject a source raster to UTM and crop it to the municipal extent."""
    aoi = gpd.GeoSeries([geometry], crs="EPSG:4326").to_crs(CRS_UTM).iloc[0]
    minx, miny, maxx, maxy = aoi.bounds
    width = int(np.ceil((maxx - minx) / resolution))
    height = int(np.ceil((maxy - miny) / resolution))
    transform = Affine(resolution, 0, minx, 0, -resolution, maxy)
    nodata = 0
    # For uint8 layers, warp with 255 as the fill value. With a fill of 0, GDAL shifts valid 0 values to 1 so they
    # are not mistaken for no-data, which turned "0 % water occurrence" into 1 %. 255 is not a valid value in either
    # uint8 layer (ESA WorldCover classes, JRC occurrence 0-100), and it is mapped back to 0 after the warp.
    fill = 255 if categorical else nodata
    array = np.full((height, width), fill, dtype="float32" if not categorical else "uint8")
    with rasterio.open(source) as src:
        reproject(
            source=rasterio.band(src, 1), destination=array, src_transform=src.transform,
            src_crs=src.crs, src_nodata=src.nodata, dst_transform=transform,
            dst_crs=CRS_UTM, dst_nodata=fill,
            resampling=Resampling.nearest if categorical else Resampling.bilinear,
        )
    if categorical:
        array[array == 255] = nodata
    inside = geometry_mask([aoi], out_shape=array.shape, transform=transform, invert=True)
    array[~inside] = nodata
    profile = {"height": height, "width": width, "crs": CRS_UTM, "transform": transform}
    write_raster(destination, array, profile, nodata)
    return array, profile


def fetch_barangays() -> gpd.GeoDataFrame:
    params = {
        "where": "CITYMUN = 'Santa Cruz' AND PROVINCE = 'Laguna'",
        "outFields": "BRGY_NAME,CITYMUN,PROVINCE",
        "outSR": "4326", "returnGeometry": "true", "f": "geojson",
    }
    response = requests.get(BARANGAY_URL, params=params, timeout=90)
    response.raise_for_status()
    path = RAW / "sta_cruz_laguna_barangays.geojson"
    path.write_bytes(response.content)
    barangays = gpd.read_file(path)
    if len(barangays) != 26:
        raise RuntimeError(f"Expected 26 barangays; service returned {len(barangays)}. Stopping safely.")
    return barangays.to_crs("EPSG:4326")


def fetch_rivers(bounds: tuple[float, float, float, float]) -> gpd.GeoDataFrame:
    west, south, east, north = bounds
    # OSM's standard map endpoint is used in small tiles, avoiding an unstable
    # Overpass dependency and keeping every request below its area limit.
    step = 0.05
    node_coords: dict[str, tuple[float, float]] = {}
    waterway_ways: dict[str, tuple[list[str], str]] = {}
    for y in np.arange(south, north, step):
        for x in np.arange(west, east, step):
            tile_east, tile_north = min(x + step, east), min(y + step, north)
            response = requests.get(
                OSM_MAP_URL, params={"bbox": f"{x},{y},{tile_east},{tile_north}"}, timeout=180,
                headers={"User-Agent": "StaCruzLagunaGISDownloader/1.0 (educational GIS workflow)"},
            )
            response.raise_for_status()
            root = ElementTree.fromstring(response.content)
            for node in root.findall("node"):
                node_coords[node.attrib["id"]] = (float(node.attrib["lon"]), float(node.attrib["lat"]))
            for way in root.findall("way"):
                tags = {tag.attrib["k"]: tag.attrib["v"] for tag in way.findall("tag")}
                waterway = tags.get("waterway")
                if waterway in {"river", "stream", "canal", "drain", "ditch"}:
                    waterway_ways[way.attrib["id"]] = ([ref.attrib["ref"] for ref in way.findall("nd")], waterway)
            time.sleep(0.5)
    lines, tags = [], []
    for refs, waterway in waterway_ways.values():
        coords = [node_coords[ref] for ref in refs if ref in node_coords]
        if len(coords) > 1:
            lines.append(LineString(coords))
            tags.append(waterway)
    if not lines:
        raise RuntimeError("No waterways returned by OpenStreetMap. Try the script again later.")
    rivers = gpd.GeoDataFrame({"waterway": tags}, geometry=lines, crs="EPSG:4326")
    rivers.to_file(OUT / "rivers_osm.geojson", driver="GeoJSON")
    return rivers


def slope_degrees(dem: np.ndarray, resolution: float) -> np.ndarray:
    valid = dem != 0
    filled = np.where(valid, dem, np.nan)
    dy, dx = np.gradient(filled, resolution, resolution)
    result = np.degrees(np.arctan(np.sqrt(dx * dx + dy * dy))).astype("float32")
    result[~valid] = 0
    return result


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def clip_per_barangay(source_path: Path, barangays: gpd.GeoDataFrame, layer_name: str) -> None:
    for row in barangays.itertuples():
        folder = OUT / "barangays" / slug(row.BRGY_NAME)
        folder.mkdir(parents=True, exist_ok=True)
        with rasterio.open(source_path) as src:
            data, transform = mask(src, [row.geometry], crop=True, nodata=src.nodata)
            profile = src.profile.copy()
            profile.update(height=data.shape[1], width=data.shape[2], transform=transform, compress="deflate")
            with rasterio.open(folder / f"{layer_name}.tif", "w", **profile) as dst:
                dst.write(data)


def main() -> None:
    RAW.mkdir(exist_ok=True)
    OUT.mkdir(exist_ok=True)
    print("Getting the 26 Santa Cruz, Laguna barangay boundaries …")
    barangays = fetch_barangays()
    barangays.to_file(OUT / "barangays.geojson", driver="GeoJSON")
    municipality = barangays.geometry.union_all()
    buffered = municipality.buffer(0.01)
    bounds = buffered.bounds
    rivers = fetch_rivers(bounds)

    download(DEM_URL, RAW / "copernicus_dem_glo30_N14E121.tif")
    download(LAND_COVER_URL, RAW / "esa_worldcover_2021_N12E120.tif")
    download(WATER_URL, RAW / "jrc_gsw_occurrence_1984_2024_120E20N.tif")

    dem, dem_profile = reproject_clip(RAW / "copernicus_dem_glo30_N14E121.tif", municipality, 30, False, OUT / "elevation_m.tif")
    slope = slope_degrees(dem, 30)
    write_raster(OUT / "slope_degrees.tif", slope, dem_profile, 0)
    reproject_clip(RAW / "esa_worldcover_2021_N12E120.tif", municipality, 10, True, OUT / "land_cover_2021.tif")
    reproject_clip(RAW / "jrc_gsw_occurrence_1984_2024_120E20N.tif", municipality, 30, True, OUT / "historical_surface_water_occurrence_pct_1984_2024.tif")

    river_utm = rivers.to_crs(CRS_UTM)
    river_pixels = rasterize(
        [(geom, 1) for geom in river_utm.geometry], out_shape=dem.shape,
        transform=dem_profile["transform"], fill=0, dtype="uint8",
    )
    distance = (distance_transform_edt(river_pixels == 0) * 30).astype("float32")
    municipal_mask = geometry_mask([gpd.GeoSeries([municipality], crs="EPSG:4326").to_crs(CRS_UTM).iloc[0]], out_shape=dem.shape, transform=dem_profile["transform"], invert=True)
    distance[~municipal_mask] = -9999
    write_raster(OUT / "distance_to_osm_waterway_m.tif", distance, dem_profile, -9999)

    for name in ["elevation_m", "slope_degrees", "land_cover_2021", "historical_surface_water_occurrence_pct_1984_2024", "distance_to_osm_waterway_m"]:
        clip_per_barangay(OUT / f"{name}.tif", barangays.to_crs(CRS_UTM), name)

    sources = {"barangays": BARANGAY_URL, "elevation": DEM_URL, "land_cover": LAND_COVER_URL, "historical_water": WATER_URL, "rivers": OSM_MAP_URL}
    (OUT / "sources.json").write_text(json.dumps(sources, indent=2), encoding="utf-8")
    metadata = {"created": str(date.today()), "barangay_count": len(barangays), "crs": CRS_UTM, "distance_units": "metres", "historical_water": "JRC GSW v1.5 occurrence (1984-2024)"}
    (OUT / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print("Complete. Open the GeoTIFF files under output/barangays in QGIS or ArcGIS.")


if __name__ == "__main__":
    main()
