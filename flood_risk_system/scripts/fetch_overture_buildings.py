"""Fetch supplementary building footprints from the Overture Maps Foundation.

OpenStreetMap building coverage in Santa Cruz, Laguna is genuinely sparse in
several barangays (e.g. Alipit, Jasaan, Labuin, Malinao have 0-8 mapped
buildings on the live OSM API). The Overture Maps Foundation `building` theme
merges Google Open Buildings (CC BY 4.0), Microsoft ML Building Footprints
(ODbL-1.0) and OpenStreetMap (ODbL-1.0) footprints and is downloadable without
an API key. This script caches the municipal area to:

    data/osm/buildings_overture.geojson

with compact per-feature provenance (`src_dataset`, `src_license`,
`src_record`). Features whose only source is OpenStreetMap are DROPPED: those
footprints already exist in `buildings.geojson` with richer attributes, and
keeping both would draw duplicate geometry.

The study-area bbox is discovered from the barangay boundary file (never
hardcoded). Existing caches are never overwritten without --force.

Usage:
    python scripts/fetch_overture_buildings.py [--force]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

BOUNDARIES = Path(__file__).resolve().parent.parent / "data" / "boundaries"
OUT_PATH = PROJECT_ROOT / "data" / "osm" / "buildings_overture.geojson"
BOUNDARY_CANDIDATES = (
    PROJECT_ROOT.parent / "Sta_Cruz_Laguna_GIS_Download" / "output" / "barangays.geojson",
    BOUNDARIES / "barangays.geojson",
)


def discover_bbox() -> tuple[float, float, float, float]:
    """(west, south, east, north) of the barangay boundary union (EPSG:4326)."""
    import geopandas as gpd

    path = next((p for p in BOUNDARY_CANDIDATES if p.exists()), None)
    if path is None:
        raise RuntimeError(
            "barangay boundary file not found (looked in: "
            + "; ".join(str(p) for p in BOUNDARY_CANDIDATES) + ")")
    gdf = gpd.read_file(path)
    if gdf.crs is None:
        raise RuntimeError(f"{path} has no CRS; refusing to guess the study-area bbox")
    if str(gdf.crs).upper() not in ("EPSG:4326", "WGS 84", "4326"):
        gdf = gdf.to_crs("EPSG:4326")
    west, south, east, north = gdf.geometry.union_all().bounds
    # small margin so boundary-adjacent footprints are complete
    return (round(west - 0.002, 4), round(south - 0.002, 4),
            round(east + 0.002, 4), round(north + 0.002, 4))


def compact_properties(props: dict) -> dict | None:
    """Compact provenance-carrying properties; drop OSM-only duplicates."""
    sources = props.get("sources") or []
    if not sources:
        return None
    primary = sources[0]
    dataset = primary.get("dataset") or "unknown"
    if dataset == "OpenStreetMap":
        # Already served (with names/levels) from the OSM extract.
        return None
    height = props.get("height")
    try:
        height = round(float(height), 1) if height is not None else None
    except (TypeError, ValueError):
        height = None
    return {
        "class": props.get("class"),
        "subtype": props.get("subtype"),
        "height_m": height,
        "src_dataset": dataset,
        "src_license": primary.get("license"),
        "src_record": primary.get("record_id"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true",
                        help="re-download even if the cache exists")
    args = parser.parse_args()

    if OUT_PATH.exists() and not args.force:
        print(f"{OUT_PATH} already exists ({OUT_PATH.stat().st_size / 1e6:.1f} MB); "
              "use --force to re-download")
        return 0

    try:
        import overturemaps  # noqa: F401
    except ImportError:
        print("The `overturemaps` package is required: pip install overturemaps",
              file=sys.stderr)
        return 1

    bbox = discover_bbox()
    print(f"Study-area bbox (W, S, E, N): {bbox}")

    with tempfile.TemporaryDirectory() as tmp:
        raw = Path(tmp) / "raw.geojson"
        cmd = [sys.executable, "-m", "overturemaps", "download",
               "--bbox", ",".join(str(v) for v in bbox),
               "-f", "geojson", "-t", "building", "-o", str(raw)]
        print("Running:", " ".join(cmd))
        result = subprocess.run(cmd)
        if result.returncode != 0 or not raw.exists():
            print("Overture download failed; existing layers are unchanged.",
                  file=sys.stderr)
            return 1

        fc = json.loads(raw.read_text(encoding="utf-8"))
        kept, dropped = [], 0
        for f in fc.get("features", []):
            props = compact_properties(f.get("properties") or {})
            if props is None:
                dropped += 1
                continue
            kept.append({"type": "Feature", "properties": props,
                         "geometry": f["geometry"]})

        OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        out = {
            "type": "FeatureCollection",
            "attribution": ("Supplementary building footprints from the Overture Maps "
                            "Foundation; Google Open Buildings (CC BY 4.0), Microsoft ML "
                            "Building Footprints (ODbL-1.0). Not to be confused with OSM "
                            "data: see src_dataset on each feature."),
            "features": kept,
        }
        OUT_PATH.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")

    print(f"{OUT_PATH}: {len(kept)} footprints written "
          f"({OUT_PATH.stat().st_size / 1e6:.1f} MB; {dropped} OSM duplicates dropped)")
    print("License: Google Open Buildings CC BY 4.0; Microsoft ML Buildings ODbL-1.0; "
          "attribution is rendered by the map client.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
