"""Run a generated Gradio app on a Google Colab runtime through the official Colab CLI and return the REAL public URL.

Flow (every step is checked, none is assumed):
  1. validate the generated app + requirements with the existing static validators (never weakened here);
  2. confirm the `colab` command is Google's CLI and that a real authenticated call works;
  3. request a runtime (optionally with a GPU) and VERIFY the hardware with nvidia-smi on the runtime;
  4. pip-install the requirements on the runtime (success judged by a nonce marker, not by CLI exit status);
  5. transfer exactly four fixed files (app.py, requirements.txt, launcher, optional auth) and verify SHA-256 hashes;
  6. start the launcher as a DETACHED process on the runtime (it survives the exec call and kernel restarts);
  7. poll until the launcher reports model-loaded and a share URL, OR fails, OR dies, OR times out;
  8. check the URL is a gradio.live/gradio.app host and answers an HTTP request from this machine;
  9. on ANY failure, cancel, or timeout: stop the runtime (best effort, verified against `colab sessions`).

Honesty rules: the URL shown is parsed only from marker lines of THIS run (per-run random prefix); nothing is ever
fabricated or reused; "running" means model loaded + URL reachable, NOT that inference quality was verified (the
optional self-test runs the app's own first example and reports pass/fail/none separately).

What is never sent to the runtime: .env contents, HF/GitHub tokens, API keys, local paths. The payload is exactly
app.py, requirements.txt, the fixed launcher and (only if you set one) a username/password for the public link.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import re
import secrets
import threading
import time
import traceback
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .colab_cli import ColabCli, CliResult, truncate
from .colab_gen import _loader_name
from .config import Settings
from .error_classify import ErrorInfo, classify_failure, classify_text, compat_error, may_retry, retry_delay
from .gradio_gen import GenerationError, split_app_code
from .preflight import render_probe_runner, run_preflight
from .security import redact_secrets
from .ui_errors import env_secrets
from .validate import ValidationError, validate_app_code, validate_requirements_text

log = logging.getLogger("m2g.colab")

REMOTE_DIR = "/content/m2g_app"
CPU_LABEL = "CPU (no GPU)"
MAX_APP_BYTES = 400_000
_SHARE_URL_RE = re.compile(r"^https://[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*\.gradio\.(?:live|app)/?$")
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_GOOGLE_TOKEN_RE = re.compile(r"(ya29\.[\w.-]+|1//[\w.-]+|4/[\w.-]{20,})")
# Phase 4: credentials inside URLs (https://user:secret@host) and key=value pairs. 'code=' is deliberately NOT a secret key here:
# diagnostics legitimately contain 'code=pip_unsatisfiable'.
_URL_CREDENTIALS_RE = re.compile(r"(?<=://)[^/\s:@]+:[^/\s@]+@")
_SECRET_PAIR_RE = re.compile(r"(?i)\b(password|passwd|secret|api[_-]?key|access[_-]?token|auth[_-]?token|token)=([^\s&'\x22]+)")

# states
# Stages of one launch, in order. A failure or cancellation is a SEPARATE terminal state; the stage it happened in is
# kept in LaunchSnapshot.failed_stage. Idle -> Checking CLI -> Creating runtime -> Probing hardware -> Installing
# dependencies -> Transferring files -> Starting app -> Waiting for readiness -> Running.
IDLE, PREPARING, CHECKING, CREATING, HARDWARE, INSTALLING, UPLOADING, STARTING, WAITING = (
    "idle", "validating app", "checking CLI", "creating runtime", "probing hardware", "installing dependencies",
    "transferring files", "starting app", "waiting for readiness")
RUNNING, FAILED, CANCELLED, STOPPED, DISCONNECTED = "running", "failed", "cancelled", "stopped", "disconnected"
ACTIVE_STATES = {PREPARING, CHECKING, CREATING, HARDWARE, INSTALLING, UPLOADING, STARTING, WAITING}
STAGE_ORDER = (IDLE, CHECKING, CREATING, HARDWARE, INSTALLING, UPLOADING, STARTING, WAITING, RUNNING)
# Deployment-stage label put in front of every log line (see ColabLauncher._log). Steps that share a launcher state (preflight,
# model loading, inference, verification, cleanup) override it with ``_in_stage`` / ``stage=``.
_STAGE_OF_STATE = {IDLE: "idle", PREPARING: "inspection", CHECKING: "inspection", CREATING: "runtime", HARDWARE: "runtime",
                   INSTALLING: "dependencies", UPLOADING: "transfer", STARTING: "startup", WAITING: "startup", RUNNING: "running",
                   FAILED: "result", CANCELLED: "result", STOPPED: "cleanup", DISCONNECTED: "cleanup"}

# --------------------------------------------------------------------------------------------- remote scripts
# Every @@KEY@@ is replaced by a complete Python string/number LITERAL (json.dumps), never raw text. All templates are
# pure ASCII on purpose (Windows pipes use a legacy code page unless forced).

HW_SCRIPT = '''import json, os, platform, shutil, subprocess
_hw = {"python": platform.python_version(), "gpu": None, "gpu_mem_mib": None, "ram_gib": None, "disk_free_gib": None}
try:
    _lines = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                            capture_output=True, text=True, timeout=30).stdout.strip().splitlines()
    if _lines:
        _name, _mem = [_p.strip() for _p in _lines[0].split(",", 1)]
        _hw["gpu"], _hw["gpu_mem_mib"] = _name, int(float(_mem))
except Exception:
    pass
try:
    _hw["ram_gib"] = round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**30, 1)
    _hw["disk_free_gib"] = round(shutil.disk_usage("/content").free / 2**30, 1)
except Exception:
    pass
print(@@PREFIX@@ + " HW " + json.dumps(_hw), flush=True)
'''

INSTALL_SCRIPT = '''import json, subprocess, sys
_reqs = json.loads(@@REQS@@)
_r = subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check", "--no-input"] + _reqs,
                    capture_output=True, text=True)
print((_r.stdout or "")[-1500:])
print((_r.stderr or "")[-2500:])
print(@@PREFIX@@ + (" PIP_OK" if _r.returncode == 0 else " PIP_FAIL " + str(_r.returncode)), flush=True)
'''

WRITE_SCRIPT = '''import hashlib, json, os
_files = json.loads(@@PAYLOAD@@)
_dir = @@DIR@@
os.makedirs(_dir, exist_ok=True)
_sums = {}
for _n, _t in _files.items():
    _path = os.path.join(_dir, _n)
    with open(_path, "wb") as _f:
        _f.write(_t.encode("utf-8"))
    if _n == "m2g_auth.json":
        os.chmod(_path, 0o600)
    with open(_path, "rb") as _f:
        _sums[_n] = hashlib.sha256(_f.read()).hexdigest()
print(@@PREFIX@@ + " WRITTEN " + json.dumps(_sums, sort_keys=True), flush=True)
'''

START_SCRIPT = '''import os, subprocess, sys
_dir = @@DIR@@
_log = open(os.path.join(_dir, "run.log"), "wb")
_p = subprocess.Popen([sys.executable, "-u", os.path.join(_dir, "m2g_launch.py")], cwd=_dir, stdout=_log,
                      stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
with open(os.path.join(_dir, "run.pid"), "w") as _f:
    _f.write(str(_p.pid))
print(@@PREFIX@@ + " STARTED " + str(_p.pid), flush=True)
'''

# Phase 4: the link password is only needed while the app starts (the launcher reads it into memory first). Once the app is up it is
# overwritten with zero bytes and deleted, so it does not sit on the runtime's disk for the rest of the session.
REMOVE_SECRETS_SCRIPT = '''import os
_dir = @@DIR@@
_gone = True
for _n in ("m2g_auth.json",):
    _p = os.path.join(_dir, _n)
    try:
        if os.path.exists(_p):
            _size = os.path.getsize(_p)
            with open(_p, "wb") as _f:
                _f.write(bytes(_size))
            os.remove(_p)
    except Exception:
        pass
    if os.path.exists(_p):
        _gone = False
print(@@PREFIX@@ + (" SECRETS_REMOVED" if _gone else " SECRETS_KEPT"), flush=True)
'''

POLL_SCRIPT = '''import json, os
_dir = @@DIR@@
_P = @@PREFIX@@
def _state(pid):
    try:
        with open("/proc/%d/stat" % pid) as _f:
            return _f.read().rsplit(")", 1)[1].split()[0]
    except Exception:
        return None
_alive = False
try:
    with open(os.path.join(_dir, "run.pid")) as _f:
        _alive = _state(int(_f.read().strip())) not in (None, "Z", "X")
except Exception:
    _alive = False
_marks = []
_tail = ""
try:
    with open(os.path.join(_dir, "run.log"), "rb") as _f:
        for _raw in _f:
            for _seg in _raw.decode("utf-8", "replace").replace("\\r", "\\n").split("\\n"):
                if _seg.startswith(_P + " "):
                    _marks.append(_seg)
        _f.seek(0, 2)
        _size = _f.tell()
        _f.seek(max(0, _size - 8000))
        _tail = _f.read().decode("utf-8", "replace")
except Exception:
    pass
print(_P + " ALIVE " + ("1" if _alive else "0"), flush=True)
for _m in _marks[-40:]:
    print(_m, flush=True)
print(_P + " TAIL " + json.dumps(_tail[-6000:]), flush=True)
'''

# Runs on the VM as a detached process. Fixed text; not generated from model metadata.
LAUNCHER_SCRIPT = '''import contextlib, io, json, os, runpy, sys, time, traceback
_P = @@PREFIX@@


def _say(tag, payload=""):
    print("\\n" + _P + " " + tag + ((" " + payload) if payload else ""), flush=True)


# Bounded retry budget for the public tunnel: 6 attempts, waits of 5/10/20/30/30 s (about 95 s of waiting). The previous
# budget (3 tries, 5 s apart) covered only ~10 s; a refused tunnel login is a server-side condition whose duration is
# unknown, so the window is wider but still finite.
_SHARE_DELAYS = (5.0, 10.0, 20.0, 30.0, 30.0)
_SHARE_TRIES = len(_SHARE_DELAYS) + 1


def _share_transient(text):
    _low = (text or "").lower()
    if "checksum mismatch" in _low or "missing file" in _low:
        return False
    return any(_s in _low for _s in ("login to server failed", "connection refused", "connection reset",
                                     "i/o timeout", "timed out", "no route to host", "status.gradio.app"))


def _share_reason(out, err):
    _lines = [_ln.strip() for _ln in (err or "").splitlines() if _ln.strip()]
    _r = ""
    for _line in _lines:
        if any(_t in _line.lower() for _t in ("login to server failed", "connection refused", "connection reset",
                                              "i/o timeout", "no route to host", "fail", "error")):
            _r = _line
            break
    if not _r and _lines:
        _r = _lines[-1]
    for _line in (out or "").splitlines():
        if "Could not create share link" in _line:
            _r = (_r + " | " + _line.strip()) if _r else _line.strip()
    return _r


def _tunnel_server(reason):
    # frpc reports "dial tcp HOST:PORT: connect: ..."; return HOST:PORT so the failing server is named in the log.
    if "dial tcp " not in (reason or ""):
        return ""
    return reason.split("dial tcp ", 1)[1].split(": ", 1)[0].strip()


def _probe(target):
    import socket
    _h, _, _p = target.rpartition(":")
    try:
        socket.create_connection((_h.strip("[]"), int(_p)), timeout=5).close()
        return target + " accepts connections"
    except BaseException as _e:
        return target + " " + type(_e).__name__


_gv = "unknown"
try:
    _here = os.path.dirname(os.path.abspath(__file__))
    os.chdir(_here)
    sys.path.insert(0, _here)
    try:  # diagnostic only: read the installed version from package metadata, without importing gradio
        from importlib import metadata as _md
        _gv = _md.version("gradio")
    except BaseException as _ve:
        _gv = "not installed (" + type(_ve).__name__ + ")"
    print("Installed Gradio version: " + _gv, flush=True)
    _ns = runpy.run_path(os.path.join(_here, "app.py"), run_name="m2g_app")
    _demo = _ns["demo"]
    _say("IMPORTED")
    _ns[@@LOADER@@]()
    _say("LOADED")
    _ex = getattr(_demo, "examples", None)
    if "predict" in _ns and isinstance(_ex, list) and _ex and isinstance(_ex[0], list):
        try:
            _say("SELFTEST_OK", json.dumps(str(_ns["predict"](*_ex[0]))[:300]))
        except BaseException as _e:
            # short summary first (the panel shows its first characters), then the full traceback for the diagnostics
            _say("SELFTEST_FAIL", json.dumps((type(_e).__name__ + ": " + str(_e))[:300] + " | " + traceback.format_exc()[-3000:]))
    else:
        _say("SELFTEST_NONE")
    _auth = None
    _ap = os.path.join(_here, "m2g_auth.json")
    if os.path.exists(_ap):
        with open(_ap, encoding="utf-8") as _f:
            _auth = tuple(json.load(_f))
    _url, _why, _t0, _tried = None, "", time.time(), 0
    for _n in range(1, _SHARE_TRIES + 1):
        _tried = _n
        _o, _e = io.StringIO(), io.StringIO()
        try:
            with contextlib.redirect_stdout(_o), contextlib.redirect_stderr(_e):
                _res = _demo.launch(server_name="127.0.0.1", share=True, prevent_thread_lock=True, quiet=True,
                                    show_error=True, auth=_auth)
        except BaseException as _exc:
            sys.stdout.write(_o.getvalue())
            sys.stderr.write(_e.getvalue())
            _why = type(_exc).__name__ + ": " + str(_exc)
            break
        _out, _err = _o.getvalue(), _e.getvalue()
        sys.stdout.write(_out)
        sys.stderr.write(_err)
        _url = getattr(_demo, "share_url", None)
        if not _url and isinstance(_res, tuple) and len(_res) > 2:
            _url = _res[2]
        if _url:
            break
        _why = _share_reason(_out, _err)
        if _n == _SHARE_TRIES or not _share_transient(_out + _err):
            break
        _wait = _SHARE_DELAYS[_n - 1]
        _say("SHARE_RETRY", json.dumps({"attempt": _n, "of": _SHARE_TRIES, "reason": _why, "wait": _wait,
                                        "elapsed": int(time.time() - _t0)}))
        time.sleep(_wait)
    if not _url:
        _msg = ("Gradio did not create a public share link after " + str(_tried) + " attempt(s) in "
                + str(int(time.time() - _t0)) + " s. " + (_why or "no reason reported"))
        _srv = _tunnel_server(_why)
        if _srv and _share_transient(_why):
            _msg += " | Probe from the runtime: " + _probe("api.gradio.app:443") + "; " + _probe(_srv)
        raise RuntimeError(_msg)
    _say("URL", str(_url))
    _demo.block_thread()
except BaseException:
    _tb = traceback.format_exc()
    print(_tb, flush=True)
    _say("FAIL", json.dumps("Installed Gradio version: " + _gv + "\\n" + _tb[-6000:]))
    sys.exit(1)
'''


_PLACEHOLDER_RE = re.compile(r"@@([A-Z0-9_]+)@@")


def _render(template: str, **values: Any) -> str:
    """Single-pass substitution: inserted values are never re-scanned, so payload text cannot inject placeholders."""
    def sub(match: "re.Match[str]") -> str:
        key = match.group(1)
        if key not in values:
            raise ValueError(f"unresolved template placeholder {key}")
        return json.dumps(values[key])

    return _PLACEHOLDER_RE.sub(sub, template)


# --------------------------------------------------------------------------------------------- parsing helpers
def parse_markers(output: str, prefix: str) -> Dict[str, Any]:
    """Read ONLY lines that start with this run's random prefix, so app output cannot fake a URL or success."""
    found: Dict[str, Any] = {"hw": None, "pip": None, "written": None, "started": None, "alive": None, "imported": False,
                             "loaded": False, "selftest": None, "selftest_detail": "", "url": None, "fail": None, "tail": "",
                             "share_retry": None, "probe": None, "secrets": None}

    def _json(payload: str) -> Any:
        try:
            return json.loads(payload)
        except ValueError:
            return None

    for raw in (output or "").splitlines():
        line = raw.rstrip("\r")
        if not line.startswith(prefix + " "):
            continue
        tag, _, payload = line[len(prefix) + 1:].partition(" ")
        payload = payload.strip()
        if tag == "HW":
            found["hw"] = _json(payload)
        elif tag == "PIP_OK":
            found["pip"] = 0
        elif tag == "PIP_FAIL":
            found["pip"] = int(payload) if payload.lstrip("-").isdigit() else 1
        elif tag == "WRITTEN":
            found["written"] = _json(payload)
        elif tag == "STARTED":
            found["started"] = payload
        elif tag == "ALIVE":
            found["alive"] = payload == "1"
        elif tag == "IMPORTED":
            found["imported"] = True
        elif tag == "LOADED":
            found["loaded"] = True
        elif tag in ("SELFTEST_OK", "SELFTEST_FAIL", "SELFTEST_NONE"):
            found["selftest"] = tag.split("_", 1)[1].lower()
            found["selftest_detail"] = str(_json(payload) or "") if payload else ""
        elif tag == "SHARE_RETRY":
            val = _json(payload)
            found["share_retry"] = val if isinstance(val, dict) else None
        elif tag == "PROBE":
            found["probe"] = _json(payload)
        elif tag == "SECRETS_REMOVED":
            found["secrets"] = "removed"
        elif tag == "SECRETS_KEPT":
            found["secrets"] = "kept"
        elif tag == "URL":
            found["url"] = payload
        elif tag == "FAIL":
            val = _json(payload)
            found["fail"] = val if isinstance(val, str) else payload
        elif tag == "TAIL":
            val = _json(payload)
            found["tail"] = val if isinstance(val, str) else ""
    return found


