"""Security primitives: URL validation, SSRF-safe fetching, secret redaction, safe identifiers.

Nothing in this module (or the rest of the platform) imports, executes or evaluates code that
comes from an inspected repository. Repositories are only ever read as bytes/text over HTTPS.
"""
from __future__ import annotations

import http.client
import ipaddress
import json
import re
import socket
import ssl
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .config import (
    GITHUB_TOKEN_HOSTS,
    HF_TOKEN_HOSTS,
    REDIRECT_HOST_SUFFIXES,
    TRUSTED_HOSTS,
    Settings,
)

# --------------------------------------------------------------------------- errors


class SecurityError(ValueError):
    """Input or network target rejected by a security rule."""


class FetchError(RuntimeError):
    """Base class for network retrieval problems."""


class NetworkError(FetchError):
    """DNS/connect/TLS/timeout failure."""


# The only response headers an HTTPStatusError keeps: they tell a rate limit from a denied request. Nothing else (cookies,
# authentication or tracking headers) is ever stored on the exception.
_ERROR_HEADERS = ("retry-after", "x-ratelimit-remaining", "x-ratelimit-reset", "x-ratelimit-resource")


class HTTPStatusError(FetchError):
    def __init__(self, status: int, url: str, message: str = "", headers: Optional[Dict[str, str]] = None):
        self.status = status
        self.url = redact_secrets(url)
        self.headers: Dict[str, str] = {str(k).lower(): str(v)[:100] for k, v in (headers or {}).items() if str(k).lower() in _ERROR_HEADERS}
        super().__init__(f"HTTP {status} for {self.url}" + (f": {redact_secrets(message)}" if message else ""))


class ResponseTooLarge(FetchError):
    pass


# --------------------------------------------------------------------------- secrets

_SECRET_PATTERNS = [
    re.compile(r"hf_[A-Za-z0-9]{20,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{12,}"),
]


def redact_secrets(text: str, extra: Sequence[str] = ()) -> str:
    out = str(text)
    for secret in extra:
        if secret and len(secret) >= 6:
            out = out.replace(secret, "[REDACTED]")
    for pat in _SECRET_PATTERNS:
        out = pat.sub("[REDACTED]", out)
    return out


def contains_secret(text: str, extra: Sequence[str] = ()) -> bool:
    return redact_secrets(text, extra) != text


def assert_no_secrets(text: str, settings: Optional[Settings] = None) -> None:
    extra = [t for t in ((settings.hf_token, settings.github_token) if settings else ()) if t]
    if contains_secret(text, extra):
        raise SecurityError("Generated content contains something that looks like a credential; refusing to write it.")


# --------------------------------------------------------------------------- source URL parsing


@dataclass(frozen=True)
class SourceRef:
    kind: str  # "huggingface" | "github"
    repo_id: str  # "owner/name" (HF legacy models may be just "name")
    owner: Optional[str]
    name: str
    revision: Optional[str]
    canonical_url: str


_MAX_URL_LEN = 300
_HF_HOSTS = {"huggingface.co", "www.huggingface.co"}
_GH_HOSTS = {"github.com", "www.github.com"}
_HF_RESERVED = {
    "datasets", "spaces", "docs", "blog", "models", "papers", "collections", "organizations", "settings",
    "api", "join", "login", "new", "pricing", "tasks", "chat", "learn", "posts", "search", "welcome",
    "enterprise", "support", "terms-of-service", "privacy", "huggingface", "sponsors",
}
_GH_RESERVED = {
    "orgs", "settings", "marketplace", "features", "topics", "sponsors", "login", "about", "pricing",
    "explore", "search", "notifications", "pulls", "issues", "new", "apps", "collections", "trending",
    "enterprise", "readme", "security", "site", "customer-stories", "join", "organizations",
}
_HF_EXTRA = {"tree", "blob", "resolve", "raw", "commit", "discussions", "viewer"}
_GH_EXTRA = {"tree", "blob", "issues", "pulls", "releases", "wiki", "commits", "actions", "discussions"}
_HF_SEG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")
_GH_OWNER = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
_GH_REPO = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
_REV = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")


