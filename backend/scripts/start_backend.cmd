@echo off
rem Paperbook backend (FastAPI + local Whisper ASR + SAPI/CosyVoice/MiniMax TTS)
cd /d "%~dp0.."
python -m uvicorn app.main:app --host 0.0.0.0 --port 8790
