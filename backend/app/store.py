"""Mission logging and export.

Every mission is written to SQLite and can be exported as GeoJSON or CSV. The
export is the artefact a rescue coordinator would actually be handed, so it is
worth showing in the demo rather than leaving as an implied capability.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

DB_PATH = Path("missions.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS missions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at REAL, ended_at REAL, scene TEXT,
    area_m2 REAL, coverage_pct REAL, victims INTEGER, report TEXT
);
"""


def _conn():
    c = sqlite3.connect(DB_PATH)
    c.execute(SCHEMA)
    return c


def save(report: dict) -> int:
    c = _conn()
    cur = c.execute(
        "INSERT INTO missions (started_at, ended_at, scene, area_m2, coverage_pct, victims, report)"
        " VALUES (?,?,?,?,?,?,?)",
        (report.get("started_at"), time.time(), report.get("scene"),
         (report.get("plan") or {}).get("area_m2"), report.get("coverage_pct"),
         len(report.get("victims", [])), json.dumps(report)),
    )
    c.commit()
    mid = cur.lastrowid
    c.close()
    return mid


def list_missions(limit: int = 25):
    c = _conn()
    rows = c.execute(
        "SELECT id, started_at, ended_at, scene, area_m2, coverage_pct, victims"
        " FROM missions ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    c.close()
    keys = ["id", "started_at", "ended_at", "scene", "area_m2", "coverage_pct", "victims"]
    return [dict(zip(keys, r)) for r in rows]


def get(mission_id: int):
    c = _conn()
    row = c.execute("SELECT report FROM missions WHERE id = ?", (mission_id,)).fetchone()
    c.close()
    return json.loads(row[0]) if row else None


def to_geojson(report: dict) -> dict:
    features = []
    for v in report.get("victims", []):
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [v["lon"], v["lat"]]},
            "properties": {k: val for k, val in v.items() if k not in ("lat", "lon", "thumb")},
        })
    return {"type": "FeatureCollection",
            "properties": {"scene": report.get("scene"),
                           "coverage_pct": report.get("coverage_pct"),
                           "accuracy": report.get("accuracy")},
            "features": features}


def to_csv(report: dict) -> str:
    cols = ["rank", "id", "lat", "lon", "status", "priority_level", "priority",
            "confidence", "sightings", "in_water", "distance_from_base_m"]
    lines = [",".join(cols)]
    for v in report.get("victims", []):
        lines.append(",".join(str(v.get(c, "")) for c in cols))
    return "\n".join(lines)
