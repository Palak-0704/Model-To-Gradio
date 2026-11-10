"""Adapter around the OFFICIAL Google Colab CLI (PyPI ``google-colab-cli``, console command ``colab``).

What the official README documents (checked 2026-10-29 against github.com/googlecolab/google-colab-cli):
  * ``colab new [-s NAME] [--gpu T4|L4|G4|H100|A100] [--tpu ...] [--high-mem]``, ``colab sessions``, ``colab status``,
    ``colab exec [-s NAME] [-f FILE] [--timeout N]`` (code on stdin), ``colab stop [-s NAME]``, ``colab version``.
  * The global option ``--auth {oauth2,adc}`` goes BEFORE the sub-command. Its default differs between releases
    (README: ``adc``; 0.5.x on PyPI: ``oauth2``), so this adapter ALWAYS passes it explicitly.
  * PLATFORM: the README says "Linux and macOS only. Windows is not supported at this time." This adapter runs the CLI
    natively on Windows as an EXPERIMENTAL path and never assumes it works: ``check()`` makes a real authenticated call.
  * Sign-in is the CLI's own copy-paste OAuth flow (its bundled Google client). The CLI caches its token itself. This
    application never reads, parses or stores that token.

NOT documented, so never assumed: the exit status of ``colab exec`` when the executed code raises. Callers must judge
success from markers printed by the executed code, not from the exit code.

Security: ``shell=False`` everywhere, argument lists only, session names and GPU names are validated, ``.bat``/``.cmd``
launchers are refused, and the child process never inherits this app's tokens/API keys.
"""
from __future__ import annotations

import inspect
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Dict, Optional, Sequence, Tuple

INSTALL_HINT = (
    "Install Google's official Colab CLI: `uv tool install google-colab-cli` (or `pip install google-colab-cli`), "
    "reopen the terminal, and restart this app. If `colab` is still not found, set COLAB_COMMAND in .env to the full "
    "path of the colab executable. Google lists Windows as unsupported for this CLI; the route that matches Google's "
    "documentation is WSL2/Linux (see docs/COLAB_SETUP.md), and native Windows use is experimental."
)
AUTH_HINT = (
    "Google is not connected yet. Click 'Sign in with Google', open Google's page, approve access and paste the code "
    "Google shows."
)
WINDOWS_NOTE = (
    "Google lists Windows as unsupported for the Colab CLI. If this keeps failing, run "
    "`python scripts\\real_colab_smoke.py` to see the exact error, or run this whole app under WSL2/Linux "
    "(see docs/COLAB_SETUP.md)."
)
# Fallback only when `colab new --help` cannot be parsed. Taken from the official README.
DOCUMENTED_GPUS: Tuple[str, ...] = ("T4", "L4", "G4", "A100", "H100")

_GPU_TOKEN_RE = re.compile(r"^[A-Z][A-Z0-9]{1,7}$")
_SESSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,40}$")
_AUTH_MARKERS = ("eoferror", "authoriz", "credential", "oauth", "login", "401", "403", "token", "sign in", "unauthenticated")
# Output that means "the CLI itself cannot run here" (broken install / unsupported platform), NOT "you must sign in".
# Checked BEFORE the auth markers because tracebacks mention words like oauth/token/credential in module paths.
_BROKEN_CLI_MARKERS = ("modulenotfounderror", "importerror", "no module named", "winerror", "is not recognized as",
                       "not supported on windows", "unix system modules")
_UNSAFE_LAUNCHER_SUFFIXES = (".bat", ".cmd", ".ps1", ".vbs")
# Never forward this app's secrets to the CLI process; it does not need them.
_SCRUB_EXACT = {"GEMINI_API_KEY", "GOOGLE_API_KEY", "HF_TOKEN", "HUGGING_FACE_HUB_TOKEN", "GITHUB_TOKEN", "GH_TOKEN",
                "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"}
