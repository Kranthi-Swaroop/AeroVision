"""Mission orchestrator: the loop that ties every component together.

Runs at sim_hz, renders a frame at detect_hz, pushes it through YOLO,
back-projects each box to WGS84, fuses it into victim tracks, and broadcasts
telemetry, frames and metrics to connected dashboards.
"""

from __future__ import annotations

import asyncio
import base64
import time

import cv2

from shapely.geometry import Point, Polygon

from .astar import OccupancyGrid, astar, simplify
from .camera import Scene, SyntheticCamera
from .drone import DroneSim
from .fusion import VictimFuser
from .geo import Intrinsics, along_track_length, distance_m, haversine_m
from .planner import coverage_fraction, plan_coverage
from .triage import score_victims

MATCH_RADIUS_M = 15.0  # how close a fused track must be to count as a true positive


class MissionRunner:
    def __init__(self, scene: Scene, settings, detector, broadcast):
        self.scene = scene
        self.cfg = settings
        self.detector = detector
        self.broadcast = broadcast

        self.intr = Intrinsics(settings.img_width, settings.img_height, settings.hfov_deg)
        self.camera = SyntheticCamera(scene, self.intr)
        self.drone = DroneSim(
            scene.enu, alt=settings.altitude_m, tilt=settings.tilt_deg,
            speed=settings.speed_ms, endurance_s=settings.endurance_s,
        )
        self.fuser = VictimFuser(settings.merge_radius_m, settings.confirm_at)

        self.task: asyncio.Task | None = None
        self.plan: dict | None = None
        self.polygon_ll: list = []
        self.footprints: list = []
        self.errors: list[float] = []
        self.truth_in_area: list[dict] = []
        self.started_at: float | None = None
        self.finished = False

    # ------------------------------------------------------------------
    @property
    def running(self) -> bool:
        return self.task is not None and not self.task.done()

    async def start(self, polygon_ll):
        await self.stop()

        self.polygon_ll = [tuple(p) for p in polygon_ll]
        self.plan = plan_coverage(
            self.polygon_ll, self.scene.enu, self.cfg.altitude_m,
            self.intr, sidelap=self.cfg.sidelap,
        )
        if not self.plan["waypoints_enu"]:
            await self.broadcast({"type": "error",
                                  "message": "Area too small for one scan leg at this altitude."})
            return False

        self.fuser = VictimFuser(self.cfg.merge_radius_m, self.cfg.confirm_at)
        self.footprints, self.errors = [], []
        # Only score against people inside the requested scan area. Counting a
        # person the operator never asked us to look for as a miss would make
        # recall a function of where the scene author put bystanders.
        area = Polygon([self.scene.enu.to_enu(la, lo) for la, lo in self.polygon_ll])
        self.truth_in_area = [
            t for t in self.scene.truth
            if area.contains(Point(*self.scene.enu.to_enu(t["lat"], t["lon"])))
        ]
        self.finished = False
        self.started_at = time.time()

        home = self.scene.enu.to_enu(*self.scene.base)
        self.drone.arm(self.plan["waypoints_enu"], home=home)

        await self.broadcast({
            "type": "plan",
            "waypoints": [[lat, lon] for lat, lon in self.plan["waypoints_ll"]],
            "polygon": [[lat, lon] for lat, lon in self.polygon_ll],
            "swath_m": round(self.plan["swath_m"], 1),
            "spacing_m": round(self.plan["spacing_m"], 1),
            "legs": self.plan["legs"],
            "area_m2": round(self.plan["area_m2"], 1),
            "path_length_m": round(self.plan["length_m"], 1),
            "estimated_s": round(self.plan["length_m"] / max(0.1, self.cfg.speed_ms), 0),
        })

        self.task = asyncio.create_task(self._run())
        return True

    async def stop(self):
        if self.task and not self.task.done():
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        self.task = None

    # ------------------------------------------------------------------
    async def _run(self):
        dt = 1.0 / self.cfg.sim_hz
        sim_dt = dt * self.cfg.time_scale
        tick = 0

        # Distance-based camera triggering. Firing on a clock instead couples
        # the number of times a victim is imaged to ground speed and to the
        # demo's time compression, which silently starves the fusion stage.
        trigger_dist = max(
            1.0,
            along_track_length(self.drone.pose, self.intr) * (1.0 - self.cfg.forward_overlap),
        )
        min_interval = 1.0 / max(0.1, self.cfg.detect_hz)  # GPU ceiling
        last_capture_m = -1e9
        last_capture_t = -1e9

        try:
            while True:
                alive = self.drone.step(sim_dt)
                pose = self.drone.pose

                now = time.monotonic()
                due = self.drone.distance_m - last_capture_m >= trigger_dist
                if (due and now - last_capture_t >= min_interval) or last_capture_m < -1e8:
                    last_capture_m = self.drone.distance_m
                    last_capture_t = now
                    await self._process_frame(pose)

                await self.broadcast({
                    "type": "telemetry",
                    "drone": self.drone.telemetry(),
                    "footprint": [list(self.scene.enu.to_ll(e, n))
                                  for e, n in self._footprint_enu(pose)],
                })

                if tick % 10 == 0:
                    await self.broadcast(self._metrics_message())

                if not alive:
                    break
                tick += 1
                await asyncio.sleep(dt)
        except asyncio.CancelledError:
            raise
        finally:
            self.finished = True
            await self.broadcast(self._metrics_message())
            await self.broadcast({"type": "mission_complete",
                                  "report": self.report()})

    def _footprint_enu(self, pose):
        from .geo import footprint
        return footprint(pose, self.intr)

    async def _process_frame(self, pose):
        frame = self.camera.render(pose)
        boxes, latency = await self.detector.detect(frame)

        fp_ll = [list(self.scene.enu.to_ll(e, n)) for e, n in self._footprint_enu(pose)]
        self.footprints.append(fp_ll)

        payload = []
        for b in boxes:
            ll = self.camera.pixel_to_ll(b["anchor_u"], b["anchor_v"], pose)
            if ll is None:
                continue  # detection above the horizon cannot be georeferenced
            lat, lon = ll
            track, is_new = self.fuser.add(lat, lon, b["conf"])
            err = self._truth_error(lat, lon)
            if err is not None:
                self.errors.append(err)
            payload.append({
                "box": [round(b["x1"]), round(b["y1"]), round(b["x2"]), round(b["y2"])],
                "conf": round(b["conf"], 3),
                "lat": round(lat, 7), "lon": round(lon, 7),
                "victim_id": track.vid, "new": is_new,
                "error_m": round(err, 2) if err is not None else None,
            })

        await self.broadcast({
            "type": "frame",
            "jpeg": self._encode(frame),
            "width": self.intr.width, "height": self.intr.height,
            "detections": payload,
            "latency_ms": round(latency, 1),
        })
        if payload:
            await self.broadcast({"type": "victims", "victims": self._victims()})

    def _encode(self, frame) -> str:
        w = self.cfg.stream_width
        if frame.shape[1] > w:
            h = int(frame.shape[0] * w / frame.shape[1])
            frame = cv2.resize(frame, (w, h), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", frame,
                               [cv2.IMWRITE_JPEG_QUALITY, self.cfg.jpeg_quality])
        return base64.b64encode(buf).decode() if ok else ""

    # ------------------------------------------------------------------
    def _truth_error(self, lat, lon):
        """Distance to the nearest ground-truth person, if one is close enough.

        Only meaningful in the synthetic scene, where truth is known. This is
        what lets the demo quote a georeferencing error instead of asserting
        that the coordinates are accurate.
        """
        best = None
        for t in (self.truth_in_area or self.scene.truth):
            d = haversine_m(lat, lon, t["lat"], t["lon"])
            if d <= MATCH_RADIUS_M and (best is None or d < best):
                best = d
        return best

    def _victims(self):
        return score_victims(list(self.fuser.tracks.values()), self.scene.enu,
                             self.scene.water, self.scene.base, self.cfg.confirm_at)

    def accuracy(self):
        """TP / FP / FN of confirmed tracks against ground truth."""
        confirmed = self.fuser.confirmed()
        in_area = self.truth_in_area or self.scene.truth
        in_area_ids = {t["id"] for t in in_area}

        # A confirmed track counts as correct if it matches ANY real person.
        # An oblique camera routinely sees past the boundary of the drawn box,
        # so spotting someone just outside it is a correct detection, not a
        # false alarm -- but it must not inflate recall either, which is scored
        # only over the area the operator actually asked us to search.
        unmatched = list(self.scene.truth)
        tp, outside = 0, 0
        for t in confirmed:
            hit = None
            for gt in unmatched:
                if haversine_m(t.lat, t.lon, gt["lat"], gt["lon"]) <= MATCH_RADIUS_M:
                    hit = gt
                    break
            if hit is not None:
                unmatched.remove(hit)
                tp += 1
                if hit["id"] not in in_area_ids:
                    outside += 1
        fp = len(confirmed) - tp
        fn = sum(1 for gt in unmatched if gt["id"] in in_area_ids)
        tp_in_area = tp - outside
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp_in_area / (tp_in_area + fn) if tp_in_area + fn else 0.0
        errs = sorted(self.errors)
        return {
            "ground_truth": len(self.truth_in_area or self.scene.truth),
            "confirmed": len(confirmed),
            "true_positives": tp, "false_positives": fp, "false_negatives": fn,
            "detected_outside_area": outside,
            "precision": round(prec, 3), "recall": round(rec, 3),
            "f1": round(2 * prec * rec / (prec + rec), 3) if prec + rec else 0.0,
            "mean_geo_error_m": round(sum(errs) / len(errs), 2) if errs else None,
            "p95_geo_error_m": round(errs[int(len(errs) * 0.95)], 2) if len(errs) > 4 else None,
            "max_geo_error_m": round(errs[-1], 2) if errs else None,
            "localisation_samples": len(errs),
        }

    def _metrics_message(self):
        cov = coverage_fraction(self.polygon_ll, self.footprints, self.scene.enu) \
            if self.polygon_ll else 0.0
        return {
            "type": "metrics",
            "detector": self.detector.stats(),
            "accuracy": self.accuracy(),
            "coverage_pct": round(cov * 100.0, 1),
            "area_m2": round(self.plan["area_m2"], 1) if self.plan else 0.0,
            "victims_tracked": len(self.fuser.tracks),
            "wall_clock_s": round(time.time() - self.started_at, 1) if self.started_at else 0.0,
        }

    # ------------------------------------------------------------------
    def route_to(self, lat, lon):
        """A* route from the staging base to a victim, avoiding water/debris."""
        pts = [self.scene.enu.to_enu(la, lo) for la, lo in self.polygon_ll] or \
              [(0.0, 0.0), (self.scene.width_m, -self.scene.height_m)]
        pad = 60.0
        bounds = (min(e for e, _ in pts) - pad, min(n for _, n in pts) - pad,
                  max(e for e, _ in pts) + pad, max(n for _, n in pts) + pad)

        grid = OccupancyGrid(bounds, self.scene.water + self.scene.obstacles,
                             self.scene.enu, resolution=self.cfg.grid_res_m)
        path = astar(grid, self.scene.enu.to_enu(*self.scene.base),
                     self.scene.enu.to_enu(lat, lon))
        if not path:
            return None
        path = simplify(path)
        length = sum(haversine_m(*self.scene.enu.to_ll(*path[i]),
                                 *self.scene.enu.to_ll(*path[i + 1]))
                     for i in range(len(path) - 1))
        return {
            "path": [list(self.scene.enu.to_ll(e, n)) for e, n in path],
            "length_m": round(length, 1),
            "nodes": len(path),
        }

    def report(self):
        return {
            "scene": self.scene.name,
            "started_at": self.started_at,
            "plan": {k: v for k, v in (self.plan or {}).items()
                     if k not in ("waypoints_enu", "waypoints_ll")},
            "drone": self.drone.telemetry(),
            "detector": self.detector.stats(),
            "accuracy": self.accuracy(),
            "coverage_pct": round(
                coverage_fraction(self.polygon_ll, self.footprints, self.scene.enu) * 100.0, 1)
            if self.polygon_ll else 0.0,
            "victims": self._victims(),
        }
