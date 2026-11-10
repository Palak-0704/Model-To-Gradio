"""End-to-end orchestration: URL -> inspect -> analyze -> generate app -> generate notebook -> validate -> write.

Expected failures never raise: they come back in ``BuildResult.error`` with the failing ``stage`` and a message that has
been passed through the secret redactor. Unexpected bugs still raise so they are not hidden.
"""
from __future__ import annotations

import contextlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .analyzer import Analysis, analyze
from .colab_gen import generate_notebook, notebook_to_json
from .config import Settings
from .evidence_bridge import attach_to_info, gather_evidence
from .gradio_gen import (
    GenerationError, finalize_generated_code, generate_app, generate_requirements, join_package_code, split_app_code,
)
from .inspector import InspectionError, inspect_source
from .local_script import launcher_filename, windows_launcher
from .package_store import MANIFEST_NAME, build_manifest, cached_package_sources, fingerprints_for, model_lock, write_package
from .rea_client import ReaReport, not_invoked
from .security import (
    FetchError,
    SafeFetcher,
    SecurityError,
    ensure_within,
    parse_source_url,
    redact_secrets,
    safe_slug,
)
from .validate import ValidationError, combine_package_source, validate_app_code, validate_requirements_text

EXPECTED_ERRORS = (SecurityError, InspectionError, GenerationError, ValidationError, FetchError)
# The model package is app.py + loader.py + requirements.txt + one launcher named run_<folder>.bat (see ``launcher_filename``; its name
# depends on the model's folder, so it is added in ``_audit_artifacts``). analysis.json and colab_notebook.ipynb are the platform's
# own records for the status page and the Colab workflow, not part of the package.
GENERATED_ARTIFACTS = (
    "app.py",
    "loader.py",
    "requirements.txt",
    "analysis.json",
    "colab_notebook.ipynb",
)

def required_artifacts(folder_name: str) -> Tuple[str, ...]:
    """Every file a complete generated package has in its folder: ``GENERATED_ARTIFACTS`` plus the model's own launcher
    (``run_<folder>.bat``). One definition for every consumer that has to decide whether a package is complete."""
    return (*GENERATED_ARTIFACTS, launcher_filename(folder_name))


# Ordered steps reported to the UI/API as (key, label). ``BuildResult.stage`` keeps its older, coarser values
# (parse | inspect | rea | analyze | generate | write | done) for existing callers.
PIPELINE_STEPS = (
    ("inspection", "Model inspection"),
    ("analysis", "Model analysis"),
    ("eligibility", "Generation eligibility"),
    ("generation", "OpenCode app generation"),
    ("validation", "Code validation"),
    ("requirements", "Requirements generation"),
    ("notebook", "Notebook generation"),
    ("writing", "Final artifact writing"),
)
STEP_LABELS = dict(PIPELINE_STEPS)
# Step status: pending (not reached), ok, failed, blocked (generation intentionally refused: analysis-only), skipped (not run).
PENDING, OK, FAILED, BLOCKED, SKIPPED = "pending", "ok", "failed", "blocked", "skipped"
# Artifact status: written (on disk, content verified), not_generated (never produced), not_written (produced in memory, not saved),
# write_failed (the write stage failed for it).
WRITTEN, NOT_GENERATED, NOT_WRITTEN, WRITE_FAILED = "written", "not_generated", "not_written", "write_failed"
_STAGE_TO_STEP = {"start": "inspection", "parse": "inspection", "inspect": "inspection", "rea": "inspection",
                  "analyze": "analysis", "generate": "generation", "write": "writing"}
# generate_app() runs the eligibility re-check, the OpenCode call and its own validation in one call; its GenerationError text says which.
_ELIGIBILITY_PREFIXES = ("Analysis ", "App generation is blocked", "Cannot generate", "Only ", "No capability is supported",
                         "A GitHub analysis", "A Hugging Face analysis", "The model to load", "The linked ")


