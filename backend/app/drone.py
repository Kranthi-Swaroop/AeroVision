"""Drone kinematics: a waypoint follower with a real battery budget.

Endurance and speed are taken from the flight-tested S500 airframe, so the
simulated mission is bounded by the same limits as the physical one. A scan
area too large for the battery aborts to return-to-launch mid-mission, and the
dashboard reports partial coverage instead of pretending the job finished.
"""

from __future__ import annotations

import math

from .geo import LocalENU, Pose


class DroneSim:
    def __init__(self, enu: LocalENU, alt: float = 40.0, tilt: float = 25.0,
                 speed: float = 6.0, yaw_rate: float = 60.0,
                 endurance_s: float = 780.0, reserve_frac: float = 0.20):
        self.enu = enu
        self.alt = alt
        self.tilt = tilt
        self.speed = speed              # m/s cruise
        self.yaw_rate = yaw_rate        # deg/s
        self.endurance_s = endurance_s  # 13 min, mid-point of measured 10-15
        self.reserve = reserve_frac

        self.east = 0.0
        self.north = 0.0
        self.heading = 0.0
        self.battery = 1.0
        self.waypoints: list[tuple[float, float]] = []
        self.index = 0
        self.state = "idle"     # idle | scanning | rtl | landed
        self.distance_m = 0.0
        self.elapsed_s = 0.0
        self.home = (0.0, 0.0)

    # ------------------------------------------------------------------
    def arm(self, waypoints, home=None):
        self.waypoints = list(waypoints)
        self.index = 0
        self.home = tuple(home) if home else (self.east, self.north)
        if self.waypoints:
            self.east, self.north = self.waypoints[0]
            self.state = "scanning"

    @property
    def pose(self) -> Pose:
        return Pose(self.east, self.north, self.alt, self.heading, self.tilt)

    @property
    def target(self):
        if self.state == "rtl":
            return self.home
        if self.index < len(self.waypoints):
            return self.waypoints[self.index]
        return None

    def _turn_toward(self, tgt, dt):
        desired = math.degrees(math.atan2(tgt[0] - self.east, tgt[1] - self.north)) % 360.0
        err = (desired - self.heading + 180.0) % 360.0 - 180.0
        step = self.yaw_rate * dt
        self.heading = (self.heading + max(-step, min(step, err))) % 360.0
        return abs(err)

    def step(self, dt: float):
        """Advance one tick. Returns True while still flying."""
        if self.state in ("idle", "landed"):
            return False

        self.elapsed_s += dt
        self.battery = max(0.0, self.battery - dt / self.endurance_s)

        if self.state == "scanning" and self.battery <= self.reserve:
            self.state = "rtl"

        tgt = self.target
        if tgt is None:
            self.state = "rtl" if self.state == "scanning" else "landed"
            return self.state != "landed"

        err = self._turn_toward(tgt, dt)
        # bleed speed through hard turns, the way a real multirotor does
        v = self.speed * (0.35 if err > 60.0 else 1.0)
        dist = math.hypot(tgt[0] - self.east, tgt[1] - self.north)
        travel = min(v * dt, dist)
        if dist > 1e-6:
            self.east += (tgt[0] - self.east) / dist * travel
            self.north += (tgt[1] - self.north) / dist * travel
            self.distance_m += travel

        if dist - travel < 1.0:
            if self.state == "rtl":
                self.state = "landed"
                return False
            self.index += 1
            if self.index >= len(self.waypoints):
                self.state = "rtl"
        return True

    def telemetry(self):
        lat, lon = self.enu.to_ll(self.east, self.north)
        total = max(1, len(self.waypoints))
        return {
            "lat": round(lat, 7), "lon": round(lon, 7),
            "alt": self.alt, "heading": round(self.heading, 1), "tilt": self.tilt,
            "battery": round(self.battery, 4),
            "state": self.state,
            "waypoint_index": self.index,
            "waypoint_total": len(self.waypoints),
            "waypoint_pct": round(100.0 * min(self.index, total) / total, 1),
            "distance_m": round(self.distance_m, 1),
            "elapsed_s": round(self.elapsed_s, 1),
            "eta_s": round(max(0.0, (self.endurance_s * (self.battery - self.reserve))), 0),
        }
