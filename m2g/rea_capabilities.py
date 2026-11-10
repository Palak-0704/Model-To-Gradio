"""Capability-driven REA tool discovery and selection, and a small evidence-gap planner for the analysis stage.

Why this module exists
  ``m2g.rea_client`` used to allow-list exactly three tools and hard-code every argument. That is safe but blind: the REA 6.8.0
  server advertises 229 tools, and a handful of them can deepen the analysis (page retained Evidence, enumerate residual
  unknowns, measure the byte entropy of a header) with no execution and no network. This module turns "which tools may run" into
  a policy over the tool descriptions and input schemas the server ACTUALLY advertises, instead of a fixed list.

What is safe to call, and why
  A tool may be invoked only when ALL of these hold, judged from the advertised metadata alone:

    * its name is not on a small denylist of obviously environment-mutating operations (``record_unknown``, ``update_unknown``,
      ``import_evidence_bundle``, ``update_analysis_settings`` ...);
    * ``destructiveHint`` is not true;
    * ``openWorldHint`` is not true (nothing that reaches the network / external world);
    * its required arguments are a subset of the ones the platform can actually supply from data it already holds - so a tool
      that needs a live debugger session, an Android device, a browser endpoint or an application package is never selected.

  The client still validates every argument against the advertised schema and still refuses unknown keys, so nothing reaches the
  server that the server did not say it accepts. This is a policy, not an allow-list: adding a safe tool to the server needs no
  platform change, while anything that mutates state, reaches the network or needs a target the platform does not have is refused.

Selections the platform makes
  ``plan_capability_investigation`` is a small planner: it looks at what the header inspection already produced (retained Evidence
  ids, the header byte counts) and at what the model evidence is still missing, and returns an ordered, deduplicated list of tool
  calls. A tool is never planned twice for the same target, and the investigation stops when the plan is exhausted.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

# Tools that mutate REA's session state or its persisted evidence/unknown ledger, or retune the analysis runtime. They are
# cheap and safe in isolation, but this stage only ever observes: it must not write to a shared session other clients may use.
MUTATING_TOOLS: frozenset = frozenset({
    "record_unknown", "update_unknown", "import_evidence_bundle", "export_evidence_bundle", "export_environment_bundle",
    "update_analysis_settings", "close_binary", "close_gdb_session", "close_rizon_debug_session", "close_rizin_debug_session",
    "close_frida_session", "open_binary",
})

# The required-argument names the platform can actually fill from data it already holds. A tool that requires anything else
# (a debugger ``serial``, an ``address``, a browser ``cdp_endpoint``, a ``package`` ...) needs a target this stage never has.
# ``source``/``view`` are the retained-Evidence paging shape the platform builds itself; the rest address our own scratch files.
SUPPLIABLE_ARGS: frozenset = frozenset({"path", "detail", "hash", "offset", "length", "block_size", "filters", "evidence_id",
                                        "limit", "source", "view"})


@dataclass(frozen=True)
class CapabilityPolicy:
    """The rule that decides whether one advertised tool may be invoked by the analysis stage."""

    denylist: frozenset = MUTATING_TOOLS
    suppliable: frozenset = SUPPLIABLE_ARGS

    def eligible(self, tool: Mapping[str, Any]) -> bool:
        name = tool.get("name")
        if not isinstance(name, str) or not name or name in self.denylist:
            return False
        ann = tool.get("annotations") if isinstance(tool.get("annotations"), dict) else {}
        if ann.get("destructiveHint") is True or ann.get("openWorldHint") is True:
            return False
        schema = tool.get("inputSchema") if isinstance(tool.get("inputSchema"), dict) else {}
        required = schema.get("required")
        required = required if isinstance(required, list) else []
        if not all(isinstance(r, str) and r in self.suppliable for r in required):
            return False
        return True

    def eligible_names(self, tools: Mapping[str, Mapping[str, Any]]) -> List[str]:
        return sorted(n for n, t in tools.items() if isinstance(t, Mapping) and self.eligible(t))


# --------------------------------------------------------------------------- investigation planner


@dataclass
class PlannedCall:
    """One REA tool call the planner decided to make, with the reason it was selected (kept for the report and the UI)."""

    tool: str
    arguments: Dict[str, Any]
    target: str
    reason: str


@dataclass
class PlanContext:
    """What the planner knows before it plans: the tools the server advertises and the evidence gathered so far."""

    available: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)  # name -> advertised tool
    header_files: Sequence[Tuple[str, str, Optional[int]]] = ()  # (file name, local scratch path, header byte count) for each inspected file
    retained_ids: Mapping[str, str] = field(default_factory=dict)  # file name -> the retained Evidence id its inspection advertised
    missing_evidence: Sequence[str] = ()  # evidence gaps the analysis still wants (free-form keys, see below)


# Evidence-gap keys the analyzer/bridge can raise; each maps to an analysis INTENT that one or more REA capabilities can fill.
GAP_BYTE_ENTROPY = "weight_byte_entropy"
GAP_RESIDUAL_UNKNOWNS = "residual_unknowns"
GAP_RETAINED_METADATA = "retained_metadata"
GAP_EVIDENCE_BUNDLE = "evidence_bundle"


@dataclass(frozen=True)
class Intent:
    """One analysis intent: a preferred tool, keywords for discovering an equivalent, and how to build its arguments.

    Selection is capability-driven: the preferred tool is used when the server advertises it, otherwise the policy scans the
    advertised tools for an eligible one whose name matches the keywords. Either way the arguments are filtered to the properties
    that tool's own schema declares, so a rename or a new equivalent tool on the server is discovered, not hard-coded."""

    key: str
    preferred: str
    keywords: Tuple[str, ...]