def _new_steps() -> Dict[str, Dict[str, str]]:
    return {k: {"label": label, "status": PENDING, "detail": ""} for k, label in PIPELINE_STEPS}


def _new_artifacts() -> Dict[str, Dict[str, str]]:
    return {n: {"status": NOT_GENERATED, "detail": ""} for n in GENERATED_ARTIFACTS}


@dataclass
class BuildResult:
    url: str
    stage: str = "start"  # parse | inspect | rea | analyze | generate | write | done
    analysis: Optional[Analysis] = None
    rea: Optional[ReaReport] = None
    app_code: Optional[str] = None
    requirements: Optional[str] = None
    notebook: Optional[Dict[str, Any]] = None
    out_dir: Optional[Path] = None
    files: List[str] = field(default_factory=list)
    error: Optional[str] = None
    notes: List[str] = field(default_factory=list)
    steps: Dict[str, Dict[str, str]] = field(default_factory=_new_steps)  # per-step status/detail, see PIPELINE_STEPS
    artifacts: Dict[str, Dict[str, str]] = field(default_factory=_new_artifacts)  # per-file status/detail, see GENERATED_ARTIFACTS
    failed_step: Optional[str] = None  # key of the PIPELINE_STEPS step that failed (None: nothing failed)
    evidence: Optional[Any] = None  # m2g.evidence.EvidenceBundle from the evidence stage; None when that stage was switched off
    loader_code: Optional[str] = None  # loader.py of the package (app_code is the single validated source both files are cut from)
    fingerprint: Optional[str] = None  # deterministic SHA-256 of what OpenCode is asked to write (None: not computed / not writing)
    reused_package: bool = False  # True: the existing package's app code was reused and OpenCode was not called
    kept_existing: bool = False  # True: a complete earlier package was left untouched until the new generation succeeded

    @property
    def ok(self) -> bool:
        return self.error is None and self.app_code is not None and self.notebook is not None

    def mark(self, step: str, status: str, detail: str = "") -> None:
        self.steps[step] = {"label": STEP_LABELS[step], "status": status, "detail": detail}

    @property
    def analysis_ok(self) -> bool:
        """Analysis succeeded, independent of whether an app could be generated afterwards."""
        return self.analysis is not None and self.steps["analysis"]["status"] == OK

    @property
    def analysis_only(self) -> bool:
        """Generation was intentionally refused for this model (not a failure)."""
        return self.steps["eligibility"]["status"] == BLOCKED

    def failure_summary(self) -> str:
        """'<failed step> failed: <actual error>' or an empty string."""
        if not self.error:
            return ""
        return f"{STEP_LABELS.get(self.failed_step or '', self.stage)} failed: {self.error}"

    @property
    def evidence_summary(self) -> Optional[Dict[str, Any]]:
        """Status counts of the collected evidence (None: the evidence stage did not run). Collection problems never fail a build."""
        if self.evidence is None:
            return None
        by_status = self.evidence.summary()["by_status"]
        return {"collected": by_status.get("collected", 0), "not_collected": sum(v for k, v in by_status.items() if k != "collected"),
                "by_status": by_status}

    def status_report(self) -> Dict[str, Any]:
        """Plain JSON-serialisable status for the UI and the API."""
        return {
            "analysis_succeeded": self.analysis_ok,
            "generation_succeeded": self.ok,
            "analysis_only": self.analysis_only,
            "artifacts_saved": all(v["status"] == WRITTEN for v in self.artifacts.values()),
            "failed_step": self.failed_step,
            "failed_step_label": STEP_LABELS.get(self.failed_step or "", "") if self.failed_step else "",
            "error": self.error,
            "stage": self.stage,
            "steps": [{"key": k, **self.steps[k]} for k, _ in PIPELINE_STEPS],
            "artifacts": {n: dict(v) for n, v in self.artifacts.items()},
            "files_written": [n for n, v in self.artifacts.items() if v["status"] == WRITTEN],
            "output_dir": str(self.out_dir) if self.out_dir else None,
            "evidence": self.evidence_summary,
        }

    @property
    def status(self) -> str:
        if self.error:
            return "error"
        return self.analysis.status if self.analysis else "unknown"


