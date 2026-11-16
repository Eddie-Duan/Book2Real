@echo off
rem Optional: start CosyVoice TTS on 9890, then backend.
rem For SAPI (default, 10-80ms first audio) you only need start_backend.cmd.
cd /d "%~dp0.."
start "paperbook-tts" cmd /c scripts\start_local_tts.cmd
timeout /t 3 /nobreak >nul
scripts\start_backend.cmd
