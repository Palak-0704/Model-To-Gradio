"""Error visibility for the platform UI (standard library only, so it is testable without Gradio).

Why this exists: Gradio shows a bare "Error" toast when a callback raises and ``show_error`` is off, and the traceback is
then only in the console (or nowhere, if logging is not configured). ``guarded`` wraps a callback so that

  * the full traceback is logged to the console, with secrets and Google/Hugging Face/GitHub tokens redacted;
  * the person sees a short, redacted ``Type: message`` instead of a bare "Error" (raised as ``error_cls``, which the UI
    passes as ``gr.Error``), or, for background polling callbacks, a harmless fallback value.

Nothing here hides a failure: every exception is logged, and none is converted into a fake success.
"""
from __future__ import annotations

import functools
import logging
import os
import re
import sys
import traceback
from typing import Any, Callable, Iterable, Optional, Sequence, Tuple

log = logging.getLogger("m2g.ui")

REDACTED = "[redacted]"
_PATTERNS: Tuple["re.Pattern[str]", ...] = (
    re.compile(r"hf_[A-Za-z0-9]{20,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"ya29\.[\w.-]+"),  # Google access token
    re.compile(r"1//[\w.-]{20,}"),  # Google refresh token
    re.compile(r"4/[\w.-]{20,}"),  # Google one-time authorization code
    re.compile(r"(?i)\b(bearer|authorization)\b\s*[:=]?\s*[A-Za-z0-9._~+/=-]{12,}"),
)
_KEY_VALUE = re.compile(r"(?i)\b(password|passwd|secret|token|api[_-]?key|code)=([^\s&'\"]+)")
_SECRET_ENV_SUFFIXES = ("_TOKEN", "_SECRET", "_API_KEY", "_PASSWORD", "_SECRET_KEY", "_ACCESS_KEY")
_SECRET_ENV_EXACT = {"HF_TOKEN", "HUGGING_FACE_HUB_TOKEN", "GITHUB_TOKEN", "GH_TOKEN"}


def env_secrets(environ: Optional[dict] = None) -> Tuple[str, ...]:
    """Values of secret-looking environment variables (never their names' contents elsewhere)."""
    env = os.environ if environ is None else environ
    out = []
    for name, value in env.items():
        up = name.upper()
        if (up in _SECRET_ENV_EXACT or up.endswith(_SECRET_ENV_SUFFIXES)) and value and len(value) >= 8:
            out.append(value)
    return tuple(out)


def scrub(text: Any, extra_secrets: Iterable[str] = ()) -> str:
    """Redact explicit secrets, secret-looking environment values and token-shaped strings."""
    s = str(text if text is not None else "")
    for secret in tuple(extra_secrets) + env_secrets():
        if secret and len(secret) >= 4:
            s = s.replace(secret, REDACTED)
    for pattern in _PATTERNS:
        s = pattern.sub(REDACTED, s)
    return _KEY_VALUE.sub(lambda m: f"{m.group(1)}={REDACTED}", s)


def format_exception(exc: BaseException, extra_secrets: Iterable[str] = ()) -> str:
    """The complete traceback of ``exc`` as text, redacted."""
    text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    return scrub(text, extra_secrets)


def short_message(exc: BaseException, name: str, limit: int = 300, extra_secrets: Iterable[str] = ()) -> str:
    detail = scrub(str(exc), extra_secrets).strip().replace("\n", " ")
    if len(detail) > limit:
        detail = detail[: limit - 1] + "…"
    head = f"{name} failed: {type(exc).__name__}"
    return f"{head}: {detail} (full traceback is in the console window)" if detail else \
        f"{head} (full traceback is in the console window)"


def guarded(name: str, *, error_cls: type = RuntimeError, fallback: Optional[Callable[[], Any]] = None,
            secrets: Callable[[], Sequence[str]] = lambda: ()) -> Callable[[Callable], Callable]:
    """Decorator for UI callbacks.

    On an ordinary ``Exception``: log the redacted traceback, then either return ``fallback()`` (for background polling
    such as a timer, logged once per distinct failure so the console is not flooded) or raise ``error_cls`` with a
    short redacted message. An exception that is already an ``error_cls`` is a deliberate user-facing error and passes
    through untouched. ``BaseException`` (KeyboardInterrupt, SystemExit) is never intercepted.
    """

    def decorate(fn: Callable) -> Callable:
        last_signature = {"value": None}

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return fn(*args, **kwargs)
            except Exception as exc:
                if isinstance(exc, error_cls) and error_cls is not RuntimeError:
                    raise
                try:
                    extra = tuple(secrets())
                except Exception:
                    extra = ()
                signature = (type(exc).__name__, scrub(str(exc), extra))
                if fallback is None or signature != last_signature["value"]:
                    log.error("UI callback '%s' failed:\n%s", name, format_exception(exc, extra))
                last_signature["value"] = signature
                if fallback is not None:
                    return fallback()
                raise error_cls(short_message(exc, name, extra_secrets=extra)) from None

        return wrapper

    return decorate


_configured = {"done": False}


def configure_logging(level: int = logging.INFO, stream: Any = None) -> None:
    """Make sure ``m2g.*`` log records reach the console (idempotent). Honors ``M2G_LOG_LEVEL`` when set."""
    if _configured["done"]:
        return
    name = (os.environ.get("M2G_LOG_LEVEL") or "").strip().upper()
    if name and isinstance(logging.getLevelName(name), int):
        level = logging.getLevelName(name)
    logger = logging.getLogger("m2g")
    logger.setLevel(level)
    if not any(getattr(h, "_m2g_console", False) for h in logger.handlers):
        handler = logging.StreamHandler(stream or sys.stderr)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        handler._m2g_console = True  # type: ignore[attr-defined]
        logger.addHandler(handler)
    _configured["done"] = True
