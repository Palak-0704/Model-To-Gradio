"""Run a generated Gradio app on this machine: the security checks and the structured status the web API exposes.

``local_workflow.LocalLauncher`` owns one app process (launch ``run_local.bat``, capture its output, detect the Gradio URL,
stop the whole process tree). This module is the gate in front of it:

* ``resolve_app_dir``      - the slug must name a direct child of the configured output directory (no traversal, no links).
* ``verify_generated_app`` - only apps the generation pipeline produced AND that still pass the code/requirements validators.
* ``prepare_launcher``     - the BAT that runs is always the backend's own template, never a user-supplied or edited script.
* ``run_hardware_preflight`` - Windows only: probe this PC against the model's ``analysis.json`` hardware block (``local_hardware``) after
                               verification and before the launcher is prepared; only a clear, unavoidable failure refuses the launch.
* ``start_local_app``      - the three above, then an asynchronous launch; a second launch of a running app is a 409.
* ``snapshot_payload`` / ``logs_payload`` - JSON-safe status and incremental logs for the API.

Nothing here executes repository code; the only things ever run are the backend-written ``run_local.bat`` and, through it,
the generated ``app.py`` inside the app's own ``.venv``.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from .local_hardware import WARN, Finding, HardwareReport, check_local_hardware
from .local_script import launcher_filename, windows_launcher
from .local_workflow import (
    CREATING_ENVIRONMENT,
    ENVIRONMENT,
    FAILED,
    HARDWARE,
    IDLE,
    INSTALLING_DEPENDENCIES,
    PREFLIGHT,
    PREPARING,
    RUNNING,
    STARTING,
    STOPPED,
    AlreadyRunningError,
    LocalLauncher,
    LocalSnapshot,
)
from .package_store import PackageBusyError, model_lock, verify_package
from .security import redact_secrets
from .validate import combine_package_source, require_package_link, validate_app_code, validate_requirements_text

# Files the generation pipeline writes before an app counts as generated. The launcher (run_<folder>.bat) is not listed: the backend
# (re)writes it from its template right before every launch, so apps generated before the launcher existed can still run.
REQUIRED_ARTIFACTS = ("app.py", "loader.py", "requirements.txt", "analysis.json", "colab_notebook.ipynb")

# How long a launch waits for an analysis/generation of the same model to finish replacing its files before it answers 409.
LAUNCH_LOCK_WAIT_S = 3.0

# Same shape the pipeline's safe_slug() produces: starts with a letter/digit, then letters, digits, '.', '_' or '-'.
_SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")

STAGE_LABELS = {
    IDLE: "Not started",
    PREPARING: "Preparing environment",
    HARDWARE: "Preparing environment",
    ENVIRONMENT: "Preparing environment",
    CREATING_ENVIRONMENT: "Creating virtual environment",
    INSTALLING_DEPENDENCIES: "Installing dependencies",
    PREFLIGHT: "Checking compatibility",
    STARTING: "Starting Gradio app",
    RUNNING: "App ready",
    STOPPED: "Stopped",
    FAILED: "Failed",
}
_STARTING_STATES = {PREPARING, HARDWARE, ENVIRONMENT, CREATING_ENVIRONMENT, INSTALLING_DEPENDENCIES, PREFLIGHT, STARTING}


class LocalRunError(Exception):
    """A request the backend refuses. ``status`` is the HTTP status the API answers with."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


# --------------------------------------------------------------------------- validation


def validate_slug(slug: Any) -> str:
    """The app identifier only; the directory itself is checked by ``resolve_app_dir``."""
    if not isinstance(slug, str) or not _SLUG_RE.match(slug) or ".." in slug:
        raise LocalRunError("Invalid app identifier.", 400)
    return slug


def resolve_app_dir(output_root: Any, slug: Any) -> Path:
    """The generated app's directory: a real (non-link) direct child of the output root, or a LocalRunError."""
    validate_slug(slug)
    root = Path(output_root).expanduser().resolve()
    candidate = root / slug
    if candidate.is_symlink():
        raise LocalRunError("The app directory must not be a link.", 400)
    resolved = candidate.resolve()  # also resolves Windows junctions
    if resolved.parent != root:
        raise LocalRunError("The app directory is outside the configured output directory.", 400)
    if not resolved.is_dir():
        raise LocalRunError("App directory not found. Generate the app first.", 404)
    return resolved


