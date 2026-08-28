$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$backendDir = Join-Path $repoRoot "backend"
$pythonExe = Join-Path $backendDir ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $pythonExe)) {
    throw "Backend environment is missing. Run .\scripts\setup.ps1 first."
}

Push-Location $backendDir
try { & $pythonExe -m uvicorn app.main:app --reload --reload-dir app --port 8000 }
finally { Pop-Location }
