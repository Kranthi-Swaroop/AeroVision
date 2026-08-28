"""Turn ordinary person photos into transparent cutouts for the scene builder.

Finding pre-cut transparent PNGs of people is slow. Finding ordinary photos of
people is not. This does the cutting, so you can grab a dozen free-licence
photos and be done in a couple of minutes.

    pip install rembg onnxruntime
    python tools/cutout_people.py --in assets/raw/photos --out assets/raw/people

The first run downloads a ~176 MB segmentation model. Review the output before
building the scene: a cutout that kept a chunk of background will paste into
the ortho as an obvious rectangle and YOLO will not read it as a person.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

MIN_ALPHA = 12
EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


def tight_crop(rgba: np.ndarray, pad: int = 4) -> np.ndarray:
    """Trim transparent margin so the person fills the image.

    This matters more than it looks: build_scene.py scales each cutout so its
    long axis equals 1.7 m on the ground. Leftover transparent padding makes
    the person render smaller than a real human, and small is exactly what
    YOLO struggles with.
    """
    alpha = rgba[:, :, 3]
    rows = np.where(alpha.max(axis=1) > MIN_ALPHA)[0]
    cols = np.where(alpha.max(axis=0) > MIN_ALPHA)[0]
    if len(rows) == 0 or len(cols) == 0:
        return rgba
    r0, r1 = max(0, rows[0] - pad), min(rgba.shape[0], rows[-1] + pad + 1)
    c0, c1 = max(0, cols[0] - pad), min(rgba.shape[1], cols[-1] + pad + 1)
    return rgba[r0:r1, c0:c1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="src", default="assets/raw/photos")
    ap.add_argument("--out", dest="dst", default="assets/raw/people")
    ap.add_argument("--min-px", type=int, default=120,
                    help="reject cutouts whose long axis is smaller than this")
    args = ap.parse_args()

    import cv2
    from rembg import new_session, remove

    src, dst = Path(args.src), Path(args.dst)
    dst.mkdir(parents=True, exist_ok=True)
    photos = [p for p in sorted(src.iterdir()) if p.suffix.lower() in EXTS]
    if not photos:
        raise SystemExit(f"no images found in {src}")

    session = new_session("u2net_human_seg")  # human-specific, cleaner than general
    kept = 0

    for p in photos:
        img = cv2.imread(str(p), cv2.IMREAD_COLOR)
        if img is None:
            print(f"  skip  {p.name}  (unreadable)")
            continue

        rgba = remove(cv2.cvtColor(img, cv2.COLOR_BGR2RGBA), session=session)
        rgba = tight_crop(np.asarray(rgba))

        if max(rgba.shape[:2]) < args.min_px:
            print(f"  skip  {p.name}  (only {max(rgba.shape[:2])} px after crop)")
            continue
        covered = float((rgba[:, :, 3] > MIN_ALPHA).mean())
        if covered > 0.9:
            print(f"  warn  {p.name}  ({covered*100:.0f}% opaque -- background may "
                  f"not have been removed)")

        out = dst / f"{p.stem}.png"
        cv2.imwrite(str(out), cv2.cvtColor(rgba, cv2.COLOR_RGBA2BGRA))
        kept += 1
        print(f"  ok    {p.name} -> {out.name}  {rgba.shape[1]}x{rgba.shape[0]}")

    print(f"\n{kept}/{len(photos)} cutouts written to {dst}")
    print("open them and check each one is a clean person on transparency "
          "before running build_scene.py")


if __name__ == "__main__":
    main()