def validate_revision(rev: str) -> str:
    if not isinstance(rev, str) or not _REV.match(rev) or ".." in rev:
        raise SecurityError("Invalid revision/branch name.")
    return rev


def parse_source_url(raw: str) -> SourceRef:
    """Strictly parse a Hugging Face model URL or GitHub repository URL. Raises SecurityError."""
    if not isinstance(raw, str):
        raise SecurityError("URL must be a string.")
    s = raw.strip()
    if not s:
        raise SecurityError("URL is empty.")
    if len(s) > _MAX_URL_LEN:
        raise SecurityError("URL is too long.")
    if re.search(r"[\x00-\x20\x7f\\]", s):
        raise SecurityError("URL contains whitespace, control characters or backslashes.")
    try:
        parts = urllib.parse.urlsplit(s)
        port = parts.port
    except ValueError:
        raise SecurityError("URL is malformed.")
    if parts.scheme != "https":
        raise SecurityError("Only https:// URLs are accepted.")
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise SecurityError("URLs with embedded credentials are not accepted.")
    if port not in (None, 443):
        raise SecurityError("Non-standard ports are not accepted.")
    host = (parts.hostname or "").lower()
    if not host or not host.isascii():
        raise SecurityError("Host must be plain ASCII.")
    path = parts.path
    if "%" in path or "//" in path.strip("/") or ".." in path.split("/"):
        raise SecurityError("Path contains encoded or traversal sequences.")
    segs = [x for x in path.split("/") if x]
    if host in _HF_HOSTS:
        return _parse_hf(segs)
    if host in _GH_HOSTS:
        return _parse_github(segs)
    raise SecurityError("Only huggingface.co and github.com URLs are supported.")


def _parse_hf(segs: List[str]) -> SourceRef:
    if not segs or segs[0].lower() in _HF_RESERVED:
        raise SecurityError("Not a Hugging Face model repository URL (datasets/spaces/other pages are not supported).")
    if len(segs) == 1:
        owner, name = None, segs[0]
        rest: List[str] = []
    else:
        owner, name, rest = segs[0], segs[1], segs[2:]
    for seg in [x for x in (owner, name) if x]:
        if not _HF_SEG.match(seg):
            raise SecurityError("Invalid Hugging Face repository name.")
    revision = None
    if rest:
        if rest[0] not in _HF_EXTRA:
            raise SecurityError("Unsupported Hugging Face URL path.")
        if rest[0] in {"tree", "blob", "resolve", "raw", "commit"} and len(rest) > 1:
            revision = validate_revision(rest[1])
    repo_id = f"{owner}/{name}" if owner else name
    return SourceRef("huggingface", repo_id, owner, name, revision, f"https://huggingface.co/{repo_id}")


def _parse_github(segs: List[str]) -> SourceRef:
    if len(segs) < 2 or segs[0].lower() in _GH_RESERVED:
        raise SecurityError("Not a GitHub repository URL (expected github.com/<owner>/<repo>).")
    owner, name, rest = segs[0], segs[1], segs[2:]
    if name.endswith(".git"):
        name = name[:-4]
    if not _GH_OWNER.match(owner) or not _GH_REPO.match(name) or name in {".", ".."}:
        raise SecurityError("Invalid GitHub owner/repository name.")
    revision = None
    if rest:
        if rest[0] not in _GH_EXTRA:
            raise SecurityError("Unsupported GitHub URL path.")
        if rest[0] in {"tree", "blob"} and len(rest) > 1:
            revision = validate_revision(rest[1])  # first path segment only (branches with '/' unsupported)
    return SourceRef("github", f"{owner}/{name}", owner, name, revision, f"https://github.com/{owner}/{name}")


# --------------------------------------------------------------------------- outbound URL / SSRF guard

Resolver = Callable[[str, int], List[str]]


def default_resolver(host: str, port: int) -> List[str]:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError, OSError) as exc:
        raise NetworkError(f"DNS resolution failed for {host}: {exc}")
    return sorted({i[4][0] for i in infos})


