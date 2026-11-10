"""Evidence collection entry point: runs each source collector independently and assembles one ``EvidenceBundle``.

Collection only. Nothing here interprets a task, classifies a capability or generates code."""
from __future__ import annotations

import dataclasses
from typing import Any, Callable, List, Optional, Tuple, TypeVar

from ..config import Settings
from ..security import SafeFetcher, SecurityError, redact_secrets
from .context import CollectionContext, EvidenceLimits
from .files import collect_files
from .guards import validate_reference
from .hub import collect_hub
from .provenance import METHOD_DERIVED, SOURCE_REPO_INFO, Clock, Provenance, platform_provenance, utc_now
from .rea import collect_rea
from .schema import COLLECTED, INACCESSIBLE, INVALID, MISSING, SKIPPED, EvidenceBundle, EvidenceItem
from .tensors import collect_tensors

T = TypeVar("T")
ReaRunner = Callable[[Any, SafeFetcher], Any]


def _step(bundle: EvidenceBundle, key: str, clock: Clock, fn: Callable[[], T]) -> Optional[T]:
    """Run one collector; an unexpected crash becomes a ``skipped`` item instead of aborting the whole bundle."""
    try:
        return fn()
    except Exception as exc:
        bundle.add(EvidenceItem(f"{key}:error", "collector-error", SKIPPED, platform_provenance(clock, key), untrusted=False,
                                error=f"internal error in the '{key}' collector: {type(exc).__name__}: {redact_secrets(str(exc))[:200]}"))
        return None


def _add_all(bundle: EvidenceBundle, items: Optional[List[EvidenceItem]]) -> None:
    for item in items or []:
        bundle.add(item)


def _target_model(info: Any) -> Tuple[Optional[Any], str]:
    """The Hugging Face RepoInfo to collect for: the model itself, or a GitHub repository's linked primary model.

    Returns ``(None, "")`` for a GitHub repository with no linked model: that repository is collected from its OWN files by
    :func:`_collect_github` (raw.githubusercontent.com), so it is not a gap."""
    if info.source.kind == "huggingface":
        return info, ""
    model = getattr(info, "primary_model_info", None)
    if model is not None:
        return model, f"Evidence is for the Hugging Face model '{model.source.repo_id}' linked from '{info.source.repo_id}'."
    return None, ""


def _listing_fallback(bundle: EvidenceBundle, model: Any, clock: Clock) -> List[Tuple[str, Optional[int]]]:
    """Hub API unavailable: reuse the file listing of the earlier inspection (clearly marked as derived)."""
    files = list(getattr(model, "files", []) or [])
    prov = Provenance(SOURCE_REPO_INFO, "RepoInfo.files", METHOD_DERIVED, None, clock())
    if files:
        bundle.add(EvidenceItem("hub:files", "file-listing", COLLECTED, prov, data=[{"rfilename": n, "size": s} for n, s in files],
                                notes=["Hub API unavailable during collection; listing copied from the earlier inspection."]))
    else:
        bundle.add(EvidenceItem("hub:files", "file-listing", MISSING, prov, error="no file listing: the Hub API failed and no earlier listing exists"))
    return files


def _collect_model(bundle: EvidenceBundle, model: Any, ctx0: CollectionContext, requested: Optional[str],
                   inspect_tensors: bool = True) -> None:
    hub = _step(bundle, "hub", ctx0.clock, lambda: collect_hub(ctx0, requested))
    if hub is not None:
        _add_all(bundle, hub.items)
    listing = hub.listing if hub is not None and hub.listing is not None else None
    if listing is None and (bundle.get("hub:files") is None):
        listing = _listing_fallback(bundle, model, ctx0.clock)
    listing = listing or []
    sha = hub.sha if hub is not None else None
    bundle.resolved_revision = sha or requested
    ctx = dataclasses.replace(ctx0, revision=sha or requested or "main")
    _add_all(bundle, _step(bundle, "files", ctx.clock, lambda: collect_files(ctx, listing)))
    _add_all(bundle, _step(bundle, "tensors", ctx.clock, lambda: collect_tensors(ctx, listing, inspect_tensors)))


def _github_listing(info: Any) -> List[Tuple[str, Optional[int]]]:
    """The files to consider for a GitHub repository: its git tree (from the earlier inspection)."""
    return list(getattr(info, "files", []) or [])


