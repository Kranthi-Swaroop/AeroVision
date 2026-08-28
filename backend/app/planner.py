"""Coverage path planning: boustrophedon (lawnmower) decomposition.

Deliberately NOT A*. A* is a point-to-point shortest-path search; it has no
notion of "visit every part of this region". Area coverage is a different
problem and boustrophedon decomposition is its standard solution. A* is used
in this project too, but for rescue routing (see astar.py), which is genuinely
a point-to-point problem.
"""

from __future__ import annotations

import math

import numpy as np
from shapely.geometry import LineString, Polygon
from shapely.ops import unary_union

from .geo import Intrinsics, LocalENU, swath_width


def _longest_edge_angle(poly: Polygon) -> float:
    """Angle (rad) of the longest edge of the minimum rotated rectangle.

    Sweeping parallel to it minimises the number of turns, which matters
    because turns cost battery and produce motion blur.
    """
    rect = list(poly.minimum_rotated_rectangle.exterior.coords)[:4]
    best, best_len = 0.0, -1.0
    for i in range(4):
        (x1, y1), (x2, y2) = rect[i], rect[(i + 1) % 4]
        d = math.hypot(x2 - x1, y2 - y1)
        if d > best_len:
            best_len, best = d, math.atan2(y2 - y1, x2 - x1)
    return best


def _rot(pts: np.ndarray, theta: float) -> np.ndarray:
    c, s = math.cos(theta), math.sin(theta)
    return pts @ np.array([[c, s], [-s, c]])


def plan_coverage(
    polygon_ll: list[tuple[float, float]],
    enu: LocalENU,
    alt: float,
    intr: Intrinsics,
    sidelap: float = 0.25,
    min_leg_m: float = 5.0,
):
    """Plan a serpentine scan of `polygon_ll` (list of (lat, lon)).

    Returns dict with waypoints in ENU and lat/lon, plus the derived geometry
    the dashboard shows so the numbers on screen are the numbers actually used.
    """
    pts = np.array([enu.to_enu(lat, lon) for lat, lon in polygon_ll])
    poly = Polygon(pts)
    if not poly.is_valid:
        poly = poly.buffer(0)
    if poly.is_empty or poly.area < 1.0:
        return {"waypoints_enu": [], "waypoints_ll": [], "spacing_m": 0.0,
                "swath_m": 0.0, "area_m2": 0.0, "length_m": 0.0, "legs": 0}

    swath = swath_width(alt, intr)
    spacing = swath * (1.0 - sidelap)

    theta = _longest_edge_angle(poly)
    rpts = _rot(pts, -theta)
    rpoly = Polygon(rpts)
    minx, miny, maxx, maxy = rpoly.bounds

    # sweep lines run along x, stepping in y; start half a spacing in so the
    # first and last legs sit inside the region rather than on its edge
    ys = np.arange(miny + spacing / 2.0, maxy, spacing)
    if len(ys) == 0:
        ys = np.array([(miny + maxy) / 2.0])

    legs: list[list[tuple[float, float]]] = []
    for i, y in enumerate(ys):
        line = LineString([(minx - 10.0, y), (maxx + 10.0, y)])
        clipped = rpoly.intersection(line)
        if clipped.is_empty:
            continue
        parts = clipped.geoms if hasattr(clipped, "geoms") else [clipped]
        segs = []
        for part in parts:
            if part.geom_type != "LineString" or part.length < min_leg_m:
                continue
            (x1, y1), (x2, y2) = part.coords[0], part.coords[-1]
            if x1 > x2:
                (x1, y1), (x2, y2) = (x2, y2), (x1, y1)
            segs.append([(x1, y1), (x2, y2)])
        segs.sort(key=lambda s: s[0][0])
        if i % 2 == 1:  # serpentine: alternate direction each leg
            segs = [[s[1], s[0]] for s in reversed(segs)]
        legs.extend(segs)

    rway = [p for seg in legs for p in seg]
    if not rway:
        return {"waypoints_enu": [], "waypoints_ll": [], "spacing_m": spacing,
                "swath_m": swath, "area_m2": poly.area, "length_m": 0.0, "legs": 0}

    way = _rot(np.array(rway), theta)
    length = float(sum(math.dist(way[i], way[i + 1]) for i in range(len(way) - 1)))

    return {
        "waypoints_enu": [(float(e), float(n)) for e, n in way],
        "waypoints_ll": [enu.to_ll(float(e), float(n)) for e, n in way],
        "spacing_m": float(spacing),
        "swath_m": float(swath),
        "area_m2": float(poly.area),
        "length_m": length,
        "legs": len(legs),
    }


def coverage_fraction(target_ll, footprints_ll, enu: LocalENU) -> float:
    """Fraction of the drawn polygon actually imaged so far.

    Computed from the union of real camera footprints, not from "waypoints
    visited", so an aborted mission reports honestly.
    """
    tgt = Polygon([enu.to_enu(lat, lon) for lat, lon in target_ll])
    if tgt.is_empty or tgt.area <= 0:
        return 0.0
    shot = []
    for fp in footprints_ll:
        p = Polygon([enu.to_enu(lat, lon) for lat, lon in fp])
        if p.is_valid and not p.is_empty:
            shot.append(p)
    if not shot:
        return 0.0
    return float(unary_union(shot).intersection(tgt).area / tgt.area)
