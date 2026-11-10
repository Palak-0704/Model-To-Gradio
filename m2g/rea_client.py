"""REA (reverse-engineering MCP server) integration: a small stdio MCP client plus the model-inspection stage of the pipeline.

Pipeline position:  Hugging Face URL -> metadata inspection -> **REA inspection (this module)** -> analysis -> OpenCode generation.

What REA is used for here, and which tools
  The REA 6.8.0 server advertises 229 tools (schemas in ``diagnostics/rea_tools.md``). Almost all of them need a local binary,
  a device, a debugger or a browser session. The stage drives two REQUIRED tools by hand and selects every other tool through a
  capability policy (``m2g.rea_capabilities``) that reads the schemas the server actually advertises:

  * ``binary_session``          no arguments; reports server identity/status without starting any analysis (health check).
  * ``inspect_model_artifact``  ``path`` (a LOCAL file, never a URL), ``hash`` ("header" = declarations only, never "full"),
                                ``detail``. Reads GGUF/safetensors declarations without loading models, executing code,
                                using the network or deserializing pickle.
  * ``inspect_analysis_view``   pages REA's RETAINED Evidence from the model inspection just performed (for example the
                                ``metadata`` collection of a GGUF file: ``general.architecture`` and friends). It is called only
                                when the model-inspection result advertises a retained ``parent_evidence_id``; it reads no bytes
                                from disk and reopens nothing.
  * capability-selected tools    the analysis raises evidence gaps (retained metadata, byte entropy, residual unknowns, the
                                Evidence bundle); the planner resolves each to a tool the server advertises that the policy permits
                                (never destructive, never open-world, and only with arguments this platform can supply) and calls it
                                with arguments filtered to that tool's own schema. These calls are OPTIONAL: a failure is recorded
                                and noted but never changes the stage's verdict.

How a header-only inspection works (sparse files)
  ``inspect_model_artifact`` validates the tensor offsets declared in the header against the file's LOGICAL size, so a file that
  holds only the header is rejected ("offsets outside the file"). The platform therefore:

    1. fetches ONLY the weight file's declarations with HTTP range requests (safetensors: 8 length bytes then the JSON header;
       GGUF: a bounded, doubling window from the start until the tensor table is complete, then trimmed to the exact header end
       using offsets the local parser reported). The ``Content-Range`` of every response carries the real size of the remote
       file, which is validated, bounded and cross-checked against the size the Hub listed;
    2. validates the header locally (safetensors: contiguous offsets, spans that match shape x dtype, declared data == file
       size - header; GGUF: magic, version, KV block and tensor table);
    3. creates a genuine SPARSE scratch file (NTFS ``FSCTL_SET_SPARSE``), writes the header into it and sets the logical size to
       the remote size. The tensor region is a hole: nothing is downloaded and (verified by measuring the allocation) no disk
       space is used;
    4. lets REA inspect that file with ``hash="header"`` (and, when it offers a retained evidence id, pages its metadata view),
       then deletes it.

  If the filesystem does not give a really sparse file, the stage reports ``not_applicable`` and no REA tool is called.
  A tool the capability policy does not permit (destructive, open-world, or needing a target this stage does not have) is refused
  by the client even if the caller asks for it.

Honest status
  ``completed``       every planned tool call really ran and succeeded
  ``partial``         the model inspection ran for at least one file, but something else failed or was skipped
  ``failed``          REA was needed but no model inspection succeeded (REA missing, crashed, refused the file, ...)
  ``not_applicable``  nothing REA can inspect (no safetensors/GGUF weights, or no sparse-file support); no REA tool was called
  ``not_invoked``     REA was not run at all (not configured / disabled); no REA tool was called
  A status is never ``completed`` or ``partial`` unless ``inspect_model_artifact`` was actually called and returned a result.
"""
from __future__ import annotations

import json
import logging
import os
import queue
import re
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.parse
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from .colab_cli import child_environment, kill_process_tree
from .config import PROJECT_ROOT, Settings
from .rea_capabilities import (
    Budget,
    CapabilityPolicy,
    EvidenceState,
    filter_plan,
    generate_questions,
    known_safe_tools,
    observe,
    plan_questions,
    state_signature,
    tag_tools,
)
from .security import (
    FetchError,
    SafeFetcher,
    SecurityError,
    ensure_within,
    redact_secrets,
    validate_repo_id,
    validate_revision,
)

log = logging.getLogger("m2g.rea")

DEFAULT_REA_COMMAND = r"C:\Users\Palak\AppData\Roaming\npm\rea.cmd"
PROTOCOL_VERSION = "2025-06-18"
CLIENT_INFO = {"name": "model-to-gradio", "version": "0.1"}

HEALTH_TOOL = "binary_session"
MODEL_TOOL = "inspect_model_artifact"
VIEW_TOOL = "inspect_analysis_view"
# The tools the header-inspection contract depends on: their absence makes the stage fail, because without them there is no
# model evidence at all. Every OTHER tool is chosen by the capability policy (m2g.rea_capabilities) from the advertised schemas.
REQUIRED_TOOLS = (HEALTH_TOOL, MODEL_TOOL)
# Kept for the tools the platform drives by hand with fixed arguments. Capability-selected tools carry their own arguments.
ALLOWED_TOOLS = (HEALTH_TOOL, MODEL_TOOL, VIEW_TOOL)
# Arguments are fixed by the platform. "header" reads declarations only ("full" would stream every byte of the file);
# "summary" is the detail level the tool documents for retained evidence. Both values come from the advertised schema text.
FIXED_ARGS: Dict[str, Dict[str, Any]] = {HEALTH_TOOL: {}, MODEL_TOOL: {"hash": "header", "detail": "summary"}}

MAX_SHARDS = 3  # at most this many weight files are header-inspected per model
MAX_HEADER_BYTES = 8 * 1024 * 1024  # our own ceiling; REA's is 100 MB
MAX_GGUF_WINDOW_BYTES = 8 * 1024 * 1024  # largest GGUF header window we will fetch (doubling stops here)
MAX_REMOTE_FILE_BYTES = 1 << 40  # 1 TiB: a remote size above this is treated as a lie, never as a sparse-file length
MAX_OUTPUT_CHARS = 8_000  # stored per tool result
MAX_INVESTIGATION_CALLS = 40  # adaptive-loop safety ceiling (a runaway guard, not a tool allow-list)
MAX_INVESTIGATION_SECONDS = 120.0  # adaptive-loop wall-clock budget
MAX_INVESTIGATION_FAILURES = 5  # stop after this many failed optional calls
INIT_TIMEOUT_S = 90.0
CALL_TIMEOUT_S = 120.0
SCRATCH_SUBDIR = (".m2g", "rea")  # inside the project; ``.m2g/`` is already git-ignored
SPARSE_SLACK_BYTES = 1 << 20  # allocation allowed beyond the header (filesystem metadata / cluster rounding)
SPARSE_PROBE_BYTES = 64 << 20  # logical size of the one-off probe file used to test sparse support before downloading anything
STALE_SCRATCH_S = 3600  # leftover hdr_* folders (a crashed earlier run) older than this are swept
HEALTH_TTL_S = 300.0

COMPLETED, PARTIAL, FAILED, NOT_APPLICABLE, NOT_INVOKED = "completed", "partial", "failed", "not_applicable", "not_invoked"
STATUS_LABELS = {COMPLETED: "Completed", PARTIAL: "Partially completed", FAILED: "Failed",
                 NOT_APPLICABLE: "Not applicable", NOT_INVOKED: "Not invoked"}

