"""Synthetic camera: renders what the drone sees from a georeferenced ortho.

The scene is a single large ortho image whose top-left corner is pinned to a
known lat/lon at a known ground sample distance. Rendering a frame is a
homography warp of that ortho into the camera's image plane, so every pixel in
the rendered frame has an exact, invertible ground position. That invertibility
is the whole point: a YOLO box in this frame maps back to real coordinates by
geometry rather than by assumption.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from .geo import Intrinsics, LocalENU, Pose, ground_homography, image_to_ground


@dataclass
class Scene:
    ortho: np.ndarray
    gsd: float                       # metres per ortho pixel
    enu: LocalENU                    # origin at ortho top-left corner
    truth: list[dict]                # ground-truth people: {id, lat, lon}
    water: list[list]                # water polygons as [(lat, lon), ...]
    obstacles: list[list]
    base: tuple[float, float]
    name: str

    @property
    def width_m(self) -> float:
        return self.ortho.shape[1] * self.gsd

    @property
    def height_m(self) -> float:
        return self.ortho.shape[0] * self.gsd

    def bounds_ll(self):
        """(south, west, north, east) for the Leaflet image overlay."""
        n, w = self.enu.to_ll(0.0, 0.0)
        s, e = self.enu.to_ll(self.width_m, -self.height_m)
        return s, w, n, e

    @classmethod
    def load(cls, path: str | Path) -> "Scene":
        path = Path(path)
        meta = json.loads(path.read_text())
        img_path = path.parent / meta["ortho"]
        ortho = cv2.imread(str(img_path), cv2.IMREAD_COLOR)
        if ortho is None:
            raise FileNotFoundError(f"could not read ortho image: {img_path}")
        return cls(
            ortho=ortho,
            gsd=float(meta["gsd"]),
            enu=LocalENU(float(meta["anchor_lat"]), float(meta["anchor_lon"])),
            truth=meta.get("truth", []),
            water=[[tuple(p) for p in ring] for ring in meta.get("water", [])],
            obstacles=[[tuple(p) for p in ring] for ring in meta.get("obstacles", [])],
            base=tuple(meta.get("base", [meta["anchor_lat"], meta["anchor_lon"]])),
            name=meta.get("name", path.stem),
        )


class SyntheticCamera:
    def __init__(self, scene: Scene, intr: Intrinsics):
        self.scene = scene
        self.intr = intr

    def _ortho_to_world(self, off_c: int = 0, off_r: int = 0) -> np.ndarray:
        g = self.scene.gsd
        return np.array([[g, 0.0, off_c * g],
                         [0.0, -g, -off_r * g],
                         [0.0, 0.0, 1.0]])

    def render(self, pose: Pose) -> np.ndarray:
        """Render the view at `pose`. Sky (rays above horizon) renders black."""
        h = ground_homography(pose, self.intr)

        # Crop the ortho to just the visible region first. Warping a 6000px
        # ortho every frame is wasteful; the footprint is a small slice of it.
        corners = []
        for u, v in [(0, 0), (self.intr.width, 0),
                     (self.intr.width, self.intr.height), (0, self.intr.height)]:
            g = image_to_ground(u, v, pose, self.intr)
            if g is not None:
                corners.append(g)
        oh, ow = self.scene.ortho.shape[:2]
        if corners:
            cs = [e / self.scene.gsd for e, _ in corners]
            rs = [-n / self.scene.gsd for _, n in corners]
            pad = 32
            c0 = max(0, int(min(cs)) - pad)
            r0 = max(0, int(min(rs)) - pad)
            c1 = min(ow, int(max(cs)) + pad)
            r1 = min(oh, int(max(rs)) + pad)
        else:
            c0 = r0 = 0
            c1, r1 = ow, oh
        if c1 <= c0 or r1 <= r0:
            return np.zeros((self.intr.height, self.intr.width, 3), dtype=np.uint8)

        sub = self.scene.ortho[r0:r1, c0:c1]
        h_total = h @ self._ortho_to_world(c0, r0)
        return cv2.warpPerspective(
            sub, h_total, (self.intr.width, self.intr.height),
            flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT,
            borderValue=(18, 20, 24),
        )

    def pixel_to_ll(self, u: float, v: float, pose: Pose):
        """Back-project a detection pixel to (lat, lon), or None above horizon."""
        g = image_to_ground(u, v, pose, self.intr)
        if g is None:
            return None
        return self.scene.enu.to_ll(g[0], g[1])
