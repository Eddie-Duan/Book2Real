@echo off
rem Paperbook backend (FastAPI + local Whisper ASR + SAPI/CosyVoice/MiniMax TTS)
rem Prefer the repo venv built by scripts\setup_env.ps1; the system python may be
rem a different interpreter without uvicorn installed.
cd /d "%~dp0.."
if exist "..\.venv\Scripts\python.exe" goto venv
python -m uvicorn app.main:app --host 0.0.0.0 --port 8790
goto :eof
:venv
"..\.venv\Scripts\python.exe" -m uvicorn app.main:app --host 0.0.0.0 --port 8790
