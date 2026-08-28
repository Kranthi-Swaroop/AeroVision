"""Pre-demo sanity check: does YOLO actually see the people in this scene?

Flies a virtual camera directly over each ground-truth person at a sweep of
altitudes and tilts, and reports detection rate and georeferencing error for
each combination. Run this before demo day and fly the settings that win. If
detection rate is low everywhere, the problem is the person cutouts (too
small, wrong angle, low contrast against the terrain), not the pipeline.

    python tools/calibrate.py --alts 25 30 40 --tilts 15 25 35
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.camera import Scene, SyntheticCamera            # noqa: E402
from app.config import settings                          # noqa: E402
from app.detector import build_detector                  # noqa: E402
from app.geo import Intrinsics, Pose, haversine_m        # noqa: E402


def run(scene, intr, detector, alt, tilt, offset_m):
    """Place the camera so each victim lands near frame centre, then detect."""
    cam = SyntheticCamera(scene, intr)
    hits, errs = 0, []
    for gt in scene.truth:
        e, n = scene.enu.to_enu(gt["lat"], gt["lon"])
        # an oblique camera looks forward, so stand off behind the target
        pose = Pose(e, n - offset_m, alt, 0.0, tilt)
        frame = cam.render(pose)
        boxes, _ = detector._infer(frame)

        best = None
        for b in boxes:
            ll = cam.pixel_to_ll(b["anchor_u"], b["anchor_v"], pose)
            if ll is None:
                continue
            d = haversine_m(ll[0], ll[1], gt["lat"], gt["lon"])
            if d < 20.0 and (best is None or d < best):
                best = d
        if best is not None:
            hits += 1
            errs.append(best)
    rate = hits / max(1, len(scene.truth))
    mean = sum(errs) / len(errs) if errs else float("nan")
    return rate, mean, errs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scene", default=settings.scene_path)
    ap.add_argument("--alts", nargs="+", type=float, default=[25, 30, 40, 50])
    ap.add_argument("--tilts", nargs="+", type=float, default=[0, 15, 25, 35])
    ap.add_argument("--conf", type=float, default=settings.conf_threshold)
    args = ap.parse_args()

    scene = Scene.load(args.scene)
    intr = Intrinsics(settings.img_width, settings.img_height, settings.hfov_deg)
    settings.conf_threshold = args.conf
    detector = build_detector(settings)
    if detector.stats()["device"].startswith("stub"):
        raise SystemExit("no YOLO model loaded; install ultralytics first")

    print(f"scene '{scene.name}': {len(scene.truth)} ground-truth people, "
          f"conf={args.conf}, imgsz={settings.imgsz}\n")
    print(f"{'alt':>6} {'tilt':>6} {'detected':>10} {'mean err':>10}")
    print("-" * 36)

    best = None
    for alt in args.alts:
        for tilt in args.tilts:
            offset = alt * __import__("math").tan(__import__("math").radians(tilt))
            rate, mean, _ = run(scene, intr, detector, alt, tilt, offset)
            flag = ""
            if best is None or rate > best[0]:
                best = (rate, alt, tilt, mean)
                flag = "  <- best so far"
            err = f"{mean:8.2f} m" if mean == mean else "       n/a"
            print(f"{alt:6.0f} {tilt:6.0f} {rate*100:9.0f}% {err}{flag}")

    if best:
        rate, alt, tilt, mean = best
        print(f"\nbest: alt={alt:.0f} m tilt={tilt:.0f} deg -> "
              f"{rate*100:.0f}% detected, {mean:.2f} m mean error")
        print(f"set it with:  AV_ALT={alt:.0f} AV_TILT={tilt:.0f} uvicorn app.main:app --port 8000")
        if rate < 0.6:
            print("\nlow detection rate. things to try, in order:")
            print("  1. lower altitude (people occupy more pixels)")
            print("  2. raise AV_IMGSZ to 1280")
            print("  3. lower AV_CONF to 0.25")
            print("  4. replace the person cutouts with higher-contrast overhead shots")


if __name__ == "__main__":
    main()