def _secrets(settings: Settings) -> List[str]:
    return [s for s in (settings.hf_token, settings.github_token) if s]


def _fail(res: BuildResult, stage: str, exc: Exception, settings: Settings, step: Optional[str] = None) -> BuildResult:
    res.stage = stage
    res.error = redact_secrets(f"{type(exc).__name__}: {exc}", _secrets(settings))
    res.failed_step = step or _STAGE_TO_STEP.get(stage, "inspection")
    res.mark(res.failed_step, FAILED, res.error)
    return res


def _generate_error_step(exc: Exception) -> str:
    """Which step a failure raised inside generate_app() belongs to."""
    text = str(exc)
    if isinstance(exc, ValidationError) or "failed validation" in text:
        return "validation"
    if text.startswith(_ELIGIBILITY_PREFIXES):
        return "eligibility"
    return "generation"


def _audit_artifacts(res: BuildResult, settings: Settings, produced: Dict[str, Optional[str]]) -> None:
    """Record, per expected file, whether it is really on disk with the content produced in this run. ``produced`` maps a file name
    to its text, or to None when it exists only as in-memory data (never counted as written)."""
    out = res.out_dir
    if out is None and res.analysis is not None:
        out = Path(settings.output_dir) / _out_slug(res.analysis)  # only used to point out stale files of an earlier run
    if res.analysis_only:
        why = "not generated: app generation is blocked for this model (analysis-only result)"
    elif res.failed_step:
        why = f"not generated: {STEP_LABELS[res.failed_step]} failed"
    else:
        why = "not generated"
    for name in (*GENERATED_ARTIFACTS, *((launcher_filename(out.name),) if out is not None else ())):
        on_disk = out is not None and (out / name).is_file()
        text = produced.get(name)
        if name in produced:
            if text is not None and res.out_dir is not None and on_disk and (out / name).read_text(encoding="utf-8") == text:
                res.artifacts[name] = {"status": WRITTEN, "detail": str(out / name)}
            elif res.stage == "write" and res.failed_step == "writing":
                res.artifacts[name] = {"status": WRITE_FAILED, "detail": "the write stage failed before this file was saved and verified"}
            else:
                reason = (f"{STEP_LABELS[res.failed_step]} failed" if res.failed_step else
                          "the existing generated package was kept unchanged" if res.kept_existing else "writing was not requested")
                res.artifacts[name] = {"status": NOT_WRITTEN, "detail": f"produced but not saved: {reason}"}
        else:
            stale = " An older file from a previous run is still in the folder and was not changed." if on_disk else ""
            res.artifacts[name] = {"status": NOT_GENERATED, "detail": why + "." + stale}


def _finish(res: BuildResult, settings: Settings, produced: Dict[str, Optional[str]], write: bool) -> BuildResult:
    """Close the books: steps that never ran become 'skipped' with the reason, and every artifact gets its status."""
    _audit_artifacts(res, settings, produced)
    res.files = sorted(n for n, v in res.artifacts.items() if v["status"] == WRITTEN)  # downloads/listing follow verified disk state
    for key, label in PIPELINE_STEPS:
        if res.steps[key]["status"] != PENDING:
            continue
        if key == "writing" and not write:
            res.mark(key, SKIPPED, "write=False: nothing was saved")
        elif key == "writing" and res.analysis_only and res.artifacts["analysis.json"]["status"] == WRITTEN:
            res.mark(key, OK, "analysis.json written (analysis-only result)")
        elif res.failed_step:
            res.mark(key, SKIPPED, f"not run: {STEP_LABELS[res.failed_step]} failed"
                     + ("; analysis.json was kept" if key == "writing" and res.artifacts["analysis.json"]["status"] == WRITTEN else ""))
        elif res.analysis_only:
            res.mark(key, SKIPPED, "not run: app generation is blocked for this model")
        else:
            res.mark(key, SKIPPED, "not run")
    return res