def is_public_ip(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str.split("%")[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        elif ip.sixtofour is not None:
            return is_public_ip(str(ip.sixtofour))
        elif ip.teredo is not None:
            return False
    return bool(ip.is_global and not ip.is_multicast)


def _host_allowed(host: str) -> bool:
    return host in TRUSTED_HOSTS or any(host.endswith(sfx) for sfx in REDIRECT_HOST_SUFFIXES)


def validate_outbound_url(url: str, resolver: Resolver = default_resolver) -> Tuple[urllib.parse.SplitResult, List[str]]:
    """Check scheme/host allow-list and that every resolved address is public. Returns (parts, ips)."""
    try:
        parts = urllib.parse.urlsplit(url)
        port = parts.port
    except ValueError:
        raise SecurityError("Outbound URL is malformed.")
    if parts.scheme != "https":
        raise SecurityError("Outbound requests must use https.")
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise SecurityError("Outbound URL contains credentials.")
    if port not in (None, 443):
        raise SecurityError("Outbound URL uses a non-standard port.")
    host = (parts.hostname or "").lower()
    if not host or not host.isascii():
        raise SecurityError("Outbound host is invalid.")
    try:
        ipaddress.ip_address(host)
        raise SecurityError("IP-literal hosts are not allowed.")
    except ValueError:
        pass
    if not _host_allowed(host):
        raise SecurityError(f"Host '{host}' is not on the allow-list.")
    ips = resolver(host, 443)
    if not ips:
        raise NetworkError(f"No addresses for {host}.")
    bad = [ip for ip in ips if not is_public_ip(ip)]
    if bad:
        raise SecurityError(f"Host '{host}' resolves to a non-public address; request blocked.")
    return parts, ips


# --------------------------------------------------------------------------- fetcher

Transport = Callable[[urllib.parse.SplitResult, List[str], Dict[str, str], float, int], Tuple[int, Dict[str, str], bytes]]

_REDIRECTS = {301, 302, 303, 307, 308}
_ALLOWED_EXTRA_HEADERS = {"accept", "x-github-api-version", "range"}
_RANGE_RE = re.compile(r"bytes=\d{1,12}-\d{1,12}")  # one explicit byte range only (used for header-only reads of weight files)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """Connects to a pre-validated IP while keeping SNI/cert verification for the hostname (anti DNS-rebinding)."""

    def __init__(self, host: str, ip: str, **kw):
        super().__init__(host, **kw)
        self._pinned_ip = ip

    def connect(self):  # pragma: no cover - needs real network
        sock = socket.create_connection((self._pinned_ip, self.port or 443), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def _read_capped(resp, max_bytes: int) -> bytes:  # pragma: no cover - needs real network
    chunks, total = [], 0
    while True:
        chunk = resp.read(65536)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise ResponseTooLarge(f"Response exceeded {max_bytes} bytes.")
        chunks.append(chunk)
    return b"".join(chunks)


def _default_transport(parts, ips, headers, timeout, max_bytes):  # pragma: no cover - needs real network
    path = (parts.path or "/") + (("?" + parts.query) if parts.query else "")
    last: Optional[Exception] = None
    for ip in ips:
        conn = _PinnedHTTPSConnection(parts.hostname, ip, timeout=timeout, context=ssl.create_default_context())
        try:
            conn.request("GET", path, headers=headers)
            resp = conn.getresponse()
            hdrs = {k.lower(): v for k, v in resp.getheaders()}
            body = _read_capped(resp, max_bytes) if 200 <= resp.status < 300 else b""
            return resp.status, hdrs, body
        except (OSError, http.client.HTTPException) as exc:  # includes ssl.SSLError, timeouts
            last = exc
        finally:
            conn.close()
    raise NetworkError(f"Connection to {parts.hostname} failed: {type(last).__name__}: {last}")


@dataclass
class FetchResult:
    status: int
    headers: Dict[str, str]
    body: bytes
    final_url: str


class SafeFetcher:
    """GET-only HTTPS client: host allow-list, public-IP check, manual validated redirects, size cap,
    credentials only for their own API hosts, and a per-session circuit breaker for dead hosts."""

    def __init__(self, settings: Optional[Settings] = None, resolver: Resolver = default_resolver,
                 transport: Transport = _default_transport):
        self.settings = settings or Settings()
        self._resolver = resolver
        self._transport = transport
        self._dead_hosts: Dict[str, str] = {}
        self.request_log: List[str] = []  # redacted URLs actually requested (useful for tests/diagnostics)

    # -- headers
    def _headers_for(self, host: str, extra: Optional[Dict[str, str]]) -> Dict[str, str]:
        h = {"User-Agent": "model-to-gradio/0.1 (+inspection)", "Accept": "*/*", "Accept-Encoding": "identity"}
        for k, v in (extra or {}).items():
            if k.lower() in _ALLOWED_EXTRA_HEADERS:
                if k.lower() == "range" and not _RANGE_RE.fullmatch(str(v)):
                    continue
                h[k] = v
        s = self.settings
        if s.hf_token and host in HF_TOKEN_HOSTS:
            h["Authorization"] = f"Bearer {s.hf_token}"
        elif s.github_token and host in GITHUB_TOKEN_HOSTS:
            h["Authorization"] = f"Bearer {s.github_token}"
        return h

    # -- core
    def fetch(self, url: str, extra_headers: Optional[Dict[str, str]] = None, max_bytes: Optional[int] = None) -> FetchResult:
        limit = max_bytes or self.settings.max_response_bytes
        current = url
        for _hop in range(self.settings.max_redirects + 1):
            parts, ips = validate_outbound_url(current, self._resolver)
            host = parts.hostname.lower()
            if host in self._dead_hosts:
                raise NetworkError(f"{host} was unreachable earlier in this session ({self._dead_hosts[host]}); not retrying.")
            self.request_log.append(redact_secrets(current))
            try:
                status, hdrs, body = self._transport(parts, ips, self._headers_for(host, extra_headers),
                                                     self.settings.http_timeout, limit)
            except NetworkError as exc:
                self._dead_hosts[host] = redact_secrets(str(exc))[:120]
                raise
            if status in _REDIRECTS:
                loc = hdrs.get("location")
                if not loc:
                    raise HTTPStatusError(status, current, "redirect without Location")
                current = urllib.parse.urljoin(current, loc)  # next loop iteration re-validates everything
                continue
            if not 200 <= status < 300:
                raise HTTPStatusError(status, current, headers={str(k).lower(): v for k, v in (hdrs or {}).items()})
            return FetchResult(status, hdrs, body, redact_secrets(current))
        raise SecurityError("Too many redirects.")

    def get_text(self, url: str, extra_headers: Optional[Dict[str, str]] = None, max_bytes: Optional[int] = None) -> str:
        return self.fetch(url, extra_headers, max_bytes).body.decode("utf-8", errors="replace")

    def get_json(self, url: str, extra_headers: Optional[Dict[str, str]] = None, max_bytes: Optional[int] = None):
        text = self.get_text(url, extra_headers, max_bytes)
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise FetchError(f"Invalid JSON from {redact_secrets(url)}: {exc.msg}")


# --------------------------------------------------------------------------- safe identifiers / paths

_PIP_REQ = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*(\[[A-Za-z0-9,_-]+\])?((>=|<=|==|~=|>|<)[A-Za-z0-9.*+!_-]+(,(>=|<=|==|~=|>|<)[A-Za-z0-9.*+!_-]+)*)?$")


def validate_pip_requirement(req: str) -> str:
    if not _PIP_REQ.match(req):
        raise SecurityError(f"Unsafe pip requirement rejected: {req!r}")
    return req


def validate_repo_id(repo_id: str) -> str:
    if not re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}(/[A-Za-z0-9][A-Za-z0-9._-]{0,95})?$", repo_id or ""):
        raise SecurityError("Invalid repo id.")
    return repo_id


def safe_slug(text: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("._")[:80]
    return slug or "model"


def ensure_within(base: Path, target: Path) -> Path:
    base_r, target_r = Path(base).resolve(), Path(target).resolve()
    if base_r != target_r and base_r not in target_r.parents:
        raise SecurityError("Refusing to write outside the output directory.")
    return target_r