# bytes per element of the safetensors dtypes whose width is a whole number of bytes (sub-byte dtypes are not size-checked)
_DTYPE_BYTES = {"BOOL": 1, "U8": 1, "I8": 1, "F8_E4M3": 1, "F8_E5M2": 1, "F8_E8M0": 1, "I16": 2, "U16": 2, "F16": 2, "BF16": 2,
                "I32": 4, "U32": 4, "F32": 4, "I64": 8, "U64": 8, "F64": 8, "C64": 8}
_CONTENT_RANGE_RE = re.compile(r"bytes (\d{1,15})-(\d{1,15})/(\d{1,15})")


class ReaError(RuntimeError):
    """Expected REA problem (missing launcher, protocol error, timeout, refused call, bad remote header)."""


class ReaRpcError(ReaError):
    def __init__(self, code: Any, message: str):
        super().__init__(f"JSON-RPC error {code}: {message}")
        self.code, self.rpc_message = code, message


class SparseFileError(ReaError):
    """A really sparse scratch file could not be created (unsupported filesystem, permissions, allocation check failed)."""


# --------------------------------------------------------------------------- report


@dataclass
class ReaToolCall:
    tool: str
    arguments: Dict[str, Any]
    ok: bool
    duration_s: float = 0.0
    target: str = ""  # what was inspected, e.g. the repository file the header came from
    output: str = ""  # redacted, size-capped text returned by REA
    output_truncated: bool = False
    error: Optional[str] = None


@dataclass
class ReaReport:
    status: str = NOT_INVOKED
    reason: str = ""
    server: Dict[str, Any] = field(default_factory=dict)
    tools_discovered: int = 0
    capabilities: List[str] = field(default_factory=list)  # advertised tools the capability policy would allow (reporting only)
    capabilities_by_tag: Dict[str, List[str]] = field(default_factory=dict)  # capability tag -> eligible tool names
    refusals: Dict[str, str] = field(default_factory=dict)  # advertised tool -> why the policy refuses it (reporting only)
    questions: List[Dict[str, Any]] = field(default_factory=list)  # evidence-gap questions raised, with whether they were answered
    investigation: List[Dict[str, Any]] = field(default_factory=list)  # capability-selected calls: {tool, target, reason, ok, detail}
    stop_reason: str = ""  # why the adaptive investigation stopped (evidence complete / no capability / no progress / budget)
    budget: Dict[str, Any] = field(default_factory=dict)  # the resource budget the loop ran under, and what it used
    calls: List[ReaToolCall] = field(default_factory=list)
    header_summaries: List[Dict[str, Any]] = field(default_factory=list)  # parsed locally from the fetched header
    shards_total: int = 0
    shards_inspected: int = 0
    bytes_downloaded: int = 0  # response bytes received for the safetensors headers (never tensor data)
    http_requests: int = 0  # successful range requests made for the safetensors headers
    health_check: str = "not_run"  # ran | cached | not_run
    findings: List[str] = field(default_factory=list)
    log: List[str] = field(default_factory=list)
    _t0: float = field(default_factory=time.monotonic, repr=False, compare=False)
    _tools: Dict[str, Any] = field(default_factory=dict, repr=False, compare=False)  # advertised tool metadata (for the planner)
    _policy: Any = field(default=None, repr=False, compare=False)  # the CapabilityPolicy that governed this run

    def note(self, message: str) -> None:
        line = f"[{time.monotonic() - self._t0:6.2f}s] {redact_secrets(message)}"
        self.log.append(line)
        log.info("REA: %s", message)

    @property
    def tools_invoked(self) -> List[str]:
        return [c.tool for c in self.calls]

    @property
    def label(self) -> str:
        return STATUS_LABELS.get(self.status, self.status)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d.pop("_t0", None)
        d.pop("_tools", None)
        d.pop("_policy", None)
        return d


class HealthCache:
    """Remembers that the REA status check (``binary_session``) succeeded recently for one launcher, so consecutive analyses do
    not repeat it. Opt-in (pass it to ``run_rea_inspection``/``make_runner``); any failed call invalidates the entry."""

    def __init__(self, ttl: float = HEALTH_TTL_S, clock: Callable[[], float] = time.monotonic):
        self.ttl, self._clock = ttl, clock
        self._ok: Dict[Tuple[str, ...], float] = {}

    def is_fresh(self, key: Sequence[str]) -> bool:
        t = self._ok.get(tuple(key))
        return t is not None and (self._clock() - t) < self.ttl

    def record(self, key: Sequence[str]) -> None:
        self._ok[tuple(key)] = self._clock()

    def invalidate(self, key: Optional[Sequence[str]] = None) -> None:
        if key is None:
            self._ok.clear()
        else:
            self._ok.pop(tuple(key), None)


SHARED_HEALTH = HealthCache()  # used by the web UI and the CLI


# --------------------------------------------------------------------------- command


def build_command(launcher: str) -> List[str]:
    """argv for ``<launcher> --mcp``. ``.cmd``/``.bat`` shims (how npm installs rea) are started through ``cmd.exe /c`` exactly as
    in ``opencode.json``. The launcher path comes from configuration, never from repository data, and the only argument is the
    constant ``--mcp``."""
    launcher = (launcher or "").strip().strip('"')
    if not launcher or any(c in launcher for c in '&|<>^%"\r\n\0'):
        raise ReaError("The REA launcher path is empty or contains characters that are not allowed.")
    if Path(launcher).suffix.lower() in (".cmd", ".bat"):
        return ["cmd.exe", "/c", launcher, "--mcp"]
    return [launcher, "--mcp"]


def resolve_command(environ: Optional[Mapping[str, str]] = None) -> List[str]:
    env = os.environ if environ is None else environ
    return build_command((env.get("M2G_REA_COMMAND") or "").strip() or DEFAULT_REA_COMMAND)


# --------------------------------------------------------------------------- MCP client


def _check_arguments(tool: str, schema: Mapping[str, Any], args: Mapping[str, Any]) -> None:
    """Validate ``args`` against the input schema the server advertised for ``tool`` (types, required, no unknown keys)."""
    props = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    unknown = sorted(k for k in args if k not in props)
    if unknown:
        raise ReaError(f"{tool}: argument(s) {unknown} are not in the schema this REA server advertises.")
    missing = [k for k in (schema.get("required") or []) if k not in args]
    if missing:
        raise ReaError(f"{tool}: required argument(s) {missing} missing.")
    kinds = {"string": str, "integer": int, "object": dict, "array": list, "boolean": bool, "number": (int, float)}
    for key, value in args.items():
        want = props[key].get("type") if isinstance(props[key], dict) else None
        if want in kinds and (not isinstance(value, kinds[want]) or (want != "boolean" and isinstance(value, bool))):
            raise ReaError(f"{tool}: argument '{key}' must be of type {want}.")


