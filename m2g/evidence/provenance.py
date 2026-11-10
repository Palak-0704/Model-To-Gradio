"""Provenance: where an evidence item came from and how it was obtained."""
from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

# sources
SOURCE_HUB_API = "huggingface_hub_api"
SOURCE_REPO_FILE = "huggingface_repo_file"
SOURCE_SAFETENSORS = "safetensors_header"
SOURCE_REA = "rea_report"
SOURCE_REPO_INFO = "repo_info"
SOURCE_PLATFORM = "platform"
# methods
METHOD_GET = "http_get"
METHOD_RANGE = "http_range"
METHOD_DERIVED = "derived"
METHOD_REPORT = "rea_report"

Clock = Callable[[], str]


def utc_now() -> str:
    """Current UTC time as an ISO-8601 string (second precision)."""
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class Provenance:
    """Origin of one item: ``source`` (system), ``location`` (redacted URL/id), ``method``, pinned ``revision``, time, extras."""

    source: str
    location: str = ""
    method: str = METHOD_GET
    revision: Optional[str] = None
    fetched_at: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)


def platform_provenance(clock: Clock, what: str = "") -> Provenance:
    """Provenance for facts the platform itself produced (selection decisions, inventories), not repository content."""
    return Provenance(SOURCE_PLATFORM, what, METHOD_DERIVED, None, clock())
