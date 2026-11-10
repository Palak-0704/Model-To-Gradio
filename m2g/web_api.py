"""
Lightweight REST API server for the Model-to-Gradio frontend.

Exposes HTTP endpoints that the frontend (frontend/) calls via fetch().
Serves the frontend static files and translates HTTP requests into calls
against the existing m2g pipeline, local_workflow and colab_workflow
infrastructure. No logic is reimplemented here — everything is delegated
to the existing, tested code.

Endpoints
  POST /api/analyze                   Start analysis (async job)
  GET  /api/analyze/<job_id>          Poll analysis status
  GET  /api/apps                      List output/ directory apps
  GET  /api/apps/<slug>               Get one app detail
  GET  /api/apps/<slug>/files/<name>  Get file content
  POST /api/apps/<slug>/validate      Re-validate generated app
  POST /api/apps/<slug>/generate      Generate app (async job)
  GET  /api/generate/<job_id>         Poll generation status
  POST /api/local/<slug>/start        Start local deployment
  GET  /api/local/<slug>/status       Local deployment status + logs
  GET  /api/local/<slug>/logs         Incremental local logs (?since=<index>)
  POST /api/local/<slug>/stop         Stop local deployment
  GET  /api/colab/status              Colab CLI connection status
  POST /api/colab/check               Check Colab CLI + auth
  GET  /api/colab/gpus                Available GPU options
  POST /api/colab/<slug>/start        Start Colab deployment
  GET  /api/colab/<slug>/status       Colab deployment snapshot
  POST /api/colab/<slug>/stop         Stop Colab runtime
  GET  /api/colab/<slug>/logs         Fetch live logs
  GET  /api/activity                  Activity log
  GET  /*                             Serve frontend static files

Security: this server binds to 127.0.0.1 only by default. All path
components (slugs, file names) are validated before they reach the
filesystem. Secrets are never returned.
"""
from __future__ import annotations

import contextlib
import json
import logging
import mimetypes
import os
import re
import threading
import time
import traceback
import uuid
from dataclasses import asdict, dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

from .security import redact_secrets


@dataclass(frozen=True)
class ArtifactSelection:
    """Explicit authorization target; it contains no download authority by itself."""
    path: str
    revision: str


def _onnx_candidates(detail: Dict[str, Any]) -> tuple[list[str], Optional[str]]:
    analysis = detail.get("analysis") if isinstance(detail.get("analysis"), dict) else {}
    native = analysis.get("native") if isinstance(analysis.get("native"), dict) else {}
    contract = native.get("inference_contract") if isinstance(native.get("inference_contract"), dict) else {}
    paths = [p for p in contract.get("weight_files", []) if isinstance(p, str) and p.lower().endswith(".onnx")]
    revision = contract.get("revision") if isinstance(contract.get("revision"), str) else analysis.get("revision")
    return paths, revision if isinstance(revision, str) else None


def _selection_for(detail: Dict[str, Any], body: Dict[str, Any]) -> ArtifactSelection:
    paths, revision = _onnx_candidates(detail)
    selected = body.get("artifact_path")
    if selected is None and len(paths) == 1:
        selected = paths[0]
    if not isinstance(selected, str) or selected not in paths:
        raise ValueError("Select one declared ONNX artifact path; ambiguous or undeclared paths are refused.")
    if not revision or body.get("revision") != revision:
        raise ValueError("Artifact selection must name the stored pinned revision exactly.")
    return ArtifactSelection(selected, revision)


def _record_local_onnx(app_dir: Path, selection: ArtifactSelection, source: str) -> Dict[str, Any]:
    """Publish an explicit local file and atomically record its limited provenance.

    The file is never called authentic merely because its digest was calculated locally.
    """
    from .package_store import MANIFEST_NAME, model_lock, publish_verified_binary, sha256_file
    src = Path(source)
    if not source or src.is_symlink() or not src.is_file() or src.suffix.lower() != ".onnx":
        raise ValueError("Local artifact must be an existing regular .onnx file.")
    size, digest = src.stat().st_size, sha256_file(src)
    if size <= 0:
        raise ValueError("Local ONNX artifact is empty.")
    with model_lock(app_dir.parent, app_dir.name):
        target = publish_verified_binary(app_dir.parent, app_dir.name, src, "model.onnx", expected_size=size, expected_sha256=digest)
        manifest_path = app_dir / MANIFEST_NAME
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ValueError("Package manifest is required before an artifact can be recorded.") from exc
        manifest["onnx_artifact"] = {"selected_path": selection.path, "revision": selection.revision,
                                     "published_file": target.name, "size": size, "sha256": digest,
                                     "verification": "locally_calculated_only", "source": "user_provided_local",
                                     "authorization": "explicit_user_action"}
        readiness = manifest.setdefault("readiness", {})
        readiness["state"] = "scaffold"
        readiness["reason"] = "Artifact acquired and locally integrity-checked; runtime validation and an evidence-backed smoke fixture are still required."
        staged = manifest_path.with_suffix(".tmp")
        staged.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(staged, manifest_path)
    return manifest["onnx_artifact"]
from .security import redact_secrets as _redact  # the Colab start branch re-imports redact_secrets locally, which shadows it for that whole handler

