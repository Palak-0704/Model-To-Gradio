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
    return refs


def refs_from_evidence(a: Any) -> List[EvidenceRef]:
    """References from the Phase-1 evidence bundle (only ``collected`` items are established facts)."""
    refs: List[EvidenceRef] = []
    bundle = getattr(a, "evidence", None) or {}
    items = bundle.get("items") if isinstance(bundle, dict) else None
    if not items:
        return refs
    for it in items:
        if not isinstance(it, dict) or it.get("status") != "collected":
            continue
        prov = it.get("provenance") or {}
        refs.append(EvidenceRef(f"ev:{it.get('key')}", str(it.get("kind", "")), "evidence", DIRECT,
                                value=it.get("key"), detail=str(it.get("error") or it.get("kind") or ""),
                                location=str(prov.get("location", "")), agrees=None))
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
    """Build the validated claim set for an analysis. Every claim names the evidence behind it."""
    refs = [*refs_from_analysis(a), *refs_from_evidence(a), *refs_from_source_analysis(src)]
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
