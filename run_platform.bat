@echo off
rem Starts the platform UI on http://127.0.0.1:7860 (local only).
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run setup_windows.bat first.
  exit /b 1
)
set PYTHONPATH=%CD%
".venv\Scripts\python.exe" -m m2g.web