def _write(base: Path, slug: str, files: Dict[str, str]) -> Path:
    """Persist ``files`` into ``<base>/<slug>`` independent of the caller's working directory (the base is resolved here too, for direct
    callers that build Settings(output_dir=...) by hand). The files are staged and verified first and replace the live ones only then;
    a failure restores the previous package (see ``package_store.write_package``). .bat files get CRLF so the package runs when
    double-clicked."""
    return write_package(base, slug, files)


def _slug_for_ref(ref: Any) -> str:
    """``_out_slug`` for a parsed source reference, known before anything has been fetched (the model lock needs it that early)."""
    base = ref.repo_id.replace("/", "__")
    return safe_slug("github__" + base if ref.kind == "github" else base)


def _package_dir(output_dir: Any, slug: str) -> Path:
    return Path(output_dir).expanduser().resolve() / slug


def _has_complete_package(output_dir: Any, slug: str) -> bool:
    """A folder that already holds every file of a generated package. Such a package is only ever replaced by a complete new one."""
    folder = _package_dir(output_dir, slug)
    return folder.is_dir() and all((folder / name).is_file() for name in required_artifacts(slug))


def _verify_artifacts(out: Path, names: List[str]) -> None:
    """Fail the write stage unless every promised artifact exists as a regular file."""
    missing = [name for name in names if not (out / name).is_file()]
    if missing:
        raise OSError("Artifact write incomplete; missing: " + ", ".join(sorted(missing)))


def _out_slug(a: Analysis) -> str:
    """Output folder name. A GitHub run gets its own ``github__`` folder so it can never overwrite the output of a Hugging Face
    run for a model that happens to have the same owner/name."""
    base = a.source.repo_id.replace("/", "__")
    return safe_slug("github__" + base if a.source.kind == "github" else base)