_SCRUB_SUFFIXES = ("_TOKEN", "_SECRET", "_API_KEY", "_PASSWORD", "_SECRET_KEY", "_ACCESS_KEY")


def truncate(text: str, limit: int = 400) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


@dataclass
class CliResult:
    args: list
    returncode: Optional[int]
    stdout: str
    stderr: str
    duration_s: float
    error: Optional[str] = None  # "unavailable" | "timeout" | "os_error" | "cancelled" when the CLI itself could not finish

    @property
    def ok(self) -> bool:
        return self.error is None and self.returncode == 0

    @property
    def combined(self) -> str:
        return "\n".join(part for part in (self.stdout, self.stderr) if part)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class GpuChoices:
    names: Tuple[str, ...]
    verified: bool  # True = parsed from THIS installed CLI's `colab new --help`; False = documented fallback list
    source: str


def parse_gpu_choices(help_text: str) -> Tuple[str, ...]:
    """Extract accelerator names from ``colab new --help``. Handles ``{T4,L4}``, ``T4|L4`` and plain-prose forms."""
    text = help_text or ""
    m = re.search(r"--gpu\s+[A-Za-z_]*\s*\{([^}]+)\}", text)
    if m:
        raw = re.split(r"[,|]", m.group(1))
    else:
        m = re.search(r"--gpu\s+([A-Z0-9]+(?:\|[A-Z0-9]+)+)", text)
        raw = m.group(1).split("|") if m else []
    names = [t.strip().upper() for t in raw]
    names = [n for n in names if _GPU_TOKEN_RE.match(n)]
    if not names:  # prose fallback: only documented names mentioned in the --gpu option's own help paragraph
        idx = text.find("--gpu")
        if idx >= 0:
            block = text[idx: idx + 400]
            names = [g for g in DOCUMENTED_GPUS if re.search(rf"\b{g}\b", block)]
    return tuple(dict.fromkeys(names))


def _is_secret_name(name: str) -> bool:
    up = name.upper()
    return up in _SCRUB_EXACT or up.endswith(_SCRUB_SUFFIXES)


def child_environment(environ: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """Environment for the CLI process: UTF-8 everywhere (Windows pipes default to a legacy code page), unbuffered
    output, no colours, and none of this app's tokens/API keys (including values loaded from .env)."""
    env = {k: v for k, v in dict(os.environ if environ is None else environ).items() if not _is_secret_name(k)}
    env.update({"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1", "NO_COLOR": "1"})
    return env


def kill_process_tree(proc, os_name: Optional[str] = None) -> None:
    """Windows launchers (colab.exe) start a child python.exe; killing only the launcher leaves it running."""
    name = os.name if os_name is None else os_name
    if name == "nt":
        try:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, timeout=30,
                           shell=False, check=False)
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        proc.kill()
    except OSError:
        pass


def _pump(stream, name: str, sink: list, on_line: Callable) -> None:
    """Read ``stream`` line by line until EOF, keeping every line and reporting each one as soon as it arrives."""
    try:
        for line in iter(stream.readline, ""):
            sink.append(line)
            try:
                on_line(name, line.rstrip("\r\n"))
            except Exception:  # a broken callback must never break (or hang) the CLI call itself
                pass
    except (OSError, ValueError):
        pass
    finally:
        try:
            stream.close()
        except OSError:
            pass


def _run_streaming(proc, cmd, input: str, timeout: float, on_line: Callable):
    """Wait for ``proc`` while reporting its stdout/stderr live (``communicate()`` only returns output when the call ends,
    which left the log empty for the whole of a multi-minute ``colab new``/``pip install``). Same result as communicate()."""
    out_lines: list = []
    err_lines: list = []
    readers = [threading.Thread(target=_pump, args=(proc.stdout, "stdout", out_lines, on_line), daemon=True),
               threading.Thread(target=_pump, args=(proc.stderr, "stderr", err_lines, on_line), daemon=True)]

    def feed() -> None:  # a separate thread, so a large payload cannot deadlock against full output pipes
        try:
            if input:
                proc.stdin.write(input)
            proc.stdin.close()
        except (OSError, ValueError):
            pass

    feeder = threading.Thread(target=feed, daemon=True)
    for t in (*readers, feeder):
        t.start()
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        kill_process_tree(proc)
        try:
            proc.wait(timeout=10)
        except Exception:
            pass
        for t in readers:
            t.join(5)
        raise subprocess.TimeoutExpired(cmd, timeout, output="".join(out_lines), stderr="".join(err_lines)) from exc
    for t in readers:
        t.join(10)
    feeder.join(2)
    return subprocess.CompletedProcess(cmd, proc.returncode, "".join(out_lines), "".join(err_lines))


