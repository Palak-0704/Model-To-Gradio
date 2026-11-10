"""One safe GET through ``SafeFetcher`` whose failure is returned as an evidence status instead of raised."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..security import SafeFetcher
from .guards import classify_error
from .schema import COLLECTED


@dataclass(frozen=True)
class FetchOutcome:
    status: str
    body: Optional[bytes] = None
    http_status: Optional[int] = None
    error: Optional[str] = None


def fetch_bytes(fetcher: SafeFetcher, url: str, max_bytes: int) -> FetchOutcome:
    """GET ``url`` (size-capped). Never raises: network, HTTP and security failures become a classified outcome."""
    try:
        res = fetcher.fetch(url, None, max_bytes=max_bytes)
    except Exception as exc:  # deliberately broad: one failing source must not abort evidence collection
        status, message = classify_error(exc)
        return FetchOutcome(status, error=message, http_status=getattr(exc, "status", None))
    return FetchOutcome(COLLECTED, res.body, res.status)
