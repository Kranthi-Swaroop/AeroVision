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
                 conf=0.60, imgsz=1280, clip_model="ViT-B-32",
                 clip_pretrained="laion2b_s34b_b79k", clip_threshold=0.62):
        # Keep Ultralytics runtime settings inside the project. This avoids a
        # hidden dependency on a writable user profile on managed machines.
        config_dir = Path(__file__).resolve().parents[1] / ".ultralytics"
        config_dir.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("YOLO_CONFIG_DIR", str(config_dir))
        model_cache = Path(__file__).resolve().parents[1] / ".models"
        model_cache.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("HF_HOME", str(model_cache / "huggingface"))
        os.environ.setdefault("TORCH_HOME", str(model_cache / "torch"))
        import torch
        import open_clip
        from ultralytics import YOLO

        if device == "auto":
            device = "cuda:0" if torch.cuda.is_available() else "cpu"
        self.model = YOLO(model_path)
        self.device = device
        self.conf = conf
        self.imgsz = imgsz
        self.clip_threshold = clip_threshold
        self.torch = torch
        self.clip, _, self.clip_preprocess = open_clip.create_model_and_transforms(
            clip_model, pretrained=clip_pretrained, device=device,
        )
        self.clip.eval()
        tokenizer = open_clip.get_tokenizer(clip_model)
        positive_prompts = [
            "a photograph of a real human person",
            "a stranded person in flood water",
            "a human flood victim",
            "a visible human body",
        ]
        negative_prompts = [
            "a pile of white flood debris and garbage",
            "rocks rubble and construction debris",
            "a fallen tree or branches",
            "an inanimate object in flood water",
        ]
        with torch.inference_mode():
            pos = self.clip.encode_text(tokenizer(positive_prompts).to(device))
            neg = self.clip.encode_text(tokenizer(negative_prompts).to(device))
            pos = pos / pos.norm(dim=-1, keepdim=True)
            neg = neg / neg.norm(dim=-1, keepdim=True)
            prototypes = torch.stack((pos.mean(0), neg.mean(0)))
            self.clip_text_features = prototypes / prototypes.norm(dim=-1, keepdim=True)
        self.latencies = deque(maxlen=120)
        self.frames = 0
        self.detections = 0
        self._lock = asyncio.Lock()
        self._warm = False

    def warmup(self):
        blank = np.zeros((self.imgsz // 2, self.imgsz, 3), dtype=np.uint8)
        self._infer(blank)
        self._verify_person_crops(blank, np.array([[0, 0, 224, 224]], dtype=float))
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
        out = []
        if res.boxes is not None and len(res.boxes):
            xyxy = res.boxes.xyxy.cpu().numpy()
            confs = res.boxes.conf.cpu().numpy()
            clip_scores = self._verify_person_crops(frame, xyxy)
            for (x1, y1, x2, y2), c, clip_score in zip(xyxy, confs, clip_scores):
                if clip_score < self.clip_threshold:
                    print(f"[clip-reject] yolo={float(c):.3f} person={clip_score:.3f}")
                    continue
                out.append({
                    "x1": float(x1), "y1": float(y1),
                    "x2": float(x2), "y2": float(y2),
                    "conf": float(c),
                    "clip_conf": float(clip_score),
                    # feet-on-ground: for an oblique camera the bottom edge of
                    # the box is where the person contacts the ground plane,
                    # which is the only point we can georeference correctly
                    "anchor_u": float((x1 + x2) / 2.0),
                    "anchor_v": float(y2),
                })

        dt = (time.perf_counter() - t0) * 1000.0
        self.frames += 1
        self.detections += len(out)
        self.latencies.append(dt)
        return out, dt

    def _verify_person_crops(self, frame: np.ndarray, boxes):
        """Zero-shot hard-negative verification; receives image pixels only."""
        from PIL import Image

        height, width = frame.shape[:2]
        crops = []
        for x1, y1, x2, y2 in boxes:
            pad_x = max(4, int((x2 - x1) * 0.20))
            pad_y = max(4, int((y2 - y1) * 0.20))
            left, top = max(0, int(x1) - pad_x), max(0, int(y1) - pad_y)
            right, bottom = min(width, int(x2) + pad_x), min(height, int(y2) + pad_y)
            crop = frame[top:bottom, left:right, ::-1]
            crops.append(self.clip_preprocess(Image.fromarray(crop.copy())))
        if not crops:
            return []
        batch = self.torch.stack(crops).to(self.device)
        with self.torch.inference_mode():
            features = self.clip.encode_image(batch)
            features = features / features.norm(dim=-1, keepdim=True)
            probabilities = (100.0 * features @ self.clip_text_features.T).softmax(dim=-1)
        return probabilities[:, 0].float().cpu().tolist()

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
                           settings.conf_threshold, settings.imgsz,
                           settings.clip_model, settings.clip_pretrained,
                           settings.clip_threshold)
        d.warmup()
        return d
    except Exception as exc:  # noqa: BLE001
        print(f"[detector] falling back to stub: {exc}")
        return StubDetector()
