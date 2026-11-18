"""SQLite persistence for households, locations and GIS extractions.

Design:
- Socio-economic household attributes live in ``household`` and are never
  mixed with the GIS extraction results.
- Household locations are append-only in ``household_location``; the active
  location is flagged. A verified location is never silently overwritten:
  callers must pass ``confirm_overwrite=True`` (enforced in the API layer).
- Every raster extraction run is stored with per-feature provenance, so a
  location change or layer regeneration keeps a full audit trail.
- ``assessment`` stores survey outcome labels (e.g. observed flood impact)
  entered by an authorised user. The system never generates labels itself.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from app.config import DB_PATH

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS household (
    household_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    code            TEXT UNIQUE,                -- user-facing identifier, e.g. HH-0001
    label           TEXT,                       -- free-text description (no PII required)
    barangay_name   TEXT,                       -- informational; verified separately by GIS
    household_size  INTEGER,
    children_count  INTEGER,
    elderly_count   INTEGER,
    pwd_count       INTEGER,
    house_material  TEXT,
    notes           TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS household_location (
    location_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    household_id          INTEGER NOT NULL REFERENCES household(household_id),
    latitude              REAL NOT NULL,
    longitude             REAL NOT NULL,
    coordinate_source     TEXT NOT NULL,        -- map_click | manual_entry | gps | address_geocode
    verification_status   TEXT NOT NULL DEFAULT 'unverified',  -- unverified | verified
    captured_at           TEXT NOT NULL,
    is_active             INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_location_household ON household_location(household_id);

CREATE TABLE IF NOT EXISTS gis_extraction (
    extraction_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    household_id         INTEGER REFERENCES household(household_id),
    location_id          INTEGER REFERENCES household_location(location_id),
    latitude             REAL NOT NULL,
    longitude            REAL NOT NULL,
    barangay_name        TEXT,
    inside_gis_coverage  TEXT NOT NULL,         -- inside | outside | unknown
    extraction_status    TEXT NOT NULL,         -- ok | partial | failed
    elevation_m          REAL,
    slope_degrees        REAL,
    distance_waterway_m  REAL,
    land_cover_code      INTEGER,
    land_cover_class     TEXT,
    water_occurrence_pct REAL,
    source_rasters       TEXT,                  -- JSON {feature: raster path}
    feature_statuses     TEXT,                  -- JSON {feature: {status, reason, ...}}
    extraction_version   TEXT NOT NULL,
    created_at           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_extraction_household ON gis_extraction(household_id);

CREATE TABLE IF NOT EXISTS assessment (
    assessment_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    household_id   INTEGER NOT NULL REFERENCES household(household_id),
    extraction_id  INTEGER REFERENCES gis_extraction(extraction_id),
    collected_at   TEXT NOT NULL,
    risk_level     TEXT,                        -- observed/survey label; never auto-generated
    notes          TEXT,
    created_at     TEXT NOT NULL
);
"""

VALID_COORD_SOURCES = {"map_click", "manual_entry", "gps", "address_geocode"}


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connect(db_path: Path | None = None) -> Iterator[sqlite3.Connection]:
    path = Path(db_path) if db_path else DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(SCHEMA)
        conn.commit()
        yield conn
    finally:
        conn.close()


def _row_to_dict(row: sqlite3.Row | None) -> dict | None:
    return None if row is None else {k: row[k] for k in row.keys()}


# ---------------------------------------------------------------- households