def _regular_file(app_dir: Path, name: str) -> Optional[Path]:
    path = app_dir / name
    if path.is_symlink() or not path.is_file() or path.resolve().parent != app_dir:
        return None
    return path


def ensure_package_matches_analysis(app_dir: Path, report: Any, analysis: Any = None) -> None:
    """analysis.json and the code beside it must come from the same generation run: the Hugging Face model and the pinned revision
    in loader.py (``report`` is what ``validate_app_code`` returned for it) must be the ones analysis.json records. A failed
    regeneration leaves the new analysis.json next to the previous run's app.py/loader.py, and that mixed folder must not be run or
    sent to Colab. Only values that are really present on both sides are compared; nothing is guessed."""
    if analysis is None:
        path = _regular_file(app_dir, "analysis.json")
        if path is None:
            return
        try:
            analysis = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
    if not isinstance(analysis, dict) or not isinstance(report, dict):
        return
    model_source = analysis.get("model_source")  # a GitHub analysis loads its linked Hugging Face model
    ref = model_source if isinstance(model_source, dict) and model_source.get("repo_id") else analysis.get("source")
    want_id = ref.get("repo_id") if isinstance(ref, dict) else None
    want_rev = analysis.get("revision")
    problems = []
    if isinstance(want_id, str) and isinstance(report.get("model_id"), str) and want_id != report["model_id"]:
        problems.append(f"loader.py loads {report['model_id']} but analysis.json describes {want_id}")
    if isinstance(want_rev, str) and want_rev and isinstance(report.get("revision"), str) and want_rev != report["revision"]:
        problems.append(f"loader.py pins revision {report['revision'][:12]} but analysis.json records {want_rev[:12]}")
    if problems:
        raise LocalRunError(redact_secrets("The generated files do not belong together: " + "; ".join(problems)
                                           + ". An earlier generation probably failed part-way; generate the app again."), 422)


