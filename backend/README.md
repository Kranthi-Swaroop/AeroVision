# AeroVision — Phase 2 simulation backend

Browser-controlled drone search-and-rescue simulation with a real detection
and georeferencing pipeline. Draw a boundary on the map, the drone plans and
flies a coverage scan, YOLO runs on each rendered frame, and every detection
is back-projected to WGS84 coordinates through the camera model.

## What is real and what is simulated

State this plainly in the demo; it is the difference between a credible
prototype and an overclaim.

| Component | Status |
|---|---|
| YOLOv8n person detection | Real model, real inference, measured latency |
| Camera projection & georeferencing | Real photogrammetry, exact for flat ground |
| Coverage planning, A* routing, fusion, triage | Real algorithms |
| Accuracy metrics | Measured against known ground truth |
| Terrain and people | Synthetic — composited ortho, not a real flight |
| Physical drone autonomy | Not claimed. Naza-M V2 cannot accept waypoints |

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install torch --index-url https://download.pytorch.org/whl/cu121   # match your CUDA
pip install -r requirements.txt
```

## Build a scene

You need two things in `assets/raw/`:

1. `flood_aerial.jpg` — a top-down aerial or satellite image of a flood or
   disaster area, ideally 3000 px or wider.
2. `people/` — 5 to 10 person cutout PNGs with transparent backgrounds.
   Overhead or high-oblique shots detect best.

```bash
python tools/build_scene.py \
  --base assets/raw/flood_aerial.jpg \
  --people assets/raw/people \
  --count 14 --gsd 0.03 --anchor 21.2514 81.6296 --out assets
```

`--gsd` is metres per ortho pixel and sets the physical size of the scene.
0.03 across a 5000 px image gives a 150 m span, about one battery's work.

## Verify detection before demo day

```bash
python tools/calibrate.py --alts 25 30 40 --tilts 15 25 35
```

This flies the camera over every known person at each altitude and tilt and
reports the detection rate. Fly whatever wins. If nothing clears 60%, the
person cutouts are the problem, not the pipeline — the tool prints what to
try, in order.

## Run

```bash
uvicorn app.main:app --reload --port 8000
```

Environment overrides all use an `AV_` prefix: `AV_ALT`, `AV_TILT`,
`AV_CONF`, `AV_IMGSZ`, `AV_SIDELAP`, `AV_FWD_OVERLAP`, `AV_TIME_SCALE`.

## Tests

```bash
python tools/test_math.py      # 34 geometry, planner, fusion and routing checks
python tools/test_pipeline.py  # full mission loop, no GPU required
```

`test_pipeline.py` swaps YOLO for an oracle detector that returns boxes where
people actually project, so the error it reports is the floor contributed by
geometry and fusion alone — currently about 0.13 m. Real YOLO adds
bounding-box error on top of that.

## Two design decisions worth being able to defend

**Coverage planning is boustrophedon, not A\*.** A\* is a point-to-point
shortest-path search and has no notion of visiting every part of a region.
A\* is used in this system, but for routing a ground team to a victim around
water and debris, which is genuinely a point-to-point problem.

**The camera triggers on distance, not on a clock.** Firing on a timer ties
the number of times each victim is imaged to ground speed, which starves the
fusion stage at higher speeds. Triggering every `(1 - forward_overlap)` of the
along-track footprint guarantees roughly five sightings per ground point
regardless of how fast the drone flies. This is standard photogrammetric
practice and it is what makes the sighting count a meaningful confidence
signal.

## API

| Endpoint | Purpose |
|---|---|
| `GET /api/scene` | Scene bounds, water polygons, settings |
| `GET /api/ortho` | Ortho image for the Leaflet overlay |
| `POST /api/mission/start` | `{"polygon": [[lat, lon], ...]}` |
| `POST /api/mission/stop` | Abort |
| `POST /api/route` | `{"lat":…, "lon":…}` → A\* ground route |
| `GET /api/mission/report` | Full mission report |
| `GET /api/missions/{id}/geojson` | Export |
| `POST /api/telemetry/ingest` | Live GPS from ESP32 + NEO-6M |
| `WS /ws` | telemetry, frame, victims, metrics, mission_complete |
