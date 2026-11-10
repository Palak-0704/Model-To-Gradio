"""Claim-level evidence validation.

The old report copied Analyzer values into a structure and called it "validated". That is not validation. This module
builds one :class:`Claim` per conclusion, each carrying the evidence that supports it, the evidence that contradicts it,
an explicit status, a confidence derived only from the evidence (never an arbitrary number) and the unknowns and
limitations that remain.

Statuses
  VERIFIED    at least one direct (authoritative) observation supports it and nothing contradicts it.
  SUPPORTED   structural evidence supports it and nothing contradicts it.
  INFERRED    only indirect/heuristic evidence supports it.
  CONFLICTED  independent evidence both supports and contradicts it: the disagreement is kept and explained.
  UNKNOWN     there is no usable evidence, or the value could not be established.

Confidence is a deterministic function of the *directness* of the best supporting evidence, the number of *independent*
corroborating observations (two copies of the same underlying observation are NOT independent), and the presence of a
conflict or of a coverage gap. It is not an LLM opinion and it is not a fabricated percentage: the exact inputs are
recorded on the claim so the number can be recomputed by hand.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

VERIFIED, SUPPORTED, INFERRED, CONFLICTED, UNKNOWN = "VERIFIED", "SUPPORTED", "INFERRED", "CONFLICTED", "UNKNOWN"
STATUSES = (VERIFIED, SUPPORTED, INFERRED, CONFLICTED, UNKNOWN)

# Directness of an observation, strongest first. The analyzer's evidence "tier" maps onto these.
DIRECT, STRUCTURAL, DERIVED, HEURISTIC = "direct", "structural", "derived", "heuristic"
_DIRECTNESS_RANK = {DIRECT: 3, STRUCTURAL: 2, DERIVED: 1, HEURISTIC: 0}
_TIER_TO_DIRECTNESS = {"authoritative": DIRECT, "structural": STRUCTURAL, "derived": DERIVED, "heuristic": HEURISTIC}
# Base confidence by the directness of the single best observation. Documented, deterministic, and small enough that a lone
# heuristic never looks like a verified fact.
_BASE = {DIRECT: 0.90, STRUCTURAL: 0.70, DERIVED: 0.55, HEURISTIC: 0.35}
_CORROBORATION_BONUS = 0.05  # per independent corroborating observation (distinct source kind + distinct location)
_CONFLICT_PENALTY = 0.40
_COVERAGE_PENALTY = 0.05


@dataclass(frozen=True)
class EvidenceRef:
    """One observation that bears on a claim. ``id`` is stable so a claim can name it and a reader can trace it."""

    id: str
    field: str
    source_kind: str  # python_analyzer | evidence | rea | source_analysis | provider_metadata | repository
    directness: str  # direct | structural | derived | heuristic
    value: Any = None
    detail: str = ""
    location: str = ""
    agrees: Optional[bool] = None  # None: unknown; True supports the claim value; False contradicts it

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "field": self.field, "source_kind": self.source_kind, "directness": self.directness,
                "value": self.value, "detail": self.detail[:200], "location": self.location[:200], "agrees": self.agrees}


@dataclass
class Claim:
    """One conclusion with its evidence, status, derived confidence and remaining unknowns."""

    claim_id: str
    field: str
    value: Any
    status: str
    confidence: float
    supporting: List[str] = field(default_factory=list)  # evidence ids
    contradicting: List[str] = field(default_factory=list)
    explanation: str = ""
    unknowns: List[str] = field(default_factory=list)
    limitations: List[str] = field(default_factory=list)
    confidence_basis: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {"claim_id": self.claim_id, "field": self.field, "value": self.value, "status": self.status,
                "confidence": round(self.confidence, 3), "supporting": list(self.supporting),
                "contradicting": list(self.contradicting), "explanation": self.explanation,
                "unknowns": list(self.unknowns), "limitations": list(self.limitations),
                "confidence_basis": dict(self.confidence_basis)}


def _independent_key(ref: EvidenceRef) -> Tuple[str, str]:
    """The identity of the underlying observation, used so repeated copies are not counted as independent corroboration."""
    return (ref.source_kind, ref.location or ref.detail or str(ref.value))


def validate(field_name: str, value: Any, refs: Sequence[EvidenceRef], *, claim_id: Optional[str] = None,
             unknowns: Optional[Sequence[str]] = None, limitations: Optional[Sequence[str]] = None,
             coverage_gap: bool = False) -> Claim:
    """Turn a value and the observations bearing on it into a validated claim.

    ``refs`` are the observations; ``agrees`` on each says whether it supports the value (True), contradicts it (False) or
    is neutral (None). Two references that share an :func:`_independent_key` are the same observation: they are kept for
    provenance but counted once for corroboration."""
    supporting = [r for r in refs if r.agrees is True]
    contradicting = [r for r in refs if r.agrees is False]
    unknowns = list(unknowns or [])
    limitations = list(limitations or [])

    if value in (None, "", [], {}) and not supporting:
        return Claim(claim_id or field_name, field_name, value, UNKNOWN, 0.0, [], [r.id for r in contradicting],
                     explanation="No evidence established this field.", unknowns=unknowns or [f"{field_name} could not be established"],
                     limitations=limitations, confidence_basis={"reason": "no supporting evidence"})

    distinct_support = {_independent_key(r) for r in supporting}
    distinct_contradict = {_independent_key(r) for r in contradicting}
    best = max((_DIRECTNESS_RANK[r.directness] for r in supporting), default=-1)
    best_directness = next((d for d, rank in _DIRECTNESS_RANK.items() if rank == best), HEURISTIC)

    if supporting and contradicting:
        status = CONFLICTED
    elif best_directness == DIRECT:
        status = VERIFIED
    elif best_directness == STRUCTURAL:
        status = SUPPORTED
    else:
        status = INFERRED

    confidence = _BASE.get(best_directness, 0.0)
    confidence += _CORROBORATION_BONUS * max(0, len(distinct_support) - 1)
    if contradicting:
        confidence -= _CONFLICT_PENALTY
    if coverage_gap:
        confidence -= _COVERAGE_PENALTY
    confidence = max(0.0, min(1.0, confidence))

    if status == CONFLICTED:
        explanation = (f"Evidence disagrees: {len(supporting)} observation(s) support {value!r} and {len(contradicting)} "
                       f"contradict it. The disagreement is kept, not resolved silently.")
    elif status == VERIFIED:
        explanation = f"A direct observation supports {value!r}; nothing contradicts it."
    elif status == SUPPORTED:
        explanation = f"Structural evidence supports {value!r}; nothing contradicts it."
    else:
        explanation = f"Only indirect evidence supports {value!r}; treat it as an inference, not a fact."

    basis = {"best_directness": best_directness, "independent_support": len(distinct_support),
             "independent_contradict": len(distinct_contradict), "conflict": bool(contradicting), "coverage_gap": coverage_gap,
             "formula": "base(directness) + 0.05*(independent_support-1) - 0.40*conflict - 0.05*coverage_gap"}
    return Claim(claim_id or field_name, field_name, value, status, confidence, [r.id for r in supporting],
                 [r.id for r in contradicting], explanation, unknowns, limitations, basis)


# --------------------------------------------------------------------------- assembling references from the analysis


def _tier_to_directness(tier: str) -> str:
    return _TIER_TO_DIRECTNESS.get(str(tier or "").lower(), HEURISTIC)


def refs_from_analysis(a: Any) -> List[EvidenceRef]:
    """The observations the Analyzer already collected, as evidence references. Nothing new is inspected."""
    refs: List[EvidenceRef] = []
    for i, e in enumerate(getattr(a, "task_evidence", []) or []):
        refs.append(EvidenceRef(f"py:task:{i}", "task", "python_analyzer", _tier_to_directness(getattr(e, "tier", "")),
                                value=getattr(e, "task", None), detail=str(getattr(e, "detail", "")),
                                location=str(getattr(e, "source", "")), agrees=None))
    for i, c in enumerate(getattr(a, "capabilities", []) or []):
        for j, e in enumerate(getattr(c, "evidence", []) or []):
            ev = e if isinstance(e, dict) else {}
            refs.append(EvidenceRef(f"py:cap:{getattr(c, 'name', i)}:{j}", "capability", "python_analyzer",
                                    _tier_to_directness(ev.get("tier", "")), value=getattr(c, "name", None),
                                    detail=str(ev.get("detail", "")), location=str(ev.get("source", "")), agrees=None))
    for key, value in (getattr(a, "dependency_evidence", {}) or {}).items():
        refs.append(EvidenceRef(f"py:dep:{key}", "dependencies", "python_analyzer", STRUCTURAL, value=key,
                                detail=str(value), location="dependencies", agrees=None))
    for i, fw in enumerate(getattr(a, "frameworks", []) or []):
        refs.append(EvidenceRef(f"py:fw:{i}", "frameworks", "python_analyzer", STRUCTURAL, value=fw,
                                detail=f"framework detected: {fw}", location="frameworks", agrees=None))
    native = getattr(a, "native", {}) or {}
    for i, s in enumerate(native.get("signals") or []):
        refs.append(EvidenceRef(f"py:native:{i}", "repository_classification", "repository", STRUCTURAL, value=s,
                                detail=str(s), location="native.signals", agrees=None))
    # The Python Analyzer's own parameter figure (Hub safetensors metadata, or a size-based estimate). Recorded so REA's header
    # read and the collected evidence can corroborate or contradict it in the parameter_count claim.
    hw = getattr(a, "hardware", None)
    params = getattr(hw, "params", None)
    if isinstance(params, int) and not isinstance(params, bool):
        refs.append(EvidenceRef("py:params", "parameter_count", "python_analyzer", STRUCTURAL, value=params,
                                detail=f"{params:,} parameters ({getattr(hw, 'params_source', 'unknown source')})",
                                location="hardware.params", agrees=None))
    return refs


def refs_from_evidence(a: Any) -> List[EvidenceRef]:
    """References from the Phase-1 evidence bundle (only ``collected`` items are established facts).

    ``build_evidence_digest`` stores ``items`` as a keyed mapping; older callers and unit tests may pass a list. Both shapes are
    accepted so a collected fact is never silently dropped. A collected tensor-header item additionally contributes the one
    aggregate parameter count and the weight format it declared, which is what the ``parameter_count``/``weight_format`` claims
    corroborate against."""
    refs: List[EvidenceRef] = []
    bundle = getattr(a, "evidence", None) or {}
    items = bundle.get("items") if isinstance(bundle, dict) else None
    if not items:
        return refs
    rows = list(items.items()) if isinstance(items, dict) else [(None, it) for it in items]
    total_params = 0
    saw_params = False
    for key, it in rows:
        if not isinstance(it, dict) or it.get("status") != "collected":
            continue
        ident = key or it.get("key")
        location = str(it.get("location") or (it.get("provenance") or {}).get("location") or ident or "")
        refs.append(EvidenceRef(f"ev:{ident}", str(it.get("kind", "")), "evidence", DIRECT,
                                value=ident, detail=str(it.get("error") or it.get("kind") or ""), location=location, agrees=None))
        ts = it.get("tensor_summary") if isinstance(it.get("tensor_summary"), dict) else None
        if ts and _num(ts.get("parameters")) is not None:
            total_params += int(ts["parameters"])
            saw_params = True
        if ts and ts.get("format"):
            refs.append(EvidenceRef(f"ev:{ident}:fmt", "weight_format", "evidence", DIRECT, value=ts.get("format"),
                                    detail=f"{ident} declares format {ts.get('format')}", location=location, agrees=None))
    if saw_params:
        refs.append(EvidenceRef("ev:params:total", "parameter_count", "evidence", DIRECT, value=total_params,
                                detail="sum of the collected safetensors/GGUF header declarations", location="evidence bundle",
                                agrees=None))
    return refs


def _num(value: Any) -> Optional[int]:
    """A parameter count as an int, or None when the value is not a number."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def refs_from_rea(a: Any) -> List[EvidenceRef]:
    """References from the REA inspection result already attached to the analysis (``a.rea``).

    REA read the weight files' own headers, so its declarations are DIRECT observations of the artifact. Everything here is
    read from data the analysis already holds: nothing is re-inspected and nothing is invented. A REA finding that failed or a
    tool that was never called contributes no reference, so an unsupported claim can never be 'verified' by REA.

    Coverage discipline (never present a partial total as complete): REA's parameter sum is only a COMPLETE observation when it
    inspected every discovered weight file. When only some shards were read (``shards_inspected < shards_total``) the sum is a
    LOWER BOUND, so it is recorded under the separate ``parameter_count_lower_bound`` field - it can corroborate a larger Python
    figure but can never be presented as the total or contradict one."""
    rea = getattr(a, "rea", None)
    if not isinstance(rea, dict):
        return []
    refs: List[EvidenceRef] = []
    params_total = 0
    saw_params = False
    for s in rea.get("header_summaries") or []:
        if not isinstance(s, dict):
            continue
        file = str(s.get("file") or "weight file")
        if _num(s.get("parameters")) is not None:
            params_total += int(s["parameters"])
            saw_params = True
        if s.get("format"):
            refs.append(EvidenceRef(f"rea:fmt:{file}", "weight_format", "rea", DIRECT, value=str(s["format"]),
                                    detail=f"REA read the header of {file}", location=file, agrees=None))
        if s.get("architecture"):
            refs.append(EvidenceRef(f"rea:arch:{file}", "architecture", "rea", DIRECT, value=str(s["architecture"]),
                                    detail=f"the header of {file} declares architecture {s['architecture']}",
                                    location=file, agrees=None))
    if saw_params:
        total_files = _num(rea.get("shards_total")) or 0
        inspected = _num(rea.get("shards_inspected")) or 0
        complete = (not total_files) or inspected >= total_files
        if complete:
            refs.append(EvidenceRef("rea:params:total", "parameter_count", "rea", DIRECT, value=params_total,
                                    detail="sum of the safetensors/GGUF header declarations REA read", location="REA header inspection",
                                    agrees=None))
        else:
            refs.append(EvidenceRef("rea:params:lower_bound", "parameter_count_lower_bound", "rea", DIRECT, value=params_total,
                                    detail=f"a LOWER BOUND: REA read {inspected} of {total_files} discovered weight file(s)",
                                    location="REA header inspection", agrees=None))
    for i, call in enumerate(rea.get("calls") or []):
        if not isinstance(call, dict) or not call.get("ok"):
            continue
        tool = str(call.get("tool") or "tool")
        target = str(call.get("target") or "")
        refs.append(EvidenceRef(f"rea:call:{i}:{tool}", "rea_evidence", "rea", DIRECT, value=tool,
                                detail=str(call.get("output") or "REA observation")[:200], location=target, agrees=None))
    for i, inv in enumerate(rea.get("investigation") or []):
        if not isinstance(inv, dict) or not inv.get("ok"):
            continue
        refs.append(EvidenceRef(f"rea:inv:{i}:{inv.get('tool')}", "rea_investigation", "rea", DIRECT, value=inv.get("tool"),
                                detail=str(inv.get("question") or inv.get("detail") or "")[:200],
                                location=str(inv.get("target") or ""), agrees=None))
    return refs