def create_household(conn: sqlite3.Connection, *, code: str | None, label: str | None,
                     barangay_name: str | None, household_size: int | None,
                     children_count: int | None, elderly_count: int | None,
                     pwd_count: int | None, house_material: str | None,
                     notes: str | None) -> dict:
    now = utcnow()
    cur = conn.execute(
        """INSERT INTO household (code, label, barangay_name, household_size,
           children_count, elderly_count, pwd_count, house_material, notes,
           created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (code, label, barangay_name, household_size, children_count, elderly_count,
         pwd_count, house_material, notes, now, now))
    conn.commit()
    return get_household(conn, cur.lastrowid)


def get_household(conn: sqlite3.Connection, household_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM household WHERE household_id = ?", (household_id,)).fetchone()
    return _row_to_dict(row)


def list_households(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """SELECT h.*, l.latitude AS latitude, l.longitude AS longitude,
                  l.verification_status AS verification_status,
                  l.coordinate_source AS coordinate_source,
                  l.captured_at AS location_captured_at
           FROM household h
           LEFT JOIN household_location l
                  ON l.household_id = h.household_id AND l.is_active = 1
           ORDER BY h.household_id""").fetchall()
    return [_row_to_dict(r) for r in rows]


def update_household_attributes(conn: sqlite3.Connection, household_id: int, fields: dict) -> dict | None:
    allowed = {"code", "label", "barangay_name", "household_size", "children_count",
               "elderly_count", "pwd_count", "house_material", "notes"}
    updates = {k: v for k, v in fields.items()
               if k in allowed and (v is not None or k == "barangay_name")}
    if not updates:
        return get_household(conn, household_id)
    updates["updated_at"] = utcnow()
    sets = ", ".join(f"{k} = ?" for k in updates)
    conn.execute(f"UPDATE household SET {sets} WHERE household_id = ?",
                 (*updates.values(), household_id))
    conn.commit()
    return get_household(conn, household_id)


# ----------------------------------------------------------------- locations

def get_active_location(conn: sqlite3.Connection, household_id: int) -> dict | None:
    row = conn.execute(
        "SELECT * FROM household_location WHERE household_id = ? AND is_active = 1 "
        "ORDER BY location_id DESC LIMIT 1", (household_id,)).fetchone()
    return _row_to_dict(row)


def has_verified_location(conn: sqlite3.Connection, household_id: int) -> bool:
    row = conn.execute(
        "SELECT 1 FROM household_location WHERE household_id = ? AND is_active = 1 "
        "AND verification_status = 'verified'", (household_id,)).fetchone()
    return row is not None


def set_location(conn: sqlite3.Connection, household_id: int, lat: float, lon: float,
                 coordinate_source: str, verification_status: str = "unverified",
                 confirm_overwrite: bool = False) -> dict:
    """Set the household's active location (append-only).

    Raises ValueError when the household already has a *verified* active
    location and ``confirm_overwrite`` was not supplied.
    """
    if coordinate_source not in VALID_COORD_SOURCES:
        raise ValueError(f"coordinate_source must be one of {sorted(VALID_COORD_SOURCES)}")
    if has_verified_location(conn, household_id) and not confirm_overwrite:
        raise ValueError("household already has a verified location; "
                         "pass confirm_overwrite=true to replace it")
    conn.execute("UPDATE household_location SET is_active = 0 WHERE household_id = ?", (household_id,))
    cur = conn.execute(
        "INSERT INTO household_location (household_id, latitude, longitude, coordinate_source,"
        " verification_status, captured_at, is_active) VALUES (?,?,?,?,?,?,1)",
        (household_id, float(lat), float(lon), coordinate_source, verification_status, utcnow()))
    conn.commit()
    return get_active_location(conn, household_id)


def verify_location(conn: sqlite3.Connection, household_id: int) -> dict | None:
    """Explicitly mark the household's active location as user-verified."""
    loc = get_active_location(conn, household_id)
    if loc is None:
        return None
    conn.execute("UPDATE household_location SET verification_status = 'verified' "
                 "WHERE location_id = ?", (loc["location_id"],))
    conn.commit()
    return get_active_location(conn, household_id)


# --------------------------------------------------------------- extractions

def save_extraction(conn: sqlite3.Connection, result: dict, *, household_id: int | None,
                    location_id: int | None) -> dict:
    """Store one extraction run from an extract_features() result dict."""
    feats = result["features"]
    src = {name: f.get("source_raster") for name, f in feats.items() if f.get("source_raster")}
    lc_code = feats.get("LandCoverCode", {}).get("value")
    lc_class = feats.get("LandCoverClass", {}).get("value")
    cur = conn.execute(
        """INSERT INTO gis_extraction (household_id, location_id, latitude, longitude,
           barangay_name, inside_gis_coverage, extraction_status, elevation_m,
           slope_degrees, distance_waterway_m, land_cover_code, land_cover_class,
           water_occurrence_pct, source_rasters, feature_statuses, extraction_version,
           created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (household_id, location_id,
         result["input"]["latitude"], result["input"]["longitude"],
         (result.get("barangay") or {}).get("name"),
         result["inside_gis_coverage"], result["extraction_status"],
         feats.get("ElevationMeters", {}).get("value"),
         feats.get("SlopeDegrees", {}).get("value"),
         feats.get("DistanceToWaterwayM", {}).get("value"),
         lc_code, lc_class,
         feats.get("HistoricalWaterOccurrencePct", {}).get("value"),
         json.dumps(src), json.dumps(feats), result["extraction_version"], utcnow()))
    conn.commit()
    return get_extraction(conn, cur.lastrowid)


def get_extraction(conn: sqlite3.Connection, extraction_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM gis_extraction WHERE extraction_id = ?", (extraction_id,)).fetchone()
    out = _row_to_dict(row)
    if out:
        out["source_rasters"] = json.loads(out["source_rasters"]) if out["source_rasters"] else None
        out["feature_statuses"] = json.loads(out["feature_statuses"]) if out["feature_statuses"] else None
    return out


def latest_extraction(conn: sqlite3.Connection, household_id: int) -> dict | None:
    row = conn.execute("SELECT extraction_id FROM gis_extraction WHERE household_id = ? "
                       "ORDER BY extraction_id DESC LIMIT 1", (household_id,)).fetchone()
    return get_extraction(conn, row["extraction_id"]) if row else None


# ---------------------------------------------------------------- assessments

def save_assessment(conn: sqlite3.Connection, household_id: int, collected_at: str,
                    risk_level: str | None, notes: str | None,
                    extraction_id: int | None = None) -> dict:
    cur = conn.execute(
        "INSERT INTO assessment (household_id, extraction_id, collected_at, risk_level, notes, created_at)"
        " VALUES (?,?,?,?,?,?)",
        (household_id, extraction_id, collected_at, risk_level, notes, utcnow()))
    conn.commit()
    row = conn.execute("SELECT * FROM assessment WHERE assessment_id = ?", (cur.lastrowid,)).fetchone()
    return _row_to_dict(row)
