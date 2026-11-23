# One-command launcher for the merged backend (FastAPI, single process).
# Serves the reader page /, the film player /film, Q&A/voice APIs, book and video search.
# NOTE: Windows blocks unsigned .ps1 by default; use start_backend.cmd in this folder,
#       or: powershell -ExecutionPolicy Bypass -File scripts\start_backend.ps1
param(
    [string]$BindHost = "0.0.0.0",
    [int]$Port = 8790
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
# Use the repo venv (scripts\setup_env.ps1 builds it) — never the system `py`,
# which may be a different interpreter without uvicorn installed.
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    $py = (Get-Command python -ErrorAction SilentlyContinue).Source
}
if (-not $py) {
    throw "No Python found. Run scripts\setup_env.ps1 first (Python 3.10-3.12)."
}
Push-Location (Join-Path $root "backend")
try {
    Write-Host "[backend] http://127.0.0.1:$Port/  (reader)   /film?book=cn01  (film)   /docs  (OpenAPI)" -ForegroundColor Cyan
    & $py -m uvicorn app.main:app --host $BindHost --port $Port
}
finally {
    Pop-Location
}
