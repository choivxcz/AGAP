"""MGB (Mines and Geosciences Bureau) flood susceptibility at a point.

Used by both the training dataset (Activities/AGAP_NOAH_Dataset/build_noah_dataset.py) and the app, so the two
always agree. Classes: NotMapped, Moderate, High, VeryHigh. Where polygons overlap, the worse class wins.
"NotMapped" means outside MGB's mapped flood-susceptible areas, not "no risk".
"""
from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

from shapely import make_valid, prepare
from shapely.geometry import Point, shape

MGB_PATH = Path(os.environ.get('FLR_MGB_PATH', Path(__file__).resolve().parents[1] / 'data' / 'assessment_sources'
                               / 'mgb_flood_susceptibility_sta_cruz_laguna.geojson'))
CODES = {'MF': 'Moderate', 'HF': 'High', 'VHF': 'VeryHigh'}
ORDER = ['NotMapped', 'Moderate', 'High', 'VeryHigh']
# MGB class on the 0-3 flood hazard scale used with NOAH (None 0, Low 1, Medium 2, High 3), from MGB's depth ranges:
# Moderate 0.5-1 m -> 2 (NOAH Medium, 0.5-1.5 m); High 1-2 m -> 2.5 (spans Medium and High); VeryHigh > 2 m -> 3.
HAZARD = {'NotMapped': 0.0, 'Moderate': 2.0, 'High': 2.5, 'VeryHigh': 3.0}
SOURCE = 'MGB Detailed Flood Susceptibility (Geospatial Data Inventory)'


@lru_cache(maxsize=1)
def _zones() -> list[tuple[int, object]]:
    data = json.loads(MGB_PATH.read_text(encoding='utf-8'))
    zones = []
    for feature in data['features']:
        geom = make_valid(shape(feature['geometry']))
        prepare(geom)
        zones.append((ORDER.index(CODES[feature['properties']['FloodSusc']]), geom))
    return zones


def susceptibility(lat: float, lon: float) -> str:
    point = Point(lon, lat)
    return ORDER[max((level for level, geom in _zones() if geom.covers(point)), default=0)]
