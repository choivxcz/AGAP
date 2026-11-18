"""SQLite tables of the MDRRMO operations module (same database file as the rest of the app).

  resident        registered households: contact, location, household profile, stored location features,
                  baseline risk (60 historical rain events) and risk under the active rain scenario
  incident        reports from residents (public page) and staff: flooding, rescue, blocked road, ...
  incident_log    append-only history of every incident change (who/what/when)
  facility        rescue bases, evacuation centres and hospitals
  ops_setting     key/value settings, e.g. the active rain scenario
"""
from __future__ import annotations

import json
import secrets
import sqlite3
from contextlib import contextmanager
from typing import Iterator

from app import config
from app.database import utcnow

SCHEMA = """
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS resident (
    resident_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ref_code          TEXT UNIQUE,
    full_name         TEXT NOT NULL,
    contact_number    TEXT,
    address_text      TEXT,
    barangay          TEXT,
    latitude          REAL NOT NULL,
    longitude         REAL NOT NULL,
    location_source   TEXT NOT NULL DEFAULT 'map_click',
    age               INTEGER NOT NULL,
    housing_material  TEXT NOT NULL,
    house_members     INTEGER NOT NULL,
    children_count    INTEGER NOT NULL,
    elder_count       INTEGER NOT NULL,
    pwd_count         INTEGER NOT NULL,
    special_needs     TEXT,
    features_json     TEXT,
    baseline_risk     TEXT,
    baseline_json     TEXT,
    current_risk      TEXT,
    current_probs     TEXT,
    scenario_stamp    TEXT,
    priority_score    REAL,
    evac_status       TEXT NOT NULL DEFAULT 'at_home',
    evac_facility_id  INTEGER,
    notes             TEXT,
    is_demo           INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_resident_barangay ON resident(barangay);
CREATE INDEX IF NOT EXISTS idx_resident_risk ON resident(current_risk);

CREATE TABLE IF NOT EXISTS incident (
    incident_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ref_code          TEXT UNIQUE NOT NULL,
    type              TEXT NOT NULL,
    latitude          REAL NOT NULL,
    longitude         REAL NOT NULL,
    barangay          TEXT,
    water_depth       TEXT,
    people_affected   INTEGER,
    description       TEXT,
    reporter_name     TEXT,
    reporter_contact  TEXT,
    source            TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'new',
    priority          TEXT NOT NULL,
    assigned_team     TEXT,
    resident_id       INTEGER REFERENCES resident(resident_id) ON DELETE SET NULL,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    resolved_at       TEXT
);
CREATE INDEX IF NOT EXISTS idx_incident_status ON incident(status);

CREATE TABLE IF NOT EXISTS incident_log (
    log_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id  INTEGER NOT NULL REFERENCES incident(incident_id) ON DELETE CASCADE,
    at           TEXT NOT NULL,
    action       TEXT NOT NULL,
    note         TEXT
);

CREATE TABLE IF NOT EXISTS facility (
    facility_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    type          TEXT NOT NULL,              -- rescue_base | evacuation_center | hospital
    latitude      REAL NOT NULL,
    longitude     REAL NOT NULL,
    barangay      TEXT,
    capacity      INTEGER,
    is_active     INTEGER NOT NULL DEFAULT 1,
    source        TEXT NOT NULL,              -- osm | manual
    osm_id        TEXT,
    hazard_json   TEXT,
    notes         TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    UNIQUE(osm_id, type)
);

CREATE TABLE IF NOT EXISTS ops_setting (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

RESIDENT_FIELDS = ["full_name", "contact_number", "address_text", "latitude", "longitude", "location_source", "age",
                   "housing_material", "house_members", "children_count", "elder_count", "pwd_count",
                   "special_needs", "notes", "evac_status", "evac_facility_id"]


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    path = config.DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(SCHEMA)
        yield conn
    finally:
        conn.close()


def row(r: sqlite3.Row | None) -> dict | None:
    if r is None:
        return None
    out = {k: r[k] for k in r.keys()}
    for key in ("features_json", "baseline_json", "current_probs", "hazard_json"):
        if key in out:
            out[key.replace("_json", "")] = json.loads(out.pop(key)) if out[key] else None
    return out


# ------------------------------------------------------------------ settings

def get_setting(conn, key: str, default=None):
    r = conn.execute("SELECT value FROM ops_setting WHERE key = ?", (key,)).fetchone()
    return json.loads(r["value"]) if r else default


def set_setting(conn, key: str, value) -> None:
    conn.execute("INSERT INTO ops_setting(key, value, updated_at) VALUES (?,?,?) "
                 "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                 (key, json.dumps(value), utcnow()))
    conn.commit()


# ------------------------------------------------------------------ residents

def insert_resident(conn, data: dict) -> dict:
    now = utcnow()
    cols = [c for c in RESIDENT_FIELDS if c in data] + ["barangay", "features_json", "baseline_risk", "baseline_json",
                                                        "is_demo", "created_at", "updated_at"]
    vals = [data.get(c) for c in RESIDENT_FIELDS if c in data] + [
        data.get("barangay"), json.dumps(data.get("features")), data.get("baseline_risk"),
        json.dumps(data.get("baseline")), int(bool(data.get("is_demo"))), now, now]
    cur = conn.execute(f"INSERT INTO resident ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", vals)
    rid = cur.lastrowid
    conn.execute("UPDATE resident SET ref_code = ? WHERE resident_id = ?", (f"RES-{rid:05d}", rid))
    conn.commit()
    return get_resident(conn, rid)


def get_resident(conn, rid: int) -> dict | None:
    return row(conn.execute("SELECT * FROM resident WHERE resident_id = ?", (rid,)).fetchone())


def list_residents(conn, barangay: str | None = None, risk: str | None = None, q: str | None = None,
                   evac_status: str | None = None, limit: int = 5000) -> list[dict]:
    sql, args = "SELECT * FROM resident WHERE 1=1", []
    if barangay:
        sql += " AND barangay = ?"; args.append(barangay)
    if risk:
        sql += " AND current_risk = ?"; args.append(risk)
    if evac_status:
        sql += " AND evac_status = ?"; args.append(evac_status)
    if q:
        sql += " AND (full_name LIKE ? OR ref_code LIKE ? OR address_text LIKE ? OR contact_number LIKE ?)"
        args += [f"%{q}%"] * 4
    sql += " ORDER BY priority_score DESC NULLS LAST, resident_id LIMIT ?"
    args.append(limit)
    return [row(r) for r in conn.execute(sql, args).fetchall()]


def update_resident(conn, rid: int, fields: dict) -> dict | None:
    allowed = set(RESIDENT_FIELDS) | {"barangay", "features_json", "baseline_risk", "baseline_json", "current_risk",
                                      "current_probs", "scenario_stamp", "priority_score"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if updates:
        updates["updated_at"] = utcnow()
        conn.execute(f"UPDATE resident SET {', '.join(f'{k} = ?' for k in updates)} WHERE resident_id = ?",
                     (*updates.values(), rid))
        conn.commit()
    return get_resident(conn, rid)


def delete_resident(conn, rid: int) -> bool:
    cur = conn.execute("DELETE FROM resident WHERE resident_id = ?", (rid,))
    conn.commit()
    return cur.rowcount > 0


# ------------------------------------------------------------------ incidents

def new_ref() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"         # no 0/O, 1/I: easy to read over the phone
    return "RPT-" + "".join(secrets.choice(alphabet) for _ in range(6))


def insert_incident(conn, data: dict) -> dict:
    now = utcnow()
    cols = ["ref_code", "type", "latitude", "longitude", "barangay", "water_depth", "people_affected", "description",
            "reporter_name", "reporter_contact", "source", "status", "priority", "resident_id", "created_at",
            "updated_at"]
    for _ in range(5):
        data["ref_code"] = new_ref()
        try:
            cur = conn.execute(f"INSERT INTO incident ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                               [data.get(c) for c in cols[:-2]] + [now, now])
            break
        except sqlite3.IntegrityError:
            continue
    else:
        raise RuntimeError("could not allocate a report number")
    log(conn, cur.lastrowid, "reported", f"via {data['source']}")
    return get_incident(conn, cur.lastrowid)


def log(conn, incident_id: int, action: str, note: str | None = None) -> None:
    conn.execute("INSERT INTO incident_log(incident_id, at, action, note) VALUES (?,?,?,?)",
                 (incident_id, utcnow(), action, note))
    conn.commit()


def get_incident(conn, iid: int, with_log: bool = False) -> dict | None:
    out = row(conn.execute("SELECT * FROM incident WHERE incident_id = ?", (iid,)).fetchone())
    if out and with_log:
        out["log"] = [row(r) for r in conn.execute(
            "SELECT at, action, note FROM incident_log WHERE incident_id = ? ORDER BY log_id", (iid,))]
    return out


def get_incident_by_ref(conn, ref: str) -> dict | None:
    r = conn.execute("SELECT incident_id FROM incident WHERE ref_code = ?", (ref.strip().upper(),)).fetchone()
    return get_incident(conn, r["incident_id"], with_log=True) if r else None


PRIORITY_ORDER = "CASE priority WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END"


def list_incidents(conn, status: str | None = None, open_only: bool = False, limit: int = 1000) -> list[dict]:
    sql, args = "SELECT * FROM incident WHERE 1=1", []
    if status:
        sql += " AND status = ?"; args.append(status)
    if open_only:
        sql += " AND status NOT IN ('resolved', 'dismissed')"
    sql += f" ORDER BY (status IN ('resolved','dismissed')), {PRIORITY_ORDER}, created_at DESC LIMIT ?"
    args.append(limit)
    return [row(r) for r in conn.execute(sql, args).fetchall()]


def update_incident(conn, iid: int, fields: dict, note: str | None) -> dict | None:
    allowed = {"status", "priority", "assigned_team", "water_depth", "people_affected", "type", "resident_id"}
    updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
    before = get_incident(conn, iid)
    if before is None:
        return None
    if updates:
        updates["updated_at"] = utcnow()
        if updates.get("status") in ("resolved", "dismissed"):
            updates["resolved_at"] = updates["updated_at"]
        conn.execute(f"UPDATE incident SET {', '.join(f'{k} = ?' for k in updates)} WHERE incident_id = ?",
                     (*updates.values(), iid))
        conn.commit()
    changes = [f"{k}: {before.get(k) or '-'} -> {v}" for k, v in updates.items()
               if k not in ("updated_at", "resolved_at") and before.get(k) != v]
    if changes or note:
        log(conn, iid, "; ".join(changes) or "note", note)
    return get_incident(conn, iid, with_log=True)


# ------------------------------------------------------------------ facilities

def list_facilities(conn, type_: str | None = None, active_only: bool = False) -> list[dict]:
    sql, args = "SELECT * FROM facility WHERE 1=1", []
    if type_:
        sql += " AND type = ?"; args.append(type_)
    if active_only:
        sql += " AND is_active = 1"
    sql += " ORDER BY type, name"
    out = [row(r) for r in conn.execute(sql, args).fetchall()]
    counts = dict(conn.execute("SELECT evac_facility_id, SUM(house_members) FROM resident "
                               "WHERE evac_status = 'evacuated' AND evac_facility_id IS NOT NULL "
                               "GROUP BY evac_facility_id").fetchall())
    for f in out:
        f["occupancy"] = int(counts.get(f["facility_id"]) or 0)
    return out


def get_facility(conn, fid: int) -> dict | None:
    return row(conn.execute("SELECT * FROM facility WHERE facility_id = ?", (fid,)).fetchone())


def insert_facility(conn, data: dict) -> dict:
    now = utcnow()
    cur = conn.execute(
        "INSERT OR IGNORE INTO facility(name, type, latitude, longitude, barangay, capacity, is_active, source, osm_id,"
        " hazard_json, notes, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (data["name"], data["type"], data["latitude"], data["longitude"], data.get("barangay"), data.get("capacity"),
         int(data.get("is_active", 1)), data.get("source", "manual"), data.get("osm_id"),
         json.dumps(data.get("hazard")), data.get("notes"), now, now))
    conn.commit()
    return get_facility(conn, cur.lastrowid) if cur.lastrowid else None


def update_facility(conn, fid: int, fields: dict) -> dict | None:
    allowed = {"name", "capacity", "is_active", "notes", "type"}
    updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if updates:
        updates["updated_at"] = utcnow()
        conn.execute(f"UPDATE facility SET {', '.join(f'{k} = ?' for k in updates)} WHERE facility_id = ?",
                     (*updates.values(), fid))
        conn.commit()
    return get_facility(conn, fid)
