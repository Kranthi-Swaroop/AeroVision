"""End-to-end test of the mission loop without needing torch.

Substitutes an oracle detector that returns a box wherever a ground-truth
person actually projects into the frame, with a few pixels of jitter to stand
in for real bounding-box imprecision. Any localisation error this test reports
is error contributed by the geometry and the fusion stage alone, which is the
floor the real pipeline can approach but not beat.
"""

from __future__ import annotations

import asyncio
import json
import math
import random
import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.camera import Scene                                   # noqa: E402
from app.config import Settings                                # noqa: E402
from app.geo import LocalENU, distance_m, ground_to_image      # noqa: E402
from app.mission import MissionRunner                          # noqa: E402

BOX_JITTER_PX = 3.0
PERSON_H_PX_AT_1M = 1.7


def make_scene(tmp: Path, gsd=0.04, w=3000, h=2400, n_people=10, seed=3):
    """Procedural terrain with high-contrast markers at known positions."""
    rng = np.random.default_rng(seed)
    ortho = np.zeros((h, w, 3), np.uint8)
    ortho[:, :] = (60, 110, 70)
    noise = rng.integers(-18, 18, (h // 8, w // 8, 3), dtype=np.int16)
    ortho = np.clip(ortho.astype(np.int16) +
                    cv2.resize(noise, (w, h), interpolation=cv2.INTER_LINEAR), 0, 255).astype(np.uint8)
    cv2.ellipse(ortho, (w // 2, h // 2), (w // 3, h // 5), 20, 0, 360, (130, 95, 55), -1)

    anchor = (21.2514, 81.6296)
    enu = LocalENU(*anchor)
    truth, pts = [], []
    rand = random.Random(seed)
    while len(truth) < n_people:
        cx = rand.randint(200, w - 200)
        cy = rand.randint(200, h - 200)
        if any(math.dist((cx, cy), p) * gsd < 15.0 for p in pts):
            continue
        pts.append((cx, cy))
        r = max(3, int((PERSON_H_PX_AT_1M / gsd) / 2))
        cv2.circle(ortho, (cx, cy), r, (40, 40, 220), -1)
        lat, lon = enu.to_ll(cx * gsd, -cy * gsd)
        truth.append({"id": f"gt{len(truth)+1}", "lat": lat, "lon": lon})

    cv2.imwrite(str(tmp / "ortho.png"), ortho)
    (tmp / "scene.json").write_text(json.dumps({
        "name": "synthetic_test", "ortho": "ortho.png", "gsd": gsd,
        "anchor_lat": anchor[0], "anchor_lon": anchor[1],
        "base": list(enu.to_ll(20.0, -20.0)),
        "water": [], "obstacles": [], "truth": truth,
    }))
    return Scene.load(tmp / "scene.json")


class OracleDetector:
    """Returns a plausible box for every truth point currently in frame."""

    def __init__(self, scene, runner_ref, jitter=BOX_JITTER_PX, seed=11):
        self.scene = scene
        self.ref = runner_ref
        self.jitter = jitter
        self.rand = random.Random(seed)
        self.frames = 0
        self.raw = 0

    def warmup(self):
        pass

    async def detect(self, frame):
        runner = self.ref[0]
        pose, intr = runner.drone.pose, runner.intr
        self.frames += 1
        out = []
        for gt in self.scene.truth:
            e, n = self.scene.enu.to_enu(gt["lat"], gt["lon"])
            uv = ground_to_image(e, n, pose, intr)
            if uv is None:
                continue
            u, v = uv
            if not (10 <= u < intr.width - 10 and 10 <= v < intr.height - 10):
                continue
            ju = u + self.rand.uniform(-self.jitter, self.jitter)
            jv = v + self.rand.uniform(-self.jitter, self.jitter)
            out.append({"x1": ju - 12, "y1": jv - 30, "x2": ju + 12, "y2": jv,
                        "conf": self.rand.uniform(0.55, 0.92),
                        "anchor_u": ju, "anchor_v": jv})
        self.raw += len(out)
        return out, 6.0

    def stats(self):
        return {"frames_processed": self.frames, "raw_detections": self.raw,
                "latency_ms": 6.0, "latency_p95_ms": 8.0,
                "throughput_fps": 166.0, "device": "oracle"}


async def main():
    tmp = Path(tempfile.mkdtemp())
    scene = make_scene(tmp)
    print(f"scene: {scene.ortho.shape[1]}x{scene.ortho.shape[0]} px "
          f"({scene.width_m:.0f}x{scene.height_m:.0f} m), {len(scene.truth)} people\n")

    cfg = Settings()
    cfg.altitude_m, cfg.tilt_deg = 40.0, 25.0
    cfg.sim_hz, cfg.time_scale, cfg.detect_hz = 10.0, 6.0, 30.0
    cfg.sidelap, cfg.forward_overlap = 0.40, 0.80
    cfg.endurance_s = 4000.0  # long battery so the whole area gets scanned

    ref = [None]
    messages: dict[str, int] = {}

    async def broadcast(msg):
        messages[msg["type"]] = messages.get(msg["type"], 0) + 1

    runner = MissionRunner(scene, cfg, OracleDetector(scene, ref), broadcast)
    ref[0] = runner

    enu = scene.enu
    poly = [enu.to_ll(15, -15), enu.to_ll(scene.width_m - 15, -15),
            enu.to_ll(scene.width_m - 15, -(scene.height_m - 15)),
            enu.to_ll(15, -(scene.height_m - 15))]

    ok = await runner.start(poly)
    assert ok, "planning failed"
    p = runner.plan
    print(f"plan: {p['legs']} legs, {p['swath_m']:.0f} m swath, "
          f"{p['spacing_m']:.0f} m spacing, {p['length_m']:.0f} m path, "
          f"{p['area_m2']/10000:.1f} ha\n")

    await asyncio.wait_for(runner.task, timeout=300)

    acc = runner.accuracy()
    cov = runner.report()["coverage_pct"]
    print("results")
    print(f"  frames processed     {runner.detector.frames}")
    print(f"  coverage             {cov:.1f}%")
    print(f"  raw detections       {runner.detector.raw}")
    print(f"  tracks after fusion  {len(runner.fuser.tracks)}")
    print(f"  ground truth         {acc['ground_truth']}")
    print(f"  confirmed victims    {acc['confirmed']}")
    print(f"  precision / recall   {acc['precision']:.2f} / {acc['recall']:.2f}")
    print(f"  mean geo error       {acc['mean_geo_error_m']} m")
    print(f"  p95 geo error        {acc['p95_geo_error_m']} m")
    print(f"  max geo error        {acc['max_geo_error_m']} m")
    print(f"  ws messages          {messages}\n")

    top = runner.report()["victims"][:3]
    for v in top:
        near = min(distance_m(v["lat"], v["lon"], g["lat"], g["lon"]) for g in scene.truth)
        print(f"  {v['id']}  rank {v['rank']}  {v['priority_level']:<8} "
              f"prio {v['priority']:.2f}  {v['sightings']} sightings  {near:.2f} m from truth")

    fails = []
    if cov < 90:
        fails.append(f"coverage only {cov:.1f}%")
    if acc["recall"] < 0.9:
        fails.append(f"recall {acc['recall']}")
    if acc["false_positives"] > 1:
        fails.append(f"{acc['false_positives']} false positives from fusion")
    if acc["mean_geo_error_m"] is None or acc["mean_geo_error_m"] > 3.0:
        fails.append(f"mean error {acc['mean_geo_error_m']} m")

    print("\n" + "=" * 44)
    if fails:
        print("FAILED: " + "; ".join(fails))
        sys.exit(1)
    print("end-to-end pipeline OK")


if __name__ == "__main__":
    asyncio.run(main())