_DIAGNOSES: Sequence[Tuple[Tuple[str, ...], str]] = (
    (("cuda out of memory", "outofmemoryerror", "torch.cuda.outofmemory"),
     "The GPU ran out of memory. Pick a larger GPU (L4/A100/H100) or a smaller model."),
    (("gated repo", "access to model", "is restricted", "401 client error", "403 client error", "repository not found",
      "invalid username or password"),
     "Hugging Face refused the download (gated/private model or bad id). This tool never sends your Hugging Face token to "
     "Colab, so gated models are not supported here."),
    (("no space left on device",), "The runtime ran out of disk space for the model weights. Pick a smaller model."),
    (("could not create share link", "frpc", "share link"),
     "Gradio could not create the public gradio.live link from the runtime (its tunnel server refused or did not answer, "
     "or the tunnel helper could not be set up). Temporary tunnel failures are retried automatically; the reason is below. "
     "Recovery: check https://status.gradio.app and launch again in a few minutes (the runtime was released, so nothing "
     "is left running). This is not a Google sign-in problem."),
    (("modulenotfounderror", "importerror", "no module named"),
     "A Python dependency is missing or incompatible on the runtime. Check the log for the module name."),
    (("does not recognize this architecture", "unrecognized configuration class", "unrecognized model"),
     "The installed transformers/diffusers version does not know this model architecture. It may be too new or unsupported."),
    (("connectionerror", "max retries exceeded", "name or service not known", "temporary failure in name resolution"),
     "The runtime could not reach the internet or Hugging Face (network error)."),
)


