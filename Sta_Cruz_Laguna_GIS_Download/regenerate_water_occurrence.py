"""Regenerate the JRC historical surface-water occurrence layers.

The original download used the JRC tile ``occurrence_120E_10N_v1_5_2024.tif``.
JRC GSW tiles are named by their upper-left corner, so that tile covers
0-10 deg N. Santa Cruz, Laguna lies at about 14.2 deg N, so every resampled
output pixel fell outside the source and the municipal and barangay
occurrence rasters were written as 100% NoData (fill value 0).

This script downloads the correct tile (``120E_20N``, covering 10-20 deg N),
verifies coverage before writing anything, and regenerates only the
occurrence rasters using the exact same processing functions as
``download_and_prepare.py``. The overwritten files previously contained no
usable information, so no data is lost.

Usage:
    python regenerate_water_occurrence.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import download_and_prepare as prep  # noqa: E402

RAW_TILE = prep.RAW / "jrc_gsw_occurrence_1984_2024_120E20N.tif"
OUT_LAYER = "historical_surface_water_occurrence_pct_1984_2024"


def raw_covers_study_area(path: Path, bounds: tuple[float, float, float, float]) -> bool:
    with rasterio.open(path) as src:
        left, bottom, right, top = src.bounds
    west, south, east, north = bounds
    return left <= west and bottom <= south and right >= east and top >= north


def valid_pct(path: Path) -> float:
    with rasterio.open(path) as src:
        data = src.read(1)
        nodata = src.nodata
        valid = data[np.isfinite(data) & (data != nodata)] if nodata is not None else data[np.isfinite(data)]
    return 100.0 * valid.size / data.size


def main() -> int:
    barangays = gpd.read_file(prep.OUT / "barangays.geojson").to_crs("EPSG:4326")
    barangays_utm = barangays.to_crs(prep.CRS_UTM)
    municipality = barangays.geometry.union_all()
    west, south, east, north = municipality.bounds

    prep.download(prep.WATER_URL, RAW_TILE)

    if not raw_covers_study_area(RAW_TILE, (west, south, east, north)):
        print("ERROR: downloaded JRC tile still does not cover the study area "
              f"(bounds {west:.4f},{south:.4f},{east:.4f},{north:.4f}). Aborting; nothing written.")
        return 1

    municipal = prep.OUT / f"{OUT_LAYER}.tif"
    print(f"Regenerating {municipal.name} …")
    prep.reproject_clip(RAW_TILE, municipality, 30, True, municipal)
    print(f"  valid pixels: {valid_pct(municipal):.2f}%")

    for row in barangays_utm.itertuples():
        folder = prep.OUT / "barangays" / prep.slug(row.BRGY_NAME)
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f"{OUT_LAYER}.tif"
        with rasterio.open(municipal) as src:
            data, transform = rasterio.mask.mask(src, [row.geometry], crop=True, nodata=src.nodata)
            profile = src.profile.copy()
            profile.update(height=data.shape[1], width=data.shape[2], transform=transform, compress="deflate")
            with rasterio.open(target, "w", **profile) as dst:
                dst.write(data)
        print(f"  {row.BRGY_NAME:24s} valid pixels: {valid_pct(target):6.2f}%")

    # Keep sources.json truthful about the fixed source.
    sources_path = prep.OUT / "sources.json"
    sources = {}
    if sources_path.exists():
        sources = json.loads(sources_path.read_text(encoding="utf-8"))
    sources["historical_water"] = prep.WATER_URL
    sources_path.write_text(json.dumps(sources, indent=2), encoding="utf-8")
    print("Done. Sources updated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
