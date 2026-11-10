"""Dynamic REA capability discovery, evidence-gap-driven question generation, and a bounded adaptive investigation planner.

Why this module exists
  The REA stage used to drive a fixed three-tool allow-list with hard-coded arguments, then a fixed four-intent, two-round plan.
  That is safe but blind: the REA 6.8.0 server advertises 229 tools, and the interesting ones (page retained Evidence, enumerate
  residual unknowns, measure the entropy of a header, list the artifacts a target really contains) change with the server and with
  the model. This module replaces the fixed intent list and the fixed round count with:

    * a SAFETY classifier derived from the metadata the server actually advertises (annotations + schema), not a name allow-list;
    * a CAPABILITY TAGGER that infers what each eligible tool does from its own name/description, so a renamed or newly added tool
      is discovered rather than missed;
    * QUESTION GENERATORS that read the Analyzer's collected evidence state and raise specific, targetable investigation questions
      (unknown format, unconfirmed architecture, retained metadata not yet paged, partial shard coverage, ...);
    * a PLANNER that answers the currently-open questions with the eligible tools that can serve them, and returns nothing once no
      open question is answerable - so the loop is bounded by the evidence, never by a round counter.

What is safe to call, and why
  A tool may be invoked only when ALL of these hold, judged from the advertised metadata alone:

    * it is positively classified read-only - either the server's ``readOnlyHint`` is true, or it is a pure inspection/inventory
      tool (name/description say so) that is neither destructive nor open-world and does not itself write to the ledger;
    * ``destructiveHint`` is not true;
    * ``openWorldHint`` is not true (nothing that reaches the network / external world);
    * its required arguments are a subset of the ones the platform can supply from data it already holds.

  Anything else - a mutating ledger write, a destructive session close, a tool that needs a live debugger session, an address, an
  Android device or a browser endpoint, or a tool whose safety cannot be positively established - is refused, and the client still
  validates every argument against the advertised schema. This is a policy, not an allow-list.

Stopping
  The adaptive loop (see ``m2g.rea_client``) keeps asking for a plan until the open questions are exhausted, no eligible capability
  answers them, a step produces no new evidence, the same call would repeat, tool failures pass a bounded retry budget, or a
  configurable call/time budget is spent. There is no fixed round limit and no fixed global tool-count cap; the bounds are the
  evidence and the budget.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, FrozenSet, List, Mapping, Optional, Sequence, Set, Tuple

# Tools that mutate REA's session state or its persisted evidence/unknown ledger, or retune the analysis runtime. They are cheap
# and safe in isolation, but this stage only ever observes: it must not write to a shared session other clients may use.
MUTATING_TOOLS: frozenset = frozenset({
    "record_unknown", "update_unknown", "import_evidence_bundle", "export_evidence_bundle", "export_environment_bundle",
    "update_analysis_settings", "close_binary", "close_gdb_session", "close_rizon_debug_session", "close_rizin_debug_session",
    "close_frida_session", "open_binary",
})

# The required-argument names the platform can actually fill from data it already holds. A tool that requires anything else
# (a debugger ``serial``/``session_id``, an ``address``, a browser ``cdp_endpoint``, a ``package`` ...) needs a target this stage
# never has. ``source``/``view`` are the retained-Evidence paging shape the platform builds itself; the rest address our scratch.
SUPPLIABLE_ARGS: frozenset = frozenset({"path", "detail", "hash", "offset", "length", "block_size", "filters", "evidence_id",
                                        "limit", "source", "view", "artifact", "target", "kind"})

# Words that positively identify an inspection/inventory tool. Used ONLY to recognise a read-only tool when the server does not set
# ``readOnlyHint``; it can never make a destructive/open-world tool eligible.
_INSPECT_WORDS = ("inspect", "list", "get", "view", "summary", "overview", "enumerate", "describe", "read", "show", "report")
_WRITE_WORDS = ("write", "record", "update", "set", "create", "delete", "remove", "import", "export", "mutate", "apply", "patch",
                "close", "open", "execute", "exec", "run", "launch", "send", "post", "put", "install", "attach", "kill")
_EVIDENCE_ID_RE = re.compile(r"ev_[0-9a-fA-F]{8,}")


# --------------------------------------------------------------------------- safety policy


@dataclass(frozen=True)
class CapabilityPolicy:
    """The rule that decides whether one advertised tool may be invoked by the analysis stage, and why not."""

    denylist: frozenset = MUTATING_TOOLS
    suppliable: frozenset = SUPPLIABLE_ARGS

    def classification(self, tool: Mapping[str, Any]) -> Tuple[bool, str]:
        """(eligible, reason). ``reason`` explains a refusal, or names the safety signal that allowed the tool."""
        name = tool.get("name")
        if not isinstance(name, str) or not name:
            return False, "the tool has no name"
        if name in self.denylist:
            return False, "it mutates REA's session or its persisted evidence/unknown ledger"
        ann = tool.get("annotations") if isinstance(tool.get("annotations"), dict) else {}
        if ann.get("destructiveHint") is True:
            return False, "the server marks it destructive"
        if ann.get("openWorldHint") is True:
            return False, "it reaches the network / external world"
        schema = tool.get("inputSchema") if isinstance(tool.get("inputSchema"), dict) else {}
        required = schema.get("required")
        required = required if isinstance(required, list) else []
        unsuppliable = [r for r in required if not (isinstance(r, str) and r in self.suppliable)]
        if unsuppliable:
            return False, f"it needs argument(s) {unsuppliable} this analysis stage cannot supply"
        # Positive read-only classification. ``readOnlyHint`` is authoritative; without it a pure inspection name may qualify,
        # but a name that reads like a mutation is refused outright - safety must be established, not assumed.
        if ann.get("readOnlyHint") is True:
            return True, "readOnlyHint"
        low = name.lower()
        if any(w in low for w in _WRITE_WORDS):
            return False, "its name reads like a mutation and it is not marked read-only"
        if any(w in low for w in _INSPECT_WORDS):
            return True, "inspection tool"
        return False, "its safety could not be positively established"

    def eligible(self, tool: Mapping[str, Any]) -> bool:
        return self.classification(tool)[0]

    def eligible_names(self, tools: Mapping[str, Mapping[str, Any]]) -> List[str]:
        return sorted(n for n, t in tools.items() if isinstance(t, Mapping) and self.eligible(t))

    def refusals(self, tools: Mapping[str, Mapping[str, Any]]) -> Dict[str, str]:
        """Every advertised tool the policy refuses, with the reason (kept for the report and the UI)."""
        out: Dict[str, str] = {}
        for n, t in tools.items():
            if isinstance(t, Mapping):
                ok, why = self.classification(t)
                if not ok:
                    out[n] = why
        return out


# --------------------------------------------------------------------------- dynamic capability tagging


@dataclass(frozen=True)
class CapabilitySpec:
    """A capability the analysis may want, recognised from a tool's OWN advertised metadata (never a fixed tool name)."""

    tag: str
    keywords: Tuple[str, ...]  # matched against a tool's name + description
    produces: Tuple[str, ...]  # evidence keys a successful call of this capability establishes


