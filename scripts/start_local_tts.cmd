@echo off
rem Local streaming TTS server (CosyVoice3). Run with the cosyvoice conda env.
rem For the low-latency local demo, use TTS_PROVIDER=sapi instead (no server needed).
cd /d "%~dp0.."
if "%COSYVOICE_PY%"=="" (
  echo [ERROR] Set COSYVOICE_PY to the python.exe of your cosyvoice environment.
  exit /b 1
)
if "%COSYVOICE_HOME%"=="" (
  echo [ERROR] Set COSYVOICE_HOME to the CosyVoice repository directory.
  exit /b 1
)
"%COSYVOICE_PY%" scripts\start_local_tts.py --engine cosyvoice --port 9890
