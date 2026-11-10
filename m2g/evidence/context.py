"""Shared collection settings: size/count budgets and the per-run context passed to every collector."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Tuple

from ..security import SafeFetcher
from .provenance import Clock, utc_now


@dataclass(frozen=True)
class EvidenceLimits:
    """Resource budgets. Nothing here is task- or architecture-specific."""

    max_hub_bytes: int = 4_000_000  # Hub API response
    max_file_bytes: int = 1_500_000  # one text/metadata file
    max_index_bytes: int = 4_000_000  # *.index.json weight maps
    max_files: int = 60  # text/metadata files fetched per repository
    max_total_bytes: int = 8_000_000  # planned bytes across those files
    max_raw_chars: int = 200_000  # raw text kept per item
    max_listing_entries: int = 20_000
    max_not_fetched_names: int = 500  # names kept per skip reason
    max_weight_files: int = 8  # weight files header-inspected
    max_tensors_listed: int = 2_000  # tensor rows kept per file (patterns always cover all tensors)
    max_name_patterns: int = 500
    max_metadata_entries: int = 50


@dataclass(frozen=True)
class CollectionContext:
    """What every collector needs: the (security-checked) fetcher, the validated repo and pinned revision."""

    fetcher: SafeFetcher
    repo_id: str
    revision: str
    limits: EvidenceLimits = field(default_factory=EvidenceLimits)
    clock: Clock = utc_now
    secrets: Tuple[str, ...] = ()
    provider: str = "huggingface"  # which host serves the file bytes: huggingface | github