def _collect_github(bundle: EvidenceBundle, info: Any, ctx0: CollectionContext, inspect_tensors: bool) -> None:
    """Collect evidence for a GitHub repository that has no linked Hugging Face model, from its OWN git-tree files.

    Only text/metadata files are fetched (README, config, requirements, source), through ``raw.githubusercontent.com`` at the
    pinned commit. Weight files are never downloaded; with ``inspect_tensors`` their safetensors headers are read over two range
    requests, otherwise they are recorded as deliberately skipped."""
    listing = _github_listing(info)
    ref = getattr(info, "commit_sha", None) or getattr(info, "default_branch", None) or info.source.revision or "main"
    ctx = dataclasses.replace(ctx0, revision=ref, provider="github")
    bundle.resolved_revision = getattr(info, "commit_sha", None) or None
    if not listing:
        bundle.add(EvidenceItem("source:files", "file-listing", MISSING, platform_provenance(ctx.clock, "github listing"), untrusted=False,
                                error="the earlier GitHub inspection returned no file listing to collect from"))
        return
    bundle.add(EvidenceItem("github:files", "file-listing", COLLECTED, platform_provenance(ctx.clock, "github listing"), untrusted=False,
                            data=[{"rfilename": n, "size": s} for n, s in listing],
                            notes=["File listing from the GitHub inspection; text/metadata files are fetched from raw.githubusercontent.com."]))
    _add_all(bundle, _step(bundle, "files", ctx.clock, lambda: collect_files(ctx, listing)))
    _add_all(bundle, _step(bundle, "tensors", ctx.clock, lambda: collect_tensors(ctx, listing, inspect_tensors)))


def collect_evidence(info: Any, fetcher: Optional[SafeFetcher] = None, *, settings: Optional[Settings] = None,
                     rea_report: Optional[Any] = None, rea_runner: Optional[ReaRunner] = None,
                     limits: Optional[EvidenceLimits] = None, clock: Optional[Clock] = None,
                     inspect_tensors: bool = True) -> EvidenceBundle:
    """Collect all safely obtainable evidence for the repository described by ``info`` (an ``m2g.inspector.RepoInfo``).

    Never raises for expected problems: every failure is an item with a status and message. ``rea_report`` (default:
    ``info.rea``) is recorded as evidence; if none exists and ``rea_runner`` is given it is called once to produce one."""
    settings = settings or Settings()
    fetcher = fetcher or SafeFetcher(settings)
    limits, clock = limits or EvidenceLimits(), clock or utc_now
    secrets = tuple(t for t in (settings.hf_token, settings.github_token) if t)
    model, why = _target_model(info)
    shown = model if model is not None else info
    bundle = EvidenceBundle(repo_id=shown.source.repo_id, source_kind=shown.source.kind, collected_at=clock(),
                            limits=dataclasses.asdict(limits))
    if why:
        bundle.notes.append(why)
    if model is not None:
        requested = getattr(model, "revision_sha", None) or model.source.revision
        bundle.requested_revision = model.source.revision
        try:
            validate_reference(model.source.repo_id, requested)
        except SecurityError as exc:
            bundle.add(EvidenceItem("source:reference", "source", INVALID, platform_provenance(clock, "source"), untrusted=False,
                                    error=f"repository reference rejected by the platform's validators: {exc}"))
        else:
            _collect_model(bundle, model, CollectionContext(fetcher, model.source.repo_id, requested or "main", limits, clock, secrets), requested,
                           inspect_tensors)
    elif info.source.kind == "github":
        # A GitHub repository with no linked model is collected from its OWN files, at the commit the inspector pinned.
        try:
            validate_reference(info.source.repo_id, getattr(info, "commit_sha", None) or getattr(info, "default_branch", None) or "main")
        except SecurityError as exc:
            bundle.add(EvidenceItem("source:reference", "source", INVALID, platform_provenance(clock, "source"), untrusted=False,
                                    error=f"repository reference rejected by the platform's validators: {exc}"))
        else:
            _collect_github(bundle, info, CollectionContext(fetcher, info.source.repo_id, "main", limits, clock, secrets), inspect_tensors)
    else:
        bundle.add(EvidenceItem("source:model", "source", SKIPPED, platform_provenance(clock, "source"), untrusted=False,
                                error=f"'{info.source.repo_id}' is a {info.source.kind} repository with no model files to collect."))
    report = rea_report if rea_report is not None else getattr(info, "rea", None)
    if report is None and rea_runner is not None:
        try:
            report = rea_runner(info, fetcher)
        except Exception as exc:
            bundle.add(EvidenceItem("rea:runner", "rea-status", INACCESSIBLE, platform_provenance(clock, "rea"), untrusted=False,
                                    error=f"the REA runner failed: {type(exc).__name__}: {redact_secrets(str(exc))[:200]}"))
    _add_all(bundle, _step(bundle, "rea", clock, lambda: collect_rea(report, clock, secrets)))
    return bundle
