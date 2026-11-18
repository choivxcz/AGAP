"""Read-only location features and risk prediction with the AGAP NOAH model (dataset v2.6).

Every model input is computed the same way as in the training dataset (Activities/AGAP_NOAH_Dataset):
  * terrain and historical water at the pin ... app.gis.extraction (the lookup used to build the dataset)
  * NOAH flood hazard ........................ app.noah (barangay class and class at the point)
  * MGB flood susceptibility ................. app.mgb (class at the point)
  * rainfall ................................. the dataset's 60 historical NASA POWER rain events; the household
                                               is assessed across all of them (app.ml_model.predict_profile)
"""
import json
import os
from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app import mgb, noah
from app.gis import discovery
from app.gis.extraction import extract_features
from app.ml_model import MODEL_DIR, predict_profile, rain_events

ROOT = Path(os.environ.get('FLR_ASSESSMENT_SOURCE_DIR', Path(__file__).resolve().parents[1] / 'data' / 'assessment_sources'))
router = APIRouter(prefix='/api/assessment', tags=['Assessment'])
LAND = {10: 'TreeCover', 20: 'Shrubland', 30: 'Grassland', 40: 'Cropland', 50: 'BuiltUp', 60: 'BareSparseVegetation',
        80: 'PermanentWater', 90: 'HerbaceousWetland', 95: 'Mangroves', 100: 'MossLichen'}
TERRAIN = {'ElevationMeters': 'm', 'SlopeDegrees': 'degrees', 'DistanceToWaterwayM': 'm', 'HistoricalWaterOccurrencePct': '%'}
GIS_SOURCE = 'Sta_Cruz_Laguna_GIS_Download rasters (app.gis.extraction)'
RAIN_NOTE = ('Rainfall is not chosen by date. The prediction runs the household through 60 historical rain events '
             'from the NASA POWER record for Santa Cruz (1991-2025): 15 extreme, 15 heavy, 15 moderate and 15 light. '
             'The risk level is the most common result across the 15 extreme events.')


@lru_cache(maxsize=1)
def boundaries():
    """Barangay boundaries for the map display."""
    return json.loads((ROOT / 'barangays.geojson').read_text(encoding='utf-8'))


def field(value, source, unit='', reason=None):
    return {'value': value, 'source': source, 'unit': unit, 'status': 'missing' if value is None else 'ok', 'reason': reason}


def location_type(distance):
    """Same cut-offs as the dataset: Riverside < 100 m <= NearWaterway < 300 m <= Inland."""
    if distance is None:
        return None
    return 'Riverside' if distance < 100 else 'NearWaterway' if distance < 300 else 'Inland'


@router.get('/config')
def configuration():
    try:
        events = rain_events()
    except (OSError, ValueError, KeyError) as exc:
        raise HTTPException(503, 'Rain event source unavailable or invalid') from exc
    return {'prediction_available': (MODEL_DIR / 'model.joblib').exists(), 'prediction_mode': 'ml_profile',
            'rain_events': dict(Counter(e['EventType'] for e in events)), 'rainfall_note': RAIN_NOTE}


@router.get('/barangays')
def barangay_map():
    try:
        return boundaries()
    except (OSError, ValueError, KeyError) as exc:
        raise HTTPException(503, 'Barangay boundaries unavailable') from exc


