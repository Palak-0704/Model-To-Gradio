"""Thin adapter around the OpenCode CLI (``opencode run``).

Scope of this module: start the real OpenCode executable non-interactively, hand it a request, and return what it
produced. It does NOT build prompts, parse generated code, validate it or write app files; the dynamic generation step
(a separate module) will do that on top of this adapter.

How it stays safe
  * ``shell=False`` with an argument list. The argv contains only constants: the executable path, the ``run``
    sub-command, one fixed ASCII message, the model id and the path of a temporary request file. The request text, which
    may be derived from untrusted repository metadata, travels ONLY inside that file (attached with ``--file``).
  * The executable is launched directly. ``.bat``/``.cmd``/``.ps1`` launchers are refused, because cmd.exe would
    re-interpret their arguments (same rule as ``colab_cli``).
  * The child process never inherits this app's tokens/API keys (``colab_cli.child_environment``). A credential OpenCode
    genuinely needs can be forwarded explicitly, by name, with ``passthrough_env``.
  * The process runs from the project directory so the project-level ``opencode.json`` (and the REA MCP server declared
    in it) is discovered. Nothing generated is executed here, and no output is written anywhere except the temporary
    request file, which is removed afterwards.

OpenCode's ``--format json`` stream is parsed best-effort (one JSON object per line). Its exact schema has not been
verified against the installed version, so the raw, redacted stdout and stderr are always returned too.

No time limit
  * A run is never stopped because time has passed: OpenCode keeps generating until its own process exits. There is no default,
    no maximum and no environment setting that ends a run by elapsed time.
  * A run still ends when the process exits (its status and error events are reported), when it cannot be started, or when the
    caller cancels it (an interrupt such as Ctrl+C, or any exception raised in the waiting thread). On cancellation the whole
    process tree is killed, so no orphaned OpenCode process is left behind, and the temporary request file is removed.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from .colab_cli import child_environment, kill_process_tree
from .config import PROJECT_ROOT
from .security import SecurityError, contains_secret, ensure_within, redact_secrets

log = logging.getLogger("m2g.opencode")

DEFAULT_OPENCODE_EXE = r"C:\Users\Palak\AppData\Roaming\npm\node_modules\opencode-ai\bin\opencode.exe"
DEFAULT_MODEL = "opencode/glm-5.3-flash-free"
MAX_REQUEST_CHARS = 200_000
MAX_CAPTURE_CHARS = 4_000_000
SCRATCH_SUBDIR = (".m2g", "opencode")  # inside the project; ``.m2g/`` is already git-ignored
REQUEST_FILENAME = "request.md"
# The ONLY free text that reaches argv. It is a constant and must stay one.
PROMPT_MESSAGE = "Read the attached request file and follow the instructions in it."
# Permissions for a generation run, passed through OpenCode's documented OPENCODE_PERMISSION environment variable (inline JSON,
# same shape as the ``permission`` block of opencode.json). Deny by default, because the model only has to write text, and the
# request contains text derived from public repositories. The only exception is reading the attached request file. REA is NOT
# available to the model: the platform runs its own deterministic REA inspection before generation (m2g.rea_client) and passes the
# evidence in the request, so generation does not depend on the model calling any tool. Shell, file edits and web access stay denied.
GENERATION_PERMISSIONS: Dict[str, Any] = {
    "*": "deny",
    "read": {"*": "deny", "*request.md": "allow"},
}

_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}/[A-Za-z0-9][A-Za-z0-9._:-]{0,95}$")
_UNSAFE_SUFFIXES = (".bat", ".cmd", ".ps1", ".vbs")

Runner = Callable[..., "subprocess.CompletedProcess"]


# --------------------------------------------------------------------------- settings helpers


def resolve_executable(environ: Optional[Mapping[str, str]] = None) -> str:
    """``OPENCODE_COMMAND`` (a path to opencode.exe) if set, otherwise the installed default. No PATH lookup: a PATH hit
    on Windows is usually an ``opencode.cmd`` shim, which this adapter refuses."""
    env = os.environ if environ is None else environ
    override = (env.get("OPENCODE_COMMAND") or "").strip().strip('"')
    return override or DEFAULT_OPENCODE_EXE


# --------------------------------------------------------------------------- output parsing


def parse_events(stdout: str) -> Tuple[List[Dict[str, Any]], int]:
    """Parse OpenCode's ``--format json`` stdout (one JSON object per line). Returns (events, skipped_line_count).
    Lines that are not JSON objects (banners, progress text) are skipped, never fatal."""
    events: List[Dict[str, Any]] = []
    skipped = 0
    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            skipped += 1
            continue
        if isinstance(obj, dict):
            events.append(obj)
        else:
            skipped += 1
    return events, skipped


def extract_text_parts(events: Sequence[Mapping[str, Any]]) -> List[str]:
    """Assistant text from ``{"type": "text", "part": {"id": ..., "text": ...}}`` events (best effort; schema not verified).

    One text part can be reported more than once (a part is re-emitted when it is updated or finalised). Every event of the same part
    ``id`` describes the SAME text, so only its latest version is kept, at the position where the part first appeared; concatenating
    each emission would repeat the whole reply and make every code block in it look duplicated. Distinct parts (different ids, or no
    id at all) are never merged or dropped."""
    parts: List[str] = []
    index_by_id: Dict[str, int] = {}
    for ev in events:
        if ev.get("type") != "text":
            continue
        part = ev.get("part")
        if not (isinstance(part, dict) and isinstance(part.get("text"), str)):
            continue
        pid = part.get("id")
        if isinstance(pid, str) and pid:
            if pid in index_by_id:
                parts[index_by_id[pid]] = part["text"]  # the same part again: its newest text replaces the earlier emission
                continue
            index_by_id[pid] = len(parts)
        parts.append(part["text"])
    return parts


def _find_message(obj: Any, depth: int = 0) -> str:
    if depth > 4:
        return ""
    if isinstance(obj, str):
        return obj
    if isinstance(obj, dict):
        # "name" is last: it is usually just the exception class (e.g. "APIError"); the reason lives in data.message.
        for key in ("message", "msg", "data", "error", "name"):
            if key in obj:
                found = _find_message(obj[key], depth + 1)
                if found:
                    return found
    return ""


def error_events(events: Sequence[Mapping[str, Any]]) -> List[str]:
    """Human-readable messages of ``type == "error"`` events."""
    out: List[str] = []
    for ev in events:
        if ev.get("type") == "error":
            out.append((_find_message(ev.get("error") if "error" in ev else ev) or json.dumps(ev, default=str))[:300])
    return out


# --------------------------------------------------------------------------- process runner


def _as_text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return value or ""


def _run_process(cmd: Sequence[str], *, cwd: str, env: Dict[str, str]) -> "subprocess.CompletedProcess":
    """Run ``cmd`` (shell=False) in ``cwd``, capturing stdout and stderr SEPARATELY. stdin is closed immediately so the
    child can never wait for interactive input. There is no time limit: this waits until the child process exits by itself.
    If the wait is interrupted (KeyboardInterrupt, SystemExit or any other exception raised while waiting) the whole process
    tree is killed before the exception propagates, so a cancelled run never leaves an orphaned process behind."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    proc = subprocess.Popen(list(cmd), cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8", errors="replace", env=env, shell=False, creationflags=flags)
    try:
        out, err = proc.communicate(input="")
    except BaseException:
        try:
            kill_process_tree(proc)
        except Exception:  # cleanup must never hide the cancellation itself
            log.warning("Could not stop the OpenCode process tree after an interrupted run.")
        raise
    return subprocess.CompletedProcess(list(cmd), proc.returncode, out, err)


