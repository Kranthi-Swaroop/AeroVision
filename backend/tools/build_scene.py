"""Build a georeferenced synthetic disaster scene.

Takes a base aerial/satellite image, composites person cutouts into it at
physically correct scale, and records exactly where each one was placed. Those
recorded positions are the ground truth the pipeline is later scored against,
which is what makes the georeferencing error number meaningful.

Usage
-----
    python tools/build_scene.py \
        --base assets/raw/flood_aerial.jpg \
        --people assets/raw/people \
        --count 14 --gsd 0.03 \
        --anchor 21.2514 81.6296 \
        --out assets

`--gsd` is metres per ortho pixel: it sets how physically large the scene is.
0.03 over a 5000 px wide image gives a 150 m span, which is a realistic single
sortie for a 13-minute battery.

Person cutouts should be PNGs with transparency. Overhead or high-oblique
shots are ideal; ordinary standing-person cutouts also work because YOLO
detects prone and overhead poses, but run tools/calibrate.py afterwards to
measure how well before relying on it in a demo.
"""

from __future__ import annotations

import argparse
import json
import math
import random
from pathlib import Path

import cv2
import numpy as np

PERSON_LENGTH_M = 1.7  # long axis of a human footprint from above


def load_people(folder: Path):
    imgs = []
    for p in sorted(folder.glob("*")):
        if p.suffix.lower() not in {".png", ".webp"}:
            continue
        img = cv2.imread(str(p), cv2.IMREAD_UNCHANGED)
        if img is None:
            continue
        if img.shape[2] == 3:  # no alpha channel: treat pure white as background
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            alpha = np.where(gray > 245, 0, 255).astype(np.uint8)
            img = np.dstack([img, alpha])
        imgs.append(img)
    if not imgs:
        raise SystemExit(f"no PNG/WebP cutouts found in {folder}")
    return imgs


def alpha_paste(dst, src, cx, cy):
    """Alpha-composite `src` centred at (cx, cy), clipped to the canvas."""
    h, w = src.shape[:2]
    x0, y0 = int(cx - w / 2), int(cy - h / 2)
    x1, y1 = x0 + w, y0 + h
    sx0, sy0 = max(0, -x0), max(0, -y0)
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(dst.shape[1], x1), min(dst.shape[0], y1)
    if x1 <= x0 or y1 <= y0:
        return False
    patch = src[sy0:sy0 + (y1 - y0), sx0:sx0 + (x1 - x0)]
    a = patch[:, :, 3:4].astype(np.float32) / 255.0
    dst[y0:y1, x0:x1] = (patch[:, :, :3] * a + dst[y0:y1, x0:x1] * (1 - a)).astype(np.uint8)
    return True


