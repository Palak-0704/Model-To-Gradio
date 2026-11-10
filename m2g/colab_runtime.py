"""Google Colab runtime integration built on the OFFICIAL Colab API (colaboratory.googleapis.com, v1beta).

What is real here:
* OAuth 2.0 loopback + PKCE sign-in (no passwords are ever requested; tokens live in a local 0600-style file).
* ``runtimespecs.list`` decides which hardware the signed-in account may use - nothing is hard-coded.
* ``runtimes.create/get/delete`` manage the runtime; the returned ``connectionInfo`` (URL + proxy token) is used with the
  standard Jupyter REST + WebSocket API to run code and detect the real hardware.

What gates it: the Colab API is BETA and ALLOWLIST-ONLY. Until ``M2G_COLAB_API_APPROVED=1`` and an OAuth client id are
configured, the manager stays in the NOT_ENABLED state and makes no network call. See docs/COLAB_SETUP.md.

Everything that touches the network goes through an injectable transport so the logic is testable offline.
"""
from __future__ import annotations

import base64
import hashlib
import http.server
import json
import logging
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .colab_gen import _loader_name
from .config import PROJECT_ROOT, Settings, _env_bool, load_dotenv_file
from .error_classify import classify_text, may_retry, retry_delay
from .gradio_compat import gradio_names_used
from .preflight import render_probe_runner, run_preflight
from .ui_errors import scrub  # Phase 4: runtime text and console log lines are redacted like the launcher's
from .validate import ValidationError, validate_app_code, validate_requirements_text

log = logging.getLogger("m2g.colab")

ACCESS_FORM_URL = ("https://docs.google.com/forms/d/e/1FAIpQLSf6q_v7lM7Eqq3PGqMsQBB5pHiuc9XOSsjwJXdT5F2EypmBiA/viewform")
API_BASE = "https://colaboratory.googleapis.com"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
COLAB_SCOPE = "https://www.googleapis.com/auth/colaboratory"
SCOPES = (COLAB_SCOPE, "openid", "email")
PROXY_HEADER = "X-Colab-Runtime-Proxy-Token"
# Tokens are only ever sent to Google-owned hosts. (The runtime proxy host comes from the API response.)
ALLOWED_HOST_SUFFIXES = (".googleapis.com", ".googleusercontent.com", ".google.com")

NOT_ENABLED = "Google Colab API access not enabled"
NOT_CONNECTED = "Not connected"
CONNECTING = "Connecting"
CONNECTED = "Connected"
FAILED = "Failed"

DEFAULT_PREFERENCES = ("CPU", "T4 GPU", "L4 GPU", "A100 GPU")  # preferences only; availability always comes from Google