# --------------------------------------------------------------------------- result


@dataclass
class OpenCodeResult:
    returncode: Optional[int] = None
    stdout: str = ""  # redacted, size-capped
    stderr: str = ""  # redacted, size-capped; never mixed into stdout
    duration_s: float = 0.0
    # None on success; otherwise one of: misconfigured | unavailable | invalid_request | os_error |
    # exit_nonzero | error_event
    error: Optional[str] = None
    message: str = ""
    command: List[str] = field(default_factory=list)
    events: List[Dict[str, Any]] = field(default_factory=list)
    text_parts: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    stdout_truncated: bool = False
    stderr_truncated: bool = False

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def text(self) -> str:
        return "\n".join(self.text_parts)


def _cap(text: str, limit: int = MAX_CAPTURE_CHARS) -> Tuple[str, bool]:
    return (text, False) if len(text) <= limit else (text[:limit], True)


def _tail(text: str, limit: int = 400) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else "..." + text[-limit:]


# --------------------------------------------------------------------------- adapter


class OpenCodeAdapter:
    def __init__(self, executable: Optional[str] = None, *, model: Optional[str] = None,
                 project_dir: Optional[Path] = None, runner: Runner = _run_process,
                 environ: Optional[Mapping[str, str]] = None, passthrough_env: Sequence[str] = (),
                 permissions: Optional[Mapping[str, Any]] = GENERATION_PERMISSIONS):
        env = os.environ if environ is None else environ
        self._environ: Mapping[str, str] = env
        self.executable = str(executable) if executable else resolve_executable(env)
        self.model = (model or DEFAULT_MODEL).strip()
        self.project_dir = Path(project_dir) if project_dir is not None else Path(PROJECT_ROOT)
        self.runner = runner
        self.passthrough_env = tuple(passthrough_env)
        self.permissions = permissions  # None = leave OpenCode's own defaults untouched
        self.problem: Optional[str] = None
        if not _MODEL_RE.match(self.model):
            self.problem = f"Invalid OpenCode model id {self.model!r}; expected 'provider/model'."
        elif Path(self.executable).suffix.lower() in _UNSAFE_SUFFIXES:
            self.problem = (f"OpenCode launcher {Path(self.executable).suffix} refused: cmd.exe would re-interpret arguments. "
                            "Point OPENCODE_COMMAND at opencode.exe itself.")

    # ---- introspection
    @property
    def available(self) -> bool:
        return self.problem is None and Path(self.executable).is_file()

    def build_command(self, request_path: Path) -> List[str]:
        """The complete argv. Everything here is a constant or a path we created; no request content."""
        return [self.executable, "run", PROMPT_MESSAGE, "--format", "json", "--model", self.model,
                "--file", str(request_path)]

    def _child_env(self) -> Dict[str, str]:
        env = child_environment(dict(self._environ))
        for key in self.passthrough_env:  # explicit, by name, e.g. a provider key OpenCode needs
            if key in self._environ:
                env[key] = self._environ[key]
        if self.permissions is not None:
            env["OPENCODE_PERMISSION"] = json.dumps(dict(self.permissions), separators=(",", ":"))
        return env

    @staticmethod
    def check_request(text: Any) -> str:
        if not isinstance(text, str) or not text.strip():
            raise ValueError("The generation request is empty.")
        if "\x00" in text:
            raise ValueError("The generation request contains a NUL character.")
        if len(text) > MAX_REQUEST_CHARS:
            raise ValueError(f"The generation request is too large ({len(text)} > {MAX_REQUEST_CHARS} characters).")
        if contains_secret(text):
            raise ValueError("The generation request contains something that looks like a credential; refusing to send it.")
        return text

    # ---- run
    def run(self, request_text: str) -> OpenCodeResult:
        """Run one non-interactive OpenCode request. Expected failures are returned in ``result.error`` (never raised).
        The run has no time limit: it ends when the OpenCode process exits (or the caller cancels it)."""
        res = OpenCodeResult()

        def fail(kind: str, message: str) -> OpenCodeResult:
            res.error, res.message = kind, redact_secrets(message)
            return res

        if self.problem:
            return fail("misconfigured", self.problem)
        if not Path(self.executable).is_file():
            return fail("unavailable", f"OpenCode executable not found at {self.executable}. "
                                       "Set OPENCODE_COMMAND in .env to the full path of opencode.exe.")
        try:
            text = self.check_request(request_text)
        except ValueError as exc:
            return fail("invalid_request", str(exc))
        if not any((self.project_dir / name).is_file() for name in ("opencode.json", "opencode.jsonc")):
            res.warnings.append(f"No opencode.json in {self.project_dir}: the REA MCP server will not be discovered.")

        scratch: Optional[Path] = None
        started = time.monotonic()
        try:
            base = ensure_within(self.project_dir, self.project_dir.joinpath(*SCRATCH_SUBDIR))
            base.mkdir(parents=True, exist_ok=True)
            scratch = Path(tempfile.mkdtemp(prefix="req_", dir=str(base)))
            request_path = ensure_within(self.project_dir, scratch / REQUEST_FILENAME)
            request_path.write_text(text, encoding="utf-8", newline="\n")
            res.command = self.build_command(request_path)
            log.info("OpenCode run starting (model=%s)", self.model)
            try:
                proc = self.runner(res.command, cwd=str(self.project_dir), env=self._child_env())
            except FileNotFoundError:
                return fail("unavailable", f"OpenCode could not be started from {self.executable}.")
            except OSError as exc:
                return fail("os_error", f"{type(exc).__name__}: {exc}")
        except (OSError, SecurityError) as exc:
            return fail("os_error", f"Could not prepare the request file: {type(exc).__name__}: {exc}")
        finally:
            res.duration_s = round(time.monotonic() - started, 2)
            if scratch is not None:
                shutil.rmtree(scratch, ignore_errors=True)

        res.returncode = proc.returncode
        res.stdout, res.stdout_truncated = _cap(redact_secrets(_as_text(proc.stdout)))
        res.stderr, res.stderr_truncated = _cap(redact_secrets(_as_text(proc.stderr)))
        res.events, skipped = parse_events(res.stdout)
        res.text_parts = extract_text_parts(res.events)
        text_events = sum(1 for ev in res.events if ev.get("type") == "text" and isinstance(ev.get("part"), dict)
                          and isinstance(ev["part"].get("text"), str))
        if text_events > len(res.text_parts):
            res.warnings.append(f"{text_events - len(res.text_parts)} repeated emission(s) of an already reported text part were merged "
                                "into that part (same part id).")
        if len(res.text_parts) > 1:
            res.warnings.append(f"OpenCode's reply came as {len(res.text_parts)} separate text parts; they are read together.")
        if skipped:
            res.warnings.append(f"{skipped} non-JSON line(s) in OpenCode's stdout were ignored.")
        log.info("OpenCode run finished (status=%s, %.1fs, %d events)", proc.returncode, res.duration_s, len(res.events))

        if proc.returncode != 0:
            return fail("exit_nonzero", f"OpenCode exited with status {proc.returncode}. "
                                        f"{_tail(res.stderr) or _tail(res.stdout) or 'No output.'}")
        errors = error_events(res.events)
        if errors:
            return fail("error_event", "OpenCode reported an error: " + "; ".join(errors[:3]))
        return res