def run_pipeline(url: str, settings: Optional[Settings] = None, fetcher: Optional[SafeFetcher] = None,
                 write: bool = True, rea: Optional[Callable[..., ReaReport]] = None, evidence: Any = True) -> BuildResult:
    """``rea`` is the REA inspection stage (see ``rea_client.make_runner``). Without it the stage is recorded as ``not_invoked``:
    the pipeline never claims an REA inspection that did not run.

    ``evidence`` controls the metadata-first evidence stage (``m2g.evidence``): True (default) runs the standard collector, a callable
    ``(info, fetcher, settings, rea_report) -> EvidenceBundle`` replaces it, False/None switches it off. The stage never fails a build:
    a collection failure is recorded as evidence and analysis and generation continue from the inspection metadata."""
    settings = settings or Settings()
    res = BuildResult(url=str(url)[:300])
    produced: Dict[str, Optional[str]] = {}  # artifacts that exist in this run: name -> text (None: in memory only, not yet saved)
    step = "inspection"  # the PIPELINE_STEPS step a failure at this point belongs to
    locks = contextlib.ExitStack()  # the model's lock, released in the finally clause at the end of this function
    try:
        res.stage = "parse"
        ref = parse_source_url(url)
        if write:  # Analyze and Generate share one lock per output folder: a second run for the same model waits here
            res.stage, step = "write", "writing"
            locks.enter_context(model_lock(settings.output_dir, _slug_for_ref(ref)))
            step = "inspection"
        res.stage = "inspect"
        fetcher = fetcher or SafeFetcher(settings)
        info = inspect_source(ref, fetcher, settings)
        res.stage = "rea"
        res.rea = info.rea = rea(info, fetcher) if rea is not None else not_invoked()
        evidence_note = ""
        if evidence:
            bundle = gather_evidence(info, fetcher, settings, res.rea, evidence if callable(evidence) else None)
            attach_to_info(info, bundle)  # fills metadata gaps from collected evidence and lets analyze() record the digest
            res.evidence = bundle
            counts = res.evidence_summary or {}
            evidence_note = f"; evidence: {counts.get('collected', 0)} collected, {counts.get('not_collected', 0)} not collected"
        res.mark("inspection", OK, "repository metadata read" + (f"; REA: {res.rea.label}" if getattr(res.rea, "label", "") else "") + evidence_note)
        res.stage, step = "analyze", "analysis"
        res.analysis = a = analyze(info, settings)
        res.mark("analysis", OK, f"status '{a.status}', task '{a.task or 'not determined'}'")
        if write:
            analysis_text = _analysis_json(a, settings)
            produced["analysis.json"] = analysis_text
            locks.enter_context(model_lock(settings.output_dir, _out_slug(a)))  # re-entrant; covers a slug that differs from the URL's
            if _has_complete_package(settings.output_dir, _out_slug(a)):
                # A working package is already here. Its analysis.json is not touched until the new package is complete, so a failure
                # in any later step cannot leave a new analysis next to the previous run's code.
                res.out_dir = _package_dir(settings.output_dir, _out_slug(a))
                res.kept_existing = True
                res.notes.append("The existing generated package stays unchanged until the new generation has succeeded.")
            else:
                # No package to protect: analysis.json is saved as soon as the analysis exists, so it survives a failure in any later step
                step = "writing"
                res.stage = "write"
                res.out_dir = _write(settings.output_dir, _out_slug(a), {"analysis.json": analysis_text})
                res.files = ["analysis.json"]
                res.stage, step = "analyze", "analysis"
        if not a.generatable:
            res.stage = "done"
            blocked = [*a.reasons, *a.generation_blocked_reasons]
            res.notes.append("No app generated: " + ("; ".join(blocked) or f"status '{a.status}'"))
            res.mark("eligibility", BLOCKED, "; ".join(blocked) or f"status '{a.status}'")
            return _finish(res, settings, produced, write)
        res.mark("eligibility", OK, "the analysis marks this model as supported")
        res.stage, step = "generate", "generation"
        gen_fp: Optional[str] = None
        if write:  # the existing package stands in for the OpenCode call when nothing that goes into it has changed
            res.fingerprint, gen_fp = fingerprints_for(a, settings)
            reuse, why = cached_package_sources(_package_dir(settings.output_dir, _out_slug(a)), res.fingerprint,
                                                a.model_ref.repo_id, a.revision)
            if why:
                res.notes.append(why)
            if reuse is not None:
                try:  # the reused code goes through every check a fresh generation does
                    res.app_code = finalize_generated_code(a, join_package_code(*reuse), settings)
                    res.reused_package = True
                except (GenerationError, ValidationError) as exc:
                    res.notes.append("The existing app code could not be reused (" + redact_secrets(str(exc), _secrets(settings))[:200]
                                     + "); it is being regenerated.")
        if not res.reused_package:
            try:
                res.app_code = generate_app(a, settings)
            except (GenerationError, ValidationError) as exc:
                step = _generate_error_step(exc)  # generate_app() also re-checks eligibility and validates what OpenCode wrote
                if step == "validation":
                    res.mark("generation", OK, "OpenCode returned code, which its own checks then rejected")
                raise
        produced["app.py"] = produced["loader.py"] = None
        if res.reused_package:
            res.notes.append("Reused the existing app code: the generation fingerprint is unchanged and the package passed its integrity check. OpenCode was not called.")
            res.mark("generation", OK, "reused the existing app code (generation fingerprint unchanged); OpenCode was not called")
        else:
            # A native analysis may deliberately remain a deterministic scaffold when
            # its artifact/fixture contract is incomplete.  Once those prerequisites
            # are present it follows the same OpenCode path as every other analysis;
            # do not report that live generation as a scaffold.
            native_scaffold = a.origin == "github-native" and "M2G_NATIVE_SCAFFOLD" in res.app_code
            res.mark("generation", OK, "the independent-GitHub scaffold was built (deterministic; OpenCode was not called)"
                     if native_scaffold else "OpenCode produced the app code")
        step = "validation"
        report = validate_app_code(res.app_code, settings)
        if report["model_id"] != a.model_ref.repo_id or report["revision"] != a.revision:
            raise GenerationError("Generated app does not match the analysis (model id / revision).")
        loader_py, app_py = split_app_code(res.app_code)  # the two package files are slices of the validated source
        validate_app_code(combine_package_source(app_py, loader_py), settings)  # ... and the pair is validated exactly as it is written
        res.loader_code = loader_py
        res.mark("validation", OK, "static checks passed (loader.py + app.py); model id and revision match the analysis")
        step = "requirements"
        res.requirements = generate_requirements(a)
        validate_requirements_text(res.requirements)
        produced["requirements.txt"] = None
        res.mark("requirements", OK, f"{len(res.requirements.splitlines())} requirement line(s) validated")
        step = "notebook"
        res.notebook = generate_notebook(a, res.app_code, settings)
        produced["colab_notebook.ipynb"] = None
        res.mark("notebook", OK, "Colab notebook built and validated")
        step = "writing"
        if write:
            res.stage = "write"
            files = {
                "app.py": app_py,
                "loader.py": loader_py,
                "requirements.txt": res.requirements,
                "colab_notebook.ipynb": notebook_to_json(res.notebook),
                "analysis.json": _analysis_json(a, settings),
                launcher_filename(_out_slug(a)): windows_launcher(_out_slug(a)),  # run_<model folder>.bat: its own launcher and .venv
            }
            produced.update(files)
            # the manifest (model/revision identity, fingerprint, a SHA-256 per artifact) travels in the same staged write
            readiness, readiness_reason = ("ready", "")
            if a.origin == "github-native":
                from .native_readiness import readiness_from_validation
                readiness, readiness_reason = readiness_from_validation((a.native or {}).get("runtime_validation") or {})
            from .native_readiness import incomplete_loader_reason
            placeholder = incomplete_loader_reason(res.app_code)
            if placeholder:  # whatever a validation record claims, a placeholder loader is never inference-ready and never deployable
                if readiness == "ready":  # a scaffold's own reason (already recorded below) is kept; only a false "ready" is corrected
                    readiness, readiness_reason = "scaffold", placeholder
                res.notes.append("Scaffold only, not deployable: " + placeholder + " The package is written for inspection; it cannot be "
                                 "launched locally or on Colab until a real loader is added.")
            package = {**files, MANIFEST_NAME: build_manifest(
                files, slug=_out_slug(a), source_repo_id=a.source.repo_id, source_kind=a.source.kind, model_id=a.model_ref.repo_id,
                revision=a.revision, task=a.task, fingerprint=res.fingerprint, generator=gen_fp,
                readiness=readiness,
                readiness_reason=(readiness_reason + (" " + (a.native or {}).get("readiness_reason", "") if readiness_reason and a.origin == "github-native" else ""))
                or ((a.native or {}).get("readiness_reason", "") if a.origin == "github-native" else ""))}
            res.out_dir = _write(settings.output_dir, _out_slug(a), package)
            _verify_artifacts(res.out_dir, list(package))
            res.files = sorted(files)  # the manifest is a hidden sidecar, not one of the package's artifacts
            res.kept_existing = False
            res.mark("writing", OK, f"{len(files)} files written and verified in {res.out_dir}; package manifest recorded")
        res.stage = "done"
        return _finish(res, settings, produced, write)
    except EXPECTED_ERRORS as exc:
        _fail(res, res.stage, exc, settings, step)
        return _finish(res, settings, produced, write)
    except OSError as exc:  # disk / permission problems while writing; an OSError from another step keeps that step's name
        _fail(res, "write" if step == "writing" else res.stage, exc, settings, step)
        return _finish(res, settings, produced, write)
    finally:
        locks.close()  # always: success, expected failure, OSError or an unexpected bug that propagates


