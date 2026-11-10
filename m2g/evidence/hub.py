"""Hub metadata: one Hub API call, kept as evidence (metadata + complete file listing) with the commit sha it resolved to."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .context import CollectionContext
from .fetch import fetch_bytes
from .guards import api_url, cap_text, resolved_sha, sanitize
from .provenance import METHOD_GET, SOURCE_HUB_API, Provenance
from .schema import COLLECTED, INVALID, MISSING, EvidenceItem

Listing = List[Tuple[str, Optional[int]]]


@dataclass
class HubResult:
    items: List[EvidenceItem] = field(default_factory=list)
    listing: Optional[Listing] = None  # None = the Hub gave no usable listing
    sha: Optional[str] = None


def _listing(siblings: List[Any], limit: int) -> Tuple[Listing, List[Dict[str, Any]], int, int]:
    """(name/size pairs, original entries, malformed-entry count, entries beyond the limit)."""
    pairs: Listing = []
    entries: List[Dict[str, Any]] = []
    seen, bad, over = set(), 0, 0
    for s in siblings:
        name = s.get("rfilename") if isinstance(s, dict) else None
        if not isinstance(name, str):
            bad += 1
            continue
        if name in seen:
            continue
        seen.add(name)
        if len(entries) >= limit:
            over += 1
            continue
        size = s.get("size")
        pairs.append((name, size if isinstance(size, int) and not isinstance(size, bool) else None))
        entries.append(s)
    return pairs, entries, bad, over


def collect_hub(ctx: CollectionContext, revision: Optional[str]) -> HubResult:
    """Fetch the Hub API record for the repository. Items: ``hub:metadata`` and ``hub:files`` (together lossless)."""
    url = api_url(ctx.repo_id, revision)
    prov = Provenance(SOURCE_HUB_API, url, METHOD_GET, revision, ctx.clock())
    out = fetch_bytes(ctx.fetcher, url, ctx.limits.max_hub_bytes)
    if out.status != COLLECTED:
        return HubResult([EvidenceItem("hub:metadata", "hub-metadata", out.status, prov, error=out.error)])
    text, redacted = sanitize(out.body.decode("utf-8", errors="replace"), ctx.secrets)
    prov = Provenance(SOURCE_HUB_API, url, METHOD_GET, revision, prov.fetched_at, {"http_status": out.http_status})
    try:
        meta = json.loads(text)
    except (ValueError, RecursionError):
        raw, truncated = cap_text(text, ctx.limits.max_raw_chars)
        return HubResult([EvidenceItem("hub:metadata", "hub-metadata", INVALID, prov, raw=raw, truncated=truncated,
                                       size_bytes=len(out.body), redacted=redacted, error="Hub API response is not valid JSON")])
    if not isinstance(meta, dict):
        raw, truncated = cap_text(text, ctx.limits.max_raw_chars)
        return HubResult([EvidenceItem("hub:metadata", "hub-metadata", INVALID, prov, raw=raw, truncated=truncated,
                                       size_bytes=len(out.body), redacted=redacted, error="Hub API response is not a JSON object")])
    items = [EvidenceItem("hub:metadata", "hub-metadata", COLLECTED, prov, data={k: v for k, v in meta.items() if k != "siblings"},
                          size_bytes=len(out.body), redacted=redacted,
                          notes=["The file listing is kept in 'hub:files'; together the two items hold the whole API response."])]
    siblings = meta.get("siblings")
    if not isinstance(siblings, list):
        items.append(EvidenceItem("hub:files", "file-listing", MISSING, prov, error="the API response has no 'siblings' file list"))
        return HubResult(items, None, resolved_sha(meta.get("sha")))
    pairs, entries, bad, over = _listing(siblings, ctx.limits.max_listing_entries)
    notes = []
    if bad:
        notes.append(f"{bad} malformed listing entr{'y' if bad == 1 else 'ies'} without a file name were ignored.")
    if over:
        notes.append(f"Listing truncated: {over} further entries exceed the limit of {ctx.limits.max_listing_entries}.")
    items.append(EvidenceItem("hub:files", "file-listing", COLLECTED, prov, data=entries, truncated=bool(over), redacted=redacted,
                              notes=notes))
    return HubResult(items, pairs, resolved_sha(meta.get("sha")))
