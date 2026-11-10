@echo off
REM ─────────────────────────────────────────────────────────────
REM  Model-to-Gradio Frontend Server
REM  Opens the premium AI platform UI in your browser.
REM ─────────────────────────────────────────────────────────────
setlocal

cd /d "%~dp0"

REM Use project venv if present, otherwise system Python
if exist ".venv\Scripts\python.exe" (
    set PYTHON=.venv\Scripts\python.exe
) else (
    set PYTHON=python
)

REM Allow overriding host/port
if not defined M2G_API_HOST set M2G_API_HOST=127.0.0.1
if not defined M2G_API_PORT set M2G_API_PORT=8765

echo.
echo  Starting Model-to-Gradio Platform...
echo  Server will be available at: http://%M2G_API_HOST%:%M2G_API_PORT%
echo.
echo  Press Ctrl+C to stop.
echo.

%PYTHON% -m m2g.web_api

endlocal
