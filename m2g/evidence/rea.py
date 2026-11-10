"""REA integration: turns an existing ``ReaReport`` (the result of ``m2g.rea_client.run_rea_inspection``) into evidence items.

This module never starts REA itself. It records what REA was allowed to do (tool inventory), what it reported, and each tool
call's output, so the next phase can see exactly which facts REA produced and which tools were never used."""
from __future__ import annotations

import json
from typing import Any, List, Optional, Sequence

from ..rea_client import ALLOWED_TOOLS, FIXED_ARGS, REQUIRED_TOOLS
from .guards import cap_text, sanitize
from .provenance import METHOD_DERIVED, METHOD_REPORT, SOURCE_PLATFORM, SOURCE_REA, Clock, Provenance, platform_provenance
from .schema import COLLECTED, INACCESSIBLE, SKIPPED, EvidenceItem

_STATUS_MAP = {"completed": COLLECTED, "partial": COLLECTED, "failed": INACCESSIBLE, "not_applicable": SKIPPED, "not_invoked": SKIPPED}
_RAW_LIMIT = 8_000


def inventory_item(clock: Clock, tools_discovered: int = 0, capabilities: Optional[Sequence[str]] = None,
                   capabilities_by_tag: Optional[Any] = None, refusals: Optional[Any] = None) -> EvidenceItem:
    """What the platform lets REA do (the capability policy and its fixed arguments), as a fact about the collection, not the model."""
    data = {"allowed_tools": list(ALLOWED_TOOLS), "required_tools": list(REQUIRED_TOOLS),
            "fixed_arguments": {k: dict(v) for k, v in FIXED_ARGS.items()},
            "capability_policy": "tools are selected dynamically from the advertised schemas: never destructive, never open-world, "
                                 "read-only only, and only with arguments this platform can supply",
            "capabilities_discovered": list(capabilities or []),
            "capabilities_by_tag": {str(k): list(v) for k, v in (capabilities_by_tag or {}).items()},
            "refused_tools": dict(refusals or {}),
            "tools_advertised_by_server": tools_discovered,
            "note": "Only tools the capability policy permits can be called; every other advertised tool is refused by the client."}
    return EvidenceItem("rea:inventory", "rea-inventory", COLLECTED,
                        Provenance(SOURCE_PLATFORM, "m2g.rea_client", METHOD_DERIVED, None, clock()), data=data, untrusted=False)


def _call_item(i: int, call: Any, clock: Clock, secrets: Sequence[str], server: str) -> EvidenceItem:
    text, redacted = sanitize(str(call.output or ""), secrets)
    raw, cut = cap_text(text, _RAW_LIMIT)
    data = {"tool": call.tool, "arguments": call.arguments, "target": call.target, "duration_s": call.duration_s}
    if call.ok:
        try:
            parsed = json.loads(text)
            if isinstance(parsed, (dict, list)):
                data["parsed_output"] = parsed
        except (ValueError, RecursionError):
            pass
    prov = Provenance(SOURCE_REA, server, METHOD_REPORT, None, clock(), {"tool": call.tool, "target": call.target})
    return EvidenceItem(f"rea:call:{i}:{call.tool}", "rea-tool-call", COLLECTED if call.ok else INACCESSIBLE, prov, data=data, raw=raw,
                        size_bytes=len(text), truncated=cut or bool(call.output_truncated), redacted=redacted,
                        error=None if call.ok else sanitize(str(call.error or "tool call failed"), secrets)[0][:300])


def collect_rea(report: Optional[Any], clock: Clock, secrets: Sequence[str] = ()) -> List[EvidenceItem]:
    """Items: ``rea:inventory``, ``rea:status`` (or ``rea:report`` when no report exists) and one item per tool call."""
    if report is None:
        return [inventory_item(clock),
                EvidenceItem("rea:report", "rea-status", SKIPPED, platform_provenance(clock, "rea"), untrusted=False,
                             error="no REA report was supplied and no REA runner is configured; no REA tool was called")]
    server = " ".join(str(report.server.get(k, "")) for k in ("name", "version")).strip() or "REA"
    data = {"status": report.status, "reason": report.reason, "server": report.server, "tools_discovered": report.tools_discovered,
            "capabilities_discovered": list(getattr(report, "capabilities", []) or []),
            "capabilities_by_tag": dict(getattr(report, "capabilities_by_tag", {}) or {}),
            "refused_tools": dict(getattr(report, "refusals", {}) or {}),
            "questions": list(getattr(report, "questions", []) or []),
            "source_questions": list(getattr(report, "source_questions", []) or []),
            "prompts": list(getattr(report, "prompts", []) or []),
            "investigation": list(getattr(report, "investigation", []) or []),
            "stop_reason": str(getattr(report, "stop_reason", "") or ""),
            "budget": dict(getattr(report, "budget", {}) or {}),
            "tools_invoked": [c.tool for c in report.calls], "shards_total": report.shards_total,
            "shards_inspected": report.shards_inspected, "bytes_downloaded": report.bytes_downloaded,
            "http_requests": report.http_requests, "health_check": report.health_check, "findings": list(report.findings),
            "header_summaries": list(report.header_summaries), "log": list(report.log)}
    text, redacted = sanitize(json.dumps(data, default=str), secrets)
    if redacted:  # a secret in the report: store the redacted structure, never the original
        data = json.loads(text)
    status = _STATUS_MAP.get(report.status, INACCESSIBLE)
    items = [inventory_item(clock, report.tools_discovered, getattr(report, "capabilities", None),
                            getattr(report, "capabilities_by_tag", None), getattr(report, "refusals", None)),
             EvidenceItem("rea:status", "rea-status", status, Provenance(SOURCE_REA, server, METHOD_REPORT, None, clock()), data=data,
                          redacted=redacted, untrusted=False,
                          error=None if status == COLLECTED else sanitize(str(report.reason), secrets)[0][:300])]
    items += [_call_item(i, c, clock, secrets, server) for i, c in enumerate(report.calls)]
    return items