def diagnose_output(text: str) -> Optional[str]:
    low = (text or "").lower()
    for needles, hint in _DIAGNOSES:
        if any(n in low for n in needles):
            return hint
    return None


# Evidence that Google rejected the CLI's credentials. Deliberately NOT bare words like "token", "oauth" or "401": those
# appear in ordinary kernel/WebSocket tracebacks and used to turn a dropped connection into a false "sign in again".
# "eoferror" is deliberately absent: a bare EOFError is how the CLI reports a DROPPED KERNEL CONNECTION during `exec`,
# and it used to be blamed on the user's Google sign-in. (The sign-in flow detects it separately, in ColabSignIn.)
STRONG_AUTH_MARKERS: Tuple[str, ...] = (
    "unauthenticated", "unauthorized", "invalid_grant", "invalid_client", "expired or revoked", "reauthenticate",
    "re-authenticate", "sign in again", "not signed in", "please sign in", "authorization code",
    "refresherror", "defaultcredentialserror", "invalid credentials", "credentials were not found", "http 401")
_BROKEN_CLI_MARKERS = ("modulenotfounderror", "importerror", "no module named", "winerror", "is not recognized as",
                       "not supported on windows", "unix system modules")
# The kernel/WebSocket link to the runtime dropped or never opened. These are checked against the whole output, because a
# frame from the WebSocket client in a traceback is itself evidence.
_KERNEL_CONNECTION_MARKERS = (
    "connection was lost", "connection lost", "connectionclosed", "connection closed", "connection reset",
    "connectionreseterror", "connectionabortederror", "brokenpipeerror", "websocket", "wsclient", "kernel died",
    "kernel is dead", "kernel restart", "kernel_client", "kernel client", "remote end closed", "no close frame",
    "eof occurred in violation", "eoferror", "handshake", "bad gateway", "gateway timeout", "service unavailable")
_QUOTA_MARKERS = ("quota", "compute unit", "usage limit", "limit reached", "resource_exhausted", "no capacity",
                  "not available", "insufficient", "too many")
# Failures worth retrying: the transport broke, or the CLI exited non-zero without a recognisable reason.
_TRANSIENT_KINDS = {"kernel_connection", "cli_failed"}


def exception_lines(text: str) -> str:
    """Only the unindented, non-frame lines of Python tracebacks, i.e. the exception messages. File paths and source
    lines in traceback frames (which mention words like 'auth' or 'token') are dropped."""
    keep = []
    for line in (text or "").splitlines():
        if not line.strip() or line[0] in " \t" or line.startswith(("Traceback", "File ")):
            continue
        keep.append(line)
    return "\n".join(keep)


def classify_cli_failure(res: CliResult, auth_markers: Sequence[str] = STRONG_AUTH_MARKERS) -> Tuple[str, str]:
    """(kind, user-facing message) for a failed CLI call. Kinds: cancelled, timeout, cli_unavailable, needs_auth, cli_broken,
    kernel_connection, hardware_unavailable, cli_failed. Never includes secrets (output is redacted by the caller)."""
    if res.error == "cancelled":
        return "cancelled", "Cancelled."
    if res.error == "timeout":
        return "timeout", "The Colab CLI did not finish in time."
    if res.error == "unavailable":
        return "cli_unavailable", "The Colab CLI could not be started."
    full = res.combined.lower()
    exc = exception_lines(res.combined).lower()
    # The code that ran ON the runtime can fail because a library there lacks an API the app uses (a removed pipeline task, a name
    # that cannot be imported). Such output also contains words the checks below react to ("ImportError", "token"), and used to be
    # reported as a broken CLI or a Google sign-in problem. The original error text decides first; the kind is permanent.
    compat = compat_error(res.combined)
    if compat is not None:
        return "app_incompatible", f"{compat.title} {compat.recovery}"
    # Order matters: a transport symptom (the kernel link dropped) must never be reported as a credential problem, so the
    # kernel-connection evidence is checked BEFORE the auth markers. "Connection was lost" was previously blamed on the
    # user's Google sign-in whenever a frame path also mentioned a token.
    if any(m in full for m in _BROKEN_CLI_MARKERS):
        return "cli_broken", "The Colab CLI crashed (broken install or unsupported platform); this is not a sign-in problem."
    if any(m in full for m in _KERNEL_CONNECTION_MARKERS):
        return "kernel_connection", ("The connection to the runtime's kernel was lost or could not be opened. This is not "
                                     "a Google sign-in problem.")
    if any(m in exc for m in (auth_markers or STRONG_AUTH_MARKERS)):
        return "needs_auth", ("Google rejected the Colab CLI's credentials (sign-in missing, expired or revoked). "
                              "Sign in again.")
    if any(k in exc for k in _QUOTA_MARKERS):
        return "hardware_unavailable", ("Colab could not provide this runtime (hardware unavailable, quota or usage limit "
                                        "reached, or too many active runtimes). Try another GPU or wait.")
    return "cli_failed", "The Colab CLI reported an error."


def verify_share_url(url: str, opener: Optional[Callable] = None, timeout: int = 30,
                     allow_auth_wall: bool = False) -> Tuple[bool, str]:
    """True only for a gradio.live/gradio.app host that answers an HTTP request from THIS machine."""
    if not _SHARE_URL_RE.match(url or ""):
        return False, f"Unexpected share URL format, not trusted: {truncate(url or '', 80)}"
    open_fn = opener or urllib.request.urlopen
    request = urllib.request.Request(url, headers={"User-Agent": "model-to-gradio-url-check"})
    try:
        with open_fn(request, timeout=timeout) as response:
            status = getattr(response, "status", None) or response.getcode()
    except urllib.error.HTTPError as exc:
        code = exc.code
        try:
            exc.close()  # an HTTPError owns the response; release it now instead of leaving it to the garbage collector
        except Exception:
            pass
        if allow_auth_wall and code in (401, 403):
            return True, f"HTTP {code} from this machine (the link is password-protected, as configured)."
        return False, f"The public URL answered with HTTP {code}."
    except Exception as exc:
        return False, f"The public URL did not answer from this machine ({type(exc).__name__})."
    if 200 <= int(status) < 400:
        return True, f"HTTP {status} from this machine."
    return False, f"The public URL answered with HTTP {status}."


def gpu_name_matches(requested: Optional[str], detected: Optional[str]) -> Optional[bool]:
    """None = cannot tell (e.g. G4 naming is not documented); True/False otherwise."""
    if not requested or not detected:
        return None
    if requested.upper() == "G4":
        return None
    return requested.upper() in detected.upper()


def suggest_gpu(hw: Any, available: Sequence[str]) -> Tuple[str, str]:
    """Suggest a Colab accelerator from the analyzer's memory ESTIMATE. A suggestion, not a guarantee."""
    avail = [a.upper() for a in available]
    need = hw.est_vram_gb_4bit if (getattr(hw, "suggest_4bit", False) and hw.est_vram_gb_4bit) else hw.est_vram_gb_fp16
    if need is None:
        pick = "T4" if "T4" in avail else (avail[0] if avail else CPU_LABEL)
        return pick, "Memory need could not be estimated from the repository metadata, so a small GPU is suggested."
    if not getattr(hw, "needs_gpu", False) and getattr(hw, "runs_locally_cpu", False):
        return CPU_LABEL, f"Estimated {need:.1f} GiB: small enough for a CPU runtime (estimate); a GPU is faster."
    ladder = [(13.5, "T4"), (20.0, "L4"), (36.0, "A100"), (1e9, "H100")]
    wanted = next(name for limit, name in ladder if need <= limit)
    order = ["T4", "L4", "A100", "H100"]
    for name in order[order.index(wanted):] + order[:order.index(wanted)][::-1]:
        if name in avail:
            return name, f"Estimated {need:.1f} GiB needs about a {wanted}-class GPU (estimate)."
    return CPU_LABEL, "No suitable accelerator is offered by the installed CLI."


# --------------------------------------------------------------------------------------------- launcher
class _Abort(Exception):
    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind, self.message = kind, message