log = logging.getLogger("m2g.api")

# ── Project layout ─────────────────────────────────────────────────────────
_HERE = Path(__file__).resolve().parent
_FRONTEND = _HERE.parent / "frontend"

# ── Slug / filename safety ──────────────────────────────────────────────────
_SLUG_RE = re.compile(r"^[A-Za-z0-9._\-]{1,120}$")
_FILE_RE = re.compile(r"^[A-Za-z0-9._\-]{1,80}$")

def _safe_slug(s: str) -> Optional[str]:
    s = s.strip().replace("/", "__")
    return s if _SLUG_RE.match(s) else None

def _safe_file(s: str) -> Optional[str]:
    s = s.strip()
    return s if _FILE_RE.match(s) else None

def _within(base: Path, target: Path) -> bool:
    """True only if target is inside base (no path traversal)."""
    try:
        target.resolve().relative_to(base.resolve())
        return True
    except ValueError:
        return False

# ── Activity log ────────────────────────────────────────────────────────────
_activity: List[Dict[str, Any]] = []
_activity_lock = threading.Lock()

def _log_activity(type_: str, title: str, message: str = "", status: str = "info", details: str = "") -> None:
    ev = {
        "id": uuid.uuid4().hex,
        "type": type_,
        "title": title,
        "message": message,
        "status": status,
        "details": details[:2000],
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    with _activity_lock:
        _activity.insert(0, ev)
        del _activity[500:]

# ── Async job tracking ──────────────────────────────────────────────────────
@dataclass
class Job:
    id: str
    type: str
    status: str = "pending"     # pending | running | done | failed
    stage: str = ""
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    files: List[str] = field(default_factory=list)
    pipeline_status: Optional[Dict[str, Any]] = None  # BuildResult.status_report(): steps, per-file artifacts, failed step, error
    started_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    key: str = ""  # what the request asked for (URL or slug); see _new_or_active_job

_jobs: Dict[str, Job] = {}
_jobs_lock = threading.Lock()

def _new_job(type_: str) -> Job:
    j = Job(id=uuid.uuid4().hex, type=type_)
    with _jobs_lock:
        _jobs[j.id] = j
    return j

def _new_or_active_job(type_: str, key: str) -> Any:
    """(job, created). A pipeline run is expensive (inspection, evidence, REA and an OpenCode generation of up to minutes), so a
    repeated request for the same URL / app while its job is still pending or running joins that job instead of starting a second
    run that would write the same output folder at the same time. Check and creation happen under one lock."""
    with _jobs_lock:
        if key:
            for existing in _jobs.values():
                if existing.type == type_ and existing.key == key and existing.status in ("pending", "running"):
                    return existing, False
        j = Job(id=uuid.uuid4().hex, type=type_, key=key)
        _jobs[j.id] = j
        return j, True

def _update_job(job: Job, **kw: Any) -> None:
    for k, v in kw.items():
        setattr(job, k, v)
    job.updated_at = time.time()

def _job_dict(job: Job) -> Dict[str, Any]:
    return {
        "job_id": job.id, "status": job.status, "stage": job.stage,
        "result": job.result, "error": job.error, "files": job.files,
        "pipeline_status": job.pipeline_status,
    }

# ── Per-slug local launchers ────────────────────────────────────────────────
_local_launchers: Dict[str, Any] = {}
_local_lock = threading.Lock()

def _local_launcher(slug: str) -> Any:
    with _local_lock:
        if slug not in _local_launchers:
            from .local_workflow import LocalLauncher
            _local_launchers[slug] = LocalLauncher()
        return _local_launchers[slug]

# ── Colab launcher (one per process) ───────────────────────────────────────
_colab_launchers: Dict[str, Any] = {}
_colab_lock = threading.Lock()

def _colab_launcher(slug: str) -> Any:
    with _colab_lock:
        if slug not in _colab_launchers:
            try:
                from .colab_cli import ColabCli
                from .colab_workflow import ColabLauncher
                cli = ColabCli()
                _colab_launchers[slug] = ColabLauncher(cli)
            except Exception as exc:
                log.error("Could not create ColabLauncher: %s", exc)
                raise
        return _colab_launchers[slug]

# ── Analysis ────────────────────────────────────────────────────────────────
def _missing_artifacts(result: Any) -> List[str]:
    """Files of the complete generated package (app.py, loader.py, requirements.txt, analysis.json, colab_notebook.ipynb and the
    model's own run_<folder>.bat) that are not on disk after a pipeline run."""
    from .pipeline import GENERATED_ARTIFACTS, required_artifacts
    if result.out_dir is None:
        return list(GENERATED_ARTIFACTS)
    return [name for name in required_artifacts(result.out_dir.name) if not (result.out_dir / name).is_file()]

def _run_analysis_job(job: Job, url: str, settings: Any) -> None:
    try:
        _update_job(job, status="running", stage="parse")
        from .pipeline import run_pipeline
        from .rea_client import make_runner

        # REA_ENABLED is set in web.py but this server runs independently.
        # Read it safely — if web.py was never imported, default to False.
        try:
            from .web import REA_ENABLED
        except Exception:
            REA_ENABLED = False

        rea_kw = {"rea": make_runner(settings)} if REA_ENABLED else {}
        result = run_pipeline(url, settings, write=True, **rea_kw)

        stage = result.stage
        _update_job(job, stage=stage, pipeline_status=result.status_report())

        if result.error:
            _update_job(job, status="failed", error=result.error, stage=stage, files=list(result.files))
            _log_activity("analysis", f"Analysis failed: {url[:60]}", result.error, "error")
            return

        missing = _missing_artifacts(result) if result.analysis and result.analysis.generatable else []
        if missing:
            message = "Generation completed without saving all artifacts: " + ", ".join(missing)
            _update_job(job, status="failed", stage="write", error=message, files=list(result.files))
            _log_activity("generation", f"Generation failed: {url[:60]}", message, "error")
            return

        analysis_dict = result.analysis.to_dict() if result.analysis else {}
        slug = result.out_dir.name if result.out_dir else url.replace("/", "__")
        analysis_dict["slug"] = slug
        analysis_dict["analysis_status"] = "completed"
        if getattr(result, "reused_package", False):  # the unchanged package's app code was reused; OpenCode was not called
            analysis_dict["package_reused"] = True
        if result.ok:
            analysis_dict["generation_status"] = "completed"
            analysis_dict["artifact_status"] = "saved"
        elif result.analysis and not result.analysis.generatable:
            analysis_dict["generation_status"] = "blocked"
            analysis_dict["artifact_status"] = "analysis_only"
        else:
            analysis_dict["generation_status"] = "failed"
            analysis_dict["artifact_status"] = "missing"

        _update_job(job, status="done", stage="done",
                    result=analysis_dict, files=list(result.files))
        _log_activity("analysis", f"Analyzed: {slug}", url, "success")
    except Exception as exc:
        tb = traceback.format_exc()
        log.error("Analysis job %s failed: %s\n%s", job.id, exc, tb)
        message = redact_secrets(str(exc))  # an unexpected exception can carry a URL or token from the request
        _update_job(job, status="failed", error=message)
        _log_activity("analysis", "Analysis error", message[:300], "error")

# ── Generation ──────────────────────────────────────────────────────────────
def _reuse_current_package(job: Job, slug: str, out_dir: Path, settings: Any) -> bool:
    """Finish ``job`` as done without any pipeline run when ``out_dir`` already holds a complete package that nothing changed since this same
    generator made it (manifest verified, same generation fingerprint inputs) and that still passes the launch validators. That skips
    the REA run, the evidence collection and the OpenCode call. Any doubt returns False and the normal pipeline runs."""
    from .package_store import generator_fingerprint, model_lock, package_is_current
    from .pipeline import required_artifacts
    generator = generator_fingerprint(settings)
    with model_lock(_output_dir(settings), slug):  # waits for a run that is writing this model, then looks at what it left
        required = required_artifacts(out_dir.name)
        if not package_is_current(out_dir, generator, required):
            return False
        try:
            from .local_runner import verify_generated_app
            verify_generated_app(out_dir, settings)
        except Exception:
            return False
    files = sorted(required)
    _update_job(job, status="done", stage="done", files=files, result={"slug": slug, "files": files, "reused": True})
    _log_activity("generation", f"Generated app: {slug}", "unchanged: the existing package was reused and OpenCode was not called", "success")
    return True

def _run_generation_job(job: Job, slug: str, settings: Any, force: bool = False) -> None:
    """Re-run the pipeline generate+write stages using the stored analysis.json. An unchanged, valid package is reused unless ``force``."""
    try:
        out_dir = _output_dir(settings) / slug
        analysis_file = out_dir / "analysis.json"

        _update_job(job, status="running", stage="loading_analysis")

        if not analysis_file.is_file():
            _update_job(job, status="failed", error="No analysis.json found. Analyze the model first.")
            return

        # Reconstruct the Analysis from the stored JSON via the inspector path.
        # The simplest correct approach is to re-run only the generate+write stages
        # of the pipeline using the stored source URL from analysis.json.
        _update_job(job, stage="parsing_analysis")
        data = json.loads(analysis_file.read_text(encoding="utf-8"))
        source = data.get("source", {})
        url = source.get("canonical_url", "")

        if not url:
            repo_id = source.get("repo_id", "")
            if repo_id:
                url = f"https://huggingface.co/{repo_id}"

        if not url:
            _update_job(job, status="failed", error="Could not determine model URL from analysis.json.")
            return

        if not force and _reuse_current_package(job, slug, out_dir, settings):
            return

        _update_job(job, stage="generating")
        from .pipeline import run_pipeline
        from .rea_client import make_runner
        from . import web as _web

        rea_kw = {"rea": make_runner(settings)} if getattr(_web, 'REA_ENABLED', False) else {}
        result = run_pipeline(url, settings, write=True, **rea_kw)
        _update_job(job, pipeline_status=result.status_report())

        if result.error:
            _update_job(job, status="failed", error=result.error, files=list(result.files))
            _log_activity("generation", f"Generation failed: {slug}", result.error[:300], "error")
            return

        if not result.ok:
            notes = "; ".join(result.notes) if result.notes else "Model not generatable"
            _update_job(job, status="failed", error=notes, files=list(result.files))
            _log_activity("generation", f"Generation blocked: {slug}", notes[:300], "warning")
            return

        missing = _missing_artifacts(result)
        if missing:
            message = "Generation completed without saving all artifacts: " + ", ".join(missing)
            _update_job(job, status="failed", stage="write", error=message, files=list(result.files))
            _log_activity("generation", f"Generation failed: {slug}", message, "error")
            return

        files_written = list(result.files)
        done = {"slug": result.out_dir.name if result.out_dir else slug, "files": files_written}
        if getattr(result, "reused_package", False):
            done["reused"] = True  # the existing app code was reused; OpenCode was not called
        _update_job(job, status="done", stage="done", files=files_written, result=done)
        _log_activity("generation", f"Generated app: {slug}", "", "success")
    except Exception as exc:
        tb = traceback.format_exc()
        log.error("Generation job %s failed: %s\n%s", job.id, exc, tb)
        message = redact_secrets(str(exc))
        _update_job(job, status="failed", error=message)
        _log_activity("generation", f"Generation failed: {slug}", message[:300], "error")

# ── Output directory helpers ────────────────────────────────────────────────
def _output_dir(settings: Any) -> Path:
    """Return the one configured output root used by every API file operation."""
    return Path(settings.output_dir).expanduser().resolve()

def _list_apps(settings: Any) -> List[Dict[str, Any]]:
    output = _output_dir(settings)
    apps = []
    if not output.is_dir():
        return apps
    for entry in sorted(output.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if not entry.is_dir() or entry.name.startswith('.'):
            continue
        app = _summarize_app(entry)
        if app:
            apps.append(app)
    return apps

def _summarize_app(path: Path) -> Optional[Dict[str, Any]]:
    try:
        slug = path.name
        from .package_store import model_lock
        with model_lock(path.parent, slug, timeout=0):  # never expose a half-committed package in the listing
            analysis_file = path / "analysis.json"
            has_app = (path / "app.py").is_file()
            task = None
            status = "partial"
            created_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(path.stat().st_mtime))
            if analysis_file.is_file():
                try:
                    data = json.loads(analysis_file.read_text(encoding="utf-8"))
                    task = data.get("task")
                    status = data.get("status", "partial")
                except Exception:
                    pass
            readiness = {"state": "unknown", "reason": "No package manifest."}
            manifest_file = path / ".m2g_manifest.json"
            if manifest_file.is_file():
                try:
                    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
                    if isinstance(manifest.get("readiness"), dict):
                        readiness = manifest["readiness"]
                except Exception:
                    readiness = {"state": "invalid", "reason": "Package manifest is unreadable."}
            return {"slug": slug, "has_app": has_app, "task": task, "status": status, "readiness": readiness, "created_at": created_at}
    except Exception:
        return None

def _get_app_detail(slug: str, settings: Any) -> Optional[Dict[str, Any]]:
    output = _output_dir(settings)
    path = output / slug
    if not path.is_dir() or not _within(output, path):
        return None
    from .package_store import model_lock
    try:
        with model_lock(output, slug, timeout=3.0):
            summary = _summarize_app(path)
            if not summary:
                return None
            files = [f.name for f in sorted(path.iterdir())
                     if f.is_file() and not f.name.startswith('.') and _FILE_RE.match(f.name)]
            summary["files"] = files
            analysis_file = path / "analysis.json"
            if analysis_file.is_file():
                try:
                    summary["analysis"] = json.loads(analysis_file.read_text(encoding="utf-8"))
                except Exception:
                    pass
            return summary
    except Exception:
        return None

def _read_package_source(pkg_dir: Path) -> str:
    """app.py + loader.py of a generated package as the one program text the validators and the Colab launcher work on."""
    from .validate import combine_package_source, require_package_link
    app = (pkg_dir / "app.py").read_text(encoding="utf-8")
    loader_file = pkg_dir / "loader.py"
    loader = loader_file.read_text(encoding="utf-8") if loader_file.is_file() else None
    require_package_link(app, loader)  # raises ValidationError: app.py must import loader.py, and that file must exist
    return combine_package_source(app, loader)

# ── Validation helper ───────────────────────────────────────────────────────
def _validate_app(slug: str, settings: Any) -> Dict[str, Any]:
    output = _output_dir(settings)
    try:
        from .package_store import model_lock
        with model_lock(output, slug, timeout=3.0):
            app_file = output / slug / "app.py"
            if not app_file.is_file():
                return {"ok": False, "message": "app.py not found"}
            from .validate import validate_app_code
            code = _read_package_source(output / slug)
            report = validate_app_code(code, settings)
            return {"ok": True, "message": "Validation passed", "report": report}
    except Exception as exc:
        return {"ok": False, "message": str(exc)}

# ── Colab snapshot helper ───────────────────────────────────────────────────
def _snapshot_to_dict(snap: Any) -> Dict[str, Any]:
    try:
        d = asdict(snap) if hasattr(snap, '__dataclass_fields__') else vars(snap)
        return {k: v for k, v in d.items() if not k.startswith('_')}
    except Exception:
        return {}

# ── Request handler ─────────────────────────────────────────────────────────
class ApiHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        log.debug(fmt, *args)

    def _cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors_headers()
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        qs = parse_qs(parsed.query)

        try:
            # Route to API only when path starts with "/api/" (with trailing slash)
            # or is exactly "/api". This prevents "/api.js" from being treated as an
            # API path — it is a static frontend file.
            if path == "/api" or path.startswith("/api/"):
                self._handle_api_get(path, qs)
            else:
                self._serve_static(path)
        except Exception as exc:
            log.error("GET %s error: %s", path, exc)
            self._json_error(500, str(exc))

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        body = {}
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length > 0:
                raw = self.rfile.read(length)
                body = json.loads(raw) if raw else {}
        except Exception:
            pass

        try:
            # Same guard: only route to API when path starts with "/api/"
            if path == "/api" or path.startswith("/api/"):
                self._handle_api_post(path, body)
            else:
                self._json_error(404, "Not found")
        except Exception as exc:
            log.error("POST %s error: %s", path, exc)
            self._json_error(500, str(exc))

    # ── Static file serving ──────────────────────────────────────────────
    def _serve_static(self, path: str) -> None:
        if path == "/" or path == "":
            path = "/index.html"

        # Strip leading slash
        rel = path.lstrip("/")
        target = (_FRONTEND / rel).resolve()

        if not _within(_FRONTEND, target):
            self._json_error(403, "Forbidden")
            return

        if not target.is_file():
            # Try index.html fallback (SPA)
            index = _FRONTEND / "index.html"
            if index.is_file():
                target = index
            else:
                self._json_error(404, "Not found")
                return

        mime, _ = mimetypes.guess_type(str(target))
        # Ensure JS files are served with the correct MIME type for ES modules.
        # Some Windows registry entries return 'text/plain' for .js which causes
        # browsers to refuse to execute the module.
        suffix = target.suffix.lower()
        if suffix in (".js", ".mjs"):
            mime = "application/javascript; charset=utf-8"
        elif suffix == ".css":
            mime = "text/css; charset=utf-8"
        elif suffix in (".html", ".htm"):
            mime = "text/html; charset=utf-8"
        elif not mime:
            mime = "application/octet-stream"
        content = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(content)))
        # Allow browser to cache static assets for the session
        self.send_header("Cache-Control", "no-cache")
        self._cors_headers()
        self.end_headers()
        self.wfile.write(content)

    # ── API GET routing ──────────────────────────────────────────────────
    def _handle_api_get(self, path: str, qs: dict) -> None:
        settings = _get_settings()

        # /api/analyze/<job_id>
        m = re.match(r"^/api/analyze/([A-Za-z0-9_-]+)$", path)
        if m:
            job_id = m.group(1)
            with _jobs_lock:
                job = _jobs.get(job_id)
            if not job:
                self._json_error(404, "Job not found")
                return
            self._json_ok(_job_dict(job))
            return

        # /api/apps
        if path == "/api/apps":
            self._json_ok(_list_apps(settings))
            return

        # /api/apps/<slug>
        m = re.match(r"^/api/apps/([^/]+)$", path)
        if m:
            slug = _safe_slug(m.group(1))
            if not slug:
                self._json_error(400, "Invalid slug")
                return
            detail = _get_app_detail(slug, settings)
            if not detail:
                self._json_error(404, "App not found")
                return
            self._json_ok(detail)
            return

        # /api/apps/<slug>/files/<filename>
        m = re.match(r"^/api/apps/([^/]+)/files/([^/]+)$", path)
        if m:
            slug = _safe_slug(m.group(1))
            fname = _safe_file(m.group(2))
            if not slug or not fname:
                self._json_error(400, "Invalid slug or filename")
                return
            output = _output_dir(settings)
            file_path = output / slug / fname
            if not _within(output, file_path) or not file_path.is_file():
                self._json_error(404, "File not found")
                return
            try:
                from .package_store import PackageBusyError, model_lock
                try:
                    with model_lock(output, slug, timeout=3.0):
                        content = file_path.read_text(encoding="utf-8", errors="replace")
                        lines = content.count("\n")
                except PackageBusyError:
                    self._json_error(409, "This model is being analysed or generated right now. Try again when it has finished.")
                    return
                # Redact secrets from returned content
                from .security import redact_secrets
                content = redact_secrets(content)
                self._json_ok({"filename": fname, "content": content, "lines": lines})
            except Exception as exc:
                self._json_error(500, str(exc))
            return

        # Metadata-only ONNX candidates.  This endpoint deliberately never fetches an
        # artifact: it is the selection step that precedes a separately authorized acquisition.
        m = re.match(r"^/api/apps/([^/]+)/artifacts/onnx$", path)
        if m:
            slug = _safe_slug(m.group(1))
            detail = _get_app_detail(slug, settings) if slug else None
            if not detail:
                self._json_error(404, "App package not found"); return
            paths, revision = _onnx_candidates(detail)
            candidates = [{"path": p, "revision": revision, "size": None, "checksum": "not_acquired"} for p in paths]
            self._json_ok({"candidates": candidates, "selection_required": len(candidates) != 1,
                           "notice": "Metadata only: selecting a candidate does not authorize a download."})
            return

        # /api/generate/<job_id>
        m = re.match(r"^/api/generate/([A-Za-z0-9_-]+)$", path)
        if m:
            job_id = m.group(1)
            with _jobs_lock:
                job = _jobs.get(job_id)
            if not job:
                self._json_error(404, "Job not found")
                return
            self._json_ok(_job_dict(job))
            return

        # /api/local/<slug>/status and /api/local/<slug>/logs
        m = re.match(r"^/api/local/([^/]+)/(status|logs)$", path)
        if m:
            from .local_runner import LocalRunError, logs_payload, snapshot_payload, validate_slug
            try:
                slug = validate_slug(m.group(1))
                since = int(qs.get("since", ["0"])[0]) if m.group(2) == "logs" else 0
            except LocalRunError as exc:
                self._json_error(exc.status, exc.message); return
            except ValueError:
                self._json_error(400, "since must be an integer"); return
            launcher = _local_launcher(slug)
            if m.group(2) == "logs":
                self._json_ok(logs_payload(slug, launcher, max(0, since)))
            else:
                self._json_ok(snapshot_payload(slug, launcher))
            return

        # /api/colab/status
        if path == "/api/colab/status":
            try:
                from .colab_cli import ColabCli
                import shutil
                cli = ColabCli()
                # Use the .command attribute (list) or .available property if it exists
                available = getattr(cli, 'available', None)
                if callable(available):
                    exists = available()
                elif isinstance(available, bool):
                    exists = available
                else:
                    # Fall back: check the first element of .command list
                    cmd = getattr(cli, 'command', None)
                    if cmd and isinstance(cmd, (list, tuple)) and len(cmd) > 0:
                        exe = cmd[0]
                        from pathlib import Path as _P
                        exists = _P(exe).is_file() or shutil.which(exe) is not None
                    else:
                        exists = False
                self._json_ok({"status": "ok" if exists else "unavailable",
                               "message": "CLI found — use Check Connection to verify auth" if exists else "Colab CLI not found"})
            except Exception as exc:
                self._json_ok({"status": "unchecked", "message": "Use Check Connection to verify"})
            return

        # /api/colab/gpus
        if path == "/api/colab/gpus":
            try:
                from .colab_cli import ColabCli
                from .colab_workflow import CPU_LABEL
                cli = ColabCli()
                choices = cli.supported_gpus()
                gpus = [CPU_LABEL] + list(choices.names)
                self._json_ok({"gpus": gpus})
            except Exception as exc:
                self._json_ok({"gpus": ["CPU (no GPU)", "T4", "L4", "A100"]})
            return

        # /api/colab/<slug>/status
        m = re.match(r"^/api/colab/([^/]+)/status$", path)
        if m:
            slug = _safe_slug(m.group(1))
            if not slug:
                self._json_error(400, "Invalid slug"); return
            try:
                launcher = _colab_launcher(slug)
                snap = launcher.snapshot()
                self._json_ok(_snap_colab(snap))
            except Exception as exc:
                self._json_ok({"state": "idle", "message": str(exc)})
            return

        # /api/colab/<slug>/logs
        m = re.match(r"^/api/colab/([^/]+)/logs$", path)
        if m:
            slug = _safe_slug(m.group(1))
            if not slug:
                self._json_error(400, "Invalid slug"); return
            try:
                launcher = _colab_launcher(slug)
                logs_text = launcher.fetch_logs()
                self._json_ok({"logs": logs_text})
            except Exception as exc:
                self._json_ok({"logs": str(exc)})
            return

        # /api/activity
        if path == "/api/activity":
            limit = int(qs.get("limit", ["100"])[0])
            with _activity_lock:
                events = _activity[:limit]
            self._json_ok(events)
            return

        self._json_error(404, "API endpoint not found")

    # ── API POST routing ─────────────────────────────────────────────────
    def _handle_api_post(self, path: str, body: dict) -> None:
        settings = _get_settings()

        # /api/analyze
        if path == "/api/analyze":
            url = str(body.get("url", "")).strip()
            if not url:
                self._json_error(400, "url is required"); return
            job, created = _new_or_active_job("analysis", url)
            if created:  # a repeated request for a URL that is already being analysed joins the running job
                _update_job(job, status="running", stage="parse")
                t = threading.Thread(target=_run_analysis_job, args=(job, url, settings), daemon=True, name=f"analysis-{job.id}")
                t.start()
            self._json_ok({"job_id": job.id, "status": "pending", "reused": not created}, 202)
            return

        # /api/apps/<slug>/validate
        m = re.match(r"^/api/apps/([^/]+)/validate$", path)
        if m:
            slug = _safe_slug(m.group(1))
            if not slug:
                self._json_error(400, "Invalid slug"); return
            result = _validate_app(slug, settings)
            self._json_ok(result)
            return

        # Explicit second step after metadata selection: a local file is acquired only
        # when this POST names both the stored revision and the selected artifact path.
        m = re.match(r"^/api/apps/([^/]+)/artifacts/onnx/local$", path)
        if m:
            slug = _safe_slug(m.group(1))
            detail = _get_app_detail(slug, settings) if slug else None
            if not detail:
                self._json_error(404, "App package not found"); return
            try:
                selection = _selection_for(detail, body)
                source = body.get("local_path")
                if not isinstance(source, str):
                    raise ValueError("local_path is required for explicit local artifact acquisition.")
                record = _record_local_onnx(_output_dir(settings) / slug, selection, source)
                _log_activity("artifact", f"Acquired local ONNX artifact: {slug}", selection.path, "success")
                self._json_ok({"selection": asdict(selection), "artifact": record,
                               "readiness": {"state": "scaffold", "reason": "Runtime validation remains required."}}, 201)
            except ValueError as exc:
                self._json_error(422, redact_secrets(str(exc)))
            except Exception as exc:
                self._json_error(400, redact_secrets(str(exc)))
            return

        # /api/apps/<slug>/generate
        m = re.match(r"^/api/apps/([^/]+)/generate$", path)
        if m:
            slug = _safe_slug(m.group(1))
            if not slug:
                self._json_error(400, "Invalid slug"); return
            job, created = _new_or_active_job("generation", slug)
            if created:
                _update_job(job, status="running", stage="loading_analysis")
                t = threading.Thread(target=_run_generation_job, args=(job, slug, settings, bool(body.get("force"))), daemon=True, name=f"gen-{job.id}")
                t.start()
            self._json_ok({"job_id": job.id, "status": "pending", "reused": not created}, 202)
            return

        # /api/local/<slug>/start
        m = re.match(r"^/api/local/([^/]+)/start$", path)
        if m:
            from .local_runner import LocalRunError, snapshot_payload, start_local_app, validate_slug
            try:
                slug = validate_slug(m.group(1))
                launcher = _local_launcher(slug)
                # Only a verified, validated app inside the configured output directory can run; run_local.bat is
                # always the backend template, never a path supplied by the request.
                start_local_app(launcher, _output_dir(settings), slug, settings)
                _log_activity("deployment", f"Started local: {slug}", "", "info")
                self._json_ok(snapshot_payload(slug, launcher), 202)
            except LocalRunError as exc:
                if exc.status != 409:
                    _log_activity("deployment", f"Local start refused: {m.group(1)[:60]}", exc.message[:300], "warning")
                self._json_error(exc.status, exc.message)
            except Exception as exc:
                self._json_error(500, str(exc))
            return

        # /api/local/<slug>/stop
        m = re.match(r"^/api/local/([^/]+)/stop$", path)
        if m:
            from .local_runner import LocalRunError, snapshot_payload, validate_slug
            try:
                slug = validate_slug(m.group(1))
                launcher = _local_launcher(slug)
                launcher.stop()
                _log_activity("deployment", f"Stopped local: {slug}", "", "info")
                self._json_ok(snapshot_payload(slug, launcher))
            except LocalRunError as exc:
                self._json_error(exc.status, exc.message)
            except Exception as exc:
                self._json_error(500, str(exc))
            return

        # /api/colab/check
        if path == "/api/colab/check":
            try:
                from .colab_cli import ColabCli
                cli = ColabCli()
                ok, msg = cli.identify()
                if ok:
                    state, check_msg = cli.check()
                    self._json_ok({"status": state, "message": check_msg})
                else:
                    self._json_ok({"status": "unavailable", "message": msg})
            except Exception as exc:
                self._json_ok({"status": "unavailable", "message": str(exc)})
            return

        # /api/colab/<slug>/start
        m = re.match(r"^/api/colab/([^/]+)/start$", path)
        if m:
            slug = _safe_slug(m.group(1))
            if not slug:
                self._json_error(400, "Invalid slug"); return
            out_dir = _output_dir(settings) / slug
            app_file = out_dir / "app.py"
            req_file = out_dir / "requirements.txt"
            if not app_file.is_file() or not (out_dir / "loader.py").is_file() or not req_file.is_file():
                self._json_error(400, "app.py, loader.py or requirements.txt not found"); return
            gpu = body.get("gpu") or None
            auth_list = body.get("auth") or None
            auth = tuple(auth_list) if isinstance(auth_list, list) and len(auth_list) == 2 else None
            from .package_store import PackageBusyError, model_lock, verify_package
            guard = contextlib.ExitStack()  # no launch from a package that an analysis/generation is replacing
            try:
                guard.enter_context(model_lock(_output_dir(settings), slug, timeout=3.0))
            except PackageBusyError:
                self._json_error(409, "This model is being analysed or generated right now. Start it when that has finished."); return
            try:
                from .config import Settings
                from .security import redact_secrets
                from .validate import ValidationError
                launcher = _colab_launcher(slug)
                try:
                    app_code = _read_package_source(out_dir)  # loader.py + app.py; the launcher sends them as the same two files
                except ValidationError as exc:  # app.py does not match loader.py: a client-side package problem, not a server fault
                    self._json_error(400, redact_secrets(f"Invalid app package: {exc}")); return
                try:  # same gate as a local run: the code must be the one analysis.json describes (same model, same pinned revision)
                    from .local_runner import LocalRunError, ensure_package_matches_analysis
                    from .validate import validate_app_code
                    app_report = validate_app_code(app_code, settings)
                    ensure_package_matches_analysis(out_dir, app_report)
                    manifest = verify_package(out_dir, expect_model_id=app_report.get("model_id"),
                                              expect_revision=app_report.get("revision"))  # Phase 4.2: files must match the manifest
                    if manifest.state == "invalid":
                        self._json_error(422, manifest.message()); return
                    readiness = (manifest.manifest or {}).get("readiness") if manifest.state == "ok" else None
                    if isinstance(readiness, dict) and readiness.get("state") != "ready":
                        reason = str(readiness.get("reason") or "Model inference has not been implemented.")
                        self._json_error(422, "This package is an incomplete scaffold and cannot be deployed: " + reason); return
                    from .native_readiness import incomplete_loader_reason
                    placeholder = incomplete_loader_reason(app_code)  # judged from the code too: the manifest may be absent or silent
                    if placeholder:
                        self._json_error(422, "This package is an incomplete scaffold and cannot be deployed: " + placeholder); return
                except ValidationError:
                    pass  # the Colab launcher validates this same text and reports the problem itself
                except LocalRunError as exc:
                    self._json_error(exc.status, exc.message); return
                reqs = req_file.read_text(encoding="utf-8")
                started = launcher.start_async(app_code, reqs, gpu, settings=settings, auth=auth)
                _log_activity("deployment", f"Started Colab: {slug}", gpu or "CPU", "info")
                snap = launcher.snapshot()
                self._json_ok({**_snap_colab(snap), "started": started}, 202)
            except Exception as exc:
                self._json_error(500, _redact(str(exc)))
            finally:
                guard.close()
            return

        # /api/colab/<slug>/stop
        m = re.match(r"^/api/colab/([^/]+)/stop$", path)
        if m:
            slug = _safe_slug(m.group(1))
            if not slug:
                self._json_error(400, "Invalid slug"); return
            try:
                launcher = _colab_launcher(slug)
                snap = launcher.stop()
                _log_activity("deployment", f"Stopped Colab: {slug}", "", "info")
                self._json_ok(_snap_colab(snap))
            except Exception as exc:
                self._json_error(500, str(exc))
            return

        self._json_error(404, "API endpoint not found")

    # ── Response helpers ─────────────────────────────────────────────────
    def _json_ok(self, data: Any, code: int = 200) -> None:
        body = json.dumps(data, default=str, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def _json_error(self, code: int, message: str) -> None:
        self._json_ok({"error": message}, code)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _get_settings() -> Any:
    from .config import Settings
    return Settings.from_env()

def _snap_local(snap: Any) -> Dict[str, Any]:
    """Convert LocalSnapshot to a JSON-safe dict."""
    if snap is None:
        return {"state": "idle"}
    d: Dict[str, Any] = {}
    for attr in ("state", "python", "hardware", "environment", "error", "pid", "url"):
        d[attr] = getattr(snap, attr, None)
    logs = getattr(snap, "logs", [])
    d["logs"] = logs[-100:] if logs else []
    return d

def _snap_colab(snap: Any) -> Dict[str, Any]:
    """Convert LaunchSnapshot to a JSON-safe dict."""
    if snap is None:
        return {"state": "idle"}
    d: Dict[str, Any] = {}
    for attr in ("state", "message", "error_kind", "session", "gpu_requested",
                 "hardware", "share_url", "url_check", "model_loaded", "selftest",
                 "selftest_detail", "auth_protected", "failed_stage", "error_category",
                 "error_code", "retryable", "recovery"):
        d[attr] = getattr(snap, attr, None)
    logs = getattr(snap, "logs", [])
    d["logs"] = logs[-100:] if logs else []
    return d

# ── Server ───────────────────────────────────────────────────────────────────

class WebApiServer:
    def __init__(self, host: str = "127.0.0.1", port: int = 8765) -> None:
        self.host, self.port = host, port
        self._server: Optional[HTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._server = HTTPServer((self.host, self.port), ApiHandler)
        self._server.allow_reuse_address = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True, name="m2g-api")
        self._thread.start()
        log.info("M2G API server running at http://%s:%d", self.host, self.port)

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()

    def block(self) -> None:
        """Block until interrupted."""
        import signal
        def _sig(*_):
            self.stop()
        signal.signal(signal.SIGINT, _sig)
        signal.signal(signal.SIGTERM, _sig)
        while self._thread and self._thread.is_alive():
            self._thread.join(1.0)


def run_server(host: str = "127.0.0.1", port: int = 8765) -> None:
    """Start the M2G API + frontend server and block."""
    from .ui_errors import configure_logging
    configure_logging()

    # Enable REA if available
    from . import web as _web
    _web.REA_ENABLED = os.environ.get("M2G_REA_ENABLED", "1").strip().lower() not in {"0", "false", "no", "off"}

    server = WebApiServer(host, port)
    server.start()
    print(f"\n  ✓  Model-to-Gradio is running at  http://{host}:{port}")
    print(f"     Open this URL in your browser to access the platform.\n", flush=True)
    server.block()


if __name__ == "__main__":
    host = os.environ.get("M2G_API_HOST", "127.0.0.1")
    port = int(os.environ.get("M2G_API_PORT", "8765"))
    run_server(host, port)
