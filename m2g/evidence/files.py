"""Repository text/metadata files: format-based selection, bounded fetching, and one evidence item per file."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from ..security import SecurityError
from .context import CollectionContext, EvidenceLimits
from .fetch import fetch_bytes
from .guards import cap_text, file_url, safe_repo_path, sanitize
from .parsing import kind_for, parse_text
from .provenance import METHOD_GET, SOURCE_REPO_FILE, Provenance, platform_provenance
from .schema import COLLECTED, INVALID, SKIPPED, EvidenceItem

R_UNSAFE = "unsafe file name (path traversal / URL syntax); never requested"
R_FORMAT = "not a text or metadata format (weights, binaries, source code); never fetched"
R_SIZE = "larger than the per-file size cap"
R_COUNT = "file-count budget exhausted"
R_BYTES = "total-bytes budget exhausted"
PROSE_KINDS = ("markdown", "text", "template")


@dataclass(frozen=True)
class Candidate:
    name: str
    size: Optional[int]
    kind: str
    cap: int


def select_files(listing: List[Tuple[str, Optional[int]]], limits: EvidenceLimits) -> Tuple[List[Candidate], Dict[str, List[Dict[str, Any]]]]:
    """Choose files by syntax family and budgets only. Returns (candidates, {skip reason: [{name, size}]}). Shallow files first."""
    chosen: List[Candidate] = []
    skipped: Dict[str, List[Dict[str, Any]]] = {}
    budget = limits.max_total_bytes

    def skip(reason: str, name: str, size: Optional[int]) -> None:
        skipped.setdefault(reason, []).append({"name": name[:300], "size": size})

    for name, size in sorted(listing, key=lambda e: (e[0].count("/"), e[0])):
        try:
            safe_repo_path(name)
        except SecurityError:
            skip(R_UNSAFE, name, size)
            continue
        kind = kind_for(name)
        if kind is None:
            skip(R_FORMAT, name, size)
            continue
        cap = limits.max_index_bytes if name.endswith(".index.json") else limits.max_file_bytes
        if size is not None and size > cap:
            skip(R_SIZE, name, size)
        elif len(chosen) >= limits.max_files:
            skip(R_COUNT, name, size)
        elif (size if size is not None else cap) > budget:
            skip(R_BYTES, name, size)
        else:
            budget -= size if size is not None else cap
            chosen.append(Candidate(name, size, kind, cap))
    return chosen, skipped


def _not_fetched_item(ctx: CollectionContext, skipped: Dict[str, List[Dict[str, Any]]]) -> EvidenceItem:
    keep = ctx.limits.max_not_fetched_names
    data = {reason: {"count": len(files), "files": files[:keep], "files_truncated": len(files) > keep} for reason, files in skipped.items()}
    return EvidenceItem("files:not_fetched", "file-selection", SKIPPED, platform_provenance(ctx.clock, "file selection"), data=data,
                        untrusted=False, error="These listed files were deliberately not fetched; their names stay in 'hub:files'.")


def collect_file(ctx: CollectionContext, cand: Candidate) -> EvidenceItem:
    """Fetch and parse one file. Status: collected / missing / inaccessible / invalid (raw kept) / skipped."""
    url = file_url(ctx.repo_id, ctx.revision, cand.name, ctx.provider)
    key = f"file:{cand.name}"
    out = fetch_bytes(ctx.fetcher, url, cand.cap)
    prov = Provenance(SOURCE_REPO_FILE, url, METHOD_GET, ctx.revision, ctx.clock(),
                      {"http_status": out.http_status, "listed_size": cand.size})
    if out.status != COLLECTED or out.body is None:
        return EvidenceItem(key, cand.kind, out.status, prov, size_bytes=cand.size, error=out.error,
                            untrusted=cand.kind in PROSE_KINDS)
    notes: List[str] = []
    try:
        text = out.body.decode("utf-8")
    except UnicodeDecodeError:
        text = out.body.decode("utf-8", errors="replace")
        notes.append("not valid UTF-8; undecodable bytes were replaced")
    text, redacted = sanitize(text, ctx.secrets)
    if redacted:
        notes.append("secret-like values were redacted before storage")
    parsed = parse_text(cand.kind, text)
    raw, truncated = cap_text(text, ctx.limits.max_raw_chars)
    if truncated:
        notes.append(f"raw text truncated to {ctx.limits.max_raw_chars} characters; parsed data (if any) is complete")
    return EvidenceItem(key, cand.kind, INVALID if parsed.error else COLLECTED, prov, data=parsed.data, raw=raw,
                        size_bytes=len(out.body), truncated=truncated, redacted=redacted, error=parsed.error,
                        notes=notes + parsed.notes)


def collect_files(ctx: CollectionContext, listing: List[Tuple[str, Optional[int]]]) -> List[EvidenceItem]:
    """Evidence items for every selected text/metadata file, plus one ``files:not_fetched`` item if anything was left out."""
    candidates, skipped = select_files(listing, ctx.limits)
    items = [collect_file(ctx, c) for c in candidates]
    if skipped:
        items.append(_not_fetched_item(ctx, skipped))
    return items