@dataclass
class LaunchSnapshot:
    state: str = IDLE
    message: str = ""
    error_kind: str = ""
    session: Optional[str] = None
    gpu_requested: Optional[str] = None
    hardware: Optional[Dict[str, Any]] = None
    share_url: Optional[str] = None
    url_check: str = ""
    model_loaded: bool = False
    selftest: Optional[str] = None  # ok | fail | none | None (not reached)
    selftest_detail: str = ""
    auth_protected: bool = False
    failed_stage: str = ""  # the stage that was active when the launch failed or was cancelled
    exit_code: Optional[int] = None  # exit status of the last FAILED Colab CLI call
    retries: int = 0  # transient kernel/connection failures that were retried in this launch
    diagnostics: str = ""  # complete, redacted output of failed CLI calls (the original tracebacks)
    preflight: Optional[Dict[str, Any]] = None  # runtime preflight verdict: status, gradio_version, repaired, remaining
    error_category: str = ""  # Phase 3 classification of the failure (see m2g.error_classify)
    error_code: str = ""
    retryable: bool = False  # True only if the classified cause is transient (a new launch may succeed)
    recovery: str = ""  # the next step shown to the person
    recovery_log: List[str] = field(default_factory=list)  # every automatic retry that was made, in order
    logs: List[str] = field(default_factory=list)
    started_at: float = 0.0
    updated_at: float = 0.0


