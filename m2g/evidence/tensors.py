"""Weight-file evidence. Header-only: for safetensors, two HTTP range requests (8 length bytes, then the JSON header) via the
existing ``fetch_safetensors_header`` / ``summarize_header``; tensor data is never requested. Formats are a registry keyed by
file suffix, so a new format is added with ``register_tensor_inspector`` and no other change."""
from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..inspector import WEIGHT_EXTS
from ..rea_client import ReaError, fetch_safetensors_header, summarize_header
from ..security import FetchError, SecurityError, redact_secrets
from .context import CollectionContext
from .guards import classify_error, file_url
from .provenance import METHOD_RANGE, SOURCE_SAFETENSORS, Provenance, platform_provenance
from .schema import COLLECTED, INVALID, MISSING, SKIPPED, EvidenceItem

TensorInspector = Callable[[CollectionContext, str, Optional[int]], EvidenceItem]
_DIGITS = re.compile(r"\d+")
_SUFFIXES = tuple(sorted(WEIGHT_EXTS, key=len, reverse=True))


def _clean(value: Any, limit: int) -> str:
    return redact_secrets(str(value))[:limit]


def _tensor_table(header: Dict[str, Any], ctx: CollectionContext) -> Dict[str, Any]:
    """Names/dtypes/shapes (capped list) plus index-normalised name patterns that always cover every tensor."""
    lim = ctx.limits
    tensors: List[Dict[str, Any]] = []
    patterns: Dict[str, Dict[str, Any]] = {}
    uncovered = 0
    for name, spec in header.items():
        if name == "__metadata__":
            continue
        clean = _clean(name, 300)
        if len(tensors) < lim.max_tensors_listed:
            tensors.append({"name": clean, "dtype": spec["dtype"], "shape": spec["shape"]})
        pat = _DIGITS.sub("{n}", clean)
        entry = patterns.get(pat)
        if entry is not None:
            entry["count"] += 1
        elif len(patterns) < lim.max_name_patterns:
            patterns[pat] = {"pattern": pat, "count": 1, "dtype": spec["dtype"], "example_shape": spec["shape"]}
        else:
            uncovered += 1
    total = sum(1 for k in header if k != "__metadata__")
    return {"tensors": tensors, "tensors_truncated": total > len(tensors), "name_patterns": list(patterns.values()),
            "name_patterns_truncated": uncovered > 0, "tensors_not_in_patterns": uncovered}


def _metadata(header: Dict[str, Any], limit: int) -> Dict[str, str]:
    meta = header.get("__metadata__")
    if not isinstance(meta, dict):
        return {}
    return {_clean(k, 100): _clean(v, 1000) for k, v in list(meta.items())[:limit]}


def inspect_safetensors(ctx: CollectionContext, name: str, listed_size: Optional[int]) -> EvidenceItem:
    """Header-only evidence for one .safetensors file. Malformed header -> invalid; unreachable -> inaccessible."""
    key = f"tensors:file:{name}"
    stats: Dict[str, int] = {}
    url = ""
    details: Dict[str, Any] = {"header_only": True, "listed_size": listed_size}
    try:
        url = file_url(ctx.repo_id, ctx.revision, name)  # rejects unsafe names before any request
        remote = fetch_safetensors_header(ctx.fetcher, ctx.repo_id, ctx.revision, name, expected_size=listed_size, stats=stats)
        summary = summarize_header(remote.blob, remote.total_size)
        header = json.loads(remote.blob[8:].decode("utf-8"))
    except ReaError as exc:
        status, message = INVALID, f"safetensors header rejected: {redact_secrets(str(exc))[:250]}"
    except (FetchError, SecurityError) as exc:
        status, message = classify_error(exc)
    else:
        details.update(requests=stats.get("requests", 0), bytes_received=stats.get("bytes", 0), file_size=remote.total_size)
        data = {"format": "safetensors", "file_size": remote.total_size, **summary, "metadata": _metadata(header, ctx.limits.max_metadata_entries),
                **_tensor_table(header, ctx)}
        return EvidenceItem(key, "tensor-header", COLLECTED, Provenance(SOURCE_SAFETENSORS, url, METHOD_RANGE, ctx.revision, ctx.clock(), details),
                            data=data, size_bytes=8 + summary["header_bytes"])
    details.update(requests=stats.get("requests", 0), bytes_received=stats.get("bytes", 0))
    return EvidenceItem(key, "tensor-header", status, Provenance(SOURCE_SAFETENSORS, url, METHOD_RANGE, ctx.revision, ctx.clock(), details),
                        error=message)


TENSOR_INSPECTORS: Dict[str, TensorInspector] = {".safetensors": inspect_safetensors}


def register_tensor_inspector(suffix: str, inspector: TensorInspector) -> None:
    """Add header-only support for another weight format (e.g. '.gguf')."""
    TENSOR_INSPECTORS[suffix.lower()] = inspector


def _suffix(name: str) -> Optional[str]:
    low = name.lower()
    return next((s for s in _SUFFIXES if low.endswith(s)), None)


def _group(listing: List[Tuple[str, Optional[int]]]) -> Dict[str, List[Tuple[str, Optional[int]]]]:
    groups: Dict[str, List[Tuple[str, Optional[int]]]] = {}
    for name, size in sorted(listing, key=lambda e: (e[0].count("/"), e[0])):
        suffix = _suffix(name)
        if suffix:
            groups.setdefault(suffix, []).append((name, size))
    return groups


def _names_item(ctx: CollectionContext, key: str, reason: str, files: List[Tuple[str, Optional[int]]]) -> EvidenceItem:
    shown = [{"name": n[:300], "size": s} for n, s in files[:ctx.limits.max_not_fetched_names]]
    data = {"count": len(files), "total_bytes": sum(s for _, s in files if s), "files": shown, "files_truncated": len(files) > len(shown)}
    return EvidenceItem(key, "tensor-header", SKIPPED, platform_provenance(ctx.clock, "weight selection"), data=data, untrusted=False,
                        error=reason)


def collect_tensors(ctx: CollectionContext, listing: List[Tuple[str, Optional[int]]], inspect: bool = True) -> List[EvidenceItem]:
    """Header evidence for weight files, by registered format. Formats without an inspector and files beyond the budget are
    recorded as ``skipped`` items (names and sizes kept), never silently dropped. With ``inspect=False`` no weight URL is
    requested at all: every weight file is recorded as skipped."""
    groups = _group(listing)
    if not groups:
        return [EvidenceItem("tensors:none", "tensor-header", MISSING, platform_provenance(ctx.clock, "weight selection"),
                             untrusted=False, error="the file listing contains no weight files")]
    items: List[EvidenceItem] = []
    for suffix, files in groups.items():
        if not inspect:
            items.append(_names_item(ctx, f"tensors:not_inspected:{suffix}",
                                     f"header inspection of '{suffix}' files was not requested for this collection; weights are never downloaded", files))
            continue
        inspector = TENSOR_INSPECTORS.get(suffix)
        if inspector is None:
            items.append(_names_item(ctx, f"tensors:unsupported:{suffix}",
                                     f"no header-only inspector is registered for '{suffix}' files; weights are never downloaded", files))
            continue
        budget = ctx.limits.max_weight_files
        items.extend(inspector(ctx, n, s) for n, s in files[:budget])
        if len(files) > budget:
            items.append(_names_item(ctx, f"tensors:not_inspected:{suffix}",
                                     f"only the first {budget} '{suffix}' files are header-inspected", files[budget:]))
    return items
