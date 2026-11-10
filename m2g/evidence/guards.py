"""Centralised security checks for evidence collection. They wrap the project's existing primitives (``m2g.security``); the
network itself is only ever reached through ``SafeFetcher`` (host allow-list, public-IP check, size cap)."""
from __future__ import annotations

import re
import urllib.parse
from typing import Optional, Sequence, Tuple

from ..security import (
    FetchError,
    HTTPStatusError,
    NetworkError,
    ResponseTooLarge,
    SecurityError,
    redact_secrets,
    validate_repo_id,
    validate_revision,
)
from .schema import INACCESSIBLE, MISSING, SKIPPED

_SHA = re.compile(r"[0-9a-f]{7,64}")
_MAX_NAME = 300


def safe_repo_path(name: str) -> str:
    """A repository-relative file name that cannot traverse, be absolute or smuggle URL syntax. Raises SecurityError."""
    parts = name.split("/") if isinstance(name, str) else []
    if (not parts or not name or len(name) > _MAX_NAME or name.startswith("/") or any(p in ("", ".", "..") for p in parts)
            or any(ord(c) < 32 or c in "\\?#%" for c in name)):
        raise SecurityError("Unsafe repository file name.")
    return name


def validate_reference(repo_id: str, revision: Optional[str]) -> None:
    """Repository id and revision must pass the platform's validators. Raises SecurityError."""
    validate_repo_id(repo_id)
    if revision:
        validate_revision(revision)


def resolved_sha(value: object) -> Optional[str]:
    """The commit sha from an API response, only if it looks like one."""
    return value if isinstance(value, str) and _SHA.fullmatch(value) else None


def api_url(repo_id: str, revision: Optional[str] = None) -> str:
    base = f"https://huggingface.co/api/models/{repo_id}"
    return (f"{base}/revision/{revision}" if revision else base) + "?blobs=true"


def file_url(repo_id: str, revision: str, name: str, provider: str = "huggingface") -> str:
    """The URL of one repository file, built from validated, path-safe components only.

    Hugging Face resolves through ``/{repo}/resolve/{rev}/{file}``; GitHub serves raw file bytes from
    ``raw.githubusercontent.com/{repo}/{rev}/{file}``. An unknown provider is refused rather than guessed."""
    safe = urllib.parse.quote(safe_repo_path(name))
    if provider == "github":
        validate_repo_id(repo_id)
        validate_revision(revision)
        return f"https://raw.githubusercontent.com/{repo_id}/{revision}/{safe}"
    if provider in ("huggingface", "", None):
        return f"https://huggingface.co/{repo_id}/resolve/{revision}/{safe}"
    raise SecurityError(f"Unsupported file provider {provider!r}.")


def sanitize(text: str, secrets: Sequence[str] = ()) -> Tuple[str, bool]:
    """Redact credentials. Returns (clean_text, whether anything was redacted)."""
    clean = redact_secrets(text, secrets)
    return clean, clean != text


def cap_text(text: str, limit: int) -> Tuple[str, bool]:
    return (text[:limit], True) if len(text) > limit else (text, False)


def classify_error(exc: BaseException) -> Tuple[str, str]:
    """Map a fetch/security failure to (evidence status, safe message)."""
    if isinstance(exc, ResponseTooLarge):
        status, msg = SKIPPED, "response exceeded the size cap"
    elif isinstance(exc, HTTPStatusError):
        if exc.status in (404, 410):
            status, msg = MISSING, f"HTTP {exc.status}: not found"
        elif exc.status in (401, 403):
            status, msg = INACCESSIBLE, f"HTTP {exc.status}: access denied (gated or private repository; HF_TOKEN may be needed)"
        else:
            status, msg = INACCESSIBLE, f"HTTP {exc.status}"
    elif isinstance(exc, SecurityError):
        status, msg = SKIPPED, f"blocked by a security rule: {exc}"
    elif isinstance(exc, NetworkError):
        status, msg = INACCESSIBLE, f"network problem: {exc}"
    elif isinstance(exc, FetchError):
        status, msg = INACCESSIBLE, f"fetch failed: {exc}"
    else:
        status, msg = INACCESSIBLE, f"unexpected {type(exc).__name__}: {exc}"
    return status, redact_secrets(msg)[:300]