CAPABILITIES: Tuple[CapabilitySpec, ...] = (
    CapabilitySpec("retained_view", ("analysis_view", "evidence_view", "retained"), ("retained_metadata",)),
    CapabilitySpec("residual_unknowns", ("unknown",), ("residual_unknowns",)),
    CapabilitySpec("evidence_bundle", ("evidence_bundle", "evidence bundle"), ("evidence_bundle",)),
    CapabilitySpec("byte_entropy", ("entropy",), ("byte_entropy",)),
    CapabilitySpec("artifact_inventory", ("artifact", "model_artifact"), ("artifact_inventory",)),
    CapabilitySpec("binary_overview", ("overview", "binary_summary"), ("binary_overview",)),
    CapabilitySpec("settings_view", ("analysis_settings", "runtime_settings"), ("analysis_settings",)),
)

TAG_ORDER: Tuple[str, ...] = tuple(c.tag for c in CAPABILITIES)
_PRODUCES: Dict[str, FrozenSet[str]] = {c.tag: frozenset(c.produces) for c in CAPABILITIES}


def _match_tool(tool: Mapping[str, Any]) -> Optional[Tuple[str, bool]]:
    """The capability tag a tool serves and whether its NAME matched (True) or only its description did (False).

    A name match is far stronger evidence than a description match, so callers order by it: a tool that merely mentions a word in
    prose (e.g. an Objective-C tool whose description says "unknown") must never outrank the tool actually named for the job."""
    name = str(tool.get("name") or "").lower()
    desc = str(tool.get("description") or "").lower()
    for spec in CAPABILITIES:
        if any(k in name for k in spec.keywords):
            return spec.tag, True
    for spec in CAPABILITIES:
        if any(k in desc for k in spec.keywords):
            return spec.tag, False
    return None


