#Requires -Version 5
<#
  Create a local venv and verify the toolchain.
  Usage:  powershell -ExecutionPolicy Bypass -File scripts\setup_env.ps1
#>
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host "[1/3] python" -ForegroundColor Cyan
$py = "python"
try { & $py --version } catch { throw "python not found on PATH" }

Write-Host "[2/3] venv + deps" -ForegroundColor Cyan
if (-not (Test-Path ".venv")) { & $py -m venv .venv }
& ".venv\Scripts\python.exe" -m pip install --upgrade pip --quiet
& ".venv\Scripts\python.exe" -m pip install -r requirements.txt --quiet
& ".venv\Scripts\python.exe" -m book2real doctor

Write-Host "[3/3] ffmpeg" -ForegroundColor Cyan
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    Write-Host "  ffmpeg missing. Install it with:  winget install Gyan.FFmpeg" -ForegroundColor Yellow
    Write-Host "  or set BOOK2REAL_FFMPEG to ffmpeg.exe" -ForegroundColor Yellow
} else {
    Write-Host "  ffmpeg OK" -ForegroundColor Green
}
