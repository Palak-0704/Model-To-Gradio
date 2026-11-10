"""Local deployment controller for generated applications.

The local path is deliberately separate from the Colab launcher: selecting Local
never creates or uses a Colab runtime. It creates an isolated virtual environment
under the generated application's output directory, installs the already
validated requirements, runs the same runtime preflight, and starts the app.
"""
from __future__ import annotations

import json
import hashlib
import os
import subprocess
import sys
import threading
import time
import re
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence

from .local_check import probe_local
from .local_progress import ProgressTracker
from .preflight import PROBE_TIMEOUT_S, run_preflight
from .security import redact_secrets
from .validate import validate_app_code, validate_requirements_text


IDLE, PREPARING, HARDWARE, ENVIRONMENT, PREFLIGHT, STARTING, RUNNING, FAILED, STOPPED = (
    "idle", "preparing", "hardware", "environment", "preflight", "starting", "running", "failed", "stopped"
)
CREATING_ENVIRONMENT = "creating_environment"
INSTALLING_DEPENDENCIES = "installing_dependencies"


@dataclass
class LocalSnapshot:
    state: str = IDLE
    python: str = ""
    hardware: str = ""
    environment: str = ""
    error: str = ""
    logs: List[str] = field(default_factory=list)
    pid: Optional[int] = None
    url: str = ""
    exit_code: Optional[int] = None
    app: str = ""


class AlreadyRunningError(RuntimeError):
    """A launch was requested while this app's previous launch is still preparing or running."""


# Exit codes of the generated run_<model>.bat (see local_script) and what they mean for the person reading the status.
LAUNCHER_EXIT_MESSAGES = {
    10: "app.py, loader.py or requirements.txt is missing from the generated app folder. Generate the app again.",
    11: "Python 3.12 was not found. Install Python 3.12 from python.org and run the app again.",
    12: "The app's .venv is missing or is not a Python 3.12 environment. Delete the .venv folder and run the app again.",
    13: "Dependency installation failed. See the logs for the pip error.",
    14: "The app's virtual environment could not be activated. Delete the .venv folder and run the app again.",
}


