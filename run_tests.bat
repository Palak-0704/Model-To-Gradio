@echo off
rem Runs the offline test suite (no network, no model downloads).
cd /d "%~dp0"
set "PY=python"
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
set "PYTHONPATH=%CD%;%CD%\tests"
%PY% -m unittest discover -s tests -p "test_*.py"
