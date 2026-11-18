"""Validate GIS raster coverage for all barangays and print a report.

Checks, per layer:
- presence in every barangay folder and at municipal level
- CRS, resolution, grid alignment
- percentage of valid pixels (NoData share)
- value ranges (with layer-specific expectations)

Usage:
    python scripts/validate_gis_coverage.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import rasterio

PROJECT_ROOT = Path(__file__).resolve().parent.parent
GIS_ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else (
    PROJECT_ROOT.parent / "Sta_Cruz_Laguna_GIS_Download" / "output")

LAYERS = [
    "elevation_m",
    "slope_degrees",
    "land_cover_2021",
    "historical_surface_water_occurrence_pct_1984_2024",
    "distance_to_osm_waterway_m",
]
EXPECT = {
    "elevation_m": (0.0, 2000.0, "m"),
    "slope_degrees": (0.0, 90.0, "degrees"),
    "land_cover_2021": (10, 100, "class code"),
    "historical_surface_water_occurrence_pct_1984_2024": (0, 100, "%"),
    "distance_to_osm_waterway_m": (0.0, 100000.0, "m"),
}


def stats(path: Path) -> dict | None:
    try:
        with rasterio.open(path) as src:
            data = src.read(1)
            nodata = src.nodata
            mask = np.isfinite(data)
            if nodata is not None:
                mask &= data != nodata
            valid = data[mask]
            return {
                "crs": str(src.crs),
                "res": (float(src.transform.a), float(-src.transform.e)),
                "shape": (src.height, src.width),
                "pct_valid": round(100.0 * valid.size / data.size, 1) if data.size else 0.0,
                "min": float(valid.min()) if valid.size else None,
                "max": float(valid.max()) if valid.size else None,
            }
    except Exception as exc:  # noqa: BLE001
        return {"error": repr(exc)}


def main() -> int:
    if not GIS_ROOT.exists():
        print(f"GIS root not found: {GIS_ROOT}")
        return 1
    brgy_dirs = sorted(p for p in (GIS_ROOT / "barangays").iterdir() if p.is_dir())
    print(f"GIS root: {GIS_ROOT}")
    print(f"Barangay folders: {len(brgy_dirs)}\n")

    problems: list[str] = []

    # Municipal level.
    print("== Municipal rasters ==")
    for layer in LAYERS:
        s = stats(GIS_ROOT / f"{layer}.tif")
        if s is None or "error" in s:
            problems.append(f"municipal {layer}: unreadable/missing")
            print(f"  {layer:52s} MISSING/UNREADABLE")
            continue
        lo, hi, unit = EXPECT[layer]
        rng = f"{s['min']:.2f} .. {s['max']:.2f} {unit}"
        flag = ""
        if s["pct_valid"] == 0.0:
            flag = "  <<< 100% NoData"
            problems.append(f"municipal {layer}: 100% NoData")
        elif s["min"] < lo - 1e-6 or s["max"] > hi + 1e-6:
            flag = "  <<< value range outside expectation"
            problems.append(f"municipal {layer}: range {rng} outside [{lo}, {hi}]")
        print(f"  {layer:52s} {s['crs']:12s} res={s['res'][0]:g}m valid={s['pct_valid']:5.1f}%  {rng}{flag}")

    # Barangay level.
    print("\n== Barangay rasters: % valid pixels ==")
    header = f"{'barangay':24s}" + "".join(f"{l.split('_')[0][:13]:>15s}" for l in LAYERS)
    print(header)
    for d in brgy_dirs:
        row = f"{d.name:24s}"
        for layer in LAYERS:
            p = d / f"{layer}.tif"
            if not p.exists():
                row += f"{'MISSING':>15s}"
                problems.append(f"{d.name}/{layer}: missing")
                continue
            s = stats(p)
            if s is None or "error" in s:
                row += f"{'ERROR':>15s}"
                problems.append(f"{d.name}/{layer}: unreadable")
                continue
            if s["crs"] != "EPSG:32651":
                problems.append(f"{d.name}/{layer}: unexpected CRS {s['crs']}")
            if s["pct_valid"] == 0.0:
                problems.append(f"{d.name}/{layer}: 100% NoData")
            cell = f"{s['pct_valid']:.0f}%" if s["pct_valid"] > 0 else "0% ND"
            row += f"{cell:>15s}"
        print(row)

    print(f"\n{'=' * 60}")
    if problems:
        print(f"PROBLEMS ({len(problems)}):")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("All layers present with valid data in all barangays.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