def refs_from_source_analysis(src: Any) -> List[EvidenceRef]:
    """References from the deep source analysis (loading sites, inference sites, contract fields)."""
    refs: List[EvidenceRef] = []
    if src is None:
        return refs
    for i, s in enumerate(getattr(src, "load_sites", []) or []):
        refs.append(EvidenceRef(f"src:load:{i}", "loading_strategy", "source_analysis", STRUCTURAL, value=s.framework,
                                detail=f"{s.call} ({s.family}) in {s.module}:{s.line}", location=f"{s.module}:{s.line}", agrees=None))
    for i, s in enumerate(getattr(src, "infer_sites", []) or []):
        refs.append(EvidenceRef(f"src:infer:{i}", "inference", "source_analysis", STRUCTURAL, value=s.get("kind"),
                                detail=f"{s.get('call')} in {s.get('module')}:{s.get('line')}",
                                location=f"{s.get('module')}:{s.get('line')}", agrees=None))
    contract = getattr(src, "io_contract", {}) or {}
    for i, inp in enumerate(contract.get("inputs") or []):
        refs.append(EvidenceRef(f"src:in:{i}", "inputs", "source_analysis", STRUCTURAL, value=inp.get("modality"),
                                detail=str(inp.get("detail", "")), location=str(inp.get("source", "")), agrees=None))
    for i, out in enumerate(contract.get("outputs") or []):
        refs.append(EvidenceRef(f"src:out:{i}", "outputs", "source_analysis", STRUCTURAL, value=out.get("kind"),
                                detail=str(out.get("detail", "")), location=str(out.get("source", "")), agrees=None))
    return refs


