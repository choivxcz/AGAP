"""Fetch the additional font glyph ranges the local map labels need.

The map style uses the locally bundled "Noto Sans Regular" SDF glyph ranges
(0-255, 256-511). Actual label text in the cached OSM data also contains
characters in higher ranges (en dash, right single quote, numero sign, Roman
numerals Ⅰ-Ⅲ and a handful of CJK characters in some POI names). MapLibre
requests glyphs per 256-codepoint range; missing ranges would drop those
characters from labels.

This script downloads just the ranges that are actually referenced by the
cached data, from the font-glyphs mirror maintained by Orange Mug
(orangemug.github.io/font-glyphs), which redistributes pre-built SDF slices
for the same Noto Sans Regular font (SIL OFL 1.1). Ranges are written next to
the bundled ones; existing files are not touched.

Usage:
    python scripts/fetch_font_ranges.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FONT_DIR = PROJECT_ROOT / "app" / "static" / "fonts" / "Noto Sans Regular"
FONTSTACK_URL = ("https://orangemug.github.io/font-glyphs/glyphs/"
                 "Noto%20Sans%20Regular/{range}.pbf")
USER_AGENT = "StaCruzFloodRiskMap-fonts/1.0"

LABEL_DATA = [PROJECT_ROOT / "data" / "osm" / n for n in (
    "roads.geojson", "pois.geojson", "place.geojson", "buildings.geojson",
    "addresses.geojson", "waterways.geojson")]


def needed_ranges() -> list[int]:
    """256-wide glyph buckets referenced by any string property in the data."""
    buckets = {0, 1}
    for path in LABEL_DATA:
        if not path.exists():
            continue
        fc = json.loads(path.read_text(encoding="utf-8"))
        for f in fc.get("features", []):
            for v in (f.get("properties") or {}).values():
                if isinstance(v, str):
                    for ch in v:
                        if ord(ch) >= 32:
                            buckets.add(ord(ch) // 256)
    return sorted(buckets)


def main() -> int:
    FONT_DIR.mkdir(parents=True, exist_ok=True)
    ranges = needed_ranges()
    missing = [r for r in ranges if not (FONT_DIR / f"{r * 256}-{r * 256 + 255}.pbf").exists()]
    print(f"{len(ranges)} glyph ranges referenced; {len(missing)} missing: {missing}")
    if not missing:
        return 0
    ok, failed = 0, []
    for r in missing:
        name = f"{r * 256}-{r * 256 + 255}.pbf"
        try:
            resp = requests.get(FONTSTACK_URL.format(range=name.removesuffix(".pbf")), timeout=60,
                                headers={"User-Agent": USER_AGENT})
            resp.raise_for_status()
            if not resp.content.startswith(b"\n") or b"<" in resp.content[:16]:
                raise requests.RequestException("response is not a protobuf glyph file")
            (FONT_DIR / name).write_bytes(resp.content)
            ok += 1
        except requests.RequestException as exc:
            failed.append((name, str(exc)))
    print(f"downloaded {ok} range file(s) into {FONT_DIR}")
    for name, err in failed:
        print(f"  could not fetch {name}: {err}", file=sys.stderr)
    if failed:
        print("Labels containing characters from failed ranges degrade gracefully "
              "(those glyphs are skipped by the client).", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
