"""Verify the geometry before trusting any number the dashboard prints.

Runs without torch, so it is safe to run anywhere.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.astar import OccupancyGrid, astar                     # noqa: E402
from app.fusion import VictimFuser                             # noqa: E402
from app.geo import (Intrinsics, LocalENU, Pose, footprint,    # noqa: E402
                     ground_homography, ground_to_image,
                     haversine_m, image_to_ground)
from app.planner import coverage_fraction, plan_coverage       # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{'  ' + detail if detail else ''}")
    if not cond:
        FAILS.append(name)


def test_enu():
    print("\nlocal tangent plane")
    enu = LocalENU(21.2514, 81.6296)
    for e, n in [(0, 0), (150, -220), (-40, 90)]:
        lat, lon = enu.to_ll(e, n)
        e2, n2 = enu.to_enu(lat, lon)
        check(f"round-trip ({e},{n})", abs(e - e2) < 1e-6 and abs(n - n2) < 1e-6)
    lat, lon = enu.to_ll(100.0, 0.0)
    d = haversine_m(21.2514, 81.6296, lat, lon)
    check("100 m east measures 100 m", abs(d - 100.0) < 0.05, f"got {d:.4f} m")


def test_projection():
    print("\ncamera projection")
    intr = Intrinsics(960, 540, 62.2)
    for tilt in (0.0, 15.0, 25.0, 40.0):
        for heading in (0.0, 37.0, 180.0, 300.0):
            pose = Pose(120.0, -80.0, 40.0, heading, tilt)
            worst = 0.0
            for u, v in [(480, 270), (100, 100), (860, 460), (300, 500), (700, 60)]:
                g = image_to_ground(u, v, pose, intr)
                if g is None:
                    continue
                back = ground_to_image(g[0], g[1], pose, intr)
                worst = max(worst, math.dist((u, v), back))
            check(f"pixel round-trip tilt={tilt:.0f} hdg={heading:.0f}",
                  worst < 1e-6, f"max {worst:.2e} px")

    print("\n  nadir centre ray lands directly beneath the camera")
    pose = Pose(50.0, 30.0, 40.0, 0.0, 0.0)
    g = image_to_ground(480, 270, pose, intr)
    check("nadir centre = camera ground position",
          abs(g[0] - 50.0) < 1e-9 and abs(g[1] - 30.0) < 1e-9)

    print("\n  oblique centre ray lands forward by alt*tan(tilt)")
    pose = Pose(0.0, 0.0, 40.0, 0.0, 25.0)
    g = image_to_ground(480, 270, pose, intr)
    expect = 40.0 * math.tan(math.radians(25.0))
    check("forward offset", abs(g[1] - expect) < 1e-6, f"{g[1]:.4f} vs {expect:.4f} m")
    check("no lateral drift", abs(g[0]) < 1e-9)

    print("\n  heading 90 deg points the centre ray east")
    pose = Pose(0.0, 0.0, 40.0, 90.0, 25.0)
    g = image_to_ground(480, 270, pose, intr)
    check("east offset", abs(g[0] - expect) < 1e-6 and abs(g[1]) < 1e-6,
          f"({g[0]:.3f}, {g[1]:.3f})")

    print("\n  nadir ground sample distance matches alt/f")
    pose = Pose(0.0, 0.0, 40.0, 0.0, 0.0)
    a = image_to_ground(480, 270, pose, intr)
    b = image_to_ground(481, 270, pose, intr)
    gsd = math.dist(a, b)
    check("gsd = alt/fx", abs(gsd - 40.0 / intr.fx) < 1e-9, f"{gsd*100:.2f} cm/px")

    print("\n  homography agrees with the ray method")
    pose = Pose(10.0, -20.0, 35.0, 47.0, 22.0)
    h = ground_homography(pose, intr)
    worst = 0.0
    for e, n in [(10.0, 0.0), (40.0, -30.0), (-15.0, 5.0)]:
        p = h @ np.array([e, n, 1.0])
        if p[2] <= 0:
            continue
        worst = max(worst, math.dist((p[0] / p[2], p[1] / p[2]),
                                     ground_to_image(e, n, pose, intr)))
    check("H matches projection", worst < 1e-8, f"max {worst:.2e} px")


def test_footprint():
    print("\nfootprint")
    intr = Intrinsics(960, 540, 62.2)
    pose = Pose(0.0, 0.0, 40.0, 0.0, 0.0)
    fp = footprint(pose, intr)
    width = math.dist(fp[0], fp[1])
    expect = 2 * 40.0 * math.tan(math.radians(62.2 / 2))
    check("nadir width = 2*alt*tan(hfov/2)", abs(width - expect) < 1e-6,
          f"{width:.2f} m")
    check("footprint is centred", abs(sum(p[0] for p in fp) / 4) < 1e-9)


def test_planner():
    print("\ncoverage planner")
    enu = LocalENU(21.2514, 81.6296)
    intr = Intrinsics(960, 540, 62.2)
    corners = [enu.to_ll(0, 0), enu.to_ll(200, 0), enu.to_ll(200, -160), enu.to_ll(0, -160)]
    poly = [(lat, lon) for lat, lon in corners]

    plan = plan_coverage(poly, enu, 40.0, intr, sidelap=0.25)
    swath = 2 * 40.0 * math.tan(math.radians(62.2 / 2))
    check("swath from optics", abs(plan["swath_m"] - swath) < 1e-6, f"{plan['swath_m']:.1f} m")
    check("spacing applies sidelap", abs(plan["spacing_m"] - swath * 0.75) < 1e-6)
    check("area recovered", abs(plan["area_m2"] - 200 * 160) < 1.0,
          f"{plan['area_m2']:.0f} m2")
    check("waypoints generated", len(plan["waypoints_enu"]) >= 4,
          f"{len(plan['waypoints_enu'])} wp, {plan['legs']} legs")
    check("path longer than one crossing", plan["length_m"] > 200,
          f"{plan['length_m']:.0f} m")

    print("\n  flying higher needs fewer legs")
    high = plan_coverage(poly, enu, 70.0, intr, sidelap=0.25)
    check("legs decrease with altitude", high["legs"] < plan["legs"],
          f"{plan['legs']} -> {high['legs']}")

    print("\n  coverage fraction from real footprints")
    covered = coverage_fraction(poly, [[list(enu.to_ll(e, n)) for e, n in
                                        [(0, 0), (200, 0), (200, -160), (0, -160)]]], enu)
    check("full footprint = 100%", abs(covered - 1.0) < 0.02, f"{covered*100:.1f}%")
    half = coverage_fraction(poly, [[list(enu.to_ll(e, n)) for e, n in
                                     [(0, 0), (100, 0), (100, -160), (0, -160)]]], enu)
    check("half footprint = 50%", abs(half - 0.5) < 0.02, f"{half*100:.1f}%")


def test_fusion():
    print("\nvictim fusion")
    enu = LocalENU(21.2514, 81.6296)
    f = VictimFuser(merge_radius_m=6.0, confirm_at=3)
    base = enu.to_ll(50.0, -50.0)
    for i in range(8):  # same person, eight overlapping frames, small jitter
        lat, lon = enu.to_ll(50.0 + (i % 3) * 0.8, -50.0 - (i % 2) * 0.6)
        f.add(lat, lon, 0.6 + 0.03 * i)
    check("eight sightings collapse to one track", len(f.tracks) == 1,
          f"{len(f.tracks)} tracks")
    check("track is confirmed", len(f.confirmed()) == 1)

    far = enu.to_ll(90.0, -50.0)
    f.add(far[0], far[1], 0.8)
    check("a person 40 m away is separate", len(f.tracks) == 2)
    check("single sighting stays provisional", len(f.confirmed()) == 1)

    t = f.tracks["v1"]
    err = haversine_m(t.lat, t.lon, base[0], base[1])
    check("fused position near truth", err < 2.0, f"{err:.2f} m")


def test_astar():
    print("\nA* rescue routing")
    enu = LocalENU(21.2514, 81.6296)
    wall = [enu.to_ll(e, n) for e, n in
            [(40, -10), (60, -10), (60, -200), (40, -200)]]  # river across the middle
    grid = OccupancyGrid((-20, -220, 200, 20), [wall], enu, resolution=4.0, inflate=2.0)
    path = astar(grid, (0.0, -100.0), (150.0, -100.0))
    check("route found around water", len(path) > 2, f"{len(path)} nodes")
    if path:
        blocked = any(grid.blocked(*grid.to_cell(e, n)) for e, n in path)
        check("route avoids obstacles", not blocked)
        straight = math.dist((0, -100), (150, -100))
        length = sum(math.dist(path[i], path[i + 1]) for i in range(len(path) - 1))
        check("detour longer than straight line", length > straight,
              f"{length:.0f} m vs {straight:.0f} m direct")

    boxed = [enu.to_ll(e, n) for e, n in
             [(-20, 20), (200, 20), (200, -220), (-20, -220)]]
    grid2 = OccupancyGrid((-20, -220, 200, 20), [boxed], enu, resolution=8.0, inflate=0.0)
    check("fully blocked map returns no route", astar(grid2, (0.0, -100.0), (150.0, -100.0)) == [])


if __name__ == "__main__":
    test_enu()
    test_projection()
    test_footprint()
    test_planner()
    test_fusion()
    test_astar()
    print("\n" + "=" * 44)
    if FAILS:
        print(f"{len(FAILS)} FAILED: {', '.join(FAILS)}")
        sys.exit(1)
    print("all geometry checks passed")
