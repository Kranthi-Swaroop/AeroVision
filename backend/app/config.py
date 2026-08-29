"""Runtime configuration. Override any field with an AV_-prefixed env var."""

from __future__ import annotations

import os
from dataclasses import dataclass, field, asdict


def _f(name, default):
    return float(os.environ.get(f"AV_{name}", default))


def _i(name, default):
    return int(os.environ.get(f"AV_{name}", default))


@dataclass
class Settings:
    scene_path: str = os.environ.get("AV_SCENE", "assets/scene.json")

    # camera (defaults approximate a Pi Camera v2 on a fixed oblique mount)
    img_width: int = _i("IMG_W", 960)
    img_height: int = _i("IMG_H", 540)
    hfov_deg: float = _f("HFOV", 62.2)

    # flight
    altitude_m: float = _f("ALT", 40.0)
    tilt_deg: float = _f("TILT", 25.0)
    speed_ms: float = _f("SPEED", 6.0)
    endurance_s: float = _f("ENDURANCE", 780.0)
    sidelap: float = _f("SIDELAP", 0.40)

    # detection
    # The small model is still light enough for a 6 GB RTX 3050, while being
    # materially better on tiny people than the previous nano model.
    model: str = os.environ.get("AV_MODEL", "yolov8s.pt")
    device: str = os.environ.get("AV_DEVICE", "auto")
    # A stricter threshold suppresses debris proposals. Full-frame 1280px
    # inference retains small-person detail without tile-induced false positives.
    conf_threshold: float = _f("CONF", 0.60)
    imgsz: int = _i("IMGSZ", 1280)
    detect_hz: float = _f("DETECT_HZ", 8.0)          # GPU ceiling, not the trigger
    forward_overlap: float = _f("FWD_OVERLAP", 0.80)  # sets the capture interval

    # fusion
    merge_radius_m: float = _f("MERGE_R", 6.0)
    confirm_at: int = _i("CONFIRM_AT", 3)

    # loop / streaming
    sim_hz: float = _f("SIM_HZ", 10.0)
    time_scale: float = _f("TIME_SCALE", 4.0)   # 4x so a 13 min flight demos in 3
    jpeg_quality: int = _i("JPEG_Q", 70)
    stream_width: int = _i("STREAM_W", 720)

    # routing
    grid_res_m: float = _f("GRID_RES", 4.0)

    def as_dict(self):
        return asdict(self)


settings = Settings()