def tag_tool(tool: Mapping[str, Any]) -> Optional[str]:
    """The single capability tag that best describes an eligible tool, from its name + description."""
    match = _match_tool(tool)
    return match[0] if match else None


def tag_tools(tools: Mapping[str, Mapping[str, Any]], policy: Optional[CapabilityPolicy] = None) -> Dict[str, List[str]]:
    """tag -> eligible tool names, discovered from the advertised metadata (a capability with no tool is simply absent).

    Within a tag, tools whose NAME matched come first (then alphabetical), so the first tool a question plans is the one actually
    named for the capability whenever the server advertises one."""
    policy = policy or CapabilityPolicy()
    tagged: Dict[str, List[Tuple[bool, str]]] = {}
    for name in sorted(tools):
        tool = tools[name]
        if not isinstance(tool, Mapping) or not policy.eligible(tool):
            continue
        match = _match_tool(tool)
        if match is not None:
            tagged.setdefault(match[0], []).append((match[1], name))
    return {tag: [name for _by_name, name in sorted(pairs, key=lambda p: (not p[0], p[1]))]
            for tag, pairs in tagged.items()}


# --------------------------------------------------------------------------- questions


@dataclass(frozen=True)
class Question:
    """One specific, targetable investigation question raised by the collected evidence."""

    qid: str
    kind: str  # the capability tag that can answer it
    target: str  # the repository file, or "session" for a whole-analysis question
    text: str
    arguments: Mapping[str, Any] = field(default_factory=dict)  # candidate arguments, filtered to the chosen tool's schema


@dataclass
class EvidenceState:
    """The facts the Analyzer has already established for one target, as the question generators read them.

    This is deliberately a plain data structure: the generators do not know about RepoInfo or ReaReport, so the same logic works
    for a Hugging Face model and for a GitHub repository."""

    files: Sequence[Tuple[str, str]] = ()  # (file name, local scratch path) for each header-inspected file
    header_bytes: Mapping[str, int] = field(default_factory=dict)  # file name -> header byte count (bounds the entropy window)
    formats: Mapping[str, str] = field(default_factory=dict)  # file name -> weight format the platform detected
    architectures: Mapping[str, Optional[str]] = field(default_factory=dict)  # file name -> declared architecture, if any
    retained_ids: Dict[str, str] = field(default_factory=dict)  # retained Evidence id -> the file (or "session") it describes
    paged_ids: Set[str] = field(default_factory=set)  # retained Evidence ids already paged (so a page is never requested twice)
    entropy_done: Set[str] = field(default_factory=set)  # files whose header region has already been byte-verified
    header_ok: bool = True  # the header inspection itself produced usable facts
    coverage_partial: bool = False  # the platform inspected fewer files than the model has
    have: Set[str] = field(default_factory=set)  # evidence keys already established
    capabilities: Mapping[str, Sequence[str]] = field(default_factory=dict)  # tag -> eligible tool names


def _first_tool(state: EvidenceState, tag: str) -> Optional[str]:
    names = state.capabilities.get(tag) or ()
    return names[0] if names else None