class Location(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


def extract_location(req: Location):
    try:
        gis = extract_features(discovery.get_coverage(), req.latitude, req.longitude)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(503, 'GIS sources unavailable') from exc
    barangay = (gis.get('barangay') or {}).get('name')
    if barangay is None:
        raise HTTPException(422, 'Select a point inside a Santa Cruz barangay boundary.')

    features = {}
    for key, unit in TERRAIN.items():
        f = gis['features'].get(key, {})
        value = f.get('value') if f.get('status') == 'ok' else None
        features[key] = field(None if value is None else round(float(value), 2), GIS_SOURCE, unit,
                              None if value is not None else (f.get('reason') or 'No valid source pixel at this location'))
    code = gis['features'].get('LandCoverCode', {})
    land = LAND.get(int(code['value'])) if code.get('status') == 'ok' and code.get('value') is not None else None
    features['LandCoverType'] = field(land, 'ESA WorldCover 2021', 'class', None if land else 'Land cover unavailable')
    raw = gis['features'].get('DistanceToWaterwayM', {})
    location = location_type(raw.get('value') if raw.get('status') == 'ok' else None)   # unrounded, as in the dataset
    features['LocationType'] = field(location, 'Derived from DistanceToWaterwayM', 'class',
                                     None if location else 'Distance unavailable')

    try:
        house = noah.house_hazard(req.latitude, req.longitude)
        area = noah.barangay_hazard(barangay)
        for rp in noah.RETURN_PERIODS:
            features[f'FloodHazard{rp}'] = field(area[rp] if area else None, noah.SOURCE + ', barangay class', 'class',
                                                 None if area else 'Barangay not in the NOAH table')
            features[f'HouseFloodHazard{rp}'] = field(house[rp], noah.SOURCE + ', at the selected point', 'class')
    except (OSError, ValueError, KeyError):
        for rp in noah.RETURN_PERIODS:
            features[f'FloodHazard{rp}'] = field(None, noah.SOURCE, 'class', 'NOAH source unavailable')
            features[f'HouseFloodHazard{rp}'] = field(None, noah.SOURCE, 'class', 'NOAH source unavailable')

    try:
        features['MGBFloodSusceptibility'] = field(mgb.susceptibility(req.latitude, req.longitude),
                                                   mgb.SOURCE + ', at the selected point', 'class')
    except (OSError, ValueError, KeyError):
        features['MGBFloodSusceptibility'] = field(None, mgb.SOURCE, 'class', 'MGB source unavailable')

    warnings = [f'{k}: {v["reason"]}' for k, v in features.items() if v['status'] == 'missing']
    if features['HouseFloodHazard100yr']['value'] == 'None':
        warnings.append('NOAH "None" means the point is outside the modelled flood zones, not that it cannot flood.')
    if features['MGBFloodSusceptibility']['value'] == 'NotMapped':
        warnings.append('MGB "NotMapped" means outside the MGB mapped flood-susceptible areas, not that it cannot flood.')
    valid_land = land not in (None, 'PermanentWater')
    if not valid_land:
        warnings.append('Choose a mapped land location rather than water or an unknown land-cover pixel.')
    return {'barangay': barangay, 'latitude': req.latitude, 'longitude': req.longitude,
            'features': features, 'warnings': warnings, 'valid_land': valid_land}


@router.post('/location')
def location_features(req: Location):
    return extract_location(req)


class Assessment(Location):
    """Household values in the training dataset's format. Age is the household head's age."""
    Age: int = Field(ge=18, le=100, strict=True)
    HousingMaterial: Literal['Concrete', 'SemiConcrete', 'LightMaterials']
    HouseMembers: int = Field(ge=1, le=12, strict=True)
    ChildrenCount: int = Field(ge=0, le=12, strict=True)
    ElderCount: int = Field(ge=0, le=12, strict=True)
    PWDCount: int = Field(ge=0, le=12, strict=True)

    @model_validator(mode='after')
    def counts(self):
        if self.ChildrenCount + self.ElderCount > self.HouseMembers:
            raise ValueError('ChildrenCount + ElderCount must not exceed HouseMembers.')
        if self.PWDCount > self.HouseMembers:
            raise ValueError('PWDCount must not exceed HouseMembers.')
        if self.Age >= 60 and self.ElderCount < 1:
            raise ValueError('Count the household head in ElderCount when the head is 60 or older.')
        if self.Age < 60 and self.ChildrenCount + self.ElderCount >= self.HouseMembers:
            raise ValueError('Leave room for the household head (aged 18-59) among the household members.')
        return self


@router.post('/prepare')
def prepare_assessment(req: Assessment):
    result = extract_location(req)
    if not result['valid_land']:
        raise HTTPException(422, 'Select a valid mapped land location first.')
    household = req.model_dump(exclude={'latitude', 'longitude'})
    return {**result, 'household': household, 'prediction': predict_profile(result['features'], household),
            'privacy': 'Household details were processed for this response only; they were not saved.'}


@router.get('/model-report')
def model_report():
    if not (MODEL_DIR / 'report.html').exists():
        raise HTTPException(404, 'Train the model first (scripts/train_agap_noah_model.py).')
    return FileResponse(MODEL_DIR / 'report.html')


@router.get('/confusion_matrix.png')
def model_confusion_matrix():
    if not (MODEL_DIR / 'confusion_matrix.png').exists():
        raise HTTPException(404, 'Train the model first (scripts/train_agap_noah_model.py).')
    return FileResponse(MODEL_DIR / 'confusion_matrix.png')


@router.get('/experiments/{version}/{asset}')
def experiment_report(version: Literal['v1', 'v2'], asset: Literal['report.html', 'confusion_matrix.png']):
    """Read-only reports of the earlier experiments; never expose arbitrary artifact paths."""
    path = Path(__file__).resolve().parents[1] / 'data' / f'synthetic_ml_{version}' / asset
    if not path.exists():
        raise HTTPException(404, 'Experiment report unavailable.')
    return FileResponse(path)
