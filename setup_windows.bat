@echo off
rem Creates .venv and installs the platform's dependencies (gradio + test tools). Safe to re-run.
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo Python was not found. Install Python 3.10+ from https://www.python.org/downloads/ and tick "Add python.exe to PATH".
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  python -m venv .venv
  if errorlevel 1 (echo Failed to create the virtual environment. & exit /b 1)
)
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 (echo pip upgrade failed - check your internet connection / proxy. & exit /b 1)
".venv\Scripts\python.exe" -m pip install -r requirements-dev.txt
if errorlevel 1 (echo Dependency install failed - check your internet connection / proxy. & exit /b 1)
if not exist ".env" copy ".env.example" ".env" >nul
echo.
echo Setup complete. Next: run_tests.bat, then run_platform.bat