def generate_questions(state: EvidenceState) -> List[Question]:
    """Read the evidence state and return the OPEN questions, in a stable order, one per (kind, target).

    A question is raised only when the evidence is genuinely missing, incomplete, ambiguous or conflicting - never because a fixed
    list says so. The generators are evidence-driven: e.g. the entropy question is asked for a file whose format is unknown, not
    for every file; the retained-view question is asked only when the inspection really advertised a retained Evidence id."""
    qs: List[Question] = []
    seen: Set[Tuple[str, str]] = set()

    def add(kind: str, target: str, text: str, arguments: Mapping[str, Any]) -> None:
        if _first_tool(state, kind) is None:
            return  # no eligible capability can answer it: it is a recorded gap, not a plannable question
        key = (kind, target)
        if key in seen:
            return
        seen.add(key)
        qs.append(Question(f"{kind}:{target}", kind, target, text, dict(arguments)))

    # Per-file ambiguity/conflict: format undetermined, or an architecture the header did not confirm.
    for name, path in state.files:
        hdr = int(state.header_bytes.get(name) or 0)
        if name in state.entropy_done or hdr <= 0:
            continue
        if not state.formats.get(name):
            add("byte_entropy", name, f"the weight format of {name} could not be determined from its header",
                {"path": path, "offset": 0, "length": hdr, "detail": "summary"})
        elif not state.architectures.get(name):
            add("byte_entropy", name, f"the architecture of {name} was not declared in its header",
                {"path": path, "offset": 0, "length": hdr, "detail": "summary"})

    # Page the retained Evidence an inspection advertised (or a later call revealed), once each - never twice. Keyed by the
    # evidence id (not the file) so two ids for the same file are two distinct pages.
    for evid, target in state.retained_ids.items():
        if isinstance(evid, str) and evid.startswith("ev_") and evid not in state.paged_ids:
            add("retained_view", evid, f"the retained Evidence {evid[:11]}... for {target} has not been paged yet",
                {"source": {"kind": "retained-evidence", "evidence_id": evid},
                 "view": {"kind": "model-artifact", "collection": "metadata", "offset": 0, "limit": 64}})

    # Whole-analysis questions: is anything still unknown to REA, and what did REA actually retain?
    if "residual_unknowns" not in state.have:
        add("residual_unknowns", "session", "REA may hold residual unknowns the analysis has not seen", {"limit": 100})
    if "evidence_bundle" not in state.have:
        add("evidence_bundle", "session", "the full set of Evidence REA retained has not been summarised",
            {"detail": "summary"})

    # Coverage question: a partial inspection leaves the uninspected shards unexplained.
    if state.coverage_partial and "artifact_inventory" not in state.have:
        add("artifact_inventory", "session", "only part of the model's weight files were inspected", {"detail": "summary"})

    # An overview question when the header inspection produced nothing usable but an inspection tool exists.
    if not state.header_ok and "binary_overview" not in state.have:
        add("binary_overview", "session", "the header inspection produced no usable facts", {})

    order = {tag: i for i, tag in enumerate(TAG_ORDER)}
    qs.sort(key=lambda q: (order.get(q.kind, len(TAG_ORDER)), q.target))
    return qs


def observe(state: EvidenceState, capability: str, target: str, output: str) -> Set[str]:
    """Update the evidence state from one successful call's output and return the evidence keys it newly established.

    The extraction is generic and defensive: any retained Evidence id the output names is remembered (so a bundle or a view can
    reveal NEW paging questions), the id a page actually addressed is marked as paged (never paged twice), and a declared
    format/architecture fills the per-file facts. A malformed or unexpected payload simply adds nothing - it never raises and
    never invents a fact."""
    gained = set(_PRODUCES.get(capability, frozenset()))
    state.have |= gained
    if not isinstance(output, str):
        return gained
    for evid in set(_EVIDENCE_ID_RE.findall(output)):
        state.retained_ids.setdefault(evid, target)
    if capability == "retained_view":
        for evid in set(_EVIDENCE_ID_RE.findall(output)):
            state.paged_ids.add(evid)
    if capability == "byte_entropy":
        state.entropy_done.add(target)
    try:
        obj = json.loads(output)
    except (ValueError, TypeError):
        obj = None
    if isinstance(obj, dict):
        fmt = obj.get("format")
        if isinstance(fmt, str) and not state.formats.get(target):
            state.formats = {**state.formats, target: fmt}
        arch = obj.get("architecture")
        if isinstance(arch, str) and not state.architectures.get(target):
            state.architectures = {**state.architectures, target: arch}
    return gained


# --------------------------------------------------------------------------- planning


@dataclass
class PlannedCall:
    """One REA tool call the planner decided to make, with the question it answers (kept for the report and the UI)."""

    tool: str
    arguments: Dict[str, Any]
    target: str
    reason: str
    question: str = ""
    capability: str = ""


