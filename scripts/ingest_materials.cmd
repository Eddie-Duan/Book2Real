@echo off
rem Ingest the 6 competition picture books (PDF + script txt + query audio).
cd /d "%~dp0.."
if "%~1"=="" (
  echo Usage: scripts\ingest_materials.cmd "<competition materials zip path>"
  exit /b 1
)
python tools\ingest_competition.py --zip "%~1"