INTENTS: Tuple[Intent, ...] = (
    Intent(GAP_RETAINED_METADATA, "inspect_analysis_view", ("analysis_view", "evidence_view")),
    Intent(GAP_BYTE_ENTROPY, "inspect_byte_entropy", ("byte_entropy",)),
    Intent(GAP_RESIDUAL_UNKNOWNS, "list_unknowns", ("list_unknowns",)),
    Intent(GAP_EVIDENCE_BUNDLE, "get_evidence_bundle", ("evidence_bundle",)),
)


def _resolve_tool(intent: Intent, ctx: "PlanContext", policy: CapabilityPolicy) -> Optional[str]:
    """The advertised, eligible tool that best fills ``intent``: the preferred name, else the first eligible tool whose NAME
    contains one of the intent's keywords. Matching is name-only (never the free-text description) so a rename on the server can be
    discovered, while an unrelated tool that merely mentions the word is not selected."""
    meta = ctx.available.get(intent.preferred)
    if meta is not None and policy.eligible(meta):
        return intent.preferred
    for name in sorted(ctx.available):
        if not policy.eligible(ctx.available[name]):
            continue
        if any(k in name for k in intent.keywords):
            return name
    return None


def _arguments_for(intent: Intent, target_ctx: Dict[str, Any], props: Mapping[str, Any]) -> Dict[str, Any]:
    """Candidate arguments for an intent, filtered to the properties the chosen tool's schema advertises."""
    if intent.key == GAP_RETAINED_METADATA:
        evid = target_ctx.get("evidence_id")
        if not evid:
            return {}
        candidate: Dict[str, Any] = {"source": {"kind": "retained-evidence", "evidence_id": evid},
                                     "view": {"kind": "model-artifact", "collection": "metadata", "offset": 0, "limit": 64}}
    elif intent.key == GAP_BYTE_ENTROPY:
        path, header_bytes = target_ctx.get("path"), target_ctx.get("header_bytes")
        if not path or not header_bytes:
            return {}
        candidate = {"path": path, "offset": 0, "length": int(header_bytes), "detail": "summary"}
    elif intent.key == GAP_RESIDUAL_UNKNOWNS:
        candidate = {"limit": 100}
    elif intent.key == GAP_EVIDENCE_BUNDLE:
        candidate = {"detail": "summary"}
    else:  # pragma: no cover - defensive
        candidate = {}
    return {k: v for k, v in candidate.items() if k in props}


def plan_capability_investigation(ctx: PlanContext, policy: Optional[CapabilityPolicy] = None) -> List[PlannedCall]:
    """The ordered, deduplicated REA tool calls that deepen this analysis.

    Each raised evidence gap maps to an analysis intent; the intent is resolved to a tool the server actually advertises and the
    policy permits (preferred name, else a keyword match), and its arguments are filtered to that tool's schema. A tool is planned
    at most once per target, and the loop stops when the gaps yield nothing new, so it is bounded by the plan, never by a step cap.
    Per-file reads are bounded to the header window, so a sparse scratch file is never streamed end to end."""
    policy = policy or CapabilityPolicy()
    gaps = set(ctx.missing_evidence)
    planned: List[PlannedCall] = []
    seen: Set[Tuple[str, str]] = set()
    retained = dict(ctx.retained_ids)
    file_ctx = {name: {"path": path, "header_bytes": hdr} for name, path, hdr in ctx.header_files}

    def add(tool: str, arguments: Dict[str, Any], target: str, reason: str) -> None:
        schema = ctx.available[tool].get("inputSchema") if isinstance(ctx.available[tool].get("inputSchema"), dict) else {}
        props = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
        args = {k: v for k, v in arguments.items() if k in props}  # only what the tool's schema advertises
        required = schema.get("required") if isinstance(schema.get("required"), list) else []
        if any(r not in args for r in required):
            return
        key = (tool, target)
        if key in seen:
            return
        seen.add(key)
        planned.append(PlannedCall(tool, args, target, reason))

    for intent in INTENTS:
        if intent.key not in gaps:
            continue
        tool = _resolve_tool(intent, ctx, policy)
        if tool is None:
            continue
        schema = ctx.available[tool].get("inputSchema") if isinstance(ctx.available[tool].get("inputSchema"), dict) else {}
        props = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
        if intent.key == GAP_RETAINED_METADATA:
            for name, evid in retained.items():
                if isinstance(evid, str) and evid.startswith("ev_"):
                    add(tool, _arguments_for(intent, {"evidence_id": evid}, props), name,
                        "page REA's retained Evidence for this file")
        elif intent.key == GAP_BYTE_ENTROPY:
            for name, tctx in file_ctx.items():
                if tctx.get("header_bytes"):
                    add(tool, _arguments_for(intent, tctx, props), name,
                        "measure the byte entropy of the header region only")
        else:
            reason = {"list_unknowns": "list REA's residual unknowns for this analysis",
                      "get_evidence_bundle": "summarise every Evidence record REA retained"}.get(tool, "session view")
            add(tool, _arguments_for(intent, {}, props), "session", reason)

    return planned


def known_safe_tools(tools: Mapping[str, Mapping[str, Any]], policy: Optional[CapabilityPolicy] = None) -> List[str]:
    """The names of every advertised tool this policy would let the stage invoke (used for the inventory evidence item)."""
    return (policy or CapabilityPolicy()).eligible_names(tools)
