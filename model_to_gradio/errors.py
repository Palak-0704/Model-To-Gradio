"""Exception hierarchy. Every error carries a user-facing message and an optional hint."""

from __future__ import annotations

from typing import Any, Dict, Optional


class M2GError(Exception):
    """Base class for all expected, user-presentable errors."""

    code = "error"
    http_status = 400

    def __init__(
        self,
        message: str,
        *,
        hint: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.details = details or {}

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"code": self.code, "message": self.message}
        if self.hint:
            payload["hint"] = self.hint
        if self.details:
            payload["details"] = self.details
        return payload


class InvalidURLError(M2GError):
    code = "invalid_url"


class UnsupportedSourceError(M2GError):
    code = "unsupported_source"


class SourceNotFoundError(M2GError):
    code = "source_not_found"
    http_status = 404


class SourceAccessError(M2GError):
    code = "source_access_denied"
    http_status = 403


class RateLimitError(M2GError):
    code = "rate_limited"
    http_status = 429


class RemoteFetchError(M2GError):
    code = "remote_fetch_failed"
    http_status = 502


class AnalysisError(M2GError):
    code = "analysis_failed"
    http_status = 500


class GenerationBlockedError(M2GError):
    """The source was analysed but an app must not (or cannot) be generated for it."""

    code = "generation_blocked"
    http_status = 422


class ValidationFailedError(M2GError):
    """Generated code or notebook failed the safety/structure checks."""

    code = "validation_failed"
    http_status = 500


class WorkspaceError(M2GError):
    code = "workspace_error"
    http_status = 400