def detect_water(ortho, min_area_px=4000, simplify_eps=6.0):
    """Vectorise water from the base image by hue.

    Rough by design. It gives the triage layer a real hazard polygon to reason
    about instead of a hand-drawn rectangle, and anything it misses simply
    means a victim is scored as on dry land.
    """
    hsv = cv2.cvtColor(ortho, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    mask = (((h > 85) & (h < 130) & (s > 40)) | ((s < 45) & (v < 110))).astype(np.uint8) * 255
    kernel = np.ones((9, 9), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    out = []
    for c in contours:
        if cv2.contourArea(c) < min_area_px:
            continue
        approx = cv2.approxPolyDP(c, simplify_eps, True)
        if len(approx) >= 3:
            out.append([(int(p[0][0]), int(p[0][1])) for p in approx])
    return out, mask


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True, help="aerial/satellite base image")
    ap.add_argument("--people", required=True, help="folder of person cutout PNGs")
    ap.add_argument("--out", default="assets")
    ap.add_argument("--count", type=int, default=12)
    ap.add_argument("--gsd", type=float, default=0.03, help="metres per ortho pixel")
    ap.add_argument("--anchor", nargs=2, type=float, default=[21.2514, 81.6296],
                    metavar=("LAT", "LON"), help="lat/lon of the ortho top-left corner")
    ap.add_argument("--max-width", type=int, default=5000)
    ap.add_argument("--water-bias", type=float, default=0.45,
                    help="fraction of people placed in or near water")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--name", default="flood_scene")
    args = ap.parse_args()

    random.seed(args.seed)
    np.random.seed(args.seed)

    ortho = cv2.imread(args.base, cv2.IMREAD_COLOR)
    if ortho is None:
        raise SystemExit(f"cannot read base image: {args.base}")
    if ortho.shape[1] > args.max_width:
        scale = args.max_width / ortho.shape[1]
        ortho = cv2.resize(ortho, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)

    oh, ow = ortho.shape[:2]
    people = load_people(Path(args.people))
    water_px, water_mask = detect_water(ortho)
    print(f"[scene] ortho {ow}x{oh} px, {ow * args.gsd:.0f}x{oh * args.gsd:.0f} m, "
          f"{len(water_px)} water polygons")

    # candidate positions, split between water edges and open ground
    edge = cv2.dilate(water_mask, np.ones((41, 41), np.uint8)) - water_mask
    wet_pts = np.column_stack(np.where((water_mask > 0) | (edge > 0))[::-1])
    margin = 120

    target_px = PERSON_LENGTH_M / args.gsd  # long axis in ortho pixels
    truth, placed = [], 0
    attempts = 0
    while placed < args.count and attempts < args.count * 60:
        attempts += 1
        if len(wet_pts) and random.random() < args.water_bias:
            cx, cy = wet_pts[random.randrange(len(wet_pts))]
        else:
            cx = random.randint(margin, ow - margin)
            cy = random.randint(margin, oh - margin)
        if not (margin <= cx <= ow - margin and margin <= cy <= oh - margin):
            continue
        # keep people apart so the fusion radius cannot merge two real victims
        if any(math.dist((cx, cy), (t["px"], t["py"])) * args.gsd < 12.0 for t in truth):
            continue

        src = random.choice(people)
        long_axis = max(src.shape[:2])
        scale = (target_px * random.uniform(0.9, 1.1)) / long_axis
        sw, sh = max(4, int(src.shape[1] * scale)), max(4, int(src.shape[0] * scale))
        person = cv2.resize(src, (sw, sh), interpolation=cv2.INTER_AREA)

        m = cv2.getRotationMatrix2D((sw / 2, sh / 2), random.uniform(0, 360), 1.0)
        person = cv2.warpAffine(person, m, (sw, sh), flags=cv2.INTER_LINEAR,
                                borderValue=(0, 0, 0, 0))

        if not alpha_paste(ortho, person, cx, cy):
            continue

        east = cx * args.gsd
        north = -cy * args.gsd
        lat = args.anchor[0] + math.degrees(north / 111320.0)
        lon = args.anchor[1] + math.degrees(
            east / (111320.0 * math.cos(math.radians(args.anchor[0]))))
        placed += 1
        truth.append({"id": f"gt{placed}", "lat": lat, "lon": lon,
                      "px": int(cx), "py": int(cy)})

    def px_to_ll(c, r):
        east, north = c * args.gsd, -r * args.gsd
        lat = args.anchor[0] + math.degrees(north / 111320.0)
        lon = args.anchor[1] + math.degrees(
            east / (111320.0 * math.cos(math.radians(args.anchor[0]))))
        return [lat, lon]

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out / "ortho.png"), ortho)

    meta = {
        "name": args.name,
        "ortho": "ortho.png",
        "gsd": args.gsd,
        "anchor_lat": args.anchor[0],
        "anchor_lon": args.anchor[1],
        "size_px": [ow, oh],
        "base": px_to_ll(margin, oh - margin),   # staging point, a corner on dry land
        "water": [[px_to_ll(c, r) for c, r in ring] for ring in water_px],
        "obstacles": [],
        "truth": [{"id": t["id"], "lat": t["lat"], "lon": t["lon"]} for t in truth],
    }
    (out / "scene.json").write_text(json.dumps(meta, indent=2))
    print(f"[scene] placed {placed}/{args.count} people -> {out/'scene.json'}")
    print("[scene] next: python tools/calibrate.py  (verify YOLO actually sees them)")


if __name__ == "__main__":
    main()
