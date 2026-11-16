@echo off
REM One-command launcher (bypasses PowerShell execution policy). Double-click friendly.
REM Usage: scripts\start_backend.cmd [port]
setlocal
set PORT=%~1
if "%PORT%"=="" set PORT=8790
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_backend.ps1" -Port %PORT%
endlocal
