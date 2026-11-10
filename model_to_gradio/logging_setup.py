"""Logging with automatic redaction of credentials."""

from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

_SECRET_PATTERNS = [
    re.compile(r"hf_[A-Za-z0-9]{16,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{16,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"(?i)(authorization:\s*bearer\s+)[^\s]+"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]{16,}"),
]


def redact(text: str) -> str:
    """Replace anything that looks like an access token."""
    for pattern in _SECRET_PATTERNS:
        if pattern.groups:
            text = pattern.sub(lambda m: m.group(1) + "[REDACTED]", text)
        else:
            text = pattern.sub("[REDACTED]", text)
    return text


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # pragma: no cover - defensive
            return True
        record.msg = redact(message)
        record.args = ()
        return True


def configure_logging(log_dir: Optional[Path] = None, level: int = logging.INFO) -> logging.Logger:
    """Configure the 'model_to_gradio' logger (console plus optional rotating file)."""
    logger = logging.getLogger("model_to_gradio")
    logger.setLevel(level)
    if getattr(logger, "_m2g_configured", False):
        return logger
    formatter = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    console.addFilter(RedactingFilter())
    logger.addHandler(console)
    if log_dir is not None:
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            handler = RotatingFileHandler(
                log_dir / "m2g.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"
            )
            handler.setFormatter(formatter)
            handler.addFilter(RedactingFilter())
            logger.addHandler(handler)
        except OSError as exc:  # logging must never stop the app
            logger.warning("File logging disabled: %s", exc)
    logger.propagate = False
    logger._m2g_configured = True  # type: ignore[attr-defined]
    return logger
