"""YOLOv8 person detector.

Only COCO class 0 (person) is kept. Inference runs in a worker thread so the
asyncio event loop keeps streaming telemetry while the GPU is busy, and every
call records its own latency because inference time is a number the demo
should be able to show rather than claim.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections import deque
from pathlib import Path

import numpy as np

PERSON_CLASS = 0


class PersonDetector:
    def __init__(self, model_path="yolov8s.pt", device="auto",
                 conf=0.60, imgsz=1280):
        # Keep Ultralytics runtime settings inside the project. This avoids a
        # hidden dependency on a writable user profile on managed machines.
        config_dir = Path(__file__).resolve().parents[1] / ".ultralytics"
        config_dir.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("YOLO_CONFIG_DIR", str(config_dir))
        import torch
        from ultralytics import YOLO

        if device == "auto":
            device = "cuda:0" if torch.cuda.is_available() else "cpu"
        self.model = YOLO(model_path)
        self.device = device
        self.conf = conf
        self.imgsz = imgsz
        self.latencies = deque(maxlen=120)
        self.frames = 0
        self.detections = 0
        self._lock = asyncio.Lock()
        self._warm = False

    def warmup(self):
        blank = np.zeros((self.imgsz // 2, self.imgsz, 3), dtype=np.uint8)
        self._infer(blank)
        self._warm = True
        self.frames = 0
        self.latencies.clear()

    def _infer(self, frame: np.ndarray):
        t0 = time.perf_counter()
        res = self.model.predict(
            frame,
            imgsz=self.imgsz,
            conf=self.conf,
            classes=[PERSON_CLASS],
            device=self.device,
            verbose=False,
        )[0]
        dt = (time.perf_counter() - t0) * 1000.0

        out = []
        if res.boxes is not None and len(res.boxes):
            xyxy = res.boxes.xyxy.cpu().numpy()
            confs = res.boxes.conf.cpu().numpy()
            for (x1, y1, x2, y2), c in zip(xyxy, confs):
                out.append({
                    "x1": float(x1), "y1": float(y1),
                    "x2": float(x2), "y2": float(y2),
                    "conf": float(c),
                    # feet-on-ground: for an oblique camera the bottom edge of
                    # the box is where the person contacts the ground plane,
                    # which is the only point we can georeference correctly
                    "anchor_u": float((x1 + x2) / 2.0),
                    "anchor_v": float(y2),
                })

        self.frames += 1
        self.detections += len(out)
        self.latencies.append(dt)
        return out, dt

    async def detect(self, frame: np.ndarray):
        async with self._lock:
            return await asyncio.to_thread(self._infer, frame)

    def stats(self):
        lat = list(self.latencies)
        mean = sum(lat) / len(lat) if lat else 0.0
        return {
            "frames_processed": self.frames,
            "raw_detections": self.detections,
            "latency_ms": round(mean, 1),
            "latency_p95_ms": round(sorted(lat)[int(len(lat) * 0.95)], 1) if len(lat) > 4 else round(mean, 1),
            "throughput_fps": round(1000.0 / mean, 1) if mean > 0 else 0.0,
            "device": self.device,
        }


class StubDetector:
    """Fallback when ultralytics/torch is unavailable, so the rest of the
    pipeline can still be run and tested. It detects nothing and says so."""

    def __init__(self, *_, **__):
        self.frames = 0
        self.device = "stub"

    def warmup(self):
        pass

    async def detect(self, frame):
        self.frames += 1
        return [], 0.0

    def stats(self):
        return {"frames_processed": self.frames, "raw_detections": 0,
                "latency_ms": 0.0, "latency_p95_ms": 0.0,
                "throughput_fps": 0.0, "device": "stub (no model loaded)"}


def build_detector(settings):
    try:
        d = PersonDetector(settings.model, settings.device,
                           settings.conf_threshold, settings.imgsz)
        d.warmup()
        return d
    except Exception as exc:  # noqa: BLE001
        print(f"[detector] falling back to stub: {exc}")
        return StubDetector()
