"""Priority scoring for detected victims.

This is a transparent heuristic, not a medical assessment, and it should be
described that way. It ranks who a limited rescue team should reach first
using only signals the system can actually observe.
"""

from __future__ import annotations

from shapely.geometry import Point, Polygon

from .geo import LocalENU, haversine_m

WEIGHTS = {"detection": 0.30, "persistence": 0.20, "hazard": 0.30, "isolation": 0.20}


def score_victims(tracks, enu: LocalENU, water_ll, base_ll, confirm_at: int = 3):
    """Return victims ordered most-urgent-first with a score breakdown."""
    water = []
    for ring in water_ll:
        p = Polygon([enu.to_enu(lat, lon) for lat, lon in ring])
        if not p.is_valid:
            p = p.buffer(0)
        if not p.is_empty:
            water.append(p)

    dists = [haversine_m(t.lat, t.lon, base_ll[0], base_ll[1]) for t in tracks] or [1.0]
    far = max(dists) or 1.0

    out = []
    for t, dist in zip(tracks, dists):
        pt = Point(*enu.to_enu(t.lat, t.lon))
        in_water = any(w.contains(pt) for w in water)

        parts = {
            # how sure the model is
            "detection": min(1.0, t.best_conf),
            # seen across many frames -> almost certainly a real person
            "persistence": min(1.0, t.sightings / (confirm_at * 2.0)),
            # standing in floodwater is the single most time-critical signal
            "hazard": 1.0 if in_water else 0.15,
            # far from staging means longer to reach, so start moving sooner
            "isolation": min(1.0, dist / far),
        }
        score = sum(WEIGHTS[k] * v for k, v in parts.items())
        level = "critical" if score >= 0.7 else "high" if score >= 0.5 else "moderate"

        d = t.as_dict(confirm_at)
        d.update({
            "priority": round(score, 3),
            "priority_level": level,
            "in_water": in_water,
            "distance_from_base_m": round(dist, 1),
            "score_breakdown": {k: round(v, 3) for k, v in parts.items()},
        })
        out.append(d)

    out.sort(key=lambda d: d["priority"], reverse=True)
    for i, d in enumerate(out, 1):
        d["rank"] = i
    return out