_RUNTIME_NAME_RE = re.compile(r"^runtimes/[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class ColabError(Exception):
    """Expected failure. ``kind`` is one of: not_enabled, api_access, permission, auth_expired, auth_failed, quota,
    unavailable, disconnected, not_found, timeout, network, other."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind
        self.message = message


# --------------------------------------------------------------------------------------------------------- config
@dataclass(frozen=True)
class ColabConfig:
    client_id: Optional[str] = None
    client_secret: Optional[str] = field(default=None, repr=False)
    api_approved: bool = False
    token_path: Path = PROJECT_ROOT / ".m2g" / "colab_token.json"
    login_timeout: float = 180.0
    runtime_timeout: float = 300.0

    @classmethod
    def from_env(cls) -> "ColabConfig":
        import os
        load_dotenv_file()
        return cls(
            client_id=os.environ.get("M2G_COLAB_CLIENT_ID") or None,
            client_secret=os.environ.get("M2G_COLAB_CLIENT_SECRET") or None,
            api_approved=_env_bool("M2G_COLAB_API_APPROVED", False),
        )

    def not_enabled_reason(self) -> Optional[str]:
        if not self.api_approved:
            return ("The Colab API is beta and allowlist-only. Request access with Google's form, wait for approval, "
                    "then set `M2G_COLAB_API_APPROVED=1`.")
        if not self.client_id:
            return "`M2G_COLAB_CLIENT_ID` is not set (create a Desktop OAuth client in your approved Google Cloud project)."
        return None


# ------------------------------------------------------------------------------------------------------ transport
def check_url(url: str) -> None:
    p = urllib.parse.urlsplit(url)
    host = (p.hostname or "").lower()
    if p.scheme not in ("https", "wss") or p.username or p.password or not host or \
            not any(host.endswith(s) for s in ALLOWED_HOST_SUFFIXES):
        raise ColabError("other", f"Refusing to contact non-Google host: {host or '?'}")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):  # never forward credentials to a redirect target
        return None


class UrllibTransport:
    """Tiny JSON-over-HTTPS client: Google hosts only, no redirects, bounded response size."""

    def __init__(self) -> None:
        self._opener = urllib.request.build_opener(_NoRedirect)

    def request(self, method: str, url: str, headers: Optional[Dict[str, str]] = None, json_body: Any = None,
                form: Optional[Dict[str, str]] = None, timeout: float = 30.0) -> Tuple[int, Any]:
        check_url(url)
        hdrs = dict(headers or {})
        data = None
        if json_body is not None:
            data = json.dumps(json_body).encode("utf-8")
            hdrs["Content-Type"] = "application/json"
        elif form is not None:
            data = urllib.parse.urlencode(form).encode("utf-8")
            hdrs["Content-Type"] = "application/x-www-form-urlencoded"
        req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
        try:
            with self._opener.open(req, timeout=timeout) as r:
                status, raw = r.status, r.read(2_000_000)
        except urllib.error.HTTPError as exc:
            status, raw = exc.code, exc.read(2_000_000)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ColabError("network", f"Network error talking to Google ({type(exc).__name__}). Check your connection.")
        try:
            body: Any = json.loads(raw.decode("utf-8")) if raw else {}
        except ValueError:
            body = {"_raw": raw[:200].decode("utf-8", "replace")}
        return status, body


# ---------------------------------------------------------------------------------------------- error mapping
def _error_fields(body: Any) -> Tuple[str, str, List[str]]:
    e = body.get("error") if isinstance(body, dict) else None
    if not isinstance(e, dict):
        return "", "", []
    reasons = [d.get("reason", "") for d in e.get("details", []) if isinstance(d, dict) and d.get("reason")]
    return str(e.get("status", "")), str(e.get("message", ""))[:300], reasons


def map_http_error(status: int, body: Any) -> ColabError:
    st, msg, reasons = _error_fields(body)
    blob = " ".join([st, msg, *reasons]).upper()
    if status == 401 or st == "UNAUTHENTICATED":
        return ColabError("auth_expired", "Google sign-in expired or was revoked. Click 'Sign in' / 'Connect' to sign in again.")
    if status == 403 or st == "PERMISSION_DENIED":
        if "SERVICE_DISABLED" in blob or "HAS NOT BEEN USED" in blob or "IS DISABLED" in blob:
            return ColabError("api_access", "The Colab API is not enabled for your Google Cloud project. Run "
                                            "`gcloud services enable colaboratory.googleapis.com` (only possible after Google "
                                            "has approved your project).")
        if "SCOPE" in blob or "INSUFFICIENT" in blob:
            return ColabError("permission", "Colab access was not granted to this app. Sign in again and approve the Colab permission.")
        return ColabError("permission", "Google denied access (403). Usual causes: your project is not allowlisted for the Colab API "
                                        "yet, or the account did not grant the Colab permission." + (f" Detail: {msg}" if msg else ""))
    if status == 404 or st == "NOT_FOUND":
        return ColabError("not_found", "Not found: the runtime no longer exists (it may have been reclaimed or disconnected).")
    if status == 429 or st == "RESOURCE_EXHAUSTED":
        return ColabError("quota", "Colab rate/usage limit reached. Wait a few minutes (or check your Colab compute units) and retry.")
    return ColabError("other", f"Colab API error {status}" + (f" ({st})" if st else "") + (f": {msg}" if msg else ""))


def map_operation_error(err: Any) -> ColabError:
    err = err if isinstance(err, dict) else {}
    code, msg = err.get("code"), str(err.get("message", ""))[:300]
    reasons = [d.get("reason", "") for d in err.get("details", []) if isinstance(d, dict) and d.get("reason")]
    if code == 9:
        return ColabError("unavailable", f"Google refused to create this runtime (reason: {', '.join(reasons) or 'unspecified'}). "
                                         f"{msg} Usual causes: that GPU is unavailable on your plan or right now, usage limits, or too "
                                         "many active runtimes.")
    if code == 8:
        return ColabError("quota", f"Colab quota exhausted. {msg}")
    if code in (7, 16):
        return map_http_error(403 if code == 7 else 401, {})
    if code == 6:
        return ColabError("other", "A runtime with that id already exists.")
    return ColabError("other", f"Runtime creation failed (code {code}). {msg}")


# ------------------------------------------------------------------------------------------------------- OAuth
def pkce_pair() -> Tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    return verifier, challenge


class TokenStore:
    def __init__(self, path: Path):
        self.path = Path(path)

    def load(self) -> Dict[str, Any]:
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def save(self, data: Dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data), encoding="utf-8")
        try:
            self.path.chmod(0o600)  # best effort; a no-op for most purposes on Windows
        except OSError:
            pass

    def clear(self) -> None:
        try:
            self.path.unlink()
        except OSError:
            pass


class _CallbackServer:
    """One-shot loopback HTTP server that receives the OAuth redirect."""

    def __init__(self) -> None:
        outer = self
        self.result: Optional[Dict[str, str]] = None

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                if "code" in q or "error" in q:
                    outer.result = {k: v[0] for k, v in q.items()}
                    body, code = b"<html><body>Sign-in finished. You can close this tab and return to Model to Gradio.</body></html>", 200
                else:  # favicon etc.
                    body, code = b"waiting", 404
                self.send_response(code)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):  # silence request logging (the URL contains the auth code)
                pass

        self._srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        self._srv.timeout = 0.5
        self.port = self._srv.server_address[1]

    def wait(self, timeout: float) -> Optional[Dict[str, str]]:
        deadline = time.monotonic() + timeout
        while self.result is None and time.monotonic() < deadline:
            self._srv.handle_request()
        return self.result

    def close(self) -> None:
        self._srv.server_close()


class GoogleAuth:
    def __init__(self, cfg: ColabConfig, transport: Any, store: Optional[TokenStore] = None,
                 clock: Callable[[], float] = time.time, open_browser: Callable[[str], Any] = webbrowser.open):
        self.cfg, self._t, self._clock, self._open = cfg, transport, clock, open_browser
        self.store = store or TokenStore(cfg.token_path)

    def signed_in(self) -> bool:
        d = self.store.load()
        return bool(d.get("refresh_token") or (d.get("access_token") and d.get("expires_at", 0) > self._clock() + 60))

    def email(self) -> Optional[str]:
        return self.store.load().get("email") or None

    def logout(self) -> None:
        self.store.clear()

    def _client_form(self) -> Dict[str, str]:
        f = {"client_id": self.cfg.client_id or ""}
        if self.cfg.client_secret:
            f["client_secret"] = self.cfg.client_secret
        return f

    def login(self) -> str:
        """Interactive sign-in in the user's own browser. Returns the authenticated email."""
        if not self.cfg.client_id:
            raise ColabError("not_enabled", "M2G_COLAB_CLIENT_ID is not set.")
        verifier, challenge = pkce_pair()
        state = secrets.token_urlsafe(24)
        server = _CallbackServer()
        try:
            redirect = f"http://127.0.0.1:{server.port}"
            url = AUTH_URL + "?" + urllib.parse.urlencode({
                "client_id": self.cfg.client_id, "redirect_uri": redirect, "response_type": "code",
                "scope": " ".join(SCOPES), "state": state, "code_challenge": challenge,
                "code_challenge_method": "S256", "access_type": "offline", "prompt": "consent"})
            log.warning("Open this URL to sign in to Google if a browser tab did not open: %s", url)
            self._open(url)
            result = server.wait(self.cfg.login_timeout)
        finally:
            server.close()
        if not result:
            raise ColabError("auth_failed", "Sign-in timed out. Click Connect and finish the Google sign-in within 3 minutes.")
        if "error" in result:
            kind = "permission" if result["error"] == "access_denied" else "auth_failed"
            raise ColabError(kind, f"Google sign-in was not completed ({result['error']}).")
        if not secrets.compare_digest(result.get("state", ""), state):
            raise ColabError("auth_failed", "Sign-in response did not match this request (state mismatch); try again.")
        form = {**self._client_form(), "code": result["code"], "code_verifier": verifier,
                "redirect_uri": redirect, "grant_type": "authorization_code"}
        status, body = self._t.request("POST", TOKEN_URL, form=form)
        if status != 200 or "access_token" not in body:
            raise ColabError("auth_failed", f"Google rejected the sign-in ({body.get('error', status) if isinstance(body, dict) else status}).")
        if COLAB_SCOPE not in str(body.get("scope", "")).split():
            raise ColabError("permission", "The Colab permission was not granted. Sign in again and tick the Colab access box.")
        status, info = self._t.request("GET", USERINFO_URL, headers={"Authorization": "Bearer " + body["access_token"]})
        email = info.get("email") if status == 200 and isinstance(info, dict) else None
        if not email:
            raise ColabError("auth_failed", "Signed in, but Google did not return the account email.")
        self.store.save({"access_token": body["access_token"], "refresh_token": body.get("refresh_token", ""),
                         "expires_at": self._clock() + float(body.get("expires_in", 3600)), "email": email})
        return email

    def access_token(self) -> str:
        d = self.store.load()
        if not d.get("access_token") and not d.get("refresh_token"):
            raise ColabError("auth_expired", "Not signed in to Google.")
        if d.get("access_token") and d.get("expires_at", 0) - self._clock() > 60:
            return d["access_token"]
        if not d.get("refresh_token"):
            raise ColabError("auth_expired", "Google sign-in expired. Sign in again.")
        status, body = self._t.request("POST", TOKEN_URL, form={**self._client_form(), "refresh_token": d["refresh_token"],
                                                                "grant_type": "refresh_token"})
        if status != 200 or "access_token" not in body:
            err = body.get("error") if isinstance(body, dict) else None
            if err == "invalid_grant" or status in (400, 401):
                raise ColabError("auth_expired", "Google sign-in expired or was revoked. Sign in again.")
            raise ColabError("auth_failed", f"Could not refresh the Google sign-in ({err or status}).")
        d["access_token"] = body["access_token"]
        d["expires_at"] = self._clock() + float(body.get("expires_in", 3600))
        self.store.save(d)
        return d["access_token"]


# ----------------------------------------------------------------------------------------------------- API client
@dataclass(frozen=True)
class RuntimeSpec:
    variant: str
    accelerator: str
    shape: str
    eligible: bool

    @property
    def is_gpu(self) -> bool:
        return self.variant == "VARIANT_GPU"

    @property
    def label(self) -> str:
        acc = self.accelerator
        base = "CPU" if acc.upper() == "NONE" or self.variant == "VARIANT_CPU" else \
            (f"{acc} TPU" if self.variant == "VARIANT_TPU" else f"{acc} GPU")
        return base + (" (high-RAM)" if self.shape == "SHAPE_HIGHMEM" else "")


@dataclass
class Runtime:
    name: str
    url: str
    token: str = field(repr=False)
    expire_time: str = ""


class ColabApi:
    def __init__(self, auth: Any, transport: Any, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic, timeout: float = 300.0, poll: float = 2.0):
        self._auth, self._t, self._sleep, self._clock, self._timeout, self._poll = auth, transport, sleep, clock, timeout, poll

    def _call(self, method: str, path: str, json_body: Any = None, params: Optional[Dict[str, str]] = None) -> Any:
        url = API_BASE + path + ("?" + urllib.parse.urlencode(params) if params else "")
        status, body = self._t.request(method, url, headers={"Authorization": "Bearer " + self._auth.access_token()},
                                       json_body=json_body)
        if status >= 400:
            raise map_http_error(status, body)
        return body

    @staticmethod
    def _check_name(name: str) -> str:
        if not _RUNTIME_NAME_RE.match(name or ""):
            raise ColabError("other", "Unexpected runtime name from the Colab API.")
        return name

    def list_specs(self) -> List[RuntimeSpec]:
        out = []
        for s in self._call("GET", "/v1beta/runtimespecs").get("runtimeSpecs", []):
            k = s.get("key", {})
            out.append(RuntimeSpec(k.get("variant", ""), k.get("accelerator", ""), k.get("shape", ""), bool(s.get("eligible"))))
        return out

    def subscription_tier(self) -> str:
        return str(self._call("GET", "/v1beta/subscription").get("tier", ""))

    def get_runtime(self, name: str) -> Runtime:
        body = self._call("GET", "/v1beta/" + self._check_name(name))
        ci = body.get("connectionInfo") or {}
        if not ci.get("url") or not ci.get("token"):
            raise ColabError("other", "The Colab API returned a runtime without connection info.")
        return Runtime(name=body.get("name", name), url=ci["url"], token=ci["token"], expire_time=ci.get("expireTime", ""))

    def list_runtimes(self) -> List[str]:
        return [r.get("name", "") for r in self._call("GET", "/v1beta/runtimes").get("runtimes", [])]

    def delete_runtime(self, name: str) -> None:
        try:
            self._call("DELETE", "/v1beta/" + self._check_name(name))
        except ColabError as exc:
            if exc.kind != "not_found":  # already gone is fine
                raise

    def create_runtime(self, spec: RuntimeSpec) -> Runtime:
        rid = "m2g-" + uuid.uuid4().hex[:12]
        op = self._call("POST", "/v1beta/runtimes", params={"runtimeId": rid, "requestId": str(uuid.uuid4())},
                        json_body={"runtimeSpec": {"variant": spec.variant, "accelerator": spec.accelerator, "shape": spec.shape}})
        deadline = self._clock() + self._timeout
        while not op.get("done"):
            if self._clock() > deadline:
                raise ColabError("timeout", "Timed out waiting for Colab to assign the runtime.")
            self._sleep(self._poll)
            op = self._call("GET", "/v1/" + str(op.get("name", "")).lstrip("/"))
        if op.get("error"):
            raise map_operation_error(op["error"])
        name = (op.get("response") or {}).get("name") or (op.get("metadata") or {}).get("runtime") or f"runtimes/{rid}"
        return self.get_runtime(name)


# ----------------------------------------------------------------------------------------------- Jupyter client
@dataclass
class ExecResult:
    ok: bool
    stdout: str = ""
    result: str = ""
    error: str = ""


def _default_ws_connect(url: str, headers: Dict[str, str]):
    try:
        from websockets.sync.client import connect  # installed with Gradio (gradio_client)
    except ImportError:
        raise ColabError("other", "The `websockets` package (>=12) is required: pip install websockets")
    return connect(url, additional_headers=headers, open_timeout=30, max_size=16 * 2 ** 20)


class JupyterClient:
    """Runs code on a Colab runtime through the standard Jupyter server API."""

    def __init__(self, url: str, token: str, transport: Any, ws_connect: Optional[Callable] = None):
        check_url(url)
        self._t, self._ws_connect = transport, ws_connect or _default_ws_connect
        self._token = token
        sp = urllib.parse.urlsplit(url)
        self._base = f"{sp.scheme}://{sp.netloc}{sp.path.rstrip('/')}"
        self._query = sp.query
        self._kernel: Optional[str] = None
        self._session = uuid.uuid4().hex

    def update_token(self, token: str) -> None:
        self._token = token

    def _u(self, path: str, scheme: Optional[str] = None) -> str:
        base = self._base if scheme is None else scheme + "://" + self._base.split("://", 1)[1]
        return base + path + (("?" + self._query) if self._query else "")

    def _headers(self) -> Dict[str, str]:
        return {PROXY_HEADER: self._token}

    def _ensure_kernel(self) -> str:
        if self._kernel:
            return self._kernel
        status, body = self._t.request("POST", self._u("/api/kernels"), headers=self._headers(), json_body={"name": "python3"})
        if status in (401, 403):
            raise ColabError("auth_expired", "The runtime rejected the connection token.")
        if status == 404:
            raise ColabError("disconnected", "The runtime is gone (404).")
        if status >= 400 or not isinstance(body, dict) or not body.get("id"):
            raise ColabError("other", f"Could not start a kernel on the runtime (HTTP {status}).")
        self._kernel = str(body["id"])
        return self._kernel

    def close(self) -> bool:
        """Phase 4: delete the ONE kernel this client started, so it does not outlive the connection. Idempotent and best effort: it
        never raises, never touches any other kernel or file, and never logs the proxy token or the runtime URL. True means nothing is
        left to close (no kernel was started, it was closed already, or the runtime says it is gone: 404). False means the runtime
        refused or could not be reached; the kernel id is kept so a later call can try again (releasing the runtime ends it anyway)."""
        kid = self._kernel
        if not kid:
            return True
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", kid):  # the id came from the runtime; never put anything else into a URL
            log.warning("Not closing a runtime kernel whose id has an unexpected shape.")
            return False
        try:
            status, _ = self._t.request("DELETE", self._u(f"/api/kernels/{kid}"), headers=self._headers(), timeout=15.0)
        except Exception as exc:
            log.warning("Closing the runtime kernel failed (%s); releasing the runtime will end it.", type(exc).__name__)
            return False
        if status in (200, 202, 204, 404):
            self._kernel = None
            return True
        log.warning("The runtime refused to close its kernel (HTTP %s); releasing the runtime will end it.", status)
        return False

    def execute(self, code: str, timeout: float = 120.0) -> ExecResult:
        kid = self._ensure_kernel()
        try:
            ws = self._ws_connect(self._u(f"/api/kernels/{kid}/channels", scheme="wss"), self._headers())
        except ColabError:
            raise
        except Exception as exc:
            raise ColabError("disconnected", f"Could not open a connection to the runtime ({type(exc).__name__}).")
        msg_id = uuid.uuid4().hex
        msg = {"header": {"msg_id": msg_id, "username": "m2g", "session": self._session, "msg_type": "execute_request",
                          "version": "5.3"}, "parent_header": {}, "metadata": {}, "channel": "shell", "buffers": [],
               "content": {"code": code, "silent": False, "store_history": False, "user_expressions": {},
                           "allow_stdin": False, "stop_on_error": True}}
        out, res, err = [], "", ""
        deadline = time.monotonic() + timeout
        try:
            ws.send(json.dumps(msg))
            while True:
                left = deadline - time.monotonic()
                if left <= 0:
                    raise ColabError("timeout", f"The runtime did not finish within {int(timeout)} s.")
                try:
                    raw = ws.recv(timeout=min(left, 30.0))
                except TimeoutError:
                    continue
                m = json.loads(raw)
                if (m.get("parent_header") or {}).get("msg_id") != msg_id:
                    continue
                t, c = m.get("msg_type") or (m.get("header") or {}).get("msg_type"), m.get("content", {})
                if t == "stream":
                    out.append(str(c.get("text", "")))
                elif t == "execute_result":
                    res = str((c.get("data") or {}).get("text/plain", ""))
                elif t == "error":
                    err = f"{c.get('ename', 'Error')}: {c.get('evalue', '')}"
                elif t == "status" and c.get("execution_state") == "idle":
                    break
        except ColabError:
            raise
        except Exception as exc:
            raise ColabError("disconnected", f"Lost the connection to the runtime ({type(exc).__name__}).")
        finally:
            try:
                ws.close()
            except Exception:
                pass
        return ExecResult(ok=not err, stdout="".join(out), result=res, error=err)


_HW_CODE = '''import json, os, platform, subprocess
hw = {"python": platform.python_version(), "gpu": None, "gpu_mem_mib": None, "ram_gib": None}
try:
    lines = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                           capture_output=True, text=True, timeout=20).stdout.strip().splitlines()
    if lines:
        name, mem = [p.strip() for p in lines[0].split(",", 1)]
        hw["gpu"], hw["gpu_mem_mib"] = name, int(float(mem))
except Exception:
    pass
try:
    hw["ram_gib"] = round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**30, 1)
except Exception:
    pass
print("M2G_HW:" + json.dumps(hw))
'''


def _marker(stdout: str, marker: str) -> Optional[str]:
    for line in stdout.splitlines():
        if line.startswith(marker):
            return line[len(marker):]
    return None


def detect_hardware(jc: JupyterClient) -> Dict[str, Any]:
    r = jc.execute(_HW_CODE, timeout=60)
    payload = _marker(r.stdout, "M2G_HW:")
    if not r.ok or payload is None:
        raise ColabError("other", "The runtime answered, but hardware detection failed" + (f": {r.error}" if r.error else "."))
    return json.loads(payload)


def build_write_app_code(app_code: str) -> str:
    return f"open('app.py', 'w', encoding='utf-8').write({app_code!r})\nprint('M2G_WROTE')\n"


def build_install_code(reqs: Sequence[str]) -> str:
    return ("import subprocess, sys\n"
            f"_r = subprocess.run([sys.executable, '-m', 'pip', 'install', '-q'] + {list(reqs)!r}, capture_output=True, text=True)\n"
            "print(_r.stdout[-1500:]); print(_r.stderr[-1500:])\n"
            "assert _r.returncode == 0, 'pip install failed'\n")


def build_load_code(loader: str, env: Dict[str, str]) -> str:
    return ("import importlib, json, os, sys\nsys.path.insert(0, os.getcwd())\n"
            f"os.environ.update(json.loads({json.dumps(env)!r}))\n"
            "sys.modules.pop('app', None)\napp = importlib.import_module('app')\n"
            f"getattr(app, {loader!r})()\nprint('M2G_LOADED')\n")


def build_predict_code(args: List[Any]) -> str:
    return (f"import json\n_args = json.loads({json.dumps(args)!r})\n_out = app.predict(*_args)\n"
            "print('M2G_RESULT:' + json.dumps(_out, default=str))\n")


# --------------------------------------------------------------------------------------------------------- manager
class ColabManager:
    """Framework-free state machine used by the web UI (and tests)."""

    def __init__(self, cfg: Optional[ColabConfig] = None, transport: Any = None, ws_connect: Optional[Callable] = None,
                 auth: Any = None, api: Any = None):
        self.cfg = cfg or ColabConfig.from_env()
        self._t = transport or UrllibTransport()
        self._ws = ws_connect
        self.auth = auth or GoogleAuth(self.cfg, self._t)
        self.api = api or ColabApi(self.auth, self._t, timeout=self.cfg.runtime_timeout)
        self._lock = threading.RLock()
        self.runtime: Optional[Runtime] = None
        self.jupyter: Optional[JupyterClient] = None
        self.hardware: Optional[Dict[str, Any]] = None
        self.specs: List[RuntimeSpec] = []
        self.error_kind = ""
        self.preflight: Optional[Dict[str, Any]] = None  # verdict of the last runtime preflight (run_predict path)
        # Phase 3: bounded automatic retry of TRANSIENT failures of the install and model-load steps (see m2g.error_classify)
        self.install_attempts, self.retry_delay, self.retry_sleep = 3, 5.0, time.sleep
        self.last_error: Optional[Dict[str, Any]] = None  # classification of the last failed run_predict step
        self.kernel_cleanup: Optional[str] = None  # Phase 4: outcome of the last kernel close: 'closed' | 'failed' | None (no kernel)
        self._loaded_hash: Optional[str] = None
        reason = self.cfg.not_enabled_reason()
        self.state = NOT_ENABLED if reason else NOT_CONNECTED
        self.message = reason or ""

    # -- helpers
    def _close_kernel(self, jc: Optional[JupyterClient]) -> None:
        """Best-effort close of the kernel this manager started, BEFORE the runtime is released. Never raises; a failure is recorded in
        ``kernel_cleanup`` and never blocks the runtime release (deleting the runtime ends the kernel anyway)."""
        if jc is None:
            self.kernel_cleanup = None
            return
        try:
            self.kernel_cleanup = "closed" if jc.close() else "failed"
        except Exception:
            self.kernel_cleanup = "failed"

    def _fail(self, exc: ColabError, release: bool = True) -> None:
        self.error_kind, self.state, self.message = exc.kind, FAILED, exc.message
        jc = self.jupyter
        rt, self.runtime, self.jupyter, self.hardware, self._loaded_hash = self.runtime, None, None, None, None
        if jc is not None and release and exc.kind != "disconnected":  # a runtime that is already gone has no kernel to close
            self._close_kernel(jc)
        if rt and release and exc.kind != "disconnected":
            try:
                self.api.delete_runtime(rt.name)
            except Exception:
                self.message += " A runtime may still be running - check https://colab.research.google.com for active sessions."

    def _guard(self, exc: Exception) -> None:
        if isinstance(exc, ColabError):
            self._fail(exc)
        else:
            log.exception("Unexpected Colab integration error")
            self._fail(ColabError("other", f"Unexpected error ({type(exc).__name__}); see the console log."))

    def _ensure_signed_in(self) -> None:
        if not self.auth.signed_in():
            self.message = "Waiting for Google sign-in in your browser…"
            self.auth.login()

    def _resolve_spec(self, preference: str) -> RuntimeSpec:
        self.specs = self.api.list_specs()
        for s in self.specs:
            if s.label.lower() == (preference or "").strip().lower():
                if not s.eligible:
                    raise ColabError("unavailable", f"{s.label} is not available to this Google account/plan right now "
                                                    "(Colab reports it as ineligible).")
                return s
        offered = ", ".join(sorted({s.label for s in self.specs if s.eligible})) or "none"
        raise ColabError("unavailable", f"'{preference}' is not offered by Colab for this account. Offered now: {offered}.")

    # -- public API
    def hardware_choices(self) -> List[str]:
        return [s.label for s in self.specs if s.eligible]

    def refresh_hardware(self) -> List[str]:
        with self._lock:
            if self.state == NOT_ENABLED:
                return []
            try:
                self._ensure_signed_in()
                self.specs = self.api.list_specs()
                if self.state != CONNECTED:
                    self.state, self.message, self.error_kind = NOT_CONNECTED, "Hardware options loaded from Google.", ""
            except Exception as exc:
                if self.state == CONNECTED:
                    self.message = getattr(exc, "message", "Could not refresh hardware options.")
                else:
                    self._guard(exc)
            return self.hardware_choices()

    def connect(self, preference: str) -> None:
        with self._lock:
            if self.state == NOT_ENABLED:
                return
            if self.state == CONNECTED:
                self.message = "Already connected. Disconnect first to change hardware."
                return
            self.state, self.message, self.error_kind = CONNECTING, "Signing in…", ""
            try:
                self._ensure_signed_in()
                spec = self._resolve_spec(preference)
                self.message = f"Requesting a {spec.label} runtime…"
                self.runtime = self.api.create_runtime(spec)
                self.jupyter = JupyterClient(self.runtime.url, self.runtime.token, self._t, self._ws)
                hw = detect_hardware(self.jupyter)
                if spec.is_gpu and not hw.get("gpu"):
                    raise ColabError("unavailable", f"Colab assigned a runtime without a GPU although {spec.label} was requested.")
                self.hardware, self.state, self.message = hw, CONNECTED, "Runtime ready."
            except Exception as exc:
                self._guard(exc)

    def test(self) -> None:
        with self._lock:
            if self.state != CONNECTED or not self.runtime or not self.jupyter:
                self.message = "Not connected - nothing to test."
                return
            try:
                self.runtime = self.api.get_runtime(self.runtime.name)  # also refreshes the proxy token
                self.jupyter.update_token(self.runtime.token)
                self.hardware = detect_hardware(self.jupyter)
                self.message = "Test passed: the runtime answered and hardware was re-detected."
            except Exception as exc:
                self._guard(exc)

    def disconnect(self) -> None:
        with self._lock:
            if self.state == NOT_ENABLED:
                return
            rt, jc = self.runtime, self.jupyter
            self.runtime = self.jupyter = self.hardware = self._loaded_hash = None
            self.preflight = None
            self.error_kind, self.state, self.message = "", NOT_CONNECTED, "Disconnected."
            self._close_kernel(jc)
            if rt:
                try:
                    self.api.delete_runtime(rt.name)
                except Exception as exc:
                    self.message = ("Disconnected locally, but releasing the runtime failed" +
                                    (f": {exc.message}" if isinstance(exc, ColabError) else "") +
                                    ". Check https://colab.research.google.com for active sessions.")

    def _execute_with_recovery(self, code: str, timeout: float, stage: str, retries_allowed: int = 0) -> ExecResult:
        """jupyter.execute with a bounded retry. A failed step is replayed only when its error classifies as transient, at most
        ``retries_allowed`` times (and never above the classifier's own bound); permanent and unrecognised errors return at
        once. The result of the LAST attempt is returned unchanged, so the original error text is never lost."""
        attempt = 0
        while True:
            attempt += 1
            r = self.jupyter.execute(code, timeout=timeout)
            if r.ok:
                return r
            info = classify_text(f"{r.stdout}\n{r.error}", stage)
            if not may_retry(info, attempt, retries_allowed):
                return r
            log.warning("%s: transient failure [%s], retry %d/%d: %s", stage, info.label(), attempt, retries_allowed,
                        scrub(info.technical))
            self.retry_sleep(retry_delay(attempt, self.retry_delay))

    def _runtime_error(self, r: ExecResult, stage: str, fallback: str = "") -> str:
        """User-facing text for a failed step: the ORIGINAL error first, then its classification and next step."""
        original = scrub(r.error or fallback)
        info = classify_text(f"{r.stdout}\n{r.error}", stage)
        self.last_error = {"stage": stage, "category": info.category, "code": info.code, "transient": info.transient,
                           "original": original, "stdout_tail": scrub((r.stdout or "")[-2000:])}
        return f"Error on the runtime: {original} {info.user_text()}".strip()

    def _preflight(self, app_code: str, settings: Settings) -> Tuple[Optional[str], str]:
        """Check app_code against the Gradio installed on the connected runtime. Returns (code_to_deploy, "") on success
        (repaired if a safe repair was needed) or (None, error_text) when the app must not be started."""
        r = self.jupyter.execute(render_probe_runner(app_code, "M2G_PREFLIGHT:"), timeout=300)
        payload = _marker(r.stdout, "M2G_PREFLIGHT:")
        try:
            probe = json.loads(payload) if payload else None
        except ValueError:
            probe = None
        if not isinstance(probe, dict):
            probe = {"rc": -1, "out": "", "err": r.error or "the probe printed no result"}
        result = run_preflight(app_code, probe, revalidate=lambda c: validate_app_code(c, settings))
        self.preflight = result.to_dict()
        for line in result.log_lines:
            log.info("%s", scrub(line))
        if not result.ok:
            info = classify_text(result.message, "preflight")  # same cause label as everywhere else (e.g. a removed pipeline task)
            self.last_error = {"stage": "preflight", "category": info.category, "code": info.code, "transient": info.transient,
                               "original": scrub(result.message), "stdout_tail": ""}
            extra = f" {info.user_text()}" if info.code != "unrecognized" else ""
            return None, f"Error: preflight blocked the app. {result.message}{extra}"
        return result.code, ""

    def run_predict(self, app_code: str, requirements_text: str, args_json: str,
                    settings: Optional[Settings] = None) -> str:
        """Run the generated app's predict() on the connected runtime. Returns display text (never raises)."""
        with self._lock:
            if self.state != CONNECTED or not self.jupyter:
                return "Error: not connected to a Colab runtime."
            try:
                args = json.loads(args_json or "[]")
                if not isinstance(args, list):
                    return 'Error: inputs must be a JSON list, e.g. ["some text"].'
                if not app_code.strip():
                    return "Error: generate an app first."
                settings = settings or Settings()
                try:
                    validate_app_code(app_code, settings)
                    reqs = validate_requirements_text(requirements_text)
                except ValidationError as exc:
                    return f"Error: refusing to send unvalidated code to the runtime ({exc})."
                digest = hashlib.sha256(app_code.encode("utf-8")).hexdigest()
                if digest != self._loaded_hash:
                    r = self._execute_with_recovery(build_install_code(reqs), 900, "install", self.install_attempts - 1)
                    if not r.ok:
                        return self._runtime_error(r, "install")
                    deploy_code = app_code
                    if gradio_names_used(app_code):  # nothing to check against Gradio otherwise
                        deploy_code, problem = self._preflight(app_code, settings)
                        if deploy_code is None:
                            return problem
                    for code, tmo, stage, extra in ((build_write_app_code(deploy_code), 60, "transfer", 0),
                                                    (build_load_code(_loader_name(app_code), {}), 900, "load",
                                                     self.install_attempts - 1)):
                        r = self._execute_with_recovery(code, tmo, stage, extra)
                        if not r.ok:
                            return self._runtime_error(r, stage)
                    self._loaded_hash = digest
                r = self.jupyter.execute(build_predict_code(args), timeout=300)
                payload = _marker(r.stdout, "M2G_RESULT:")
                if not r.ok or payload is None:
                    return self._runtime_error(r, "inference", "no result returned")
                return payload
            except json.JSONDecodeError:
                return 'Error: inputs are not valid JSON, e.g. ["some text"].'
            except Exception as exc:
                self._guard(exc)
                return f"Error: {self.message}"

    def status_markdown(self) -> str:
        with self._lock:
            lines = [f"### Google Colab: **{self.state}**"]
            if self.state == NOT_ENABLED:
                lines += ["", self.message, "", f"1. [Request API access (official Google form)]({ACCESS_FORM_URL})",
                          "2. After Google approves your project, follow `docs/COLAB_SETUP.md`.",
                          "", "_Nothing is simulated: Connect stays disabled until access is configured._"]
                return "\n".join(lines)
            email = self.auth.email() if self.auth else None
            lines.append(f"**Signed in as:** {email}" if email else "**Signed in:** no")
            if self.message:
                lines += ["", self.message]
            if self.state == CONNECTED and self.runtime:
                hw = self.hardware or {}
                gpu = f"{hw['gpu']} ({round((hw.get('gpu_mem_mib') or 0) / 1024, 1)} GiB)" if hw.get("gpu") else "no GPU (CPU runtime)"
                lines += ["", f"**Runtime:** `{self.runtime.name}` · **Detected hardware:** {gpu} · RAM {hw.get('ram_gib')} GiB · "
                              f"Python {hw.get('python')}"]
            inel = sorted({s.label for s in self.specs if not s.eligible})
            if self.preflight:
                pf = self.preflight
                lines += ["", f"**Gradio preflight:** {pf.get('status')} (runtime Gradio {pf.get('gradio_version')})"]
            if inel:
                lines += ["", "Not available to your account: " + ", ".join(inel)]
            return "\n".join(lines)
