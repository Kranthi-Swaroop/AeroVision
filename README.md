# AeroVision

AeroVision is a browser-based flood search-and-rescue simulation. It combines
a textured Three.js environment, an interactive drone, real YOLO person
detection, autonomous coverage scanning, dummy-GPS geolocation, and safe rescue
route planning.

Detailed SIH architecture, algorithms, specifications, rationale, testing, and
demo guidance are available in
[docs/SIH_PROJECT_DOCUMENTATION.md](docs/SIH_PROJECT_DOCUMENTATION.md).

## Repository structure

```text
AeroVision/
├── backend/
│   ├── app/                 FastAPI API, YOLO inference and mission services
│   ├── tools/               Calibration, scene-building and verification tools
│   ├── requirements.txt
│   └── README.md
├── frontend/
│   ├── public/models/       Runtime-ready licensed 3D assets
│   ├── src/
│   │   ├── api/             REST and WebSocket clients
│   │   ├── components/      Dashboard and Three.js views
│   │   ├── hooks/           Application state hooks
│   │   ├── lib/             Survey and rescue-planning algorithms
│   │   └── styles/          Shared visual tokens
│   ├── package.json
│   └── vite.config.js
└── scripts/                 Windows setup and startup helpers
```

## Requirements

- Windows PowerShell 5.1 or newer
- Python 3.10+
- Node.js 20+
- npm 10+

## First-time setup

From the repository root:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\setup.ps1
```

The setup script creates `backend/.venv`, installs Python dependencies, and
installs the frontend packages. PyTorch/Ultralytics may take several minutes.

## Start AeroVision

Open two PowerShell terminals in the repository root.

Terminal 1:

```powershell
.\scripts\start-backend.ps1
```

Terminal 2:

```powershell
.\scripts\start-frontend.ps1
```

Open [http://localhost:5173](http://localhost:5173). The Vite server proxies
API and WebSocket traffic to the backend at port `8000`.

## Manual startup commands

```powershell
# Backend
cd backend
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --reload-dir app --port 8000

# Frontend, in a second terminal
cd frontend
npm run dev
```

## Verification

```powershell
cd frontend
npm run build

cd ..\backend
.\.venv\Scripts\python.exe tools\test_math.py
.\.venv\Scripts\python.exe tools\test_pipeline.py
```

## Large assets and attribution

Runtime models live only in `frontend/public/models`. Each third-party model
retains its accompanying `license.txt`. The compact generated backend scene is
versioned so a fresh clone starts immediately; its large raw source photos are
excluded. Builds, virtual environments, YOLO weights, databases, caches, and
local configuration are also excluded by `.gitignore`.