@dataclass
class Budget:
    """Configurable resource bounds for the adaptive loop. Not a round limit: the loop stops when questions run out, and the
    budget only caps runaway cost. ``max_calls`` is a safety ceiling far above any real investigation, never a tool allow-list."""

    max_calls: int = 40
    max_seconds: float = 120.0
    max_failures: int = 5
    clock: Callable[[], float] = time.monotonic
    started: float = field(default_factory=time.monotonic)

    def calls_exhausted(self, calls_made: int) -> bool:
        return calls_made >= self.max_calls

    def time_exhausted(self) -> bool:
        return (self.clock() - self.started) >= self.max_seconds

    def failures_exhausted(self, failures: int) -> bool:
        return failures >= self.max_failures

    def exhausted(self, calls_made: int, failures: int) -> bool:
        return self.calls_exhausted(calls_made) or self.time_exhausted() or self.failures_exhausted(failures)

    def as_dict(self) -> Dict[str, Any]:
        return {"max_calls": self.max_calls, "max_seconds": self.max_seconds, "max_failures": self.max_failures}


def _filter_arguments(arguments: Mapping[str, Any], schema: Mapping[str, Any]) -> Dict[str, Any]:
    props = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    return {k: v for k, v in arguments.items() if k in props}


def _required_satisfied(schema: Mapping[str, Any], args: Mapping[str, Any]) -> bool:
    required = schema.get("required") if isinstance(schema.get("required"), list) else []
    return all(r in args for r in required)


def plan_questions(state: EvidenceState, questions: Sequence[Question],
                   policy: Optional[CapabilityPolicy] = None) -> List[PlannedCall]:
    """Turn the open questions into tool calls the server advertises and the policy permits.

    Each question is resolved to the first eligible tool tagged with its capability (so a renamed or newly added tool is used), and
    a call is planned at most once per (tool, target) - duplicate-call prevention. A question with no eligible capability is
    skipped and stays a recorded gap."""
    planned: List[PlannedCall] = []
    seen: Set[Tuple[str, str]] = set()
    for q in questions:
        tool = _first_tool(state, q.kind)
        if tool is None:
            continue
        key = (tool, q.target)
        if key in seen:
            continue
        seen.add(key)
        planned.append(PlannedCall(tool, dict(q.arguments), q.target, q.text, q.qid, q.kind))
    return planned


def filter_plan(planned: Sequence[PlannedCall], tools: Mapping[str, Mapping[str, Any]],
                policy: Optional[CapabilityPolicy] = None) -> List[PlannedCall]:
    """Drop any planned call whose tool is not advertised/eligible, or whose arguments do not satisfy the tool's schema.

    The client calls this right before executing, so the plan can never smuggle an unadvertised tool or an invalid argument list
    past the policy."""
    policy = policy or CapabilityPolicy()
    out: List[PlannedCall] = []
    for p in planned:
        tool = tools.get(p.tool)
        if tool is None or not policy.eligible(tool):
            continue
        schema = tool.get("inputSchema") if isinstance(tool.get("inputSchema"), dict) else {}
        args = _filter_arguments(p.arguments, schema)
        if not _required_satisfied(schema, args):
            continue
        out.append(PlannedCall(p.tool, args, p.target, p.reason, p.question, p.capability))
    return out


def produced_keys(tag: str) -> FrozenSet[str]:
    """The evidence keys a capability tag establishes when it succeeds (used to update the evidence state between rounds)."""
    return _PRODUCES.get(tag, frozenset())


def state_signature(state: EvidenceState) -> Tuple[Any, ...]:
    """A comparable snapshot of the evidence state. If it is unchanged after a round, that round produced nothing new."""
    return (frozenset(state.have), frozenset(state.retained_ids.items()), frozenset(state.paged_ids),
            frozenset(state.entropy_done),
            tuple(sorted((k, v) for k, v in state.formats.items() if v)),
            tuple(sorted((k, str(v)) for k, v in state.architectures.items() if v)))


def known_safe_tools(tools: Mapping[str, Mapping[str, Any]], policy: Optional[CapabilityPolicy] = None) -> List[str]:
    """The names of every advertised tool this policy would let the stage invoke (used for the inventory evidence item)."""
    return (policy or CapabilityPolicy()).eligible_names(tools)