def _accepts_kwarg(fn: Callable, name: str) -> bool:
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False
    return name in params or any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())


def run_process(cmd, *, input: str = "", timeout: float = 120, env: Optional[dict] = None,
                on_start: Optional[Callable] = None, on_line: Optional[Callable[[str, str], None]] = None):
    """Like subprocess.run(capture_output=True, text=True) but kills the whole process tree on timeout, and tells
    ``on_start`` about the live process so another thread can interrupt it. With ``on_line(stream, line)`` the output is
    also reported live, line by line (the returned result is identical)."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            encoding="utf-8", errors="replace", env=env, shell=False, creationflags=flags)
    if on_start is not None:
        on_start(proc)
    if on_line is not None:
        return _run_streaming(proc, cmd, input, timeout, on_line)
    try:
        out, err = proc.communicate(input=input, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        kill_process_tree(proc)
        try:
            out, err = proc.communicate(timeout=10)
        except Exception:
            out, err = "", ""
        raise subprocess.TimeoutExpired(cmd, timeout, output=out, stderr=err) from exc
    return subprocess.CompletedProcess(cmd, proc.returncode, out, err)


def resolve_colab_command(environ: Optional[dict] = None, which: Callable[[str], Optional[str]] = shutil.which,
                          os_name: Optional[str] = None, home: Optional[object] = None,
                          python_dir: Optional[object] = None,
                          exists: Callable[[Path], bool] = Path.is_file) -> Optional[list]:
    """COLAB_COMMAND override, then `colab` on PATH, then (Windows) the usual `uv tool`/venv install folders.
    There is deliberately NO WSL fallback."""
    env = os.environ if environ is None else environ
    name = os.name if os_name is None else os_name
    override = (env.get("COLAB_COMMAND") or "").strip()
    if override:
        parts = shlex.split(override, posix=(name != "nt"))
        return [p.strip('"') for p in parts] if name == "nt" else parts
    found = which("colab")
    if found:
        return [found]
    if name == "nt":
        base = Path(home) if home is not None else Path.home()
        py_dir = Path(python_dir) if python_dir is not None else Path(sys.executable).parent
        for candidate in (base / ".local" / "bin" / "colab.exe", py_dir / "colab.exe", py_dir / "Scripts" / "colab.exe"):
            if exists(candidate):
                return [str(candidate)]
    return None


class ColabCli:
    def __init__(self, command: Optional[Sequence[str]] = None, *, runner: Callable = run_process,
                 auth: Optional[str] = None, environ: Optional[dict] = None,
                 which: Callable[[str], Optional[str]] = shutil.which):
        env = os.environ if environ is None else environ
        cmd = list(command) if command is not None else resolve_colab_command(env, which=which)
        self.problem: Optional[str] = None
        if cmd and Path(cmd[0]).suffix.lower() in _UNSAFE_LAUNCHER_SUFFIXES:
            self.problem = (f"COLAB_COMMAND points to a script launcher ({Path(cmd[0]).suffix}); refusing it because "
                            "cmd.exe would re-interpret arguments. Point it at colab.exe instead.")
            cmd = None
        self.command = cmd or None
        self.runner = runner
        self.auth = auth or (env.get("COLAB_AUTH") or "oauth2").strip()
        if self.auth not in ("oauth2", "adc"):
            raise ValueError("COLAB_AUTH must be 'oauth2' or 'adc'")
        self._env = child_environment(env)
        self._lock = threading.Lock()
        self._active = None
        self._cancelled = threading.Event()
        self._gpu_cache: Optional[GpuChoices] = None
        # Optional live-output sink ``(stream, line) -> None``; the launcher sets it for the long calls (new/exec) so their
        # stdout/stderr appear in the log while they run. Runners that do not accept ``on_line`` are simply not given it.
        self.on_output: Optional[Callable[[str, str], None]] = None

    @property
    def available(self) -> bool:
        return bool(self.command)

    # ------------------------------------------------------------------ interruption (Windows-safe cancel)
    def _track(self, proc) -> None:
        with self._lock:
            self._active = proc
        if self._cancelled.is_set():
            kill_process_tree(proc)

    def interrupt(self) -> None:
        """Cancel whatever CLI call is running and make later calls return 'cancelled' until clear_interrupt()."""
        self._cancelled.set()
        with self._lock:
            proc = self._active
        if proc is not None and proc.poll() is None:
            kill_process_tree(proc)

    def clear_interrupt(self) -> None:
        self._cancelled.clear()

    @property
    def interrupted(self) -> bool:
        return self._cancelled.is_set()

    # ------------------------------------------------------------------ core runner
    def run(self, args: Sequence[str], *, stdin: Optional[str] = None, timeout: int = 120) -> CliResult:
        if not self.command:
            return CliResult(list(args), None, "", "", 0.0, "unavailable")
        if self._cancelled.is_set():
            return CliResult(list(args), None, "", "", 0.0, "cancelled")
        cmd = [*self.command, "--auth", self.auth, *args]
        started = time.monotonic()
        try:
            extra = {}
            sink = self.on_output
            if sink is not None and _accepts_kwarg(self.runner, "on_line"):
                extra["on_line"] = sink
            proc = self.runner(cmd, input=stdin or "", timeout=timeout, env=self._env, on_start=self._track, **extra)
        except FileNotFoundError:
            return CliResult(cmd, None, "", "", time.monotonic() - started, "unavailable")
        except subprocess.TimeoutExpired as exc:
            out = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            err = exc.stderr.decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
            return CliResult(cmd, None, out, err, time.monotonic() - started, "timeout")
        except OSError as exc:
            return CliResult(cmd, None, "", f"{type(exc).__name__}: {exc}", time.monotonic() - started, "os_error")
        finally:
            with self._lock:
                self._active = None
        duration = round(time.monotonic() - started, 2)
        error = "cancelled" if (self._cancelled.is_set() and proc.returncode != 0) else None
        return CliResult(cmd, proc.returncode, proc.stdout or "", proc.stderr or "", duration, error)

    # ------------------------------------------------------------------ identity / version / capabilities
    def version(self) -> str:
        res = self.run(["version"], timeout=30)
        line = next((ln.strip() for ln in res.combined.splitlines() if ln.strip()), "")
        return truncate(line, 80) if res.ok else ""

    def identify(self) -> Tuple[bool, str]:
        """Is the `colab` on PATH really Google's google-colab-cli? (Other tools use the same command name.)"""
        if self.problem:
            return False, self.problem
        if not self.command:
            return False, f"The Colab CLI was not found. {INSTALL_HINT}"
        res = self.run(["--help"], timeout=60)
        if res.error == "unavailable":
            return False, f"The Colab CLI could not be started. {INSTALL_HINT}"
        if res.error:
            return False, f"The Colab CLI did not answer `--help` ({res.error})."
        text = res.combined.lower()
        missing = [w for w in ("new", "exec", "sessions", "stop") if not re.search(rf"\b{w}\b", text)]
        if missing:
            return False, ("A command named `colab` was found, but it does not look like Google's official "
                           f"google-colab-cli (its help lacks: {', '.join(missing)}). Install the official one: {INSTALL_HINT}")
        return True, "Official Colab CLI detected."

    def supported_gpus(self, refresh: bool = False) -> GpuChoices:
        """Accelerators this INSTALLED CLI accepts for `--gpu`. Falls back to the documented list, flagged unverified.
        This says nothing about what your Google account can actually get right now."""
        if self._gpu_cache is not None and not refresh:
            return self._gpu_cache
        names: Tuple[str, ...] = ()
        if self.command:
            res = self.run(["new", "--help"], timeout=60)
            if res.ok:
                names = parse_gpu_choices(res.combined)
        if names:
            choices = GpuChoices(names, True, "parsed from `colab new --help` of the installed CLI")
        else:
            choices = GpuChoices(DOCUMENTED_GPUS, False, "documented in the official README (could not be read from the CLI)")
        self._gpu_cache = choices
        return choices

    def check(self) -> Tuple[str, str]:
        """('ok'|'unavailable'|'needs_auth'|'failed', message). Uses `colab sessions` with stdin closed, so an
        unauthorized account fails fast instead of waiting for a pasted code. 'ok' means a REAL authenticated call
        to Colab succeeded; a token file or an email address proves nothing."""
        if self.problem:
            return "unavailable", self.problem
        if not self.command:
            return "unavailable", f"The Colab CLI was not found. {INSTALL_HINT}"
        res = self.run(["sessions"], timeout=60)
        if res.error == "unavailable":
            return "unavailable", f"The Colab CLI could not be started. {INSTALL_HINT}"
        if res.error == "cancelled":
            return "failed", "Cancelled."
        if res.error:
            return "failed", f"The Colab CLI check did not complete ({res.error})."
        if res.ok:
            return "ok", "Colab CLI is installed and a real authenticated call to Colab succeeded."
        text = res.combined.lower()
        note = f" {WINDOWS_NOTE}" if os.name == "nt" else ""
        if any(marker in text for marker in _BROKEN_CLI_MARKERS):
            return "failed", ("The Colab CLI started but crashed before it could talk to Google (broken install or "
                              f"unsupported platform): {truncate(res.combined, 400)}{note}")
        if any(marker in text for marker in _AUTH_MARKERS):
            return "needs_auth", AUTH_HINT
        return "failed", f"`colab sessions` exited with status {res.returncode}: {truncate(res.combined, 400)}{note}"

    # ------------------------------------------------------------------ documented sub-commands
    @staticmethod
    def check_name(name: str) -> str:
        if not _SESSION_RE.match(name or ""):
            raise ValueError("Invalid Colab session name")
        return name

    def sessions(self) -> CliResult:
        return self.run(["sessions"], timeout=60)

    def status(self, name: str) -> CliResult:
        return self.run(["status", "-s", self.check_name(name)], timeout=60)

    def new_session(self, name: str, gpu: Optional[str] = None) -> CliResult:
        self.check_name(name)
        args = ["new", "-s", name]
        if gpu:
            gpu = gpu.upper()
            if gpu not in self.supported_gpus().names:
                raise ValueError(f"Unsupported accelerator {gpu!r}; the installed CLI accepts: "
                                 f"{', '.join(self.supported_gpus().names)}")
            args += ["--gpu", gpu]
        return self.run(args, timeout=600)

    def exec_code(self, name: str, code: str, timeout: int = 600) -> CliResult:
        """Run Python on the VM kernel (code goes to the CLI on stdin). Code must be ASCII: Windows pipes use a legacy
        code page unless forced, and every template/payload here is ASCII-escaped on purpose."""
        self.check_name(name)
        if not code.isascii():
            raise ValueError("Refusing to send non-ASCII code to the Colab CLI (escape it with json.dumps).")
        return self.run(["exec", "-s", name, "--timeout", str(int(timeout))], stdin=code, timeout=int(timeout) + 90)

    def stop(self, name: str) -> CliResult:
        return self.run(["stop", "-s", self.check_name(name)], timeout=180)
