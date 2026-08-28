"""FastAPI entrypoint: WebSocket hub + mission control REST endpoints.

Run with:  uvicorn app.main:app --reload --port 8000
"""

from __future__ import annotations

import asyncio
import json

from fastapi import FastAPI, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from . import store
from .camera import Scene
from .config import settings
from .detector import build_detector
from .mission import MissionRunner

app = FastAPI(title="AeroVision Simulation API", version="0.2.0")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)


class Hub:
    """Fan-out to every connected dashboard, tolerant of slow/dead clients."""

    def __init__(self):
        self.clients: set[WebSocket] = set()
        self.lock = asyncio.Lock()

    async def join(self, ws: WebSocket):
        await ws.accept()
        async with self.lock:
            self.clients.add(ws)

    async def leave(self, ws: WebSocket):
        async with self.lock:
            self.clients.discard(ws)

    async def send(self, message: dict):
        if not self.clients:
            return
        data = json.dumps(message)
        dead = []
        for ws in list(self.clients):
            try:
                await ws.send_text(data)
            except Exception:  # noqa: BLE001
                dead.append(ws)
        if dead:
            async with self.lock:
                for ws in dead:
                    self.clients.discard(ws)


hub = Hub()
state: dict = {"scene": None, "detector": None, "runner": None}


@app.on_event("startup")
async def startup():
    scene = Scene.load(settings.scene_path)
    detector = build_detector(settings)
    state["scene"] = scene
    state["detector"] = detector
    state["runner"] = MissionRunner(scene, settings, detector, hub.send)
    print(f"[startup] scene '{scene.name}' "
          f"{scene.ortho.shape[1]}x{scene.ortho.shape[0]} px @ {scene.gsd} m/px, "
          f"{len(scene.truth)} ground-truth people")
    print(f"[startup] detector: {detector.stats()['device']}")


def runner() -> MissionRunner:
    r = state.get("runner")
    if r is None:
        raise HTTPException(503, "scene not loaded")
    return r


# ---------------------------------------------------------------------------
class StartRequest(BaseModel):
    polygon: list[list[float]] = Field(..., min_length=3,
                                       description="[[lat, lon], ...] scan boundary")


class RouteRequest(BaseModel):
    lat: float
    lon: float


@app.get("/api/scene")
def get_scene():
    s = runner().scene
    south, west, north, east = s.bounds_ll()
    return {
        "name": s.name,
        "bounds": [[south, west], [north, east]],
        "gsd": s.gsd,
        "size_m": [round(s.width_m, 1), round(s.height_m, 1)],
        "base": list(s.base),
        "water": [[list(p) for p in ring] for ring in s.water],
        "obstacles": [[list(p) for p in ring] for ring in s.obstacles],
        "truth_count": len(s.truth),
        "ortho_url": "/api/ortho",
        "settings": settings.as_dict(),
    }


@app.get("/api/ortho")
def get_ortho():
    """Serve the ortho as a PNG for the Leaflet image overlay."""
    import cv2
    from fastapi.responses import Response

    ok, buf = cv2.imencode(".png", runner().scene.ortho)
    if not ok:
        raise HTTPException(500, "encode failed")
    return Response(buf.tobytes(), media_type="image/png",
                    headers={"Cache-Control": "public, max-age=3600"})


@app.get("/api/truth")
def get_truth():
    """Ground truth. Exposed so the dashboard can overlay it for evaluation."""
    return {"truth": runner().scene.truth}


@app.post("/api/mission/start")
async def start_mission(req: StartRequest):
    ok = await runner().start([(p[0], p[1]) for p in req.polygon])
    if not ok:
        raise HTTPException(400, "could not plan a coverage path for that area")
    return {"status": "scanning", "plan": {
        k: v for k, v in runner().plan.items() if k not in ("waypoints_enu",)}}


@app.post("/api/mission/stop")
async def stop_mission():
    await runner().stop()
    await hub.send({"type": "mission_stopped"})
    return {"status": "stopped"}


@app.get("/api/mission/report")
def mission_report():
    return runner().report()


@app.post("/api/mission/save")
def save_mission():
    return {"mission_id": store.save(runner().report())}


@app.get("/api/missions")
def missions():
    return {"missions": store.list_missions()}


@app.get("/api/missions/{mission_id}/geojson")
def mission_geojson(mission_id: int):
    rep = store.get(mission_id)
    if rep is None:
        raise HTTPException(404, "no such mission")
    return JSONResponse(store.to_geojson(rep))


@app.get("/api/missions/{mission_id}/csv")
def mission_csv(mission_id: int):
    rep = store.get(mission_id)
    if rep is None:
        raise HTTPException(404, "no such mission")
    return PlainTextResponse(store.to_csv(rep), media_type="text/csv")


@app.post("/api/route")
def route(req: RouteRequest):
    r = runner().route_to(req.lat, req.lon)
    if r is None:
        raise HTTPException(422, "no obstacle-free route found")
    return r


@app.post("/api/telemetry/ingest")
async def ingest(payload: dict):
    """Live GPS ingest from a real ESP32 + NEO-6M on the airframe.

    Kept deliberately dumb: accept a fix, tag it, fan it out. This is the hook
    that puts real hardware telemetry on the same dashboard as the simulation,
    without pretending the flight controller can be commanded.
    """
    await hub.send({"type": "hardware_gps", **payload})
    return {"ok": True}


@app.post("/api/detection/frame")
async def detect_camera_frame(frame: UploadFile = File(...)):
    """Run person-only YOLO inference on one raw drone-camera image.

    The endpoint deliberately accepts only encoded image bytes. It receives no
    simulator coordinates, model IDs, ground truth, or 3D-scene metadata.
    """
    import cv2
    import numpy as np

    payload = await frame.read()
    image = cv2.imdecode(np.frombuffer(payload, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise HTTPException(400, "frame is not a decodable image")

    detector = state.get("detector")
    if detector is None:
        raise HTTPException(503, "detector is not loaded")

    boxes, latency_ms = await detector.detect(image)
    height, width = image.shape[:2]
    print(
        f"[fpv-detect] frame={width}x{height} "
        f"people={len(boxes)} latency={latency_ms:.1f}ms"
    )
    for index, box in enumerate(boxes, start=1):
        print(
            f"[fpv-detect] person#{index} confidence={box['conf']:.3f} "
            f"box=({box['x1']:.1f},{box['y1']:.1f})-"
            f"({box['x2']:.1f},{box['y2']:.1f})"
        )

    return {
        "people": boxes,
        "count": len(boxes),
        "latency_ms": round(latency_ms, 1),
        "frame": {"width": width, "height": height},
        "detector": detector.stats()["device"],
    }


@app.websocket("/ws")
async def websocket(ws: WebSocket):
    await hub.join(ws)
    try:
        r = state.get("runner")
        await ws.send_text(json.dumps({
            "type": "hello",
            "running": bool(r and r.running),
            "settings": settings.as_dict(),
        }))
        while True:
            await ws.receive_text()  # keepalive; control happens over REST
    except WebSocketDisconnect:
        pass
    finally:
        await hub.leave(ws)
