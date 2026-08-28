"""A* rescue routing on an occupancy grid.

This is the legitimate home for A* in this system: getting a ground team from
the staging point to a detected victim while avoiding water and debris. It is
point-to-point with obstacles, which is exactly the problem A* solves.
"""

from __future__ import annotations

import heapq
import math

import numpy as np
from shapely.geometry import Point, Polygon
from shapely.prepared import prep

from .geo import LocalENU

SQRT2 = math.sqrt(2.0)
NEIGHBOURS = [(-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (-1, 1), (1, -1), (1, 1)]


class OccupancyGrid:
    """Rasterised obstacle map over a local ENU bounding box."""

    def __init__(self, bounds, obstacles_ll, enu: LocalENU, resolution: float = 4.0,
                 inflate: float = 3.0):
        self.res = resolution
        self.min_e, self.min_n, self.max_e, self.max_n = bounds
        self.w = max(1, int((self.max_e - self.min_e) / resolution) + 1)
        self.h = max(1, int((self.max_n - self.min_n) / resolution) + 1)
        self.grid = np.zeros((self.h, self.w), dtype=bool)

        polys = []
        for ring in obstacles_ll:
            p = Polygon([enu.to_enu(lat, lon) for lat, lon in ring])
            if not p.is_valid:
                p = p.buffer(0)
            if not p.is_empty:
                # inflate so routes keep clearance rather than shaving corners
                polys.append(prep(p.buffer(inflate)))

        if polys:
            for r in range(self.h):
                n = self.min_n + r * resolution
                for c in range(self.w):
                    pt = Point(self.min_e + c * resolution, n)
                    if any(p.contains(pt) for p in polys):
                        self.grid[r, c] = True

    def to_cell(self, e, n):
        c = int(round((e - self.min_e) / self.res))
        r = int(round((n - self.min_n) / self.res))
        return max(0, min(self.h - 1, r)), max(0, min(self.w - 1, c))

    def to_world(self, r, c):
        return self.min_e + c * self.res, self.min_n + r * self.res

    def blocked(self, r, c) -> bool:
        return bool(self.grid[r, c])

    def nearest_free(self, r, c, radius: int = 12):
        """Snap a start/goal that landed inside an obstacle to open ground."""
        if not self.blocked(r, c):
            return r, c
        for rad in range(1, radius + 1):
            for dr in range(-rad, rad + 1):
                for dc in range(-rad, rad + 1):
                    if max(abs(dr), abs(dc)) != rad:
                        continue
                    rr, cc = r + dr, c + dc
                    if 0 <= rr < self.h and 0 <= cc < self.w and not self.blocked(rr, cc):
                        return rr, cc
        return None


def _octile(a, b):
    dr, dc = abs(a[0] - b[0]), abs(a[1] - b[1])
    return (dr + dc) + (SQRT2 - 2.0) * min(dr, dc)


def astar(grid: OccupancyGrid, start_en, goal_en):
    """Shortest obstacle-free path. Returns ENU waypoints, [] if unreachable."""
    start = grid.nearest_free(*grid.to_cell(*start_en))
    goal = grid.nearest_free(*grid.to_cell(*goal_en))
    if start is None or goal is None:
        return []

    open_heap = [(_octile(start, goal), 0.0, start)]
    came: dict = {start: None}
    cost = {start: 0.0}
    closed = set()

    while open_heap:
        _, g, cur = heapq.heappop(open_heap)
        if cur in closed:
            continue
        closed.add(cur)
        if cur == goal:
            path, node = [], cur
            while node is not None:
                path.append(grid.to_world(*node))
                node = came[node]
            return path[::-1]
        r, c = cur
        for dr, dc in NEIGHBOURS:
            rr, cc = r + dr, c + dc
            if not (0 <= rr < grid.h and 0 <= cc < grid.w) or grid.blocked(rr, cc):
                continue
            # forbid cutting the corner between two blocked orthogonal cells
            if dr and dc and (grid.blocked(r, cc) or grid.blocked(rr, c)):
                continue
            step = (SQRT2 if dr and dc else 1.0) * grid.res
            ng = g + step
            if ng < cost.get((rr, cc), float("inf")):
                cost[(rr, cc)] = ng
                came[(rr, cc)] = cur
                heapq.heappush(open_heap, (ng + _octile((rr, cc), goal) * grid.res, ng, (rr, cc)))
    return []


def simplify(path, tol: float = 2.0):
    """Drop collinear intermediate points so the map draws a clean polyline."""
    if len(path) < 3:
        return path
    out = [path[0]]
    for i in range(1, len(path) - 1):
        ax, ay = out[-1]
        bx, by = path[i]
        cx, cy = path[i + 1]
        cross = abs((bx - ax) * (cy - ay) - (by - ay) * (cx - ax))
        seg = math.hypot(cx - ax, cy - ay) or 1.0
        if cross / seg > tol:
            out.append(path[i])
    out.append(path[-1])
    return out