class ColabLauncher:
    def __init__(self, cli: ColabCli, *, verify: Optional[Callable[[str, bool], Tuple[bool, str]]] = None,
                 sleep: Optional[Callable[[float], Any]] = None, clock: Callable[[], float] = time.monotonic,
                 poll_interval: float = 10.0, launch_timeout: float = 1800.0, install_timeout: int = 1800,
                 url_retries: int = 6, url_retry_delay: float = 5.0, remote_dir: str = REMOTE_DIR,
                 nonce: Optional[Callable[[], str]] = None, exec_attempts: int = 4, retry_delay: float = 8.0,
                 create_attempts: int = 3, poll_miss_limit: int = 6, health_attempts: int = 3,
                 install_attempts: int = 3, startup_attempts: int = 3,
                 auth_markers: Sequence[str] = STRONG_AUTH_MARKERS):
        self.cli = cli
        # Phase 3 bounds: the dependency install and the app start-up are each tried at most this many times, and only when the
        # failure is classified as transient (m2g.error_classify). Permanent and unrecognised errors are never replayed.
        self.install_attempts, self.startup_attempts = max(1, int(install_attempts)), max(1, int(startup_attempts))
        self._verify = verify or (lambda url, auth: verify_share_url(url, allow_auth_wall=auth))
        self._cancel = threading.Event()
        self._sleep = sleep or (lambda s: self._cancel.wait(s))
        self._clock = clock
        self.poll_interval, self.launch_timeout, self.install_timeout = poll_interval, launch_timeout, install_timeout
        self.url_retries, self.url_retry_delay = url_retries, url_retry_delay
        self.remote_dir = remote_dir
        self._nonce = nonce or (lambda: secrets.token_hex(8))
        self._auth_markers = tuple(auth_markers)
        # Retry policy: a transient kernel/WebSocket failure is retried (bounded, with growing delay) before the launch is
        # given up; only then is the runtime released. A script that ran and reported failure is never retried.
        self.exec_attempts, self.retry_delay = max(1, int(exec_attempts)), float(retry_delay)
        # Runtime creation gets its own bound: a failed `new` is only retried after the session listing proves no runtime
        # was assigned, so a 503 that still allocated one is adopted instead of asking for a second GPU.
        self.create_attempts = max(1, int(create_attempts))
        self.poll_miss_limit, self.health_attempts = max(1, int(poll_miss_limit)), max(1, int(health_attempts))
        self._diag: List[str] = []
        self._lock = threading.RLock()
        self._snap = LaunchSnapshot()
        self._busy = False
        self._thread: Optional[threading.Thread] = None
        self._prefix = ""
        self._extra_secrets: List[str] = []
        self._auth_on = False  # a link password file was sent to the runtime in this launch
        self._tls = threading.local()  # per-thread stage override for log lines (see _in_stage)

    # ------------------------------------------------------------------ state helpers
    @property
    def busy(self) -> bool:
        with self._lock:
            return self._busy

    def snapshot(self) -> LaunchSnapshot:
        with self._lock:
            s = self._snap
            return LaunchSnapshot(**{**s.__dict__, "logs": list(s.logs), "hardware": dict(s.hardware) if s.hardware else None,
                                    "preflight": dict(s.preflight) if s.preflight else None,
                                    "recovery_log": list(s.recovery_log)})

    def reset_view(self) -> None:
        """Forget what an old page showed: the log lines and, once a launch has finished, its saved diagnostics and result.

        A launch in progress and a runtime this app tracks are real state and are never touched, so the page can never claim
        a live runtime is gone (or an idle one is running).
        """
        with self._lock:
            self._snap.logs.clear()
            if self._busy:
                return
            self._diag.clear()
            self._snap.diagnostics = ""
            if self._snap.state in (FAILED, CANCELLED, STOPPED, DISCONNECTED) and not self._snap.session:
                self._snap = LaunchSnapshot()

    def _set(self, **kw: Any) -> None:
        with self._lock:
            for k, v in kw.items():
                setattr(self._snap, k, v)
            self._snap.updated_at = time.time()

    def _scrub(self, text: str) -> str:
        """Every line that reaches the log or the diagnostics goes through here: the link password (plain and as it appears inside
        JSON/remote tracebacks), this app's own secret environment values, token shapes, URL credentials and key=value secrets."""
        out = redact_secrets(text, [*self._extra_secrets, *env_secrets()])
        out = _GOOGLE_TOKEN_RE.sub("[redacted]", out)
        out = _URL_CREDENTIALS_RE.sub("[redacted]@", out)
        return _SECRET_PAIR_RE.sub(lambda m: f"{m.group(1)}=[redacted]", out)

    def _stage_now(self) -> str:
        forced = getattr(self._tls, "stage", None)
        if forced:
            return forced
        with self._lock:
            return _STAGE_OF_STATE.get(self._snap.state, "launch")

    @contextlib.contextmanager
    def _in_stage(self, stage: str):
        """Label every log line written by THIS thread inside the block with ``stage`` (preflight, cleanup, verification ...)."""
        previous = getattr(self._tls, "stage", None)
        self._tls.stage = stage
        try:
            yield
        finally:
            self._tls.stage = previous

    def _log(self, line: str, stage: Optional[str] = None) -> None:
        """One timestamped, stage-tagged, redacted line: ``HH:MM:SS [stage] text``."""
        clean = self._scrub(line)
        tag = stage or self._stage_now()
        with self._lock:
            self._snap.logs.append(f"{time.strftime('%H:%M:%S')} [{tag}] {clean}")
            del self._snap.logs[:-600]
            self._snap.updated_at = time.time()
        log.info("[%s] %s", tag, clean[:300])

    @contextlib.contextmanager
    def _live_output(self, label: str, heartbeat: float = 20.0):
        """While a long CLI call runs, show its stdout/stderr in the log line by line (via ``cli.on_output``) and add a
        'still running' line every ``heartbeat`` seconds, so the Logs panel is never silent for minutes. Yields a one-item
        list holding how many output lines were shown. Marker lines (this run's random prefix) are never echoed, and every
        line goes through ``_log`` and therefore through the same secret redaction as the rest of the log."""
        shown = [0]

        def sink(stream: str, line: str) -> None:
            text = (line or "").strip()
            if not text or (self._prefix and text.startswith(self._prefix)):
                return
            shown[0] += 1
            self._log(f"[{stream}] {truncate(text, 400)}")

        done = threading.Event()

        def beat() -> None:
            began = time.monotonic()
            while not done.wait(heartbeat):
                self._log(f"{label}: still running ({int(time.monotonic() - began)} s elapsed)…")

        previous = getattr(self.cli, "on_output", None)
        self.cli.on_output = sink
        ticker = threading.Thread(target=beat, daemon=True, name="m2g-colab-heartbeat")
        ticker.start()
        try:
            yield shown
        finally:
            done.set()
            self.cli.on_output = previous

    def _record(self, step: str, res: CliResult, show_output: bool = True, streamed: bool = False) -> None:
        head = f"[{step}] exit={res.returncode}" + (f" error={res.error}" if res.error else "") + f" ({res.duration_s:.0f}s)"
        self._log(head)
        if not res.ok and res.error != "cancelled" and show_output:
            self._add_diag(step, res)  # keep the COMPLETE output, not just the 12-line excerpt shown in the log
        if show_output and not streamed and res.combined.strip():  # streamed output is already in the log
            lines = [ln for ln in res.combined.splitlines()
                     if ln.strip() and not (self._prefix and ln.startswith(self._prefix))]
            if lines:
                self._log("    " + truncate("\n    ".join(lines[-12:]), 1500))

    def _checkpoint(self) -> None:
        if self._cancel.is_set():
            raise _Abort("cancelled", "Cancelled by the user.")

    def _add_diag(self, step: str, res: CliResult) -> None:
        """Keep the complete (redacted) output of a failed CLI call so the original traceback is never lost."""
        cmd = " ".join(str(a) for a in res.args)  # code goes over stdin, so the argument list holds no secrets
        body = self._scrub(res.combined).strip() or "(no output)"
        block = f"[{step}] exit={res.returncode} error={res.error or '-'} after {res.duration_s:.0f}s\n$ {cmd}\n{body}"
        with self._lock:
            self._snap.exit_code = res.returncode
            if self._diag and self._diag[-1] == block[:12000]:
                return  # the same failed call was already captured by _record
            self._diag.append(block[:12000])
            del self._diag[:-8]
            self._snap.diagnostics = "\n\n".join(self._diag)

    def _add_app_diag(self, traceback_text: str, log_tail: str) -> None:
        """Keep the complete (redacted) app-startup traceback, and the end of run.log on the runtime, in diagnostics."""
        block = "[app startup] traceback from the runtime\n" + (traceback_text.strip() or "(none)")
        if log_tail.strip():
            block += "\n\n[app startup] end of run.log on the runtime\n" + log_tail.strip()
        with self._lock:
            self._diag.append(block[:12000])
            del self._diag[:-8]
            self._snap.diagnostics = "\n\n".join(self._diag)

    def _add_text_diag(self, title: str, text: str) -> None:
        """Keep complete (redacted) original error text in the diagnostics, e.g. the pip output of a failed attempt."""
        block = f"[{title}]\n{self._scrub(text).strip() or '(no output)'}"
        with self._lock:
            self._diag.append(block[:12000])
            del self._diag[:-12]
            self._snap.diagnostics = "\n\n".join(self._diag)

    def _wait_retry(self, step: str, info: ErrorInfo, attempt: int, limit: int) -> None:
        """Announce and perform the wait before an automatic retry (the caller already checked may_retry)."""
        delay = retry_delay(attempt, self.retry_delay)
        line = (f"{step}: transient failure [{info.label()}], retry {attempt}/{limit - 1} in {delay:.0f}s. "
                f"Deciding line: {truncate(self._scrub(info.technical), 200)}")
        self._log(line)
        with self._lock:
            self._snap.retries += 1
            self._snap.recovery_log.append(line)
        self._sleep(delay)
        self._checkpoint()

    def _last_exception(self, res: CliResult) -> str:
        lines = exception_lines(res.combined).splitlines()
        return truncate(self._scrub(lines[-1]), 300) if lines else ""

    def _exec_checked(self, session: str, code: str, *, timeout: int, step: str, expect: Callable[[Dict[str, Any]], bool],
                      show_output: bool = True, retry_on_mismatch: bool = False,
                      live: bool = False) -> Tuple[CliResult, Dict[str, Any]]:
        """Run `code` on the runtime until expect(markers) holds. Only TRANSPORT failures (non-zero exit with a kernel/
        WebSocket error or an unrecognised error) are retried, a bounded number of times with growing delay. A script that
        ran (exit 0) or reported its own failure is returned at once, so dependency and app errors are never retried.

        retry_on_mismatch also retries when the script ran but its own verification disagrees with what was sent. Only the
        file transfer uses it: rewriting the same files is idempotent, so a truncated/landed-imperfect write is worth one
        more attempt, while a pip or app failure is never replayed."""
        res: CliResult
        for attempt in range(1, self.exec_attempts + 1):
            self._checkpoint()
            if live:  # long call: show its output while it runs (only the dependency install uses this)
                with self._live_output(step) as shown:
                    res = self.cli.exec_code(session, code, timeout=timeout)
                self._record(step if attempt == 1 else f"{step} (retry {attempt - 1})", res, show_output=show_output,
                             streamed=shown[0] > 0)
            else:
                res = self.cli.exec_code(session, code, timeout=timeout)
                self._record(step if attempt == 1 else f"{step} (retry {attempt - 1})", res, show_output=show_output)
            self._checkpoint()
            marks = parse_markers(res.combined, self._prefix)
            if expect(marks):
                return res, marks
            if attempt == self.exec_attempts:
                return res, marks
            if res.ok:
                if not retry_on_mismatch:
                    return res, marks  # the script ran and reported its own result: replaying it would not help
                reason = "the files on the runtime did not match what was sent"
            else:
                kind, _ = classify_cli_failure(res, self._auth_markers)
                if kind not in _TRANSIENT_KINDS:
                    return res, marks
                reason = f"transient {kind} failure (exit {res.returncode})"
            delay = self.retry_delay * attempt
            self._log(f"{step}: {reason}; retrying in {delay:.0f}s "
                      f"({attempt}/{self.exec_attempts - 1} retries). The runtime is kept while retrying.")
            with self._lock:
                self._snap.retries += 1
            self._sleep(delay)
        raise AssertionError("unreachable")  # the loop always returns

    def _exec_abort(self, step: str, res: CliResult, default_kind: str, summary: str, keep_output: bool = True) -> "_Abort":
        """Build the _Abort for a step whose expected result never arrived: real classification, exit code, and the
        original exception line, instead of a guess."""
        if res.ok:
            kind, why = default_kind, ""
        else:
            kind, why = classify_cli_failure(res, self._auth_markers)
            if kind == "cli_failed":
                kind = default_kind  # unrecognised failure: keep the step-specific kind
        if keep_output:
            self._add_diag(step, res)
        else:
            with self._lock:
                self._snap.exit_code = res.returncode
        tail = self._last_exception(res)
        parts = [f"{summary} {why}".strip(), f"[stage: {step}, exit code: {res.returncode}]"]
        if tail and keep_output:
            parts.append(f"Last error: {tail}")
        return _Abort(kind, " ".join(parts))

    # ------------------------------------------------------------------ public API
    def start_async(self, app_code: str, requirements_text: str, gpu: Optional[str], *,
                    settings: Optional[Settings] = None, auth: Optional[Tuple[str, str]] = None) -> bool:
        """Start a launch in a background thread. Returns False if one is already running or a runtime is still up."""
        with self._lock:
            if self._busy or self._snap.state == RUNNING:
                return False
            self._busy = True
            self._cancel.clear()
            self.cli.clear_interrupt()
            self._snap = LaunchSnapshot(state=PREPARING, message="Preparing…", started_at=time.time())
        self._thread = threading.Thread(target=self._run_safely, args=(app_code, requirements_text, gpu, settings, auth),
                                        daemon=True, name="m2g-colab-launch")
        self._thread.start()
        return True

    def start(self, app_code: str, requirements_text: str, gpu: Optional[str], *,
              settings: Optional[Settings] = None, auth: Optional[Tuple[str, str]] = None) -> LaunchSnapshot:
        """Blocking launch (used by tests and scripts)."""
        with self._lock:
            if self._busy or self._snap.state == RUNNING:
                self._log("A launch is already in progress or a runtime is still running; stop it first.")
                return self.snapshot()
            self._busy = True
            self._cancel.clear()
            self.cli.clear_interrupt()
            self._snap = LaunchSnapshot(state=PREPARING, message="Preparing…", started_at=time.time())
        self._run_safely(app_code, requirements_text, gpu, settings, auth)
        return self.snapshot()

    def cancel(self) -> None:
        """Ask a running launch to stop; the worker then releases the runtime."""
        self._cancel.set()
        self.cli.interrupt()
        self._log("Cancel requested.")

    def wait(self, timeout: Optional[float] = None) -> None:
        t = self._thread
        if t is not None:
            t.join(timeout)

    def stop(self) -> LaunchSnapshot:
        """Stop the runtime of a RUNNING app (a launch still in progress is cancelled instead)."""
        with self._lock:
            busy, session, state = self._busy, self._snap.session, self._snap.state
        if busy:
            self.cancel()
            return self.snapshot()
        if not session or state in (STOPPED, IDLE):
            self._log("No runtime to stop.")
            return self.snapshot()
        self.cli.clear_interrupt()
        ok, detail = self._stop_session(session)
        if ok:
            self._set(state=STOPPED, message="Runtime stopped. The public link no longer works.", share_url=None,
                      url_check="", session=None)
        else:
            self._set(message=f"Stopping failed: {detail} Run `colab --auth {self.cli.auth} stop -s {session}` or open "
                              "https://colab.research.google.com/ to end the session.")
        return self.snapshot()

    def refresh(self) -> LaunchSnapshot:
        """Check (with a real call to the runtime) that the app is still alive. Clears stale links."""
        with self._lock:
            if self._busy or self._snap.state != RUNNING or not self._snap.session:
                return self.snapshot()
            session, prefix = self._snap.session, self._prefix
        self.cli.clear_interrupt()
        res = self.cli.exec_code(session, _render(POLL_SCRIPT, DIR=self.remote_dir, PREFIX=prefix), timeout=60)
        marks = parse_markers(res.combined, prefix)
        if marks["alive"] is None:
            self._record("liveness check", res)
            self._set(state=DISCONNECTED, share_url=None, url_check="",
                      message="The runtime did not answer (session expired, reclaimed or disconnected). The link is stale "
                              "and was removed. Start a new run.")
            self._cleanup_after_loss(session)
        elif not marks["alive"]:
            detail = diagnose_output(marks["tail"]) or "The app process on the runtime is no longer running."
            self._log("App process is not running. Last log lines: " + truncate(self._scrub(marks["tail"]), 800))
            info = classify_failure("app_exited", marks["tail"], marks["tail"], "start")
            if marks["tail"].strip():
                self._add_text_diag("app stopped: end of run.log on the runtime", marks["tail"])
            self._set(state=FAILED, error_kind="app_exited", share_url=None, url_check="", failed_stage=RUNNING,
                      error_category=info.category, error_code=info.code, retryable=info.transient, recovery=info.recovery,
                      message=f"The app stopped running on the runtime: {detail} The link no longer works.")
            self._cleanup_after_loss(session)
        else:
            self._log("Liveness check: the app process is running on the runtime.")
        return self.snapshot()

    def fetch_logs(self) -> str:
        """Last lines of the app's own log on the runtime (redacted)."""
        with self._lock:
            session, prefix = self._snap.session, self._prefix
        if not session:
            return "No runtime."
        self.cli.clear_interrupt()
        res = self.cli.exec_code(session, _render(POLL_SCRIPT, DIR=self.remote_dir, PREFIX=prefix), timeout=60)
        marks = parse_markers(res.combined, prefix)
        return self._scrub(marks["tail"]) or self._scrub(truncate(res.combined, 2000))

    # ------------------------------------------------------------------ worker
    def _run_safely(self, app_code, requirements_text, gpu, settings, auth) -> None:
        success = False
        try:
            try:
                self._run(app_code, requirements_text, gpu, settings, auth)
                success = True
            except _Abort as exc:
                self._finish_failure(exc)
            except Exception as exc:  # unexpected bug: report type only; the full (redacted) traceback is kept in the diagnostics
                log.exception("Unexpected error in Colab launch")
                self._add_text_diag("internal error: full traceback", traceback.format_exc())
                self._finish_failure(_Abort("internal", f"Unexpected internal error ({type(exc).__name__}); see the console log."))
        finally:
            # Cleanup runs on success, failure, cancellation and unexpected exceptions, and can never leave the launcher stuck busy.
            try:
                leftover = self._snap.session
                if not success and leftover:
                    self._release_after_failure(leftover)
            except Exception:
                log.exception("Cleanup after a Colab launch raised")
            finally:
                try:
                    # A cancelled launch that never created a runtime used to leave the CLI's interrupt latch set, so every later
                    # CLI call from the panel (Check connection, Sign in, GPU list) answered "cancelled" until the next launch.
                    self.cli.clear_interrupt()
                except Exception:
                    log.exception("Clearing the CLI interrupt raised")
                with self._lock:
                    self._busy = False

    def _release_after_failure(self, leftover: str) -> None:
        """Release the runtime a failed or cancelled launch left behind. Never raises. If it cannot be stopped, the link password is at
        least removed from it, and the person is told exactly how to stop it by hand."""
        with self._in_stage("cleanup"):
            self.cli.clear_interrupt()
            try:
                ok, detail = self._stop_session(leftover)
            except Exception as exc:
                log.exception("Stopping runtime %s raised", leftover)
                self._add_text_diag("cleanup: full traceback while stopping the runtime", traceback.format_exc())
                ok, detail = False, f"{type(exc).__name__} while stopping it"
            if ok:
                self._set(session=None)
                self._log(f"Runtime {leftover} released after the failure.")
                return
            if self._auth_on:
                self._remove_remote_secrets(leftover)
            self._set(message=self._snap.message + f" A runtime named {leftover} may still be running ({detail}). "
                              f"Stop it with `colab --auth {self.cli.auth} stop -s {leftover}` or at "
                              "https://colab.research.google.com/.")

    def _remove_remote_secrets(self, session: str) -> bool:
        """Overwrite and delete the link-password file on the runtime. Best effort: never raises and never fails a launch."""
        with self._in_stage("cleanup"):
            try:
                res = self.cli.exec_code(session, _render(REMOVE_SECRETS_SCRIPT, DIR=self.remote_dir, PREFIX=self._prefix),
                                         timeout=60)
                marks = parse_markers(res.combined, self._prefix)
            except Exception as exc:
                self._log(f"Could not remove the link password file from the runtime ({type(exc).__name__}); it stays there with "
                          "owner-only permissions until the runtime stops.")
                return False
            if marks["secrets"] == "removed":
                self._log("Removed the link password file from the runtime (it was only needed while the app started).")
                return True
            self._log("The link password file could not be confirmed as removed from the runtime; it stays there with owner-only "
                      "permissions until the runtime stops.")
            return False

    def _finish_failure(self, exc: _Abort) -> None:
        state = CANCELLED if exc.kind == "cancelled" else FAILED
        info = self._classify_abort(exc)
        message = exc.message
        if exc.kind != "cancelled" and info.recovery and info.recovery not in message:
            message = f"{message} [{info.label()}] What to do: {info.recovery}"
        # The stage that was active when the launch ended (the state is about to become FAILED/CANCELLED, so read it first).
        with self._lock:
            active = self._snap.state
            stage = active if active in ACTIVE_STATES else self._snap.failed_stage
            retries_made = self._snap.retries
        self._log(f"{state.upper()}: {message}")
        if exc.kind != "cancelled":
            self._add_text_diag("classification", f"stage={stage or '(unknown)'} category={info.category} code={info.code} "
                                                  f"transient={info.transient} automatic_retries_allowed={info.max_retries} "
                                                  f"automatic_retries_made={retries_made}\n{info.title}\n"
                                                  f"Deciding line: {info.technical or '(none)'}")
        self._set(state=state, error_kind=exc.kind, message=message, share_url=None, url_check="", error_category=info.category,
                  error_code=info.code, retryable=info.transient, recovery=info.recovery, failed_stage=stage)

    def _classify_abort(self, exc: _Abort) -> ErrorInfo:
        stage = {"install_failed": "install", "app_failed": "start", "app_exited": "start", "start_failed": "start",
                 "transfer_failed": "transfer", "preflight_failed": "preflight"}.get(exc.kind, "runtime")
        with self._lock:
            detail = self._diag[-1] if (self._diag and exc.kind in ("app_failed", "install_failed", "app_incompatible")) else ""
        return classify_failure(exc.kind, exc.message, detail, stage)

    # ------------------------------------------------------------------ the steps
    def _run(self, app_code: str, requirements_text: str, gpu: Optional[str], settings: Optional[Settings],
             auth: Optional[Tuple[str, str]]) -> str:
        files, reqs, loader, auth_on = self._prepare(app_code, requirements_text, settings, auth)
        self._checkpoint()
        self._check_cli()
        gpu_arg = self._resolve_gpu(gpu)
        self._set(gpu_requested=gpu_arg or "CPU", auth_protected=auth_on)
        self._checkpoint()
        session = self._create(gpu_arg)
        self._checkpoint()
        self._probe_hardware(session, gpu_arg)
        self._checkpoint()
        self._install(session, reqs)
        self._checkpoint()
        with self._in_stage("preflight"):
            self._preflight(session, files, settings)  # may replace files["app.py"] with a safely repaired version
        self._checkpoint()
        self._upload(session, files)
        self._checkpoint()
        self._start_and_wait(session, auth_on)
        if auth_on:  # the app is up and has read the password: do not leave the file on the runtime's disk
            self._remove_remote_secrets(session)
        return session

    def _start_and_wait(self, session: str, auth_on: bool) -> None:
        """Start the app and wait for its URL. A start-up failure that classifies as TRANSIENT (for example a model download
        that hit a network error) restarts the app process, at most ``startup_attempts`` times in total and never past the
        launch timeout; every attempt's original traceback stays in the diagnostics. Anything else fails at once."""
        began, attempt = self._clock(), 0
        while True:
            attempt += 1
            try:
                self._start_remote(session)
                self._wait_for_url(session, auth_on)
                return
            except _Abort as exc:
                if exc.kind not in ("app_failed", "app_exited"):
                    raise
                info = self._classify_abort(exc)
                if not may_retry(info, attempt, self.startup_attempts - 1) or self._clock() - began >= self.launch_timeout:
                    raise
                self._wait_retry("start app", info, attempt, self.startup_attempts)
                self._set(model_loaded=False)

    def _prepare(self, app_code, requirements_text, settings, auth):
        self._auth_on = False
        self._set(state=PREPARING, message="Validating the generated app…")
        settings = settings or Settings()
        if not isinstance(app_code, str) or len(app_code.encode("utf-8")) > MAX_APP_BYTES:
            raise _Abort("blocked", "The generated app is missing or unreasonably large; generate it again.")
        try:
            report = validate_app_code(app_code, settings)
            reqs = validate_requirements_text(requirements_text or "")
            loader = _loader_name(app_code)
        except (ValidationError, GenerationError) as exc:
            raise _Abort("blocked", f"Refusing to send this app to Colab: {exc}")
        from .native_readiness import incomplete_loader_reason  # a placeholder loader is refused before any runtime exists
        placeholder = incomplete_loader_reason(app_code)
        if placeholder:
            raise _Abort("blocked", "Not deployed to Colab: this package is an incomplete scaffold, not a working app. " + placeholder
                         + " No Colab runtime was created.")
        if not _IDENT_RE.match(loader):
            raise _Abort("blocked", "Unexpected model loader name in the generated app.")
        if report.get("trust_remote_code"):
            raise _Abort("blocked", "This app uses trust_remote_code=True. This tool never runs repository code on Colab "
                                    "automatically; that needs a separate, reviewed design.")
        auth_on = False
        user = pw = ""
        if auth and (auth[0] or auth[1]):
            user, pw = auth[0].strip(), auth[1]
            if not (1 <= len(user) <= 64 and 8 <= len(pw) <= 128) or any(ord(c) < 32 for c in user + pw):
                raise _Abort("blocked", "Public-link password protection needs a username (1-64 chars) and a password "
                                        "(8-128 chars, no control characters).")
            # The password also appears JSON-escaped inside the payload (and in any remote traceback that echoes it), so every
            # escaping level is registered as a secret too.
            variants, cur = [pw], pw
            for _ in range(4):
                cur = json.dumps(cur)[1:-1]
                variants.append(cur)
            self._extra_secrets = list(dict.fromkeys(v for v in variants if v))
            auth_on = True
            self._auth_on = True
        self._prefix = f"__M2G_{self._nonce()}__"
        files: Dict[str, str] = {
            "app.py": app_code,
            "requirements.txt": "\n".join(reqs) + "\n",
            "m2g_launch.py": _render(LAUNCHER_SCRIPT, PREFIX=self._prefix, LOADER=loader),
        }
        if auth_on:
            files["m2g_auth.json"] = json.dumps([user, pw])
        return files, reqs, loader, auth_on

    def _check_cli(self) -> None:
        self._set(state=PREPARING, message="Checking the Colab CLI and your Google connection…")
        self._log("Launch started: checking the Colab CLI and your Google connection…")  # first line, before the slow CLI calls
        ok, msg = self.cli.identify()
        self._checkpoint()  # a user cancel is CANCELLED, not a CLI failure
        self._log(f"CLI identity: {msg}")
        if not ok:
            raise _Abort("cli_unavailable", msg)
        state, msg = self.cli.check()
        self._checkpoint()
        self._log(f"CLI check: {state}")
        if state == "unavailable":
            raise _Abort("cli_unavailable", msg)
        if state == "needs_auth":
            raise _Abort("needs_auth", msg)
        if state != "ok":
            raise _Abort("cli_failed", msg)

    def _resolve_gpu(self, gpu: Optional[str]) -> Optional[str]:
        label = (gpu or "").strip()
        if not label or label.upper().startswith("CPU") or label.lower() == "none":
            self._log("Runtime request: CPU (no GPU).")
            return None
        choices = self.cli.supported_gpus()
        name = label.upper()
        if name not in choices.names:
            raise _Abort("unsupported_gpu", f"The installed Colab CLI does not offer '{label}'. It accepts: "
                                            f"{', '.join(choices.names)}.")
        how = "verified from this CLI's --help" if choices.verified else "from the CLI's documentation (--help could not be parsed)"
        self._log(f"Runtime request: {name} GPU ({how}). This is a REQUEST; the actual hardware is verified after creation.")
        return name

    def _create(self, gpu: Optional[str]) -> str:
        name = f"m2g-{time.strftime('%m%d%H%M%S')}-{secrets.token_hex(2)}"
        self._set(state=CREATING, message=f"Requesting a Colab runtime ({gpu or 'CPU'})… this can take a few minutes.",
                  session=name)  # recorded BEFORE the call so cleanup also covers a timeout/partial creation
        res: CliResult
        for attempt in range(1, self.create_attempts + 1):
            self._checkpoint()
            self._log(f"Requesting runtime {name} ({gpu or 'CPU'}) from Colab; this can take several minutes…")
            with self._live_output("create runtime") as shown:
                res = self.cli.new_session(name, gpu)
            self._record("create runtime" if attempt == 1 else f"create runtime (retry {attempt - 1})", res,
                         streamed=shown[0] > 0)
            self._checkpoint()
            if res.ok:
                return name
            kind, _ = classify_cli_failure(res, self._auth_markers)
            if kind not in _TRANSIENT_KINDS:  # needs_auth, cli_broken, hardware_unavailable/quota: never replayed
                raise self._exec_abort("create runtime", res, "create_failed", "The Colab runtime could not be created.")
            # A failed `new` can still have assigned a runtime (HTTP 503/timeout AFTER allocation). Never ask for a second
            # one: adopt the runtime Google already made, and retry only when the listing proves none exists.
            exists = self._session_listed(name)
            if exists is True:
                with self._lock:
                    self._snap.retries += 1
                self._log(f"create runtime: Google answered with an error ({kind}) but runtime {name} exists; adopting "
                          "it instead of creating a duplicate. No GPU is leaked.")
                return name
            if exists is None:
                raise self._exec_abort("create runtime", res, "create_failed",
                                       "The Colab runtime could not be confirmed; not requesting a second one.")
            if attempt == self.create_attempts:
                raise self._exec_abort("create runtime", res, "create_failed", "The Colab runtime could not be created.")
            delay = self.retry_delay * attempt
            with self._lock:
                self._snap.retries += 1
            self._log(f"create runtime: transient {kind} failure (exit {res.returncode}); retrying in {delay:.0f}s "
                      f"({attempt}/{self.create_attempts - 1} retries). The failed request left no runtime behind.")
            self._sleep(delay)
        raise AssertionError("unreachable")  # the loop always returns

    def _session_listed(self, name: str) -> Optional[bool]:
        """Does `colab sessions` list this runtime? True/False are authoritative, None means the listing itself failed
        (unknown), in which case the caller must not create another runtime."""
        listing = self.cli.sessions()
        if not listing.ok:
            self._log(f"create runtime: could not list runtimes ({listing.error or listing.returncode}); state unknown.")
            return None
        return name in listing.combined

    def _probe_hardware(self, session: str, gpu: Optional[str]) -> None:
        self._set(state=HARDWARE, message="Verifying the real hardware on the runtime…")
        res, marks = self._exec_checked(session, _render(HW_SCRIPT, PREFIX=self._prefix), timeout=120,
                                        step="hardware probe", expect=lambda m: isinstance(m["hw"], dict),
                                        show_output=False)
        hw = marks["hw"]
        if not isinstance(hw, dict):
            # Keep the REAL classification (kernel_connection / needs_auth / ...); a hard-coded guess used to make every
            # probe failure look like an auth or unreachable-runtime problem.
            raise self._exec_abort("hardware probe", res, "runtime_unreachable",
                                   "The runtime was created but did not answer a hardware check.")
        self._set(hardware=hw)
        if gpu and not hw.get("gpu"):
            raise _Abort("no_gpu", f"You requested a {gpu} GPU but the runtime reports no GPU (nvidia-smi found none). "
                                   "Colab may not have assigned one; try again later or another accelerator.")
        if gpu:
            match = gpu_name_matches(gpu, hw.get("gpu"))
            if match is False:
                self._log(f"WARNING: requested {gpu} but the runtime reports '{hw.get('gpu')}'.")
        self._log(f"Hardware detected: {hw.get('gpu') or 'no GPU'} · RAM {hw.get('ram_gib')} GiB · "
                  f"free disk {hw.get('disk_free_gib')} GiB · Python {hw.get('python')}")

    def _install(self, session: str, reqs: List[str]) -> None:
        self._set(state=INSTALLING, message="Installing dependencies on the runtime (first run can take several minutes)…")
        self._log(f"Installing {len(reqs)} requirement(s) on the runtime (first run can take several minutes)…")
        script = _render(INSTALL_SCRIPT, PREFIX=self._prefix, REQS=json.dumps(reqs))
        attempt = 0
        while True:
            attempt += 1
            res, marks = self._exec_checked(session, script, timeout=self.install_timeout,
                                            step="install dependencies" if attempt == 1 else f"install dependencies (attempt {attempt})",
                                            expect=lambda m: m["pip"] == 0, live=True)
            if marks["pip"] == 0 or not res.ok:
                break  # installed, or a transport failure that _exec_checked has already retried within its own bound
            # pip ran and said no. Keep its full output, then retry ONLY a transient cause (network, rate limit, 5xx); an
            # unsatisfiable requirement, a missing package or anything unrecognised fails now.
            self._add_text_diag(f"install dependencies, attempt {attempt}: pip output", res.combined)
            info = classify_text(res.combined, "install")
            self._log(f"install dependencies: pip failed [{info.label()}] {info.title}")
            if not may_retry(info, attempt, self.install_attempts - 1):
                break
            self._wait_retry("install dependencies", info, attempt, self.install_attempts)
        if marks["pip"] != 0:
            hint = diagnose_output(res.combined) or ""
            detail = "pip reported a failure" if marks["pip"] else "no pip result was returned"
            if res.ok:  # pip's own answer: replaying it would not help, so keep pip's message for the user
                raise _Abort("install_failed", f"Installing dependencies on the runtime failed ({detail}). {hint} "
                                               f"{truncate(self._scrub(res.combined), 500)}".strip())
            raise self._exec_abort("install dependencies", res, "install_failed",
                                   f"Installing dependencies on the runtime failed ({detail}). {hint}")

    def _preflight(self, session: str, files: Dict[str, str], settings: Optional[Settings]) -> None:
        """Check app.py against the Gradio that is installed on THIS runtime (after the dependency install, before anything
        is transferred or started). Safe repairs are applied to files["app.py"] and re-checked; anything unresolved, or an
        unreadable runtime API, aborts the launch (the caller then releases the runtime)."""
        self._set(state=INSTALLING, message="Preflight: checking the app against the Gradio installed on the runtime…")
        self._log("Preflight: reading the Gradio API installed on the runtime…")
        app_code = files["app.py"]
        code = render_probe_runner(app_code, self._prefix + " PROBE ")
        res, marks = self._exec_checked(session, code, timeout=360, step="preflight probe",
                                        expect=lambda m: isinstance(m["probe"], dict), show_output=False)
        if not isinstance(marks["probe"], dict):
            raise self._exec_abort("preflight probe", res, "preflight_failed",
                                   "Preflight could not read the Gradio API on the runtime, so the app was not started.")
        settings = settings or Settings()
        result = run_preflight(app_code, marks["probe"], revalidate=lambda c: validate_app_code(c, settings))
        for line in result.log_lines:
            self._log(line)
        info = result.to_dict()
        info["message"] = self._scrub(info["message"])
        info["remaining"] = [self._scrub(r) for r in info["remaining"]]
        self._set(preflight=info)
        if not result.ok:
            with self._lock:
                self._diag.append(("[preflight] " + info["message"] + "\n" + "\n".join(info["remaining"]))[:12000])
                del self._diag[:-8]
                self._snap.diagnostics = "\n\n".join(self._diag)
            raise _Abort("preflight_failed", "Preflight blocked the launch: " + info["message"])
        if result.code != app_code:
            if len(result.code.encode("utf-8")) > MAX_APP_BYTES:
                raise _Abort("preflight_failed", "The repaired app is unexpectedly large; not deployed.")
            files["app.py"] = result.code

    def _upload(self, session: str, files: Dict[str, str]) -> None:
        self._set(state=UPLOADING, message="Transferring the app to the runtime…")
        try:  # the runtime gets the same loader.py + app.py slices as the local package (and the notebook); one validated source stays
            loader_py, app_py = split_app_code(files["app.py"])  # the in-memory form until here so preflight/repair see the whole program
            files = {**files, "app.py": app_py, "loader.py": loader_py}
        except GenerationError:
            pass  # a single-file app (no setup/UI markers) is sent as it is: app.py then holds its own loader
        expected = {n: hashlib.sha256(t.encode("utf-8")).hexdigest() for n, t in files.items()}
        code = _render(WRITE_SCRIPT, PAYLOAD=json.dumps(files), DIR=self.remote_dir, PREFIX=self._prefix)
        # The remote script truncates and rewrites every file and checks its own SHA-256, so the transfer is idempotent:
        # a dropped connection or a short write can be replayed without leaving a half-written app behind.
        res, marks = self._exec_checked(session, code, timeout=300, step="transfer files",
                                        expect=lambda m: m["written"] == expected, show_output=False,
                                        retry_on_mismatch=True)  # the payload may include the link password: not logged
        written = marks["written"]
        if written != expected:
            missing = sorted(set(expected) - set(written))
            wrong = sorted(n for n in set(expected) & set(written) if written[n] != expected[n])
            detail = "; ".join(p for p in (("missing: " + ", ".join(missing)) if missing else "",
                                           ("wrong checksum: " + ", ".join(wrong)) if wrong else "") if p)
            raise self._exec_abort("transfer files", res, "transfer_failed",
                                   "The files on the runtime did not match what was sent "
                                   f"({detail or 'no checksums were returned'}).")
        self._log("Files transferred and checksums verified: " + ", ".join(sorted(expected)))

    def _start_remote(self, session: str) -> None:
        self._set(state=STARTING, message="Starting the app on the runtime…")
        # One dropped kernel connection here used to destroy the whole launch (and release a GPU runtime). A transport
        # blip is retried like every other exec; only a real, classified failure ends the run.
        res, marks = self._exec_checked(session, _render(START_SCRIPT, DIR=self.remote_dir, PREFIX=self._prefix),
                                        timeout=120, step="start app", expect=lambda m: m["started"] is not None)
        if marks["started"] is None:
            raise self._exec_abort("start app", res, "start_failed", "The app process could not be started.")

    def _wait_for_url(self, session: str, auth_on: bool) -> None:
        started = self._clock()
        poll_code = _render(POLL_SCRIPT, DIR=self.remote_dir, PREFIX=self._prefix)
        misses = 0
        last_state = ""
        while True:
            self._checkpoint()
            elapsed = self._clock() - started
            if elapsed > self.launch_timeout:
                raise _Abort("launch_timeout", f"The app did not publish a public URL within {int(self.launch_timeout)} s "
                                               "(very large model or slow download). The runtime was stopped.")
            res = self.cli.exec_code(session, poll_code, timeout=90)
            self._checkpoint()
            marks = parse_markers(res.combined, self._prefix)
            if marks["alive"] is None:
                misses += 1
                self._record("poll", res)
                if misses >= self.poll_miss_limit:
                    kind, msg = classify_cli_failure(res, self._auth_markers)
                    raise _Abort("runtime_lost", f"The runtime stopped answering ({msg}) {truncate(self._scrub(res.combined), 200)}".strip())
                self._sleep(self.poll_interval)
                continue
            misses = 0
            if marks["fail"]:
                hint = diagnose_output(marks["fail"] + "\n" + marks["tail"]) or ""
                full_fail = self._scrub(marks["fail"])
                self._log("App failure on the runtime (complete traceback):\n" + full_fail)
                self._add_app_diag(full_fail, self._scrub(marks["tail"]))
                final_line = next((ln.strip() for ln in reversed(full_fail.splitlines()) if ln.strip()), "")
                raise _Abort("app_failed", f"The app failed to start on the runtime. {hint} "
                                           f"{truncate(full_fail, 400)} Final error: {truncate(final_line, 600)}".strip())
            retry = marks["share_retry"]
            progress = (f"model loaded; public link attempt {retry.get('attempt')}/{retry.get('of')} failed, retrying in "
                        f"{retry.get('wait')} s ({truncate(self._scrub(str(retry.get('reason') or '')), 160)})"
                        if marks["loaded"] and isinstance(retry, dict) else
                        "model loaded, creating public link…" if marks["loaded"] else
                        "importing app…" if marks["imported"] else "starting…")
            if progress != last_state:
                self._log(f"Remote app: {progress}",
                          stage="model loading" if marks["imported"] and not isinstance(retry, dict) else "startup")
                last_state = progress
            self._set(state=WAITING, model_loaded=bool(marks["loaded"]),
                      message=f"{progress} ({int(elapsed)} s elapsed; the first run downloads the model weights)")
            if marks["url"]:
                self._set(selftest=marks["selftest"], selftest_detail=self._scrub(marks["selftest_detail"]))
                if marks["selftest"] == "fail":  # an INFERENCE error: the app is up, but its own example raised
                    info = classify_text(marks["selftest_detail"], "inference")
                    self._log(f"Self-test failed [{info.label()}]: {info.title} {info.recovery}".strip(), stage="inference")
                    self._add_text_diag("self-test (inference) failure", marks["selftest_detail"])
                elif marks["selftest"] == "ok":
                    self._log("Self-test passed: the app's own first example ran on the runtime. Output began: "
                              + truncate(marks["selftest_detail"], 120), stage="inference")
                elif marks["selftest"] == "none":
                    self._log("Self-test: this app has no built-in example, so inference was not tested.", stage="inference")
                with self._in_stage("verification"):
                    self._verify_and_publish(marks["url"], auth_on, marks)
                return
            if not marks["alive"]:
                hint = diagnose_output(marks["tail"]) or "The app process exited without publishing a URL."
                self._log("App exited. Last log lines: " + truncate(self._scrub(marks["tail"]), 900))
                raise _Abort("app_exited", f"{hint} {truncate(self._scrub(marks['tail']), 400)}".strip())
            self._sleep(self.poll_interval)

    def _verify_and_publish(self, url: str, auth_on: bool, marks: Dict[str, Any]) -> None:
        if not marks["loaded"]:
            raise _Abort("app_failed", "A URL appeared but the model never reported as loaded; not trusting it.")
        self._set(message="Checking that the public URL answers…")
        detail = ""
        for attempt in range(max(1, self.url_retries)):
            self._checkpoint()
            ok, detail = self._verify(url, auth_on)
            if ok:
                self._log(f"Public URL verified: {detail}")
                self._set(state=RUNNING, share_url=url, url_check=detail, error_kind="",
                          message="Running. The model is loaded and the public URL answers.")
                return
            if detail.startswith("Unexpected share URL format"):
                break
            self._log(f"URL check {attempt + 1}/{self.url_retries} failed: {detail}")
            self._sleep(self.url_retry_delay)
        raise _Abort("url_unreachable", f"The app started but its public URL could not be verified: {detail} The runtime "
                                        "was stopped so nothing is left running.")

    # ------------------------------------------------------------------ cleanup
    def _stop_session(self, session: str) -> Tuple[bool, str]:
        with self._in_stage("cleanup"):
            res = self.cli.stop(session)
            self._record("stop runtime", res)
            listing = self.cli.sessions()
            still = listing.ok and session in listing.combined
            if res.ok and not still:
                return True, ""
            if not res.ok and listing.ok and not still:
                return True, ""  # stop reported an error but the session is not listed any more (already gone)
            return False, truncate(self._scrub(res.combined), 200) or "the session is still listed"

    def _cleanup_after_loss(self, session: str) -> None:
        self.cli.clear_interrupt()
        ok, detail = self._stop_session(session)
        if ok:
            self._set(session=None)
        else:
            self._set(message=self._snap.message + f" A runtime named {session} may still be running; stop it with "
                              f"`colab --auth {self.cli.auth} stop -s {session}`.")