def build_claims(a: Any, repo: Any = None, src: Any = None) -> List[Claim]:
    """Build the validated claim set for an analysis. Every claim names the evidence behind it.

    Evidence comes from four independent producers: the Python Analyzer (``refs_from_analysis``), the Phase-1 evidence bundle
    (``refs_from_evidence``), the deep source analysis (``refs_from_source_analysis``) and the REA inspection
    (``refs_from_rea``). Keeping them separate is what lets a claim be CONFLICTED when, for example, the Hub metadata and REA's
    header inspection disagree about the parameter count."""
    refs = [*refs_from_analysis(a), *refs_from_evidence(a), *refs_from_source_analysis(src), *refs_from_rea(a)]
    by_field: Dict[str, List[EvidenceRef]] = {}
    for r in refs:
        by_field.setdefault(r.field, []).append(r)
    coverage_gap = bool(getattr(a, "uncertainties", None)) and not getattr(a, "task", None)
    claims: List[Claim] = []

    # task: every task observation that agrees with the chosen task supports it; one that names a different task contradicts.
    task = getattr(a, "task", None)
    task_refs = []
    for r in by_field.get("task", []):
        r = EvidenceRef(r.id, r.field, r.source_kind, r.directness, r.value, r.detail, r.location,
                        agrees=(r.value == task) if task else None)
        task_refs.append(r)
    claims.append(validate("task", task, task_refs, unknowns=[] if task else ["task"],
                           limitations=["task is decided by weighted evidence voting, not by executing the model"],
                           coverage_gap=coverage_gap))

    # frameworks / library
    claims.append(validate("frameworks", sorted({r.value for r in by_field.get("frameworks", []) if r.value}) or None,
                           by_field.get("frameworks", []),
                           unknowns=[] if by_field.get("frameworks") else ["ML framework"],
                           limitations=["frameworks are read from dependencies and imports; the code is never run"]))
    claims.append(validate("library", getattr(a, "library", None),
                           by_field.get("frameworks", []), unknowns=[] if getattr(a, "library", "unknown") != "unknown" else ["library"]))

    # loading strategy (from the source analysis)
    loads = by_field.get("loading_strategy", [])
    claims.append(validate("loading_strategy", sorted({r.value for r in loads if r.value}) or None, loads,
                           unknowns=[] if loads else ["model loading strategy (no recognised loading call found)"],
                           limitations=["a loading call was observed statically; it was not executed"]))

    # inputs / outputs (from the source analysis contract)
    for field_name in ("inputs", "outputs"):
        f_refs = by_field.get(field_name, [])
        values = sorted({r.value for r in f_refs if r.value})
        claims.append(validate(field_name, values or None, f_refs,
                               unknowns=[] if values else [f"{field_name} (no static preprocessing/inference evidence)"],
                               limitations=["input/output fields come from static signatures and calls, never from running the model"]))

    # parameter count: Python (Hub metadata / size estimate), the collected evidence headers, and REA's own header read. Each is
    # an independent observation of the same quantity, so they corroborate when they agree and CONFLICT (keeping both values)
    # when they do not.
    params = by_field.get("parameter_count", [])
    values = sorted({_num(r.value) for r in params if _num(r.value) is not None})
    if len(values) > 1:  # disagreement: every ref supports the value it names, none of them support the others
        p_refs = [EvidenceRef(r.id, r.field, r.source_kind, r.directness, r.value, r.detail, r.location, agrees=(r.value == values[0]))
                  for r in params]
    else:  # agreement (or a single observation): every ref supports the one value
        p_refs = [EvidenceRef(r.id, r.field, r.source_kind, r.directness, r.value, r.detail, r.location,
                              agrees=(_num(r.value) == values[0]) if values else None) for r in params]
    claims.append(validate("parameter_count", values[0] if len(values) == 1 else (values or None), p_refs,
                           unknowns=[] if values else ["parameter count"],
                           limitations=["weight files are read by header only; tensor values are never inspected"],
                           coverage_gap=coverage_gap))

    # A REA parameter sum that covers only part of the discovered weight files is a LOWER BOUND, kept as its own claim so it is
    # never mistaken for the total (see refs_from_rea). It corroborates a Python figure at least as large as it, and contradicts
    # a smaller one, but is never presented as the complete count.
    lb_refs = by_field.get("parameter_count_lower_bound", [])
    lb_values = sorted({_num(r.value) for r in lb_refs if _num(r.value) is not None})
    py_val = _num(getattr(getattr(a, "hardware", None), "params", None))
    lb_unknowns: List[str] = []
    if lb_values:
        lb = lb_values[0]
        if py_val is None:
            lb_unknowns.append("the total parameter count (REA read only part of the weight files, so this is a lower bound)")
        elif lb > py_val * 1.02:
            lb_unknowns.append("the parameter count (REA's partial header sum already exceeds the Python figure)")
    claims.append(validate("parameter_count_lower_bound", lb_values[0] if len(lb_values) == 1 else (lb_values or None),
                           [EvidenceRef(r.id, r.field, r.source_kind, r.directness, r.value, r.detail, r.location,
                                        agrees=(_num(r.value) == lb_values[0]) if lb_values else None) for r in lb_refs],
                           unknowns=lb_unknowns or ([] if lb_values else ["a header-derived lower bound for the parameter count"]),
                           limitations=["REA read only some of the discovered weight files; the sum is a lower bound, not the total"]))

    # weight format(s): what the files declare, from the evidence bundle and REA's header read.
    fmts = by_field.get("weight_format", [])
    fmt_values = sorted({str(r.value) for r in fmts if r.value})
    claims.append(validate("weight_format", fmt_values or None,
                           [EvidenceRef(r.id, r.field, r.source_kind, r.directness, r.value, r.detail, r.location,
                                        agrees=(str(r.value) in fmt_values) if fmt_values else None) for r in fmts],
                           unknowns=[] if fmt_values else ["weight format (no file declared one)"],
                           limitations=["format is read from the file header, never by loading the weights"]))

    # architecture: the declared class name (config.json) and/or the family a GGUF header declares.
    arch_refs = by_field.get("architecture", [])
    arch_values = sorted({str(r.value) for r in arch_refs if r.value})
    claims.append(validate("architecture", arch_values or None,
                           [EvidenceRef(r.id, r.field, r.source_kind, r.directness, r.value, r.detail, r.location,
                                        agrees=(str(r.value) in arch_values) if arch_values else None) for r in arch_refs],
                           unknowns=[] if arch_values else ["declared architecture"],
                           limitations=["an architecture name is a declaration, not a verified running model"]))

    # REA-sourced evidence and investigation: a claim that the REA stage produced traceable findings. When REA did not run or
    # nothing succeeded, there are no refs and the claim is UNKNOWN - it is never silently "verified" by an absent inspection.
    for field_name in ("rea_evidence", "rea_investigation"):
        r_refs = by_field.get(field_name, [])
        r_values = sorted({str(r.value) for r in r_refs if r.value})
        claims.append(validate(field_name, r_values or None,
                               [EvidenceRef(r.id, r.field, r.source_kind, r.directness, r.value, r.detail, r.location,
                                            agrees=(str(r.value) in r_values)) for r in r_refs],
                               unknowns=[] if r_values else [f"{field_name} (REA produced no usable observation)"],
                               limitations=["REA reads weight-file headers and retained metadata; it never executes the model"]))

    # provider / revision
    prov_refs: List[EvidenceRef] = []
    if repo is not None:
        prov_refs.append(EvidenceRef("repo:provider", "provider", "provider_metadata", DIRECT, value=repo.provider,
                                     detail="repository provider", location=repo.repo_id, agrees=True))
        if repo.resolved_revision:
            prov_refs.append(EvidenceRef("repo:revision", "revision", "provider_metadata", DIRECT, value=repo.resolved_revision,
                                         detail="resolved revision", location=repo.repo_id, agrees=True))
    claims.append(validate("provider", getattr(repo, "provider", None),
                           [r for r in prov_refs if r.field == "provider"],
                           unknowns=[] if repo else ["provider"]))
    claims.append(validate("revision", getattr(repo, "resolved_revision", None) if repo else getattr(a, "revision", None),
                           [r for r in prov_refs if r.field == "revision"],
                           unknowns=[] if (repo and repo.resolved_revision) or getattr(a, "revision", None) else ["resolved revision"],
                           limitations=["a resolved revision pins the evidence to one commit; a branch tip can move"]))
    return claims


def claims_report(claims: Sequence[Claim]) -> Dict[str, Any]:
    """The machine-readable claim set, plus a status tally, for the final report."""
    by_status = {s: 0 for s in STATUSES}
    for c in claims:
        by_status[c.status] = by_status.get(c.status, 0) + 1
    return {"schema_version": "1.0",
            "claims": [c.to_dict() for c in claims],
            "summary": {"total": len(claims), "by_status": by_status,
                        "conflicted": [c.field for c in claims if c.status == CONFLICTED],
                        "unknown": [c.field for c in claims if c.status == UNKNOWN]}}