class LocalLauncher:
    """Own exactly one local app process and its isolated environment."""

    def __init__(self, *, python: Optional[str] = None, sleep=time.sleep,
                 progress: Optional[ProgressTracker] = None) -> None:
        self.python = python or sys.executable
        self._explicit_python = python
        self._sleep = sleep
        self._lock = threading.RLock()
        self._thread: Optional[threading.Thread] = None
        self._process: Optional[subprocess.Popen] = None
        self._startup_event = threading.Event()
        self._stop_event = threading.Event()
        self._startup_error = ""
        self._metadata_file: Optional[Path] = None
        self._log_total = 0  # lines ever logged; lets a poller ask only for lines it has not seen
        # Hardware-preflight result of the current BAT launch (set by launch_batch). _run_batch builds a fresh LocalSnapshot on its own
        # thread, so the findings live here and are copied into every snapshot that launch creates instead of being lost on reset.
        self._launch_hardware = ""
        self._launch_hardware_log: List[str] = []
        self.snapshot = LocalSnapshot(python=self.python)
        self.progress = progress if progress is not None else ProgressTracker()

    @staticmethod
    def _ps_quote(value: str) -> str:
        return "'" + str(value).replace("'", "''") + "'"

    def _python_candidates(self) -> List[str]:
        if self._explicit_python:
            return [self._explicit_python]
        if os.name != "nt":
            return [self.python]
        result = subprocess.run(["py", "-0p"], capture_output=True, text=True, timeout=20)
        if result.returncode:
            raise RuntimeError(f"Unable to enumerate Python runtimes with 'py -0p': {result.stderr.strip()}")
        candidates = []
        for line in result.stdout.splitlines():
            match = re.search(r"-V:(\d+)\.(\d+).*?\s((?:[A-Za-z]:|\\\\)[^\r\n]+python(?:\.exe)?\s*)$", line)
            if match:
                version = (int(match.group(1)), int(match.group(2)))
                if 3 <= version[0] and version[1] <= 13:
                    candidates.append((version, match.group(3).strip()))
        candidates.sort(reverse=True)
        if not candidates:
            raise RuntimeError("No compatible Python 3.10-3.13 runtime was found. Install Python 3.12 or 3.13.")
        return [path for _version, path in candidates]

    def _select_python(self) -> str:
        candidates = self._python_candidates()
        selected = candidates[0]
        self._log(f"Selected Python interpreter: {selected}")
        return selected

    def _environment_root(self, output_dir: Path) -> Path:
        app_key = hashlib.sha256(str(output_dir.resolve()).encode("utf-8")).hexdigest()[:12]
        base = os.environ.get("M2G_LOCAL_ROOT")
        if not base and os.name == "nt":
            base = os.environ.get("LOCALAPPDATA") or os.environ.get("USERPROFILE")
        root = Path(base or output_dir.parent) / "ModelToGradio" / "envs" / app_key
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _run_powershell(self, script: str, *, cwd: Path, timeout: int = 1800) -> str:
        command = ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script]
        result = subprocess.run(command, cwd=str(cwd), capture_output=True, text=True, timeout=timeout)
        output = ((result.stdout or "") + "\n" + (result.stderr or "")).strip()
        self._log(output[-5000:] if output else "PowerShell command completed")
        if result.returncode:
            raise RuntimeError(f"PowerShell command failed ({result.returncode}): {output[-1800:]}")
        return output

    def _metadata_path(self, output_dir: Path) -> Path:
        return output_dir / ".m2g-local.json"

    def _write_metadata(self, output_dir: Path, data: dict) -> None:
        self._metadata_path(output_dir).write_text(json.dumps(data, indent=2), encoding="utf-8")

    def _log(self, text: str) -> None:
        with self._lock:
            self.snapshot.logs.append(redact_secrets(str(text))[:2000])
            self.snapshot.logs = self.snapshot.logs[-300:]
            self._log_total += 1

    def read_logs(self, since: int = 0):
        """(lines, next): the retained log lines with absolute index >= ``since``, and the index to ask for next time."""
        with self._lock:
            total = self._log_total
            logs = list(self.snapshot.logs)
        base = total - len(logs)
        start = min(max(int(since), base), total)
        return logs[start - base:], total

    def progress_snapshot(self) -> dict:
        """Progress picture with a live check of the process, so 'possibly stuck' is judged against what is really running.

        While the launch thread works without a child process of its own yet (validation, in-process pip) it counts as alive."""
        with self._lock:
            proc = self._process
        if proc is not None:
            alive: Optional[bool] = proc.poll() is None
        else:
            alive = True if self.active else None
        return self.progress.snapshot(process_alive=alive)

    def _apply_stage_marker(self, stage: str) -> None:
        """Turn one ``M2G_STAGE=<stage>`` line printed by run_local.bat into tracker events (unknown markers only count as activity)."""
        progress = self.progress
        if stage == "validating_app":
            progress.begin("validating")
        elif stage == "preparing_environment":
            progress.begin("preparing")
        elif stage == "env_reused":  # still verifying/activating the environment, so the preparing step stays open
            progress.skip("creating_env", "Existing environment reused", keep_active=True)
        elif stage == "creating_environment":
            progress.begin("creating_env")
        elif stage == "upgrading_pip":
            progress.begin("upgrading_pip")
        elif stage == "installing_dependencies":
            progress.begin("installing")
        elif stage == "deps_unchanged":
            progress.skip("upgrading_pip", "Requirements unchanged", keep_active=True)
            progress.skip("installing", "Requirements unchanged", keep_active=True)
        elif stage == "starting_application":
            progress.begin("starting")
        else:
            progress.touch()

    @property
    def active(self) -> bool:
        """True while a launch thread is still preparing, starting or supervising the app."""
        thread = self._thread
        return bool(thread and thread.is_alive())

    def _is_windows(self) -> bool:
        return os.name == "nt"

    def _exit_message(self, code: Optional[int]) -> str:
        """Record the launcher's exit code and explain it (run_local.bat uses fixed codes for its own failures)."""
        with self._lock:
            self.snapshot.exit_code = code
        if code in LAUNCHER_EXIT_MESSAGES:
            return f"{LAUNCHER_EXIT_MESSAGES[code]} (launcher exit code {code})"
        return self._startup_error or f"Launcher exited with code {code} before exposing a URL."

    def _terminate_tree(self, proc) -> None:
        """Stop the launcher AND everything it started (cmd.exe -> python.exe -> Gradio); terminate() alone only ends cmd.exe."""
        if proc is None or proc.poll() is not None:
            return
        if self._is_windows():
            try:
                result = subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                                        capture_output=True, text=True, timeout=20)
                self._log(f"taskkill /T /F {proc.pid}: exit {result.returncode}")
            except (OSError, subprocess.SubprocessError) as exc:
                self._log(f"taskkill failed: {exc}")
        else:
            proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)

    def _command(self, args: Sequence[str], cwd: Path, *, timeout: int = 900) -> str:
        result = subprocess.run(list(args), cwd=str(cwd), capture_output=True, text=True, timeout=timeout)
        output = ((result.stdout or "") + "\n" + (result.stderr or "")).strip()
        self._log(output[-3000:] if output else "command completed")
        if result.returncode:
            raise RuntimeError(f"Command failed ({result.returncode}): {output[-1200:]}")
        return output

    def _hardware(self) -> str:
        code = (
            "import json,platform,shutil,sys\n"
            "d={'python':platform.python_version(),'gpu':None,'ram_gib':None,'disk_free_gib':None}\n"
            "try:\n"
            " import torch\n"
            " d['gpu']=torch.cuda.get_device_name(0) if torch.cuda.is_available() else None\n"
            "except Exception: pass\n"
            "try: d['disk_free_gib']=round(shutil.disk_usage('.').free/2**30,1)\n"
            "except Exception: pass\n"
            "print(json.dumps(d))"
        )
        out = self._command([self.python, "-c", code], Path.cwd(), timeout=60).splitlines()[-1]
        data = json.loads(out)
        return "Python {python}; GPU: {gpu}; free disk: {disk} GiB".format(
            python=data.get("python", "unknown"), gpu=data.get("gpu") or "none", disk=data.get("disk_free_gib") or "unknown"
        )

    def _run(self, app_code: str, requirements: str, output_dir: Path) -> None:
        try:
            if self._stop_event.is_set():
                return
            output_dir.mkdir(parents=True, exist_ok=True)
            selected_python = self._select_python()
            env_root = self._environment_root(output_dir)
            env_dir = env_root / "venv"
            temp_root = env_root / "tmp"
            temp_root.mkdir(parents=True, exist_ok=True)
            self._metadata_file = self._metadata_path(output_dir)
            with self._lock:
                self.snapshot = LocalSnapshot(state=PREPARING, python=selected_python, environment=str(env_dir))
            validate_app_code(app_code)
            validate_requirements_text(requirements)
            self.progress.begin("preparing")
            self._log("Preparing local deployment.")
            with self._lock:
                self.snapshot.state = HARDWARE
            self.python = selected_python
            hardware = self._hardware()
            with self._lock:
                self.snapshot.hardware = hardware
                self.snapshot.state = CREATING_ENVIRONMENT
            req_hash = hashlib.sha256(requirements.encode("utf-8")).hexdigest()
            exe = env_dir / "Scripts" / "python.exe" if os.name == "nt" else env_dir / "bin" / "python"
            metadata = {}
            try:
                metadata = json.loads(self._metadata_path(output_dir).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
            reusable = exe.is_file() and metadata.get("requirements_hash") == req_hash
            if not reusable:
                self.progress.begin("creating_env")
                if os.name == "nt":
                    ps = (
                        "$ErrorActionPreference='Stop'; "
                        f"$root={self._ps_quote(str(env_root))}; "
                        f"$tmp={self._ps_quote(str(temp_root))}; "
                        "$env:TEMP=$tmp; $env:TMP=$tmp; $env:PIP_CACHE_DIR=(Join-Path $root 'pip-cache'); "
                        f"& {self._ps_quote(selected_python)} -m venv {self._ps_quote(str(env_dir))}; "
                        f"& {self._ps_quote(str(exe))} -m pip install --upgrade pip setuptools wheel"
                    )
                    self._run_powershell(ps, cwd=output_dir)
                    self.progress.skip("upgrading_pip", "Done together with creating the environment")
                else:
                    self._command([selected_python, "-m", "venv", str(env_dir)], output_dir)
                    self.progress.begin("upgrading_pip")
                    self._command([str(exe), "-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel"], output_dir)
            else:
                self._log("Reusing existing local environment; requirements are unchanged.")
                for step_id, detail in (("creating_env", "Existing environment reused"),
                                        ("upgrading_pip", "Requirements unchanged"),
                                        ("installing", "Requirements unchanged")):
                    self.progress.skip(step_id, detail, keep_active=True)
            exe = str(env_dir / ("Scripts\\python.exe" if os.name == "nt" else "bin/python"))
            req_file = output_dir / "requirements.txt"
            app_file = output_dir / "app.py"
            req_file.write_text(requirements, encoding="utf-8", newline="\n")
            package = (output_dir / "loader.py").is_file()  # a generated loader.py + app.py package on disk is already the validated source
            if not package:
                app_file.write_text(app_code, encoding="utf-8", newline="\n")
            with self._lock:
                self.snapshot.state = INSTALLING_DEPENDENCIES
            if not reusable:
                self.progress.begin("installing")
                short_req = env_root / "requirements.txt"
                short_req.write_text(requirements, encoding="utf-8", newline="\n")
                if os.name == "nt":
                    ps = (
                        "$ErrorActionPreference='Stop'; "
                        f"$root={self._ps_quote(str(env_root))}; $tmp={self._ps_quote(str(temp_root))}; "
                        "$env:TEMP=$tmp; $env:TMP=$tmp; $env:PIP_CACHE_DIR=(Join-Path $root 'pip-cache'); "
                        f"& {self._ps_quote(exe)} -m pip install -r {self._ps_quote(str(short_req))}"
                    )
                    self._run_powershell(ps, cwd=output_dir, timeout=2400)
                else:
                    self._command([exe, "-m", "pip", "install", "-r", str(short_req)], output_dir, timeout=2400)
            self._write_metadata(output_dir, {"environment": str(env_dir), "python": selected_python,
                                              "requirements_hash": req_hash, "state": "prepared"})
            with self._lock:
                self.snapshot.state = PREFLIGHT
            self.progress.begin("starting")  # the compatibility check is the last part of starting the app
            probe = probe_local(app_code, python=exe, timeout=PROBE_TIMEOUT_S + 60)
            result = run_preflight(app_code, probe, revalidate=lambda code: validate_app_code(code))
            for line in result.log_lines:
                self._log(line)
            if not result.ok:
                raise RuntimeError(result.message)
            if result.code != app_code:
                if package:
                    self._log("Compatibility repairs are not written into a loader.py / app.py package.")
                else:
                    app_file.write_text(result.code, encoding="utf-8", newline="\n")
            with self._lock:
                self.snapshot.state = STARTING
            if self._stop_event.is_set():
                return
            proc = subprocess.Popen([exe, "-u", str(app_file)], cwd=str(output_dir),
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, env=os.environ.copy())
            with self._lock:
                self._process = proc
                self.snapshot.pid = proc.pid
            self._log(f"Local app started (pid {proc.pid}).")
            self._startup_event.clear()
            reader = threading.Thread(target=self._read_process_output, args=(proc,), daemon=True,
                                      name="m2g-local-output")
            reader.start()
            deadline = time.monotonic() + 45
            while not self._startup_event.wait(timeout=0.25):
                if self._stop_event.is_set():
                    return
                if proc.poll() is not None:
                    raise RuntimeError(self._startup_error or
                                       f"Local app exited with code {proc.returncode} before exposing a URL.")
                if time.monotonic() >= deadline:
                    proc.terminate()
                    raise RuntimeError("Local app did not expose a Gradio URL within 45 seconds. See logs for startup errors.")
            with self._lock:
                if self._stop_event.is_set():
                    return
            if not self._verify_url(self.snapshot.url):
                raise RuntimeError(f"Local app announced {self.snapshot.url}, but the server did not respond. See logs for startup errors.")
            with self._lock:
                self.snapshot.state = RUNNING
                self.progress.complete()  # the health check above is the only way to 100%
                self._write_metadata(output_dir, {"environment": str(env_dir), "python": selected_python,
                                                  "requirements_hash": req_hash, "pid": proc.pid,
                                                  "url": self.snapshot.url, "state": "running"})
            self._log(f"Local app ready at {self.snapshot.url}.")
            code = proc.wait()
            with self._lock:
                self.snapshot.state = STOPPED if code == 0 else FAILED
                self._process = None
            if code:
                raise RuntimeError(f"Local app exited with code {code}. See logs for the original error.")
            self.progress.stop()
        except Exception as exc:
            if self._stop_event.is_set():
                return
            with self._lock:
                self.snapshot.state = FAILED
                self.snapshot.error = redact_secrets(f"{type(exc).__name__}: {exc}")
            self._log(self.snapshot.error)
            self.progress.fail(self.snapshot.error)

    def _read_process_output(self, proc: subprocess.Popen) -> None:
        pattern = re.compile(r"https?://(?:127\.0\.0\.1|localhost|0\.0\.0\.0):\d+(?:/\S*)?")
        if not proc.stdout:
            self._startup_error = "Local app produced no startup output."
            self._startup_event.set()
            return
        for line in proc.stdout:
            text = line.rstrip()
            self._log(text)
            self.progress.touch()
            stripped = text.strip()
            # Only the launcher prints stage markers, and only before the app itself runs; later output cannot move progress.
            if stripped.startswith("M2G_STAGE=") and not self._startup_event.is_set():
                self._apply_stage_marker(stripped.split("=", 1)[1].strip())
            selected = re.search(r"Selected Python\s+(.+)$", text)
            if selected:
                with self._lock:
                    self.snapshot.python = selected.group(1).strip()
            if "M2G_STAGE=preparing_environment" in text:
                with self._lock:
                    self.snapshot.state = PREPARING
            elif "M2G_STAGE=creating_environment" in text:
                with self._lock:
                    self.snapshot.state = CREATING_ENVIRONMENT
            elif "M2G_STAGE=installing_dependencies" in text or "Installing dependencies from requirements.txt" in text:
                with self._lock:
                    self.snapshot.state = INSTALLING_DEPENDENCIES
            elif "M2G_STAGE=starting_application" in text or "Starting Gradio application" in text:
                with self._lock:
                    self.snapshot.state = STARTING
            match = pattern.search(text)
            if match and not self._startup_event.is_set():
                with self._lock:
                    self.snapshot.url = match.group(0).rstrip(".,)")
                self.progress.begin("checking_url")  # announced, not yet verified
                self._startup_event.set()
        if proc.poll() is not None and not self._startup_event.is_set():
            self._startup_error = f"Local app exited with code {proc.returncode} before exposing a URL."
            self._startup_event.set()

    def _run_batch(self, batch_file: Path, output_dir: Path) -> None:
        """Run the app-owned Windows launcher and publish its real process state."""
        try:
            if not self._is_windows():
                raise RuntimeError("The generated run_<model>.bat launcher can only be executed on Windows.")
            if not batch_file.is_file():
                raise FileNotFoundError(f"Generated launcher not found: {batch_file.name}")
            self._metadata_file = self._metadata_path(output_dir)
            with self._lock:
                self.snapshot = LocalSnapshot(state=PREPARING, environment=str(output_dir / ".venv"), app=output_dir.name,
                                              hardware=self._launch_hardware)
            self._log("Preparing local deployment.")
            for line in list(self._launch_hardware_log):  # the preflight's summary and warnings, in this launch's own log
                self._log(line)
            if self._stop_event.is_set():
                return
            env = os.environ.copy()
            env["M2G_NO_PAUSE"] = "1"  # a managed launch must never wait for a key press
            proc = subprocess.Popen(
                ["cmd.exe", "/d", "/c", "call", str(batch_file)],
                cwd=str(output_dir),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                env=env,
            )
            with self._lock:
                self._process = proc
                self.snapshot.pid = proc.pid
                self.snapshot.state = CREATING_ENVIRONMENT
            self._log(f"Executing generated launcher: {batch_file.name}")
            self._startup_event.clear()
            reader = threading.Thread(
                target=self._read_process_output,
                args=(proc,),
                daemon=True,
                name="m2g-local-bat-output",
            )
            reader.start()
            deadline = time.monotonic() + 2400
            while not self._startup_event.wait(timeout=0.25):
                if self._stop_event.is_set():
                    return
                if proc.poll() is not None:
                    raise RuntimeError(self._exit_message(proc.returncode))
                if time.monotonic() >= deadline:
                    # the except block below kills the whole tree; proc.terminate() would only end cmd.exe
                    raise RuntimeError("Local launcher timed out while installing dependencies or starting the app.")
            if not self.snapshot.url:  # the output ended (the launcher exited) without a Gradio URL
                try:
                    code = proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    code = None
                raise RuntimeError(self._exit_message(code))
            if not self._verify_url(self.snapshot.url):
                raise RuntimeError(
                    f"Local app announced {self.snapshot.url}, but the server did not respond. "
                    "See launcher logs for startup errors."
                )
            with self._lock:
                self.snapshot.state = RUNNING
                self.progress.complete()  # the health check above is the only way to 100%
                self._write_metadata(output_dir, {
                    "environment": self.snapshot.environment,
                    "python": self.snapshot.python,
                    "pid": proc.pid,
                    "url": self.snapshot.url,
                    "state": "running",
                    "launcher": str(batch_file),
                })
            self._log(f"Local app ready at {self.snapshot.url}.")
            code = proc.wait()
            with self._lock:
                self.snapshot.exit_code = code
                stopped = self._stop_event.is_set()  # a requested stop is not a failure, whatever the exit code
                self.snapshot.state = STOPPED if (code == 0 or stopped) else FAILED
                self._process = None
            if stopped:
                return
            if code:
                raise RuntimeError(f"Local app stopped unexpectedly (exit code {code}). See the logs for the original error.")
            self._log("Local app exited normally (exit code 0).")
            self.progress.stop()
        except Exception as exc:
            if self._stop_event.is_set():
                return
            with self._lock:
                self.snapshot.state = FAILED
                self.snapshot.error = redact_secrets(f"{type(exc).__name__}: {exc}")
            self._log(self.snapshot.error)
            self.progress.fail(self.snapshot.error)
            self._terminate_tree(self._process)  # never leave an orphaned Gradio server behind a FAILED state
            with self._lock:
                self._process = None

    def _verify_url(self, url: str) -> bool:
        if not url:
            return False
        probe_url = url.replace("0.0.0.0", "127.0.0.1")
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and not self._stop_event.is_set():
            try:
                with urllib.request.urlopen(probe_url, timeout=3) as response:
                    return 200 <= response.status < 500
            except (OSError, urllib.error.URLError):
                self._sleep(0.5)
        return False

    def launch(self, app_code: str, requirements: str, output_dir: Path) -> LocalSnapshot:
        with self._lock:
            if self._thread and self._thread.is_alive():
                raise RuntimeError("A local deployment is already running.")
            self._stop_event.clear()
            self._startup_event.clear()
            self._startup_error = ""
            self._process = None
            self.progress.start()
            self.progress.begin("validating")
            self.snapshot.environment = str(self._environment_root(Path(output_dir)) / "venv")
            self._thread = threading.Thread(target=self._run, args=(app_code, requirements, Path(output_dir)),
                                             daemon=True, name="m2g-local-launch")
            self._thread.start()
            return self.snapshot

    def launch_batch(self, batch_file: Path, output_dir: Path, hardware: str = "",
                     hardware_log: Sequence[str] = ()) -> LocalSnapshot:
        """Launch the generated app-owned BAT asynchronously on Windows.

        ``hardware`` (one line for ``LocalSnapshot.hardware``) and ``hardware_log`` (lines for this launch's log) are the result of the
        hardware preflight. They are kept on the launcher because ``_run_batch`` replaces the snapshot; a launch without them clears
        whatever the previous launch had recorded."""
        output_dir = Path(output_dir).resolve()
        batch_file = Path(batch_file).resolve()
        if not batch_file.is_file():
            raise FileNotFoundError(f"Generated launcher not found: {batch_file.name}")
        if batch_file.parent != output_dir:
            raise ValueError("The generated launcher must be inside the configured app output directory.")
        with self._lock:
            if self._thread and self._thread.is_alive():
                raise AlreadyRunningError("A local deployment is already running for this app.")
            self._stop_event.clear()
            self._startup_event.clear()
            self._startup_error = ""
            self._process = None
            self._launch_hardware = str(hardware or "")
            self._launch_hardware_log = [str(line) for line in hardware_log]
            self.snapshot = LocalSnapshot(state=PREPARING, environment=str(output_dir / ".venv"), app=output_dir.name,
                                          hardware=self._launch_hardware)
            self.progress.start()
            self.progress.begin("validating")  # start_local_app has just validated the app; the BAT then re-checks its files
            self._thread = threading.Thread(
                target=self._run_batch,
                args=(batch_file, output_dir),
                daemon=True,
                name="m2g-local-bat-launch",
            )
            self._thread.start()
            return self.snapshot

    def stop(self) -> LocalSnapshot:
        with self._lock:
            proc = self._process
            self._stop_event.set()
        self._terminate_tree(proc)
        thread = self._thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=10)  # so an immediate re-launch is not refused as a duplicate
        with self._lock:
            self._process = None
            self._startup_event.set()
            self.progress.stop()  # no-op when the deployment already failed
            if self.snapshot.state in (RUNNING, STARTING, PREFLIGHT, INSTALLING_DEPENDENCIES,
                                       CREATING_ENVIRONMENT, ENVIRONMENT, HARDWARE, PREPARING):
                self.snapshot.state = STOPPED
            if self._metadata_file:
                try:
                    self._metadata_file.write_text(json.dumps(
                        {"environment": self.snapshot.environment, "python": self.snapshot.python,
                         "pid": None, "url": "", "state": "stopped"}, indent=2), encoding="utf-8")
                except OSError:
                    pass
        self._log("Local deployment stopped.")
        return self.snapshot
