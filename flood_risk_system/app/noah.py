"""Project NOAH flood hazard lookups for the risk model (dataset v2.6).

Two views of the NOAH maps, computed exactly as in the training dataset:
  * house_hazard(lat, lon)  -> class of the NOAH zone the point falls in (HouseFloodHazard5yr/25yr/100yr)
  * barangay_hazard(name)   -> the barangay's class: highest class covering at least 25% of its area
                               (FloodHazard5yr/25yr/100yr, read from barangay_flood_hazard.csv)
Classes are None / Low / Medium / High. "None" means outside NOAH's modelled flood zones, not "no risk".
"""
from __future__ import annotations

import csv
import json
import os
from functools import lru_cache
from pathlib import Path

from shapely import prepare
from shapely.geometry import Point, shape

NOAH_DIR = Path(os.environ.get("FLR_NOAH_DIR", Path(__file__).resolve().parents[1] / "data" / "noah_flood_hazard"))
RETURN_PERIODS = ("5yr", "25yr", "100yr")
LEVEL = {"None": 0, "Low": 1, "Medium": 2, "High": 3}
NAME = {v: k for k, v in LEVEL.items()}
SOURCE = "Project NOAH flood hazard maps (ODbL 1.0)"


@lru_cache(maxsize=1)
def _zones() -> dict[str, list[tuple[int, object]]]:
    """Hazard zones per return period, as (level, prepared geometry)."""
    zones = {}
    for rp in RETURN_PERIODS:
        data = json.loads((NOAH_DIR / f"santa_cruz_flood_hazard_{rp}.geojson").read_text(encoding="utf-8"))
        items = []
        for feature in data["features"]:
            geom = shape(feature["geometry"])
            prepare(geom)
            items.append((LEVEL[feature["properties"]["HazardClass"]], geom))
        zones[rp] = items
    return zones


@lru_cache(maxsize=1)
def _barangay_table() -> dict[str, dict[str, str]]:
    with (NOAH_DIR / "barangay_flood_hazard.csv").open(newline="", encoding="utf-8") as file:
        return {row["Barangay"]: row for row in csv.DictReader(file)}


def house_hazard(lat: float, lon: float) -> dict[str, str]:
    """NOAH class at the point for each return period (worst class if zones touch)."""
    point = Point(lon, lat)
    return {rp: NAME[max((level for level, geom in _zones()[rp] if geom.contains(point)), default=0)]
            for rp in RETURN_PERIODS}


def barangay_hazard(barangay: str) -> dict[str, str] | None:
    """The barangay's NOAH class for each return period, or None if the barangay is not in the table."""
    row = _barangay_table().get(barangay)
    return None if row is None else {rp: row[f"FloodHazard{rp}"] for rp in RETURN_PERIODS}
