"""Active rain scenario and per-resident flood risk under it.

The MDRRMO sets one active rain scenario (24-hour, 3-day and 7-day rain in mm), from:
  * manual entry (e.g. from a PAGASA advisory or a rain gauge),
  * one of the 60 historical rain events used to train the model, or
  * the Open-Meteo forecast for Santa Cruz (free, no key; only the town-centre coordinates are sent).

From the scenario the system derives
  * effective rain = 24 h rain + 30% of the two days before (the rule used to build the training labels),
  * the Project NOAH map used for routing: the smallest return period whose 24-h rain is at least the effective rain
    (5-, 25- or 100-year), i.e. the conservative choice,
  * an operational status (system heuristic, not an official PAGASA/NDRRMC level),
  * every registered resident's risk level with the trained model (one row per resident, rain from the scenario).
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from app.ml_model import CLASSES, _label, _scores, build_frame, load_model, predict_profile, rain_events

# Gumbel fit on the 1991-2025 NASA POWER annual maximum 24-h rain (Activities/AGAP_NOAH_Dataset/generation_metadata.json)
RETURN_LEVELS = {"2yr": 96.8, "5yr": 132.6, "25yr": 186.2, "100yr": 230.4}
TOWN_CENTRE = (14.2818, 121.4144)   # Santa Cruz town hall
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

STATUS = [  # (minimum effective rain mm, key, label, guidance)
    (0, "normal", "Normal", "Routine monitoring."),
    (50, "watch", "Watch", "Heavy rain. Monitor low-lying barangays and pre-position teams."),
    (RETURN_LEVELS["5yr"], "warning", "Warning",
     "Rain at or above the 5-year level. Prepare evacuation centres; pre-emptive evacuation of High-risk households."),
    (RETURN_LEVELS["25yr"], "severe", "Severe",
     "Rain at or above the 25-year level. Forced evacuation of High-risk households and those with PWD/elderly."),
    (RETURN_LEVELS["100yr"], "extreme", "Extreme", "Rain at or above the 100-year level. Full response."),
]


def effective_rain(r24: float, r3: float) -> float:
    return r24 + 0.30 * max(0.0, r3 - r24)


def derive(s: dict) -> dict:
    """Fill in the derived fields of a scenario."""
    eff = effective_rain(s["Rainfall24hMm"], s["Rainfall3DayMm"])
    rp = "5yr" if eff <= RETURN_LEVELS["5yr"] else "25yr" if eff <= RETURN_LEVELS["25yr"] else "100yr"
    status = [st for st in STATUS if eff >= st[0]][-1]
    return {**s, "EffectiveRainMm": round(eff, 1), "hazard_map": rp,
            "rain_factor": round(min(1.0, eff / RETURN_LEVELS["25yr"]), 3),
            "status": status[1], "status_label": status[2], "guidance": status[3],
            "hazard_map_note": f"Routing uses the NOAH {rp} flood map (smallest return period at or above the "
                               f"effective rain of {eff:.0f} mm; conservative)."}


DEFAULT = {"Rainfall24hMm": 0.0, "Rainfall3DayMm": 0.0, "Rainfall7DayMm": 0.0, "source": "default",
           "label": "No active rain scenario", "set_at": None}


def make(r24: float, r3: float, r7: float, source: str, label: str) -> dict:
    if not (0 <= r24 <= r3 <= r7):
        raise ValueError("Rain totals must satisfy 0 <= 24-hour <= 3-day <= 7-day.")
    if r7 > 2000:
        raise ValueError("7-day rain above 2000 mm is not plausible.")
    return derive({"Rainfall24hMm": round(r24, 1), "Rainfall3DayMm": round(r3, 1), "Rainfall7DayMm": round(r7, 1),
                   "source": source, "label": label,
                   "set_at": datetime.now(timezone.utc).isoformat(timespec="seconds")})


def historical_events() -> list[dict]:
    return [{"EventDate": e["EventDate"], "EventType": e["EventType"], "Rainfall24hMm": e["Rainfall24hMm"],
             "Rainfall3DayMm": e["Rainfall3DayMm"], "Rainfall7DayMm": e["Rainfall7DayMm"]} for e in rain_events()]


def forecast(fetch=None) -> dict:
    """Wettest day of the next three days in the Open-Meteo forecast, as a scenario (not saved).

    3-day and 7-day totals end on that day and include the observed/modelled rain of the past days."""
    params = {"latitude": TOWN_CENTRE[0], "longitude": TOWN_CENTRE[1], "daily": "precipitation_sum",
              "past_days": 7, "forecast_days": 7, "timezone": "Asia/Manila"}
    url = FORECAST_URL + "?" + urllib.parse.urlencode(params)
    if fetch is None:
        def fetch(u):
            req = urllib.request.Request(u, headers={"User-Agent": "flood-risk-ops/1.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode())
    data = fetch(url)
    days = data["daily"]["time"]
    rain = [float(x or 0.0) for x in data["daily"]["precipitation_sum"]]
    today = (datetime.now(timezone.utc) + timedelta(hours=8)).date().isoformat()
    start = days.index(today) if today in days else 7
    candidates = []
    for i in range(start, min(start + 3, len(days))):
        r24, r3, r7 = rain[i], sum(rain[max(0, i - 2):i + 1]), sum(rain[max(0, i - 6):i + 1])
        candidates.append({"date": days[i], "Rainfall24hMm": round(r24, 1), "Rainfall3DayMm": round(r3, 1),
                           "Rainfall7DayMm": round(r7, 1), "EffectiveRainMm": round(effective_rain(r24, r3), 1)})
    best = max(candidates, key=lambda c: c["EffectiveRainMm"])
    return {"scenario": make(best["Rainfall24hMm"], best["Rainfall3DayMm"], best["Rainfall7DayMm"], "forecast",
                             f"Open-Meteo forecast, wettest of next 3 days ({best['date']})"),
            "days": [{"date": d, "rain_mm": r} for d, r in zip(days, rain)], "candidates": candidates,
            "note": "Forecast model rain (Open-Meteo) is not the same source as the NASA POWER rain the model was "
                    "trained on; check against PAGASA advisories before acting."}


# ------------------------------------------------------------------ resident risk

def household_of(r: dict) -> dict:
    return {"Age": r["age"], "HousingMaterial": r["housing_material"], "HouseMembers": r["house_members"],
            "ChildrenCount": r["children_count"], "ElderCount": r["elder_count"], "PWDCount": r["pwd_count"]}


def baseline(features: dict, household: dict) -> tuple[str | None, dict]:
    """Risk across the 60 historical rain events (the same profile the self-assessment page shows)."""
    p = predict_profile(features, household)
    return p.get("RiskLevel"), {k: p.get(k) for k in ("extreme_counts", "all_counts", "worst", "version")}


def priority(risk: str | None, probs: dict | None, r: dict) -> float:
    """Evacuation priority (higher first): predicted risk first, then who needs help to leave.

    The weights are an operational ranking rule for ordering a list, not a model output."""
    score = {"High": 200, "Medium": 100, "Low": 0}.get(risk or "", 0)
    score += 50 * (probs or {}).get("High", 0) + 15 * (probs or {}).get("Medium", 0)
    score += 12 * r["pwd_count"] + 8 * r["elder_count"] + 5 * r["children_count"]
    score += {"LightMaterials": 15, "SemiConcrete": 5}.get(r["housing_material"], 0)
    score += 10 if (r.get("special_needs") or "").strip() else 0
    return round(score, 1)


def predict_residents(residents: list[dict], scenario: dict) -> list[dict]:
    """Risk of every resident under the scenario: one model call for all of them."""
    if not residents:
        return []
    model, meta = load_model()
    rain = {k: scenario[k] for k in ("Rainfall24hMm", "Rainfall3DayMm", "Rainfall7DayMm")}
    import pandas as pd
    frames = []
    for r in residents:
        frame, _, _ = build_frame(r.get("features") or {}, household_of(r), meta, [rain])
        frames.append(frame)
    frame = pd.concat(frames, ignore_index=True)
    for col in meta["categorical"]:
        frame[col] = pd.Categorical(frame[col], categories=meta["categories"][col])
    out = []
    for r, s in zip(residents, _scores(model, frame)):
        level = _label(s)
        probs = {c: round(float(s[c]), 4) for c in CLASSES}
        out.append({"resident_id": r["resident_id"], "current_risk": level, "current_probs": probs,
                    "priority_score": priority(level, probs, r)})
    return out
