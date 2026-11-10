"""Generate the safe, self-contained Windows launcher shipped with an app.

The launcher lives next to ``app.py`` and ``requirements.txt`` and keeps everything for that one app inside its own folder:
the virtual environment is ``<app directory>\\.venv``. Nothing is ever installed into the Model-to-Gradio environment.

Exit codes (also understood by ``local_workflow``): 10 app files missing, 11 Python 3.12 not found, 12 the virtual environment
is unusable, 13 dependency installation failed, 14 the environment could not be activated. Any other code is the exit code of
``app.py`` itself. ``M2G_NO_PAUSE=1`` (set by the platform backend) skips the final ``pause`` so a managed launch never waits
for a key press; a double-click run still keeps the window open.
"""
from __future__ import annotations

EXIT_MISSING_FILES = 10
EXIT_PYTHON_MISSING = 11
EXIT_VENV_FAILED = 12
EXIT_INSTALL_FAILED = 13
EXIT_ACTIVATE_FAILED = 14

REQUIRED_PYTHON = "3.12"


def launcher_filename(slug: str) -> str:
    """``run_<model folder name>.bat``: every generated model package has its own launcher, named after its own folder. ``slug`` is the
    already-validated output folder name (letters, digits, '.', '_' and '-' only), so it is safe as part of a file name."""
    return f"run_{slug}.bat"


def windows_launcher(slug: str) -> str:
    """Return the batch launcher text (LF line endings; the backend writes it with CRLF right before running it)."""
    return rf"""@echo off
setlocal EnableExtensions
title Model-to-Gradio Local App
REM Model-to-Gradio launcher for {slug}
REM Exit codes: 10 files missing, 11 Python 3.12 missing, 12 venv unusable, 13 install failed, 14 activate failed.
set "APP_DIR=%~dp0"
if "%APP_DIR:~-1%"=="\" set "APP_DIR=%APP_DIR:~0,-1%"
set "VENV_DIR=%APP_DIR%\.venv"
set "VENV_PY=%VENV_DIR%\Scripts\python.exe"
set "REQ_FILE=%APP_DIR%\requirements.txt"
set "APP_FILE=%APP_DIR%\app.py"
set "LOADER_FILE=%APP_DIR%\loader.py"
set "HASH_FILE=%VENV_DIR%\m2g_requirements.sha256"
set "PYTHONUNBUFFERED=1"
set "PIP_DISABLE_PIP_VERSION_CHECK=1"
set "RC=0"
set "ERR_MSG="

cd /d "%APP_DIR%"
echo M2G_STAGE=validating_app
echo Checking the generated app files...
if not exist "%APP_FILE%" goto :missing_files
if not exist "%LOADER_FILE%" goto :missing_files
if not exist "%REQ_FILE%" goto :missing_files
echo M2G_STAGE=preparing_environment
echo Preparing the local environment for this app...

if not exist "%VENV_PY%" goto :find_python
echo M2G_STAGE=env_reused
goto :verify_venv

:find_python
echo Checking for Python 3.12...
set "PY_CMD="
py -3.12 -c "import sys" >nul 2>&1
if not errorlevel 1 set "PY_CMD=py -3.12"
if defined PY_CMD goto :create_venv
python -c "import sys; sys.exit(0 if sys.version_info[:2] == (3, 12) else 1)" >nul 2>&1
if not errorlevel 1 set "PY_CMD=python"
if defined PY_CMD goto :create_venv
set "RC=11"
set "ERR_MSG=Python 3.12 was not found. Install Python 3.12 from python.org, then run this file again. No other Python version is used."
goto :fail

:create_venv
echo M2G_STAGE=creating_environment
echo Creating the virtual environment in .venv using %PY_CMD% ...
%PY_CMD% -m venv "%VENV_DIR%"
if errorlevel 1 goto :venv_failed
if not exist "%VENV_PY%" goto :venv_failed

:verify_venv
"%VENV_PY%" -c "import sys; sys.exit(0 if sys.version_info[:2] == (3, 12) else 1)" >nul 2>&1
if errorlevel 1 goto :venv_wrong_version
echo Using the app virtual environment in .venv

if not exist "%VENV_DIR%\Scripts\activate.bat" goto :activate_failed
call "%VENV_DIR%\Scripts\activate.bat"
if errorlevel 1 goto :activate_failed

set "REQ_HASH="
REM certutil handles paths with spaces; the first output line is a header and the last a status line, neither is the hash.
for /f "skip=1 delims=" %%H in ('certutil -hashfile "%REQ_FILE%" SHA256 2^>nul') do echo %%H| findstr /i /c:"certutil" >nul || if not defined REQ_HASH set "REQ_HASH=%%H"
if not defined REQ_HASH goto :install_deps
if not exist "%HASH_FILE%" goto :install_deps
findstr /x /c:"%REQ_HASH%" "%HASH_FILE%" >nul 2>&1
if errorlevel 1 goto :install_deps
echo Dependencies are unchanged; reusing the existing environment.
echo M2G_STAGE=deps_unchanged
goto :start_app

:install_deps
echo M2G_STAGE=upgrading_pip
echo Upgrading pip...
"%VENV_PY%" -m pip install --upgrade pip
if errorlevel 1 echo WARNING: pip could not be upgraded; continuing with the installed version.
echo M2G_STAGE=installing_dependencies
echo Installing dependencies from requirements.txt...
"%VENV_PY%" -m pip install -r "%REQ_FILE%"
if errorlevel 1 goto :install_failed
if not defined REQ_HASH goto :start_app
>"%HASH_FILE%" echo %REQ_HASH%

:start_app
echo.
echo M2G_STAGE=starting_application
echo Starting Gradio application...
echo Keep this window open while the application is running.
echo The Gradio URL will be printed below.
echo.
"%VENV_PY%" -u "%APP_FILE%"
set "RC=%ERRORLEVEL%"
if "%RC%"=="0" goto :app_stopped
echo.
echo ERROR: app.py exited with code %RC%. Review the messages above.
goto :finish

:app_stopped
echo.
echo The application has stopped.
goto :finish

:missing_files
set "RC=10"
set "ERR_MSG=app.py, loader.py or requirements.txt was not found next to this launcher. Generate the app again."
goto :fail

:venv_failed
set "RC=12"
set "ERR_MSG=The virtual environment could not be created in the .venv folder."
goto :fail

:venv_wrong_version
set "RC=12"
set "ERR_MSG=The existing .venv folder is not a Python 3.12 environment. Delete the .venv folder and run this file again."
goto :fail

:activate_failed
set "RC=14"
set "ERR_MSG=The virtual environment could not be activated. Delete the .venv folder and run this file again."
goto :fail

:install_failed
set "RC=13"
set "ERR_MSG=Dependency installation failed. Review the pip output above, fix the reported package issue, then run this file again."
goto :fail

:fail
echo.
echo ERROR: %ERR_MSG%

:finish
if not defined M2G_NO_PAUSE pause
exit /b %RC%
"""