def verify_generated_app(app_dir: Path, settings: Any = None) -> Tuple[str, str]:
    """Return (loader.py + app.py as one program text, requirements.txt text) once the folder is a complete, still-valid generated app."""
    missing = [name for name in REQUIRED_ARTIFACTS if _regular_file(app_dir, name) is None]
    if missing:
        raise LocalRunError("Generate the app before running it locally. Missing: " + ", ".join(missing), 400)
    try:
        analysis = json.loads(_regular_file(app_dir, "analysis.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise LocalRunError("analysis.json is unreadable; generate the app again.", 400)
    if not isinstance(analysis, dict):
        raise LocalRunError("analysis.json is not a valid analysis; generate the app again.", 400)
    try:
        app_text = _regular_file(app_dir, "app.py").read_text(encoding="utf-8")
        loader_text = _regular_file(app_dir, "loader.py").read_text(encoding="utf-8")
        app_code = combine_package_source(app_text, loader_text)
        requirements = _regular_file(app_dir, "requirements.txt").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise LocalRunError(f"The generated files could not be read: {type(exc).__name__}", 400)
    try:  # the same validators generation uses; an edited or corrupted app must not run
        require_package_link(app_text, loader_text)  # app.py must import the package's own loader.py
        report = validate_app_code(app_code, settings)
        validate_requirements_text(requirements)
    except ValueError as exc:
        raise LocalRunError(redact_secrets(f"The generated app failed validation and was not started: {exc}"), 422)
    ensure_package_matches_analysis(app_dir, report, analysis)  # same model and pinned revision as analysis.json
    # Phase 4.2: the files must be the ones the package manifest recorded (SHA-256 per artifact, same model and revision). A folder
    # generated before the manifest existed has none and stays launchable.
    check = verify_package(app_dir, expect_model_id=report.get("model_id") if isinstance(report, dict) else None,
                           expect_revision=report.get("revision") if isinstance(report, dict) else None)
    if check.state == "invalid":
        raise LocalRunError(check.message(), 422)
    if check.state == "ok":
        readiness = (check.manifest or {}).get("readiness")
        if isinstance(readiness, dict) and readiness.get("state") != "ready":
            reason = str(readiness.get("reason") or "Model inference has not been implemented.")
            raise LocalRunError("This package is an incomplete scaffold and cannot be launched: " + reason, 422)
    from .native_readiness import incomplete_loader_reason  # judged from the code too: the manifest may be absent (a legacy folder)
    placeholder = incomplete_loader_reason(app_code)
    if placeholder:
        raise LocalRunError("This package is an incomplete scaffold and cannot be launched: " + placeholder, 422)
    return app_code, requirements


def prepare_launcher(app_dir: Path) -> Path:
    """Make the folder's own launcher (``run_<folder>.bat``) exactly the backend template (CRLF line endings, which cmd.exe needs for labels).

    The file is only written when it differs, because cmd.exe reads a running batch file incrementally; callers check that the
    app is not already running first.
    """
    bat = app_dir / launcher_filename(app_dir.name)
    if bat.is_symlink():
        raise LocalRunError(f"{bat.name} must not be a link.", 400)
    expected = windows_launcher(app_dir.name).replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8")
    try:
        if not bat.is_file() or bat.read_bytes() != expected:
            bat.write_bytes(expected)
    except OSError as exc:
        raise LocalRunError(f"{bat.name} could not be prepared: {type(exc).__name__}", 500)
    return bat


# --------------------------------------------------------------------------- hardware preflight


def _read_analysis(app_dir: Path) -> Optional[Dict[str, Any]]:
    """The app's analysis.json as a dict, or None. ``verify_generated_app`` has just validated this same file (and the caller holds the
    model lock, so nothing replaces it meanwhile); a failure here only means the hardware check runs without requirements."""
    path = _regular_file(app_dir, "analysis.json")
    if path is None:
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def run_hardware_preflight(app_dir: Path) -> HardwareReport:
    """Evaluate this PC against the model's hardware needs (``local_hardware.check_local_hardware`` decides; nothing is re-implemented
    here). Never raises: if the check itself fails unexpectedly the result is one warning, never a block."""
    try:
        return check_local_hardware(app_dir, _read_analysis(app_dir))
    except Exception as exc:  # a diagnostic must never be the reason a launch fails
        return HardwareReport((Finding(WARN, "check_failed", f"The hardware check could not be completed ({type(exc).__name__}); "
                                                             "the launch was not blocked."),), "")


def hardware_snapshot_text(report: HardwareReport) -> str:
    """One line for ``LocalSnapshot.hardware`` (the existing ``hardware`` field of the status API): the probed facts, then each warning."""
    parts = [report.summary] if report.summary else []
    parts += ["Warning: " + finding.message for finding in report.warnings]
    return " | ".join(parts)


# --------------------------------------------------------------------------- start


def start_local_app(launcher: LocalLauncher, output_root: Any, slug: str, settings: Any = None,
                    *, is_windows: Optional[bool] = None) -> LocalSnapshot:
    """Validate, prepare and launch ``slug`` asynchronously. Raises LocalRunError (409 when it is already running)."""
    app_dir = resolve_app_dir(output_root, slug)
    try:
        with model_lock(output_root, slug, timeout=LAUNCH_LOCK_WAIT_S):  # never read or launch a package while it is being replaced
            return _start_verified(launcher, app_dir, settings, is_windows)
    except PackageBusyError:
        raise LocalRunError("This model is being analysed or generated right now. Start it when that has finished.", 409)


def _start_verified(launcher: LocalLauncher, app_dir: Path, settings: Any, is_windows: Optional[bool]) -> LocalSnapshot:
    app_code, requirements = verify_generated_app(app_dir, settings)
    if launcher.active:
        raise LocalRunError("This app is already starting or running.", 409)
    windows = (os.name == "nt") if is_windows is None else is_windows
    hardware_text, hardware_log = "", []
    if windows:
        # Hardware preflight: after verification, before anything is written or started. Only a clear, unavoidable failure refuses the
        # launch (422, like the other "this app cannot be started" refusals); warnings travel with the launch into its snapshot.
        report = run_hardware_preflight(app_dir)
        if report.blocked:
            raise LocalRunError(redact_secrets(report.block_message()), 422)
        hardware_text, hardware_log = hardware_snapshot_text(report), report.log_lines()
    try:
        if windows:
            return launcher.launch_batch(prepare_launcher(app_dir), app_dir, hardware=hardware_text, hardware_log=hardware_log)
        # run_local.bat only works on Windows; elsewhere keep the previous in-process local launch (same validated inputs).
        return launcher.launch(app_code, requirements, app_dir)
    except AlreadyRunningError:
        raise LocalRunError("This app is already starting or running.", 409)
    except (FileNotFoundError, ValueError) as exc:
        raise LocalRunError(redact_secrets(str(exc)), 400)


# --------------------------------------------------------------------------- status payloads


def execution_status(state: str) -> str:
    """idle | starting | running | stopped | failed"""
    if state == RUNNING:
        return "running"
    if state in (FAILED, STOPPED, IDLE):
        return {FAILED: "failed", STOPPED: "stopped", IDLE: "idle"}[state]
    return "starting" if state in _STARTING_STATES else "idle"


def describe_snapshot(snap: Optional[LocalSnapshot], slug: str = "") -> Dict[str, Any]:
    """Structured fields of one snapshot. ``url`` is only filled once the Gradio server answered (state 'running')."""
    state = getattr(snap, "state", IDLE) if snap is not None else IDLE
    return {
        "app": slug or getattr(snap, "app", "") or "",
        "slug": slug or getattr(snap, "app", "") or "",
        "state": state,
        "status": execution_status(state),
        "stage": state,
        "stage_label": STAGE_LABELS.get(state, state),
        "pid": getattr(snap, "pid", None),
        "url": (getattr(snap, "url", "") or "") if state == RUNNING else "",
        "error": getattr(snap, "error", "") or "",
        "exit_code": getattr(snap, "exit_code", None),
        "python": getattr(snap, "python", ""),
        "hardware": getattr(snap, "hardware", ""),
        "environment": getattr(snap, "environment", ""),
    }


def progress_payload(launcher: LocalLauncher) -> Dict[str, Any]:
    """Structured live progress of the deployment (see ``local_progress``): evidence-based, never time-driven.

    ``status``: idle | running | waiting | possibly_stuck | completed | failed | stopped. ``warning`` is set when there has been
    no recent activity; ``error`` only when the deployment failed. The tracker's own fields are kept alongside the named aliases.
    """
    snap = launcher.progress_snapshot()
    return {
        **snap,
        "percentage": snap["percent"],
        "stage": snap["step_id"],
        "stage_label": snap["step_label"],
        "last_activity": snap["last_activity_at"],
    }


def snapshot_payload(slug: str, launcher: LocalLauncher, log_tail: int = 100) -> Dict[str, Any]:
    """Status response: the snapshot fields plus the latest log lines and where to poll for more."""
    payload = describe_snapshot(launcher.snapshot, slug)
    lines, next_index = launcher.read_logs(0)
    payload["running"] = bool(launcher.active)
    payload["logs"] = lines[-log_tail:]
    payload["log_next"] = next_index
    payload["logs_url"] = f"/api/local/{slug}/logs"
    payload["progress"] = progress_payload(launcher)
    return payload


def logs_payload(slug: str, launcher: LocalLauncher, since: int = 0) -> Dict[str, Any]:
    """Log lines with an absolute index >= ``since``; poll again with ``since=next`` to receive only new lines."""
    lines, next_index = launcher.read_logs(since)
    snap = describe_snapshot(launcher.snapshot, slug)
    return {
        "app": slug,
        "slug": slug,
        "status": snap["status"],
        "stage": snap["stage"],
        "stage_label": snap["stage_label"],
        "running": bool(launcher.active),
        "exit_code": snap["exit_code"],
        "error": snap["error"],
        "lines": lines,
        "logs": "\n".join(lines),
        "next": next_index,
    }