def _analysis_json(a: Analysis, settings: Settings) -> str:
    return redact_secrets(json.dumps(a.to_dict(), indent=2, default=str, ensure_ascii=False), _secrets(settings)) + "\n"


def summarize(res: BuildResult) -> str:
    """Human-readable markdown summary used by both the CLI and the platform UI."""
    if res.error:
        kept = ", ".join(f"`{f}`" for f in res.files)
        head = f"### {res.failure_summary()}"
        note = ("\n\nThe analysis itself completed." + (f" Saved: {kept}." if kept else " Nothing was saved.")) if res.analysis_ok else ""
        return f"{head}\n\nStage: `{res.stage}`.{note}"
    a = res.analysis
    if a is None:
        return "No analysis available."
    hw = a.hardware
    lines = [
        f"### {a.source.repo_id}  \n**Status:** `{a.status}` · **Task:** `{a.task}` (confidence {a.task_confidence:.2f}) · "
        f"**Library:** `{a.library}`" + (f" · **Host:** `{a.host}`" if a.host else ""),
        "",
        "**Evidence for the task** (strongest first):",
    ]
    tier_rank = {"authoritative": 0, "structural": 1}
    lines += [f"- [{e.tier}] {e.signal} - {e.detail} _(source: {e.source}, weight {e.weight})_" for e in
              sorted(a.task_evidence, key=lambda e: (tier_rank.get(e.tier, 2), -e.weight))[:5]] or ["- none"]
    if a.frameworks:
        lines += ["", "**Frameworks:** " + ", ".join(a.frameworks)]
    if a.input_modalities or a.output_modalities:
        lines += ["**Modalities:** " + (", ".join(a.input_modalities) or "unknown") + " → " + (", ".join(a.output_modalities) or "unknown")]
    if a.primary_model:
        lines += [f"**Primary Hugging Face model:** `{a.primary_model}`"]
    if a.model_source is not None and a.generatable:
        lines += [f"**App generated from the linked Hugging Face model:** `{a.model_source.repo_id}` "
                  "(the GitHub code was not run, imported or installed)"]
    if a.origin == "github-native":
        n = a.native or {}
        lines += ["", "**Independent GitHub model (no Hugging Face model needed): scaffold only, no inference is implemented.**"]
        for label, key in (("Confirmed", "confirmed"), ("Inferred", "inferred"), ("Unknown", "unknown")):
            if n.get(key):
                lines += [f"**{label}:** " + "; ".join(str(x) for x in n[key][:4])]
    if a.inputs or a.outputs:
        lines += ["", "**Inputs:** " + ", ".join(f"{i.get('name', '?')} ({i.get('component', '?')})" for i in a.inputs),
                  "**Outputs:** " + ", ".join(f"{o.get('name', '?')} ({o.get('component', '?')})" for o in a.outputs)]
    lines += ["", f"**Hardware:** {hw.recommended_runtime}" + (f" · ~{hw.est_vram_gb_fp16} GiB VRAM (fp16)" if hw.est_vram_gb_fp16 else "")
              + f" · params: {hw.params if hw.params else 'unknown'} ({hw.params_source})"]
    if a.uncertainties:
        lines += ["", "**Uncertainties:**"] + [f"- {u}" for u in a.uncertainties[:6]]
    if a.reasons:
        lines += ["", "**Why it cannot be generated:**"] + [f"- {r}" for r in a.reasons]
    if a.generation_blocked_reasons:
        lines += ["", "**Why app generation is blocked:**"] + [f"- {r}" for r in a.generation_blocked_reasons]
    if a.warnings:
        lines += ["", "**Warnings:**"] + [f"- {w}" for w in a.warnings[:8]]
    if res.notes:
        lines += [""] + [f"_{n}_" for n in res.notes]
    if res.files and res.out_dir:
        lines += ["", f"**Written to:** `{res.out_dir}` - " + ", ".join(res.files)]
    return "\n".join(lines)