class ReaMcpClient:
    """MCP over stdio (newline-delimited JSON-RPC 2.0, UTF-8): initialize -> notifications/initialized -> tools/list -> tools/call.

    Only tools the capability policy allows (see ``m2g.rea_capabilities``) can be called, and only with arguments that validate
    against the schema the server advertised. The child process never inherits this app's tokens/API keys."""

    def __init__(self, command: Sequence[str], *, cwd: Optional[Path] = None, timeout: float = INIT_TIMEOUT_S,
                 call_timeout: float = CALL_TIMEOUT_S, environ: Optional[Mapping[str, str]] = None,
                 popen: Callable[..., Any] = subprocess.Popen, policy: Optional[CapabilityPolicy] = None):
        self.command = list(command)
        self.cwd = str(cwd or PROJECT_ROOT)
        self.timeout, self.call_timeout = timeout, call_timeout
        self._environ = environ
        self._popen = popen
        self.policy = policy or CapabilityPolicy()
        self._proc: Any = None
        self._inbox: "queue.Queue[Optional[Dict[str, Any]]]" = queue.Queue()
        self._stderr: List[str] = []
        self._threads: List[threading.Thread] = []
        self._next_id = 1
        self.server_info: Dict[str, Any] = {}
        self.tools: Dict[str, Dict[str, Any]] = {}
        self.ignored_lines = 0

    # ---- lifecycle
    def __enter__(self) -> "ReaMcpClient":
        self.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def start(self) -> Dict[str, Any]:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        try:
            self._proc = self._popen(self.command, cwd=self.cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, env=child_environment(None if self._environ is None else dict(self._environ)),
                                     shell=False, creationflags=flags)
        except (OSError, ValueError) as exc:
            raise ReaError(f"REA could not be started ({type(exc).__name__}: {exc}).")
        for target, stream in ((self._pump_stdout, self._proc.stdout), (self._pump_stderr, self._proc.stderr)):
            t = threading.Thread(target=target, args=(stream,), daemon=True)
            t.start()
            self._threads.append(t)
        result = self._request("initialize", {"protocolVersion": PROTOCOL_VERSION, "capabilities": {}, "clientInfo": CLIENT_INFO},
                               self.timeout)
        info = result.get("serverInfo")
        if not isinstance(info, dict) or not info.get("name"):
            raise ReaError("The process answered 'initialize' without serverInfo; it does not look like an MCP server.")
        self.server_info = {"name": str(info.get("name"))[:80], "version": str(info.get("version", ""))[:40],
                            "protocolVersion": str(result.get("protocolVersion", ""))[:20]}
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return self.server_info

    def close(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            proc.stdin.close()
        except Exception:
            pass
        try:
            proc.wait(timeout=3)
        except Exception:
            kill_process_tree(proc)
            try:
                proc.wait(timeout=5)
            except Exception:
                pass
        for t in self._threads:
            t.join(timeout=1)

    # ---- transport
    def _pump_stdout(self, stream: Any) -> None:
        try:
            for raw in iter(stream.readline, b""):
                try:
                    msg = json.loads(raw.decode("utf-8", "replace"))
                except ValueError:
                    self.ignored_lines += 1  # banners / progress text
                    continue
                if isinstance(msg, dict):
                    self._inbox.put(msg)
        except Exception:
            pass
        finally:
            self._inbox.put(None)

    def _pump_stderr(self, stream: Any) -> None:
        try:
            for raw in iter(stream.readline, b""):
                if len(self._stderr) < 200:
                    self._stderr.append(raw.decode("utf-8", "replace").rstrip()[:400])
        except Exception:
            pass

    @property
    def stderr_tail(self) -> str:
        return redact_secrets("\n".join(self._stderr[-10:]))

    def _send(self, message: Dict[str, Any]) -> None:
        if self._proc is None:
            raise ReaError("The REA process is not running.")
        data = (json.dumps(message, separators=(",", ":")) + "\n").encode("utf-8")
        try:
            self._proc.stdin.write(data)
            self._proc.stdin.flush()
        except (OSError, ValueError) as exc:
            raise ReaError(f"Could not write to the REA process ({type(exc).__name__}).")

    def _request(self, method: str, params: Dict[str, Any], timeout: float) -> Dict[str, Any]:
        req_id, self._next_id = self._next_id, self._next_id + 1
        self._send({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})
        deadline = time.monotonic() + timeout
        while True:
            try:
                msg = self._inbox.get(timeout=max(0.01, deadline - time.monotonic()))
            except queue.Empty:
                raise ReaError(f"REA did not answer '{method}' within {timeout:.0f} s.")
            if msg is None:
                self._inbox.put(None)  # keep the 'process ended' marker so any later request fails at once instead of waiting
                code = self._proc.poll() if self._proc is not None else None
                raise ReaError(f"The REA process ended while waiting for '{method}' (exit code {code}). {self.stderr_tail}".strip())
            if "method" in msg:  # server-initiated request or notification
                if "id" in msg:
                    if msg["method"] == "ping":
                        self._send({"jsonrpc": "2.0", "id": msg["id"], "result": {}})
                    else:
                        self._send({"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32601, "message": "Method not supported"}})
                continue
            if msg.get("id") != req_id:
                continue
            if "error" in msg:
                err = msg["error"] if isinstance(msg["error"], dict) else {}
                raise ReaRpcError(err.get("code"), redact_secrets(str(err.get("message", "unknown error")))[:400])
            result = msg.get("result")
            return result if isinstance(result, dict) else {}

    # ---- MCP operations
    def list_tools(self) -> Dict[str, Dict[str, Any]]:
        tools: Dict[str, Dict[str, Any]] = {}
        cursor: Optional[str] = None
        for _page in range(50):
            res = self._request("tools/list", {"cursor": cursor} if cursor else {}, self.timeout)
            for tool in res.get("tools") or []:
                if isinstance(tool, dict) and isinstance(tool.get("name"), str):
                    tools[tool["name"]] = tool
            cursor = res.get("nextCursor") if isinstance(res.get("nextCursor"), str) else None
            if not cursor:
                break
        self.tools = tools
        return tools

    def call_tool(self, name: str, arguments: Mapping[str, Any], *, target: str = "") -> ReaToolCall:
        """Really call one permitted tool. A tool-level failure (``isError``, JSON-RPC error, timeout) is returned in the
        record (``ok=False``); calls that the capability policy or the advertised schema refuse raise ``ReaError`` and send nothing."""
        tool = self.tools.get(name)
        if tool is None:
            raise ReaError(f"This REA server does not advertise '{name}'.")
        if name not in REQUIRED_TOOLS and not self.policy.eligible(tool):
            raise ReaError(f"Tool '{name}' is refused by the capability policy (destructive or open-world, or it needs a target "
                           "this analysis stage does not have).")
        args = dict(arguments)
        _check_arguments(name, tool.get("inputSchema") if isinstance(tool.get("inputSchema"), dict) else {}, args)
        started = time.monotonic()
        rec = ReaToolCall(tool=name, arguments=args, ok=False, target=target)
        try:
            res = self._request("tools/call", {"name": name, "arguments": args}, self.call_timeout)
        except ReaError as exc:
            rec.error = str(exc)[:500]
            rec.duration_s = round(time.monotonic() - started, 2)
            return rec
        rec.duration_s = round(time.monotonic() - started, 2)
        parts = [c.get("text") for c in res.get("content") or [] if isinstance(c, dict) and c.get("type") == "text"
                 and isinstance(c.get("text"), str)]
        text = "\n".join(parts)
        if not text and res.get("structuredContent") is not None:
            text = json.dumps(res["structuredContent"], ensure_ascii=False)
        text = redact_secrets(text)
        rec.output_truncated = len(text) > MAX_OUTPUT_CHARS
        rec.output = text[:MAX_OUTPUT_CHARS]
        if res.get("isError"):
            rec.error = (rec.output[:300] or "REA reported an error without text.")
        else:
            rec.ok = True
        return rec


# --------------------------------------------------------------------------- safetensors header (range requests, no weights)


def _safe_repo_file(name: str) -> str:
    if not name or name.startswith("/") or ".." in name.split("/") or any(ord(c) < 32 or c in "\\?#%" for c in name):
        raise SecurityError(f"Unsafe repository file name {name!r}.")
    return name


@dataclass(frozen=True)
class RemoteHeader:
    blob: bytes  # 8 length bytes + the JSON header; nothing else
    total_size: int  # size of the whole remote file, from a validated Content-Range


def _content_range_total(headers: Mapping[str, str], start: int, end: int) -> int:
    """Total file size from a ``Content-Range: bytes start-end/total`` header, which must describe exactly the requested bytes."""
    value = next((v for k, v in headers.items() if str(k).lower() == "content-range"), None)
    m = _CONTENT_RANGE_RE.fullmatch(str(value or "").strip())
    if not m:
        raise ReaError("The response has no valid Content-Range header, so the real file size is unknown.")
    got_start, got_end, total = (int(g) for g in m.groups())
    if (got_start, got_end) != (start, end):
        raise ReaError(f"Content-Range describes bytes {got_start}-{got_end}, but bytes {start}-{end} were requested.")
    if not 8 <= total <= MAX_REMOTE_FILE_BYTES:
        raise ReaError(f"Implausible remote file size ({total} bytes) in Content-Range.")
    if end >= total:
        raise ReaError("Content-Range ends beyond the file size it declares.")
    return total


def fetch_safetensors_header(fetcher: SafeFetcher, repo_id: str, revision: str, filename: str, *,
                             expected_size: Optional[int] = None, stats: Optional[Dict[str, int]] = None) -> RemoteHeader:
    """The first 8 + N bytes of a safetensors file (N = declared JSON header length) and the file's real size, using exactly two
    HTTP range requests. The tensor data is never requested. ``expected_size`` (the size the Hub listed) must agree with the
    Content-Range total. ``stats`` accumulates ``requests`` and ``bytes`` actually received."""
    validate_repo_id(repo_id)
    validate_revision(revision)
    url = f"https://huggingface.co/{repo_id}/resolve/{revision}/{urllib.parse.quote(_safe_repo_file(filename))}"

    def get(start: int, end: int) -> Tuple[bytes, int]:
        return _range_get(fetcher, url, start, end, stats)

    prefix, total = get(0, 7)
    n = int.from_bytes(prefix, "little")
    if not 2 <= n <= MAX_HEADER_BYTES:
        raise ReaError(f"Implausible safetensors header length ({n} bytes).")
    if 8 + n > total:
        raise ReaError(f"The declared header length ({n} bytes) does not fit in the remote file ({total} bytes).")
    if expected_size is not None and expected_size != total:
        raise ReaError(f"The remote file is {total} bytes, but the Hub listed {expected_size} bytes; refusing to trust either.")
    header, total_again = get(8, 8 + n - 1)
    if total_again != total:
        raise ReaError("The remote file size changed between the two range requests.")
    return RemoteHeader(prefix + header, total)


def summarize_header(blob: bytes, total_size: Optional[int] = None) -> Dict[str, Any]:
    """Validate the header and compute facts locally (documented safetensors layout): tensor count, parameters, dtypes.

    Tensor ``data_offsets`` must be well-formed, contiguous from 0 (no gap, no overlap), consistent with shape x dtype where the
    dtype width is known, and - when ``total_size`` is given - must exactly fill the file after the header."""
    if len(blob) < 10:
        raise ReaError("The safetensors header is too short.")
    n = int.from_bytes(blob[:8], "little")
    if not 2 <= n <= MAX_HEADER_BYTES or len(blob) != 8 + n:
        raise ReaError("The safetensors header length does not match the bytes that were read.")
    raw = blob[8:8 + n]
    if raw[:1] != b"{":
        raise ReaError("The safetensors header does not start with '{'.")
    try:
        obj = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise ReaError("The safetensors header is not valid JSON.")
    if not isinstance(obj, dict):
        raise ReaError("The safetensors header is not a JSON object.")
    total, dtypes, count = 0, {}, 0
    spans: List[Tuple[int, int]] = []
    for key, tensor in obj.items():
        if key == "__metadata__":
            continue
        shape = tensor.get("shape") if isinstance(tensor, dict) else None
        dtype = tensor.get("dtype") if isinstance(tensor, dict) else None
        if not isinstance(shape, list) or not isinstance(dtype, str) or len(shape) > 16:
            raise ReaError("Malformed tensor entry in the safetensors header.")
        size = 1
        for dim in shape:
            if isinstance(dim, bool) or not isinstance(dim, int) or dim < 0:
                raise ReaError("Malformed tensor shape in the safetensors header.")
            size *= dim
            if size > 10 ** 13:
                raise ReaError("Implausible tensor size in the safetensors header.")
        offs = tensor.get("data_offsets")
        if (not isinstance(offs, list) or len(offs) != 2 or any(isinstance(o, bool) or not isinstance(o, int) for o in offs)
                or offs[0] < 0 or offs[1] < offs[0]):
            raise ReaError("Malformed data_offsets in the safetensors header.")
        width = _DTYPE_BYTES.get(dtype)
        if width is not None and offs[1] - offs[0] != size * width:
            raise ReaError("A tensor's data_offsets span does not match its shape and dtype.")
        spans.append((offs[0], offs[1]))
        count += 1
        total += size
        if dtype[:24] in dtypes or len(dtypes) < 16:
            dtypes[dtype[:24]] = dtypes.get(dtype[:24], 0) + size
    cursor = 0
    for begin, end in sorted(spans):
        if begin != cursor:
            raise ReaError("Tensor data_offsets are not contiguous (gap or overlap) in the safetensors header.")
        cursor = end
    if total_size is not None:
        data_len = total_size - 8 - n
        if data_len < 0 or cursor != data_len:
            raise ReaError(f"The header declares {cursor} bytes of tensor data, but the file holds {max(data_len, 0)} bytes after "
                           "its header.")
    return {"header_bytes": n, "tensor_count": count, "parameters": total, "dtypes": dict(sorted(dtypes.items())),
            "data_bytes": cursor}


def _range_get(fetcher: SafeFetcher, url: str, start: int, end: int, stats: Optional[Dict[str, int]] = None) -> Tuple[bytes, int]:
    """One HTTP range request: returns (body, total file size from a validated Content-Range). Records stats when given."""
    want = end - start + 1
    res = fetcher.fetch(url, {"Range": f"bytes={start}-{end}"}, max_bytes=want + 64)
    if stats is not None:
        stats["requests"] = stats.get("requests", 0) + 1
        stats["bytes"] = stats.get("bytes", 0) + len(res.body)
    if res.status != 206:
        raise ReaError(f"The server did not honour the range request for bytes {start}-{end} (HTTP {res.status}).")
    if len(res.body) != want:
        raise ReaError(f"The server returned {len(res.body)} bytes for a {want}-byte range request.")
    return res.body, _content_range_total(res.headers, start, end)


def _repo_resolve_url(repo_id: str, revision: str, filename: str) -> str:
    validate_repo_id(repo_id)
    validate_revision(revision)
    return f"https://huggingface.co/{repo_id}/resolve/{revision}/{urllib.parse.quote(_safe_repo_file(filename))}"


def fetch_gguf_header(fetcher: SafeFetcher, repo_id: str, revision: str, filename: str, *,
                      expected_size: Optional[int] = None, stats: Optional[Dict[str, int]] = None) -> RemoteHeader:
    """The GGUF header (magic + KV block + tensor table + alignment padding) and the file's real size, using bounded HTTP range
    requests. The window doubles (1 MiB, 2 MiB, 4 MiB, 8 MiB) until the local parser reports the tensor table complete; the exact
    header extent is then trimmed with one more request, so no tensor data is ever requested. ``expected_size`` (the Hub-listed
    size) must agree with the Content-Range total."""
    from .evidence.repo_inspectors import inspect_gguf  # local import: keeps this module's import graph acyclic

    url = _repo_resolve_url(repo_id, revision, filename)
    first, total = _range_get(fetcher, url, 0, 0, stats)  # one byte first: cheap Content-Range to learn the real size
    if expected_size is not None and expected_size != total:
        raise ReaError(f"The remote file is {total} bytes, but the Hub listed {expected_size} bytes; refusing to trust either.")
    window = min(1 << 20, total)
    while True:
        end = min(window, total) - 1
        blob = first + _range_get(fetcher, url, 1, end, stats)[0]
        if len(blob) != min(window, total):
            raise ReaError("The GGUF header window is shorter than requested.")
        info = inspect_gguf(blob, total)
        if info.get("parse_error") and not info.get("version"):
            raise ReaError(f"The GGUF header could not be parsed: {info['parse_error']}.")
        if info.get("complete"):
            end_off = info.get("header_end_offset")
            if not isinstance(end_off, int) or end_off < 0:
                raise ReaError("The GGUF parser did not report where its tensor data begins.")
            if end_off > total:
                raise ReaError("The GGUF header declares tensor data beyond the end of the file.")
            if end_off > len(blob):  # alignment padding reached past the window: fetch exactly up to the header end
                if end_off > MAX_GGUF_WINDOW_BYTES:
                    raise ReaError(f"The GGUF header extent ({end_off} bytes) exceeds the {MAX_GGUF_WINDOW_BYTES}-byte window limit.")
                blob = first + _range_get(fetcher, url, 1, end_off - 1, stats)[0]
            return RemoteHeader(blob[:end_off], total)
        if window >= min(MAX_GGUF_WINDOW_BYTES, total):
            raise ReaError(f"The GGUF tensor table did not fit the {MAX_GGUF_WINDOW_BYTES}-byte header window.")
        window *= 2


def summarize_gguf(blob: bytes, total_size: Optional[int] = None) -> Dict[str, Any]:
    """Validate a fetched GGUF header and turn it into the same fact shape ``summarize_header`` returns for safetensors
    (``tensor_count``, ``parameters``, ``dtypes``) plus the GGUF-specific architecture and tensor-type counts. The local parser
    never executes code and never reads tensor data."""
    from .evidence.repo_inspectors import inspect_gguf  # local import: keeps this module's import graph acyclic

    info = inspect_gguf(blob, total_size)
    if info.get("parse_error") and not info.get("version"):
        raise ReaError(f"The GGUF header could not be parsed: {info['parse_error']}.")
    if not info.get("complete"):
        raise ReaError("The GGUF header is not complete in the bytes that were read.")
    end = info.get("header_end_offset")
    if not isinstance(end, int) or not 4 <= end <= len(blob):
        raise ReaError("The GGUF header extent does not match the bytes that were read.")
    if total_size is not None and end > total_size:
        raise ReaError("The GGUF header declares tensor data beyond the end of the file.")
    types = info.get("tensor_types") if isinstance(info.get("tensor_types"), dict) else {}
    return {"header_bytes": end, "tensor_count": int(info.get("tensor_count") or 0),
            "parameters": int(info.get("parameters") or 0), "dtypes": dict(sorted(types.items())),
            "data_bytes": max(0, (total_size or 0) - end), "format": "gguf", "gguf_version": info.get("version"),
            "architecture": info.get("architecture"), "has_chat_template": bool(info.get("has_chat_template"))}


# --------------------------------------------------------------------------- weight-format registry

@dataclass(frozen=True)
class WeightFormat:
    """One weight format the REA stage can header-inspect without downloading tensor data."""

    suffix: str
    fetch: Callable[..., RemoteHeader]
    summarize: Callable[[bytes, Optional[int]], Dict[str, Any]]


WEIGHT_FORMATS: Dict[str, WeightFormat] = {
    ".safetensors": WeightFormat(".safetensors", fetch_safetensors_header, summarize_header),
    ".gguf": WeightFormat(".gguf", fetch_gguf_header, summarize_gguf),
}


def register_weight_format(suffix: str, fetch: Callable[..., RemoteHeader],
                           summarize: Callable[[bytes, Optional[int]], Dict[str, Any]]) -> None:
    """Add a header-only weight format. Only formats listed here are ever inspected by the REA stage."""
    WEIGHT_FORMATS[suffix.lower()] = WeightFormat(suffix.lower(), fetch, summarize)


def _weight_suffix(name: str) -> Optional[str]:
    low = name.lower()
    return next((s for s in sorted(WEIGHT_FORMATS, key=len, reverse=True) if low.endswith(s)), None)


# --------------------------------------------------------------------------- sparse scratch files


def _mark_sparse(fh: Any) -> None:
    """Windows: flag the open file as sparse (FSCTL_SET_SPARSE). Elsewhere files are sparse by default when extended."""
    if os.name != "nt":
        return
    import ctypes
    import msvcrt
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.DeviceIoControl.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, wintypes.LPVOID,
                                    wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
    k32.DeviceIoControl.restype = wintypes.BOOL
    returned = wintypes.DWORD(0)
    if not k32.DeviceIoControl(msvcrt.get_osfhandle(fh.fileno()), 0x000900C4, None, 0, None, 0, ctypes.byref(returned), None):
        raise SparseFileError(f"The filesystem refused to mark the scratch file sparse (Windows error {ctypes.get_last_error()}).")


def _extend_sparse(fh: Any, logical_size: int) -> None:
    """Set the logical size of the (already sparse-marked) open file WITHOUT allocating the new region.

    On Windows ``fh.truncate(size)`` is not safe for this: on the Python used here it extends the file by zero-filling it, and the
    whole logical size ends up allocated on disk (measured: 594 MiB at once and 1024 MiB a few seconds later for a 1 GiB file).
    ``SetFilePointerEx`` + ``SetEndOfFile`` on a sparse file leaves a genuine hole (measured: 64 KiB allocated for the same file).
    Elsewhere ``truncate`` already creates a hole."""
    if os.name != "nt":
        fh.truncate(logical_size)
        return
    import ctypes
    import msvcrt
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.SetFilePointerEx.argtypes = [wintypes.HANDLE, ctypes.c_longlong, ctypes.POINTER(ctypes.c_longlong), wintypes.DWORD]
    k32.SetFilePointerEx.restype = wintypes.BOOL
    k32.SetEndOfFile.argtypes = [wintypes.HANDLE]
    k32.SetEndOfFile.restype = wintypes.BOOL
    fh.flush()  # the header must be written before the end of the file moves
    handle = msvcrt.get_osfhandle(fh.fileno())
    if not k32.SetFilePointerEx(handle, logical_size, None, 0) or not k32.SetEndOfFile(handle):
        raise SparseFileError(f"The logical size of the scratch file could not be set (Windows error {ctypes.get_last_error()}).")


def allocated_bytes(path: Path) -> Optional[int]:
    """Bytes the file really occupies on disk (not its logical size); None when that cannot be determined."""
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.GetCompressedFileSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
        k32.GetCompressedFileSizeW.restype = wintypes.DWORD
        ctypes.set_last_error(0)
        high = wintypes.DWORD(0)
        low = k32.GetCompressedFileSizeW(str(path), ctypes.byref(high))
        if low == 0xFFFFFFFF and ctypes.get_last_error() != 0:
            return None
        return (high.value << 32) | low
    blocks = getattr(os.stat(path), "st_blocks", None)
    return None if blocks is None else int(blocks) * 512


def create_sparse_file(path: Path, header: bytes, logical_size: int) -> int:
    """Create ``path`` as a genuine sparse file: ``header`` at offset 0, logical size ``logical_size``, everything else a hole.
    Nothing but the header is ever written. The result is MEASURED: if the file is not really sparse (allocation far above the
    header), ``SparseFileError`` is raised. Returns the bytes actually allocated on disk."""
    if not 0 < len(header) <= logical_size <= MAX_REMOTE_FILE_BYTES:
        raise SparseFileError("The requested sparse file size is not valid for this header.")
    created = False
    try:
        try:
            with open(path, "xb") as fh:
                created = True
                _mark_sparse(fh)
                fh.write(header)
                fh.flush()
                _extend_sparse(fh, logical_size)  # SetEndOfFile on Windows: extends the logical size without writing data
            if os.stat(path).st_size != logical_size:
                raise SparseFileError("The scratch file does not have the requested logical size.")
            allocated = allocated_bytes(path)
        except OSError as exc:
            raise SparseFileError(f"The sparse scratch file could not be created ({type(exc).__name__}: {exc}).")
        if allocated is None:
            raise SparseFileError("The on-disk size of the scratch file could not be measured, so it cannot be shown to be sparse.")
        if allocated > len(header) + SPARSE_SLACK_BYTES:
            raise SparseFileError(f"The scratch file is not sparse (it occupies {allocated} bytes for a {logical_size}-byte logical "
                                  "size); this filesystem does not support sparse files here.")
        return allocated
    except SparseFileError:
        if created:  # never leave a half-made or not-really-sparse scratch file behind (a pre-existing file is never touched)
            try:
                os.unlink(path)
            except OSError:
                pass
        raise


_SPARSE_PROBES: Dict[str, Tuple[bool, str]] = {}


def sparse_supported(base: Path) -> Tuple[bool, str]:
    """Whether a really sparse file can be created in ``base``. Tested once per folder with a small probe file, BEFORE anything
    is downloaded, so an unsupported filesystem costs no HTTP request."""
    key = str(base)
    if key not in _SPARSE_PROBES:
        probe = base / f"probe_{uuid.uuid4().hex}.sparse"
        try:
            create_sparse_file(probe, b"m2g", SPARSE_PROBE_BYTES)
            _SPARSE_PROBES[key] = (True, "")
        except SparseFileError as exc:
            _SPARSE_PROBES[key] = (False, str(exc))
        finally:
            try:
                probe.unlink()
            except OSError:
                pass
    return _SPARSE_PROBES[key]


def _remove_tree(path: Path, rep: Optional[ReaReport] = None) -> bool:
    """Delete a scratch folder, retrying briefly (a just-killed process can still hold a handle on Windows)."""
    for _attempt in range(5):
        shutil.rmtree(path, ignore_errors=True)
        if not path.exists():
            return True
        time.sleep(0.2)
    if rep is not None:
        rep.note(f"Could not remove the scratch folder {path.name}; it is swept on the next run.")
    return False


def _sweep_stale(base: Path, rep: ReaReport) -> None:
    """Remove ``hdr_*`` scratch folders left behind by a crashed earlier run. Only folders directly inside ``base``."""
    try:
        entries = list(base.glob("hdr_*"))
    except OSError:
        return
    now = time.time()
    for p in entries:
        try:
            if p.is_dir() and now - p.stat().st_mtime > STALE_SCRATCH_S:
                ensure_within(base, p)
                if _remove_tree(p):
                    rep.note(f"Removed a stale scratch folder from an earlier run: {p.name}.")
        except (OSError, SecurityError):
            continue


# --------------------------------------------------------------------------- planning


def _inspectable_model(info: Any) -> Tuple[Optional[Any], str]:
    """The Hugging Face RepoInfo REA should look at: the model itself, or a GitHub repository's linked primary model."""
    if info.source.kind == "github":
        model = getattr(info, "primary_model_info", None)
        return (model, "") if model is not None else (None, "The repository has no linked Hugging Face model whose files REA could inspect.")
    return info, ""


def plan_targets(info: Any) -> Tuple[Optional[Any], List[Tuple[str, "WeightFormat"]], int, str]:
    """(model_info, [(file name, WeightFormat)] to inspect (<= MAX_SHARDS), total inspectable weight files, not-applicable reason)."""
    model, why = _inspectable_model(info)
    if model is None:
        return None, [], 0, why
    targets = [(n, WEIGHT_FORMATS[_weight_suffix(n)]) for n, _ in sorted(model.files, key=lambda e: (e[0].count("/"), e[0]))
               if _weight_suffix(n) is not None]
    if not targets:
        other = [lbl for lbl, present in (("pickle", model.has_pickle_weights), ("ONNX", model.has_onnx)) if present]
        extra = (f" (found {', '.join(other)} weights; header-only inspection is implemented for "
                 f"{', '.join(sorted(WEIGHT_FORMATS))} only)") if other else ""
        return None, [], 0, ("The model has no weight file in a format REA can header-inspect" + extra + ".")
    return model, targets[:MAX_SHARDS], len(targets), ""


def _parent_evidence_id(call: ReaToolCall) -> Optional[str]:
    """The retained Evidence id a model-inspection result advertises for deeper pages, or None when it carries none."""
    try:
        obj = json.loads(call.output or "")
    except (ValueError, TypeError):
        return None
    if not isinstance(obj, dict):
        return None
    nr = obj.get("normalized_result")
    for candidate in ((nr or {}).get("parent_evidence_id") if isinstance(nr, dict) else None, obj.get("parent_evidence_id")):
        if isinstance(candidate, str) and candidate.startswith("ev_"):
            return candidate
    return None


# --------------------------------------------------------------------------- the inspection stage


def _finish(rep: ReaReport, status: str, reason: str) -> ReaReport:
    rep.status, rep.reason = status, redact_secrets(reason)
    rep.note(f"status={status}: {rep.reason}")
    return rep


def run_rea_inspection(info: Any, fetcher: SafeFetcher, settings: Optional[Settings] = None, *, command: Optional[Sequence[str]] = None,
                       project_dir: Optional[Path] = None, client_factory: Optional[Callable[..., ReaMcpClient]] = None,
                       health_cache: Optional[HealthCache] = None) -> ReaReport:
    """Run the REA inspection for one RepoInfo. Never raises for expected problems; every outcome is in the returned report."""
    rep = ReaReport()
    try:
        return _run(rep, info, fetcher, command, Path(project_dir) if project_dir else Path(PROJECT_ROOT),
                    client_factory or ReaMcpClient, health_cache, budget=_budget_from(settings))
    except Exception as exc:  # the stage must never take the pipeline down; the failure is shown in the UI
        log.exception("REA inspection crashed")
        return _finish(rep, FAILED, f"Unexpected error in the REA stage: {type(exc).__name__}: {exc}")


def _budget_from(settings: Optional[Settings]) -> Budget:
    """The adaptive-investigation budget: the module ceilings, overridden by any values the caller put on Settings."""
    return Budget(max_calls=getattr(settings, "rea_max_investigation_calls", MAX_INVESTIGATION_CALLS) or MAX_INVESTIGATION_CALLS,
                  max_seconds=getattr(settings, "rea_max_investigation_seconds", MAX_INVESTIGATION_SECONDS) or MAX_INVESTIGATION_SECONDS,
                  max_failures=getattr(settings, "rea_max_investigation_failures", MAX_INVESTIGATION_FAILURES) or MAX_INVESTIGATION_FAILURES)


def _run(rep: ReaReport, info: Any, fetcher: SafeFetcher, command: Optional[Sequence[str]], project_dir: Path,
         client_factory: Callable[..., ReaMcpClient], health_cache: Optional[HealthCache],
         budget: Optional[Budget] = None) -> ReaReport:
    model, targets, total, why = plan_targets(info)
    if model is None:
        rep.note("No REA tool was called: nothing to inspect.")
        return _finish(rep, NOT_APPLICABLE, why)
    rep.shards_total = total
    names = [n for n, _ in targets]
    pin = model.revision_sha or model.source.revision or "main"
    try:
        validate_revision(pin)
    except SecurityError:
        pin = "main"
    repo_id = model.source.repo_id
    listed_sizes = dict(model.files)
    rep.note(f"Plan: header-only inspection of {len(names)} of {total} weight file(s) in {repo_id}@{pin[:12]} "
             f"with the required REA tools {list(REQUIRED_TOOLS)}, plus capability-selected optional calls.")

    # Cheapest failures first: the launcher is checked before any network request is made.
    try:
        launcher = list(command) if command else resolve_command()
    except ReaError as exc:
        return _finish(rep, FAILED, str(exc))
    exe = launcher[2] if launcher[:2] == ["cmd.exe", "/c"] and len(launcher) > 2 else launcher[0]
    if command is None and not Path(exe).is_file():
        return _finish(rep, FAILED, f"The REA launcher was not found at {exe}. Set M2G_REA_COMMAND to the full path of rea.cmd.")

    scratch: Optional[Path] = None
    try:
        base = ensure_within(project_dir, project_dir.joinpath(*SCRATCH_SUBDIR))
        base.mkdir(parents=True, exist_ok=True)
        _sweep_stale(base, rep)
        supported, sparse_why = sparse_supported(base)
        if not supported:
            rep.note("No REA tool was called and nothing was downloaded: " + sparse_why)
            return _finish(rep, NOT_APPLICABLE, "Sparse scratch files are not supported here, so REA cannot validate a header-only "
                           "file without downloading the weights. " + sparse_why)
        scratch = Path(tempfile.mkdtemp(prefix="hdr_", dir=str(base)))
        files: List[Tuple[str, Path]] = []
        stats: Dict[str, int] = {}
        header_failures = sparse_failures = 0
        for i, (name, fmt) in enumerate(targets):
            try:
                remote = fmt.fetch(fetcher, repo_id, pin, name, expected_size=listed_sizes.get(name), stats=stats)
                summary = fmt.summarize(remote.blob, remote.total_size)
            except (FetchError, SecurityError, ReaError) as exc:
                header_failures += 1
                rep.note(f"Header of {name} could not be used: {type(exc).__name__}: {exc}")
                continue
            path = ensure_within(project_dir, scratch / f"shard_{i}{fmt.suffix}")
            try:
                allocated = create_sparse_file(path, remote.blob, remote.total_size)
            except SparseFileError as exc:
                sparse_failures += 1
                rep.note(f"No sparse scratch file for {name}: {exc}")
                continue
            files.append((name, path))
            rep.header_summaries.append({"file": name, **summary, "logical_size": remote.total_size, "allocated_bytes": allocated})
            rep.note(f"Fetched header of {name}: {summary['header_bytes']} bytes, {summary['tensor_count']} tensors, "
                     f"{summary['parameters']:,} parameters; sparse scratch file {remote.total_size:,} bytes logical, "
                     f"{allocated:,} bytes on disk (no weights downloaded).")
        rep.bytes_downloaded, rep.http_requests = stats.get("bytes", 0), stats.get("requests", 0)
        rep.note(f"Downloaded {rep.bytes_downloaded:,} bytes in {rep.http_requests} range request(s).")
        if not files:
            if sparse_failures and not header_failures:
                return _finish(rep, NOT_APPLICABLE, "No sparse scratch file could be created for the weight-file headers, so no REA "
                               "inspection tool was called.")
            return _finish(rep, FAILED, "No usable weight-file header could be fetched, so no REA inspection tool was called.")

        key = tuple(launcher)
        use_cached = health_cache is not None and health_cache.is_fresh(key)
        rep.health_check = "cached" if use_cached else "ran"
        if use_cached:
            rep.note(f"Status check skipped: {HEALTH_TOOL} succeeded recently for this launcher.")
        rep.note(f"Starting REA MCP server: {' '.join(launcher)}")
        core_plan = ([] if use_cached else [(HEALTH_TOOL, dict(FIXED_ARGS[HEALTH_TOOL]), "server status")]) + [
            (MODEL_TOOL, {"path": str(p), **FIXED_ARGS[MODEL_TOOL]}, name) for name, p in files]
        try:
            with client_factory(launcher, cwd=project_dir) as client:
                rep.server = dict(client.server_info)
                rep.note(f"initialize ok: {rep.server.get('name')} {rep.server.get('version')} (MCP {rep.server.get('protocolVersion')})")
                tools = client.list_tools()
                rep.tools_discovered = len(tools)
                rep.note(f"tools/list: {len(tools)} tools advertised")
                missing = [t for t in REQUIRED_TOOLS if t not in tools]
                if missing:
                    return _finish(rep, FAILED, f"This REA server does not advertise the required tool(s) {missing}; none was called.")
                # Dynamic capability discovery: the tools the server advertises, the ones the policy allows, the ones it refuses
                # (and why), and the capability tag each eligible tool serves - all from the advertised metadata.
                rep.capabilities = known_safe_tools(tools, client.policy)
                rep.capabilities_by_tag = tag_tools(tools, client.policy)
                rep.refusals = client.policy.refusals(tools)
                rep._tools = dict(tools)
                rep._policy = client.policy
                rep.note(f"capabilities: {len(rep.capabilities)} eligible of {len(tools)} advertised; "
                         f"tags {sorted(rep.capabilities_by_tag)}")
                for tool, args, target in core_plan:
                    try:
                        call = client.call_tool(tool, args, target=target)
                    except ReaError as exc:  # refused by the policy / schema check: nothing was sent
                        call = ReaToolCall(tool=tool, arguments=args, ok=False, target=target, error=f"not sent: {exc}")
                    rep.calls.append(call)
                    rep.note(f"tools/call {tool} [{target}] -> {'ok' if call.ok else 'FAILED: ' + str(call.error)[:160]} ({call.duration_s}s)")
                # Adaptive, evidence-gap-driven investigation: raise the open questions from the facts the core inspection just
                # produced, plan the eligible tools that can answer them, execute, fold the results back in, and replan until the
                # questions are exhausted (or no capability answers them, or a step stops producing evidence, or the budget is
                # spent). Every extra call is OPTIONAL: a failure is recorded and noted but never changes the stage's verdict.
                _adaptive_investigation(rep, client, files, header_ok=bool(rep.header_summaries), budget=budget)
        except ReaError as exc:
            rep.note(f"REA transport/protocol problem: {exc}")
            if health_cache is not None:
                health_cache.invalidate(key)
            if not any(c.tool == MODEL_TOOL and c.ok for c in rep.calls):
                return _finish(rep, FAILED, f"REA could not complete the inspection: {exc}")
            rep.note("Calls made before the problem are kept.")
        if health_cache is not None:  # a cached health result is only trusted while every call keeps succeeding
            if any(not c.ok for c in rep.calls if c.tool in (HEALTH_TOOL, MODEL_TOOL)):
                health_cache.invalidate(key)
            elif any(c.tool == HEALTH_TOOL and c.ok for c in rep.calls):
                health_cache.record(key)
    finally:
        if scratch is not None:
            _remove_tree(scratch, rep)

    ok_models = [c for c in rep.calls if c.tool == MODEL_TOOL and c.ok]
    rep.shards_inspected = len(ok_models)
    ok_names = {c.target for c in ok_models}
    for s in rep.header_summaries:
        if s["file"] in ok_names:
            arch = f", architecture {s['architecture']}" if s.get("architecture") else ""
            rep.findings.append(f"{s['file']}: {s['tensor_count']} tensors, {s['parameters']:,} parameters{arch}, dtypes "
                                + (", ".join(f"{k} ({v:,})" for k, v in s["dtypes"].items()) or "none") + " (REA read this header).")
    if not ok_models:
        first_err = next((c.error for c in rep.calls if c.tool == MODEL_TOOL and c.error), "no model inspection call was made")
        return _finish(rep, FAILED, f"REA did not inspect any model file: {first_err}")
    health_ok = use_cached or any(c.tool == HEALTH_TOOL and c.ok for c in rep.calls)
    if health_ok and len(ok_models) == len(names) == len(files) and rep.shards_total == len(names):
        return _finish(rep, COMPLETED, f"REA inspected the header of all {len(names)} weight file(s).")
    problems = []
    if not health_ok:
        problems.append("the status check failed")
    if len(ok_models) < len(names):
        problems.append(f"{len(ok_models)} of {len(names)} planned file(s) inspected")
    if rep.shards_total > len(names):
        problems.append(f"only the first {len(names)} of {rep.shards_total} weight files were inspected")
    return _finish(rep, PARTIAL, "REA inspected part of the model: " + "; ".join(problems) + ".")


def _evidence_state(rep: ReaReport, files: List[Tuple[str, Path]], *, header_ok: bool) -> EvidenceState:
    """Build the evidence state the question generators read, from the facts the header inspection already produced.

    ``capabilities`` is the tag -> eligible-tool map discovered from the advertised metadata; a capability the server does not
    advertise is simply absent, so no question that needs it is raised."""
    by_file = {str(s.get("file")): s for s in rep.header_summaries if isinstance(s, dict)}
    header_bytes = {n: int((by_file.get(n) or {}).get("header_bytes") or 0) for n, _ in files}
    formats = {n: str((by_file.get(n) or {}).get("format") or "") for n, _ in files}
    archs = {n: (by_file.get(n) or {}).get("architecture") for n, _ in files}
    # Count the inspected files from the calls: ``rep.shards_inspected`` is only filled in after this stage runs.
    inspected = sum(1 for c in rep.calls if c.tool == MODEL_TOOL and c.ok)
    partial = bool(rep.shards_total and inspected < rep.shards_total)
    retained = {evid: target for target, evid in _retained_ids_by_target(rep).items() if isinstance(evid, str)}
    return EvidenceState(
        files=[(n, str(p)) for n, p in files],
        header_bytes=header_bytes,
        formats=formats,
        architectures=archs,
        retained_ids=retained,
        header_ok=header_ok,
        coverage_partial=partial,
        capabilities=dict(rep.capabilities_by_tag or tag_tools(rep._tools, rep._policy)),
        tools=rep._tools,
    )


def _adaptive_investigation(rep: ReaReport, client: ReaMcpClient, files: List[Tuple[str, Path]], *, header_ok: bool,
                            budget: Optional[Budget] = None) -> int:
    """Adaptive, evidence-driven investigation on top of the header inspection.

    The loop is: read the collected evidence -> raise the open questions -> plan the eligible tools that can answer them ->
    execute -> fold the results back into the evidence state -> replan. It continues while open questions remain answerable and a
    step keeps producing new evidence; it stops when the questions are exhausted, no eligible capability answers them, a step
    yields nothing new, the same call would repeat, failures pass the retry budget, or the call/time budget is spent. There is no
    investigation-round limit: the configurable call/time/failure budgets are the runaway protection.

    Every call here is OPTIONAL: a refused or failing call is recorded and noted but never changes the stage's verdict, which the
    core header inspection already set."""
    budget = budget or Budget(max_calls=MAX_INVESTIGATION_CALLS, max_seconds=MAX_INVESTIGATION_SECONDS,
                              max_failures=MAX_INVESTIGATION_FAILURES)
    state = _evidence_state(rep, files, header_ok=header_ok)
    seen: Set[Tuple[str, str]] = set()
    made = failures = 0
    stop = "the open questions were exhausted"
    round_number = 0
    while True:
        questions = generate_questions(state)
        rep.questions = [{"qid": q.qid, "kind": q.kind, "target": q.target, "text": q.text} for q in questions]
        plan = filter_plan(plan_questions(state, questions, client.policy, client.tools), client.tools, client.policy)
        plan = [pc for pc in plan if (pc.tool, pc.target) not in seen]  # duplicate-call prevention
        if not plan:
            stop = ("the answerable investigation questions were exhausted; no further eligible capability call could answer the remaining questions" if questions
                    else "the open questions were exhausted")
            break
        if budget.exhausted(made, failures):
            stop = "the investigation resource budget was spent"
            break
        round_number += 1
        rep.note(f"Investigation round {round_number}: {len(plan)} call(s) planned for {len(questions)} open question(s).")
        before = state_signature(state)
        for pc in plan:
            if budget.exhausted(made, failures):
                stop = "the investigation resource budget was spent"
                break
            seen.add((pc.tool, pc.target))
            try:
                call = client.call_tool(pc.tool, pc.arguments, target=pc.target)
            except ReaError as exc:  # refused by the policy / schema check: nothing was sent
                call = ReaToolCall(tool=pc.tool, arguments=pc.arguments, ok=False, target=pc.target, error=f"not sent: {exc}")
            rep.calls.append(call)
            made += 1
            if call.ok:
                observe(state, pc.capability, pc.target, call.output)
            else:
                failures += 1
            rep.investigation.append({"tool": pc.tool, "target": pc.target, "reason": pc.reason, "ok": call.ok,
                                      "capability": pc.capability, "question": pc.question,
                                      "detail": (str(call.error)[:160] if not call.ok else _brief(call.output))})
            rep.note(f"tools/call {pc.tool} [{pc.target}] ({pc.capability}) -> "
                     f"{'ok' if call.ok else 'FAILED (optional): ' + str(call.error)[:120]} ({call.duration_s}s)")
        if budget.exhausted(made, failures):
            stop = "the investigation resource budget was spent"
            break
        if state_signature(state) == before:
            stop = "no further call produced new evidence"
            break
    rep.stop_reason = stop
    rep.budget = {**budget.as_dict(), "calls_made": made, "failures": failures}
    rep.note(f"Adaptive investigation made {made} optional call(s), {failures} failure(s); stopped because {stop}.")
    return made


def _brief(output: str, limit: int = 160) -> str:
    """A one-line summary of a tool output for the investigation log (never the full payload)."""
    text = " ".join(str(output or "").split())
    return text[:limit]


def _retained_ids_by_target(rep: ReaReport) -> Dict[str, str]:
    """Map each inspected file to the retained Evidence id its model-inspection result advertised (empty when none did)."""
    out: Dict[str, str] = {}
    for call in rep.calls:
        if call.tool != MODEL_TOOL or not call.ok:
            continue
        evid = _parent_evidence_id(call)
        if evid:
            out[call.target] = evid
    return out


def make_runner(settings: Optional[Settings] = None, *, command: Optional[Sequence[str]] = None,
                project_dir: Optional[Path] = None, health_cache: Optional[HealthCache] = None
                ) -> Callable[[Any, SafeFetcher], ReaReport]:
    """The callable the pipeline/UI hands to ``run_pipeline(rea=...)``."""
    def runner(info: Any, fetcher: SafeFetcher) -> ReaReport:
        return run_rea_inspection(info, fetcher, settings, command=command, project_dir=project_dir, health_cache=health_cache)
    return runner


def not_invoked(reason: str = "REA inspection is not configured for this run.") -> ReaReport:
    rep = ReaReport()
    rep.note("No REA tool was called.")
    return _finish(rep, NOT_INVOKED, reason)
