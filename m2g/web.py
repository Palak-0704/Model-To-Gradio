"""The platform's own web UI (Gradio). Runs on the user's laptop; model inference does NOT run here.

``handle_submit`` is a plain function so it can be tested without Gradio. Gradio is imported lazily in ``build_ui``.
The Colab panel is a thin wiring layer over ``m2g.colab_panel.ColabPanel`` (all logic lives there, testable offline).
"""
from __future__ import annotations

import logging
import os
import socket
import threading
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import List, Optional, Tuple

from .analyzer import Analysis
from .colab_gen import _md_safe
from .colab_panel import ColabPanel
from .colab_workflow import CPU_LABEL
from .config import Settings, check_write_access
from .local_panel import LocalPanel
from .pipeline import PIPELINE_STEPS, BuildResult, run_pipeline, summarize
from .rea_client import make_runner as make_rea_runner
from .security import SafeFetcher
from .ui_errors import configure_logging, format_exception, guarded, scrub

# Set to True by main(). While False (tests, scripts that call handle_submit directly) the REA stage is recorded as not invoked.
REA_ENABLED = False

EXAMPLES = [
    "https://huggingface.co/distilbert/distilbert-base-uncased-finetuned-sst-2-english",
    "https://huggingface.co/openai/whisper-tiny",
    "https://huggingface.co/google/vit-base-patch16-224",
]

# Small, self-contained stylesheet. It only styles elements this file tags with an ``m2g-*`` class and relies on
# Gradio's own theme variables, so it follows light/dark mode. It is injected with ``gr.HTML`` because ``css=`` moved from
# ``gr.Blocks()`` to ``launch()`` in Gradio 6 while this project supports ``gradio>=5.0``.
CSS = """
.gradio-container { max-width: 1040px !important; margin-left: auto !important; margin-right: auto !important; }
.m2g-header h1 { font-size: 1.6rem; font-weight: 650; margin: 0 0 .25rem; }
.m2g-header p { margin: 0; color: var(--body-text-color-subdued); }
.m2g-step { margin-top: 1.5rem; }
.m2g-step h2 { font-size: 1.1rem; font-weight: 600; margin: 0; padding-bottom: .4rem;
               border-bottom: 1px solid var(--border-color-primary); }
.m2g-hint, .m2g-hint p { color: var(--body-text-color-subdued); font-size: .9rem; }
.m2g-status { border: 1px solid var(--border-color-primary); border-radius: var(--radius-md); padding: .75rem 1rem; }
.m2g-status h3 { font-size: 1rem; margin: 0 0 .5rem; }
.m2g-log textarea { font-family: var(--font-mono); font-size: .8rem; }
.m2g-card { border: 1px solid var(--border-color-primary); border-radius: var(--radius-lg); padding: 1rem 1.1rem !important;
            background: var(--background-fill-primary); gap: .85rem !important; }
.m2g-colab-card { display: flex; flex-direction: column; gap: .75rem; }
.m2g-conn { display: flex; align-items: center; gap: .7rem; }
.m2g-conn-title, .m2g-run-title { font-weight: 600; }
.m2g-sub { color: var(--body-text-color-subdued); font-size: .88rem; font-weight: 400; }
.m2g-dot { display: inline-block; flex: none; width: .65rem; height: .65rem; border-radius: 50%; margin-right: .35rem; }
.m2g-conn .m2g-dot { margin-right: 0; }
.m2g-dot-ok { background: #22a06b; box-shadow: 0 0 0 3px rgba(34, 160, 107, .2); }
.m2g-dot-bad { background: #d64545; box-shadow: 0 0 0 3px rgba(214, 69, 69, .2); }
.m2g-dot-idle { background: #8a8f98; box-shadow: 0 0 0 3px rgba(138, 143, 152, .2); }
.m2g-note { font-size: .9rem; padding: .55rem .75rem; border-radius: var(--radius-md);
            background: var(--background-fill-secondary); }
.m2g-run { display: flex; flex-direction: column; gap: .45rem; border-top: 1px solid var(--border-color-primary);
           padding-top: .8rem; }
.m2g-bar { height: 6px; border-radius: 99px; background: var(--background-fill-secondary); overflow: hidden; }
.m2g-bar > div { height: 100%; background: var(--color-accent); transition: width .4s ease; }
.m2g-steps { display: flex; flex-wrap: wrap; gap: .4rem; list-style: none; margin: 0; padding: 0; font-size: .8rem; }
.m2g-steps li { padding: .15rem .65rem; border-radius: 99px; border: 1px solid var(--border-color-primary);
                color: var(--body-text-color-subdued); }
.m2g-steps li.done { color: var(--body-text-color); }
.m2g-steps li.current { color: var(--body-text-color); font-weight: 600; border-color: var(--color-accent); }
.m2g-url { font-weight: 600; word-break: break-all; color: var(--link-text-color, var(--color-accent)); }
.m2g-warn { font-size: .88rem; color: #b7791f; }
.m2g-note-bad { border-left: 3px solid #d64545; }
.m2g-urlcard { border: 1px solid var(--border-color-primary); border-radius: var(--radius-md); padding: .85rem 1rem !important;
               background: var(--background-fill-secondary); gap: .55rem !important; }
.m2g-urlbox-label { font-size: .78rem; font-weight: 600; letter-spacing: .04em; text-transform: uppercase;
                    color: var(--body-text-color-subdued); margin-bottom: .3rem; }
.m2g-url-placeholder { color: var(--body-text-color-subdued); font-style: italic; }
.m2g-urlcard .m2g-url { display: block; font-size: 1.05rem; margin: .1rem 0 .35rem; }
@media (max-width: 640px) {
  .m2g-actions { flex-wrap: wrap; }
  .m2g-actions > * { flex: 1 1 45% !important; min-width: 0 !important; }
  .m2g-urlcard button { width: 100%; }
  .m2g-urlcard .m2g-url { font-size: .95rem; }
}
"""

HEADER_MD = (
    "# Model to Gradio\n"
    "Paste a Hugging Face model URL or a GitHub AI repository. Only metadata is inspected: no repository code is run and "
    "no model weights are downloaded. You get a Gradio app, requirements, an analysis and a Colab notebook."
)


def next_steps(res: BuildResult) -> str:
    """Honest guidance on where to test: small CPU-friendly models locally, everything else on Colab."""
    if not res.ok or res.analysis is None or res.out_dir is None:
        return ""
    hw = res.analysis.hardware
    colab = ("**Colab (recommended for GPU models):** open *3. Run on Google Colab* below. It uses Google's official "
             "Colab CLI to create a runtime, install the dependencies, launch this app and show the public Gradio link. "
             "A Google sign-in alone does not guarantee a GPU. Fallback without the CLI: upload `colab_notebook.ipynb` at "
             "https://colab.research.google.com (File > Upload notebook), pick a GPU runtime, then Run all.")
    if hw.runs_locally_cpu and not hw.needs_gpu:
        gb = f" (about {hw.weights_gb_fp16} GB of weights will download)" if hw.weights_gb_fp16 else ""
        local = (f"**Locally (CPU is enough for this model{gb}), in PowerShell:**\n```\ncd \"{res.out_dir}\"\n"
                 "python -m venv .venv\n.\\.venv\\Scripts\\Activate.ps1\npip install -r requirements.txt\npython app.py\n```")
        return local + "\n\n" + colab
    return colab


# ---------------------------------------------------------------------------------------------------- result rendering
RESULTS_PLACEHOLDER = "Results appear here after you click *Analyze and generate*."
ANALYZING_MD = ("### Analyzing…\n\nReading metadata only: no repository code is run and no model weights are downloaded.")
PASTE_URL_MD = "### Please paste a Hugging Face or GitHub repository URL."
UNAVAILABLE_TITLE = "Gradio App Generation Not Available"
UNAVAILABLE_TEXT = ("This model does not provide enough verified information to safely generate a working Gradio "
                    "application. Only model analysis is available.")
GENERATED, UNAVAILABLE, FAILED = "generated", "unavailable", "failed"


def _m(text: object, limit: int = 600) -> str:
    """Untrusted metadata (repo names, reasons, errors) made safe for a markdown cell."""
    return _md_safe(text, limit)


def _code(text: object) -> str:
    return "`" + str(text).replace("`", "'").replace("\n", " ")[:300] + "`"


def generation_state(res: BuildResult) -> str:
    """The three outcomes the page must tell apart: app generated / analysis only / analysis failed."""
    if res.error or res.analysis is None:
        return FAILED
    return GENERATED if res.ok else UNAVAILABLE


def _saved_files(res: BuildResult) -> List[str]:
    return [n for n, v in res.artifacts.items() if v["status"] == "written"]


def generation_banner(res: BuildResult) -> str:
    """A plain-language statement of what happened, shown before any technical detail."""
    state = generation_state(res)
    where = f" in {_code(res.out_dir)}" if res.out_dir else ""
    if state == GENERATED:
        if len(_saved_files(res)) < len(res.artifacts):
            missing = [n for n, v in res.artifacts.items() if v["status"] != "written"]
            return ("### Analysis completed and app generated, but not all files were saved\n\nSaved" + where + ": "
                    + (", ".join(_code(f) for f in _saved_files(res)) or "nothing") + ".\n\n**Not saved:** "
                    + ", ".join(_code(f) for f in missing) + ".")
        if res.analysis is not None and res.analysis.origin == "github-native":  # a scaffold is not a working model app
            return (f"### Analysis completed and Gradio scaffold generated\n\nA Gradio SCAFFOLD was generated{where}: "
                    + ", ".join(_code(f) for f in res.files) + ". It does not run the model: the repository's architecture and inference "
                    "contract are not established, so no inference code was written.")
        return (f"### Analysis completed and Gradio app generated\n\nA working Gradio app was generated{where}: "
                + ", ".join(_code(f) for f in res.files) + ".")
    if state == UNAVAILABLE:
        a = res.analysis
        reasons = [_m(r, 700) for r in a.reasons] or [f"No specific reason was recorded (analysis status: {_code(a.status)})."]
        written = ", ".join(_code(f) for f in res.files) or "`analysis.json`"
        return "\n".join([f"### {UNAVAILABLE_TITLE}", "", "**Analysis completed, but no app was generated.**", "", UNAVAILABLE_TEXT, "",
                          "**Why generation was refused:**", *[f"- {r}" for r in reasons], "",
                          f"**No working Gradio app was generated.** Only {written} was written{where}."])
    step = STEP_LABELS_UI.get(res.failed_step or "", "")
    head = "### Analysis failed"
    if res.analysis_ok:
        head = ("### Analysis completed, but saving the files failed" if res.failed_step == "writing"
                else "### Analysis completed, but app generation failed")
    lines = [head, "", f"**Failed at stage {_code(res.stage)}:** {_m(res.error or 'The analysis produced no result.', 800)}"]
    if step:
        lines.append(f"**Failed step:** {step}")
    lines.append("")
    if res.analysis_ok:
        kept = _saved_files(res)
        what = ("The files could not all be saved." if res.failed_step == "writing"
                else "The analysis itself completed, but a working Gradio app could not be produced, so none was generated.")
        lines.append(what
                     + (f" Saved{where}: " + ", ".join(_code(f) for f in kept) + "." if kept else " Nothing was saved."))
    else:
        lines.append("No analysis and no Gradio app were produced.")
    return "\n".join(lines)


STEP_LABELS_UI = dict(PIPELINE_STEPS)
_STEP_WORDS = {"ok": "completed", "failed": "**FAILED**", "blocked": "blocked (analysis only)", "skipped": "not run",
               "pending": "not reached"}
_ARTIFACT_WORDS = {"written": "saved", "not_generated": "not generated", "not_written": "generated, not saved",
                   "write_failed": "**save failed**"}


def _cell(text: object, limit: int = 300) -> str:
    return _m(text, limit).replace("|", "/").replace("\n", " ")


def progress_line(res: BuildResult) -> str:
    """One line that keeps the milestones apart: analysis, code generation, validation, files saved."""
    def word(key: str) -> str:
        return _STEP_WORDS.get(res.steps[key]["status"], res.steps[key]["status"])
    started = res.steps["generation"]["status"] not in ("pending", "skipped")
    saved = len(_saved_files(res))
    return " · ".join([f"Analysis {word('analysis')}", "Code generation " + (word("generation") if started else "not started"),
                       f"Validation {word('validation')}", f"Files saved: {saved}/{len(res.artifacts)}"])


def pipeline_status_md(res: BuildResult) -> str:
    """Every pipeline step and every expected file with its own status, plus the actual error of a failed step."""
    lines = ["### Pipeline status", "", progress_line(res), "", "| Step | Status | Details |", "|---|---|---|"]
    for key, label in PIPELINE_STEPS:
        st = res.steps[key]
        lines.append(f"| {label} | {_STEP_WORDS.get(st['status'], st['status'])} | {_cell(st['detail'])} |")
    lines += ["", "| File | Status | Details |", "|---|---|---|"]
    for name, st in res.artifacts.items():
        lines.append(f"| `{name}` | {_ARTIFACT_WORDS.get(st['status'], st['status'])} | {_cell(st['detail'])} |")
    return "\n".join(lines)


def analysis_overview(a: Analysis) -> str:
    """The key facts as a short readable list: status, model, task, framework, inputs/outputs, warnings."""
    def named(items: List[dict]) -> str:
        return ", ".join(f"{_m(i.get('name', '?'), 60)} ({_m(i.get('component', '?'), 40)})" for i in items) or "not available"

    kind = "GitHub repository" if a.host == "github" else "Hugging Face model"
    lines = ["### Analysis result", "", f"- **Status:** {_code(a.status)}", f"- **Model / repository:** {_code(a.source.repo_id)} ({kind})"]
    if a.model_source is not None and a.generatable:
        lines.append(f"- **Model used by the app:** {_code(a.model_source.repo_id)} (the repository's linked Hugging Face model; "
                     "the GitHub code is never run)")
    elif a.primary_model:
        lines.append(f"- **Linked Hugging Face model:** {_code(a.primary_model)} (not used for generation)")
    lines.append(f"- **Task:** {_code(a.task or 'not determined')}" + (f" (confidence {a.task_confidence:.2f})" if a.task else ""))
    frameworks = list(dict.fromkeys(x for x in [a.library if a.library != "unknown" else "", *a.frameworks] if x))
    lines += ["- **Framework:** " + (", ".join(_code(x) for x in frameworks) or "not determined"),
              f"- **Inputs:** {named(a.inputs)}", f"- **Outputs:** {named(a.outputs)}"]
    if a.warnings:
        lines += ["", "**Warnings:**", *[f"- {_m(w, 700)}" for w in a.warnings[:8]]]
        if len(a.warnings) > 8:
            lines.append(f"- (+{len(a.warnings) - 8} more in the technical details)")
    return "\n".join(lines)


def rea_panel(res: BuildResult) -> str:
    """REA execution status (completed / partially completed / failed / not applicable / not invoked), the tools that really ran
    and the execution log. Everything shown comes from the report; nothing is inferred."""
    rep = res.rea
    if rep is None:
        return ""
    ran = [f"{_code(c.tool)} {'ok' if c.ok else 'FAILED'}" + (f" ({_m(c.target, 80)})" if c.tool != "binary_session" and c.target else "")
           for c in rep.calls]
    lines = [f"### REA inspection: {rep.label}", "", _m(rep.reason, 700) or "No details were recorded.", "",
             "- **REA tools executed:** " + (", ".join(ran) or "none (no REA tool was called)")]
    if rep.server:
        lines.append(f"- **REA server:** {_code(str(rep.server.get('name', '?')) + ' ' + str(rep.server.get('version', '')))} "
                     f"- tools advertised: {rep.tools_discovered}")
    if rep.findings:
        lines += ["", "**Findings:**", *[f"- {_m(f, 400)}" for f in rep.findings[:6]]]
    log_text = "\n".join(rep.log)[-4000:].replace("`", "'")
    lines += ["", "<details><summary>REA execution log</summary>", "", "```text", log_text, "```", "", "</details>"]
    return "\n".join(lines)


def render_result(res: BuildResult) -> str:
    """Banner (what happened) + readable overview + REA status/log + collapsed technical details + next steps."""
    parts = [generation_banner(res), pipeline_status_md(res)]
    if res.analysis is not None:
        parts.append(analysis_overview(res.analysis))
    if res.rea is not None:
        parts.append(rea_panel(res))
    if res.analysis is not None and not res.error:
        parts.append("<details><summary>Technical details</summary>\n\n" + summarize(res) + "\n\n</details>")
    extra = next_steps(res)
    if extra:
        parts.append("---\n" + extra)
    return "\n\n".join(parts)


def _handle_submit_ex(url: str, settings: Optional[Settings] = None, fetcher: Optional[SafeFetcher] = None
                      ) -> Tuple[str, str, str, List[str], Optional[BuildResult]]:
    settings = settings or Settings.from_env()
    if not url or not str(url).strip():
        return PASTE_URL_MD, "", "", [], None
    ok, msg = check_write_access(settings.output_dir)
    if not ok:
        return f"### Cannot write to the output folder\n\n`{settings.output_dir}`: {msg}", "", "", [], None
    try:
        # REA is switched on by main() only, so direct calls (tests, scripts) never launch the real REA server; the stage is then
        # recorded as 'not_invoked'. The extra keyword is passed only when a runner exists (callers may wrap run_pipeline).
        extra = {"rea": make_rea_runner(settings)} if REA_ENABLED else {}
        res = run_pipeline(str(url).strip(), settings, fetcher=fetcher, write=True, **extra)
    except Exception as exc:  # unexpected bug: do not leak internals to the page, but DO log them (redacted)
        logging.getLogger("m2g.ui").error("Analyze and generate failed for %r:\n%s", str(url)[:200],
                                          format_exception(exc, [s for s in (settings.hf_token, settings.github_token) if s]))
        return (f"### Analysis failed\n\nInternal error `{type(exc).__name__}`: {_m(scrub(str(exc)), 400)} - see the console log "
                "for the full traceback. No pipeline status is available for this run."), "", "", [], None
    summary = render_result(res)
    files = [str(res.out_dir / f) for f in res.files] if res.out_dir else []
    return summary, res.app_code or "", res.requirements or "", files, res


def handle_submit(url: str, settings: Optional[Settings] = None, fetcher: Optional[SafeFetcher] = None
                  ) -> Tuple[str, str, str, List[str]]:
    """Returns (summary_markdown, app_code, requirements_text, downloadable_file_paths)."""
    summary, code, reqs, files, _ = _handle_submit_ex(url, settings, fetcher)
    return summary, code, reqs, files


# ------------------------------------------------------------------------------------------------ stale-run protection
@dataclass(frozen=True)
class RunTicket:
    """Identifies one click on Analyze: which browser session, which run number, and exactly which URL."""
    sid: str
    number: int
    url: str


class RunTracker:
    """Remembers the newest Analyze run of each browser session. A slower, older run is *stale*: its result is never shown."""

    def __init__(self, max_sessions: int = 256) -> None:
        self._lock = threading.Lock()
        self._latest: "OrderedDict[str, int]" = OrderedDict()
        self._counter = 0
        self._max = max_sessions

    def begin(self, sid: str) -> int:
        with self._lock:
            self._counter += 1
            self._latest[sid] = self._counter
            self._latest.move_to_end(sid)
            while len(self._latest) > self._max:
                self._latest.popitem(last=False)
            return self._counter

    def is_current(self, ticket: Optional[RunTicket]) -> bool:
        with self._lock:
            return ticket is not None and self._latest.get(ticket.sid) == ticket.number

    def invalidate(self, ticket: Optional[RunTicket]) -> None:
        """A page reload: whatever was still running for that session may no longer be shown."""
        if ticket is not None:
            with self._lock:
                self._latest[ticket.sid] = -1


@dataclass
class AnalysisView:
    summary: str = RESULTS_PLACEHOLDER
    app_code: str = ""
    requirements: str = ""
    files: List[str] = field(default_factory=list)
    result: Optional[BuildResult] = None

    @property
    def status(self) -> Optional[dict]:
        """The run's JSON-serialisable pipeline status (steps, artifacts, failed step, error) for the API, or None."""
        return self.result.status_report() if self.result is not None else None


def begin_analysis(tracker: RunTracker, previous: Optional[RunTicket], url: str) -> Tuple[RunTicket, AnalysisView]:
    """Step 1 of Analyze, instant: take a new run number and return the CLEARED view (no old summary, code, files)."""
    sid = previous.sid if previous is not None else uuid.uuid4().hex
    text = str(url or "").strip()
    ticket = RunTicket(sid, tracker.begin(sid), text)
    return ticket, AnalysisView(summary=ANALYZING_MD if text else PASTE_URL_MD)


def finish_analysis(ticket: Optional[RunTicket], tracker: RunTracker, settings: Optional[Settings] = None,
                    fetcher: Optional[SafeFetcher] = None) -> Optional[AnalysisView]:
    """Step 2: run the analysis for the ticket's URL. Returns None (show nothing) when a newer run or a reload superseded it.

    Files the stale run wrote stay on disk; they are simply not displayed.
    """
    if not tracker.is_current(ticket):
        return None
    summary, code, reqs, files, res = _handle_submit_ex(ticket.url, settings, fetcher)
    if not tracker.is_current(ticket):  # a newer click or a reload happened while this one was running
        return None
    return AnalysisView(summary, code, reqs, files, res)


def _step(gr, title: str, hint: str = "") -> None:
    """A numbered section heading with an optional one-line hint."""
    gr.Markdown(f"## {title}", elem_classes=["m2g-step"])
    if hint:
        gr.Markdown(hint, elem_classes=["m2g-hint"])


def _build_local_panel(gr, app_code, reqs, files, target, panel: LocalPanel):
    _step(gr, "Run on My System (Local)",
          "Uses a new local virtual environment, installs the generated requirements, verifies the selected interpreter, and starts the app.")
    with gr.Column(elem_classes=["m2g-card"]):
        card = gr.HTML(panel.status_html())
        launch = gr.Button("Launch locally", variant="primary")
        stop = gr.Button("Stop local app", variant="stop", interactive=False)
        logs = gr.Textbox(show_label=False, lines=8, max_lines=14, interactive=False, elem_classes=["m2g-log"])

    def _render():
        s = panel.snapshot
        return (panel.status_html(), gr.update(interactive=s.state not in {"preparing", "hardware", "environment", "preflight", "starting", "running"}),
                gr.update(interactive=s.state == "running"), panel.logs_text())

    def _launch(code, req_text, generated_files, selected_target):
        if selected_target != "Run on My System (Local)":
            raise RuntimeError("Select 'Run on My System (Local)' before launching the local app.")
        panel.launch(code or "", req_text or "", generated_files or [])
        return _render()

    def _stop():
        panel.stop()
        return _render()

    launch.click(_launch, inputs=[app_code, reqs, files, target], outputs=[card, launch, stop, logs])
    stop.click(_stop, outputs=[card, launch, stop, logs])
    timer = gr.Timer(3.0)
    timer.tick(_render, outputs=[card, launch, stop, logs], show_progress="hidden")
    return SimpleNamespace(card_outs=[card, launch, stop, logs], on_load_outs=[card, launch, stop, logs])


def _build_colab_panel(gr, app_code, reqs, target, panel: ColabPanel):
    """Compute panel wired to the real Colab CLI. Nothing here is simulated: status comes from real CLI calls."""
    names, _ = panel.gpu_choices()
    default = "T4" if "T4" in names else names[0]
    _step(gr, "3. Run on Google Colab",
          "Connect Google Colab, check its status, and manage your remote runtime.")
    view = panel.card_view()
    with gr.Column(elem_classes=["m2g-card"]):
        card = gr.HTML(view.html)
        with gr.Row(elem_classes=["m2g-actions"]):  # the three Colab controls; their state always comes from card_view()
            signin_btn = gr.Button(view.signin_label, size="sm", interactive=view.signin_enabled)
            check_btn = gr.Button(view.check_label, size="sm", interactive=view.check_enabled)
            stop_btn = gr.Button(view.stop_label, size="sm", variant="stop", interactive=view.stop_enabled)
        with gr.Row(visible=False) as code_row:  # only while a Google sign-in is waiting for its code
            code_box = gr.Textbox(label="Authorization code", type="password", scale=3)
            submit_btn = gr.Button("Submit code", variant="primary", scale=1)
            cancel_signin_btn = gr.Button("Cancel sign-in", scale=1)
        gpu = gr.Dropdown(names, value=default, label="Runtime / GPU",
                          info="The recommended runtime is preselected after you analyze a model.")
        with gr.Accordion("Optional: password-protect the public link", open=False):
            gr.Markdown("Without this, anyone who gets the link can use the app. Username 1-64 characters, password 8-128.",
                        elem_classes=["m2g-hint"])
            with gr.Row():
                user = gr.Textbox(label="Username")
                pw = gr.Textbox(label="Password", type="password")
        launch_btn = gr.Button(view.launch_label, variant="primary", interactive=view.launch_enabled)
        with gr.Column(elem_classes=["m2g-urlcard"]):  # the verified link lives here, not in the logs
            url_box = gr.HTML(view.url_html)
            copy_btn = gr.Button("Copy URL", size="sm", interactive=bool(view.url))
        with gr.Accordion("View logs", open=False):
            details = gr.Markdown(panel.status_markdown(), elem_classes=["m2g-status"])
            logs = gr.Textbox(show_label=False, lines=8, max_lines=14, interactive=False, elem_classes=["m2g-log"])
    timer = gr.Timer(3.0)
    # Every handler returns the whole card state in this order (see _render), so the buttons always match the real state.
    card_outs = [card, signin_btn, check_btn, stop_btn, launch_btn, url_box, copy_btn, details, logs]
    typed_password = {"value": ""}  # only so a failure message/traceback can be redacted; never logged or stored

    def _guard(name, fallback=None):
        # show_error alone would show only the exception text; this also logs the full redacted traceback.
        return guarded(name, error_cls=gr.Error, fallback=fallback, secrets=lambda: [typed_password["value"]])

    def _render():
        v = panel.card_view()
        return (v.html,
                gr.update(value=v.signin_label, interactive=v.signin_enabled),
                gr.update(value=v.check_label, interactive=v.check_enabled),
                gr.update(value=v.stop_label, interactive=v.stop_enabled),
                gr.update(value=v.launch_label, interactive=v.launch_enabled),
                v.url_html, gr.update(interactive=bool(v.url)),                    # Live Gradio URL box + Copy URL
                panel.status_markdown(), panel.logs_text())                        # View logs (collapsed)

    @_guard("Check status")
    def _check_begin():
        panel.begin_check()  # disables the button at once; the real check runs in the next step
        return _render()

    @_guard("Check status")
    def _check(pref):
        panel.check_status()
        choices, _info = panel.gpu_choices()
        value = pref if pref in choices else (choices[1] if len(choices) > 1 else choices[0])
        return (*_render(), gr.Dropdown(choices=choices, value=value))

    @_guard("Sign in with Google")
    def _signin():
        _md, show = panel.begin_sign_in()
        return (*_render(), gr.update(visible=show), "")

    @_guard("Submit authorization code")
    def _submit(code):
        _md, show = panel.submit_code(code)
        return (*_render(), gr.update(visible=show), "")

    @_guard("Cancel sign-in")
    def _cancel_signin():
        _md, show = panel.cancel_sign_in()
        return (*_render(), gr.update(visible=show), "")

    @_guard("Launch on Colab")
    def _launch(code, req_text, pref, u, p, selected_target):
        if selected_target != "Run on Google Colab":
            raise RuntimeError("Select 'Run on Google Colab' before launching the Colab app.")
        typed_password["value"] = p or ""
        panel.launch(code or "", req_text or "", pref, u or "", p or "")
        return _render()

    @_guard("Stop Colab runtime")
    def _stop():
        # Stop runs in a panel-owned worker so a slow `colab stop` cannot
        # block the Gradio queue or leave a chained callback stuck in
        # "Stopping…". The timer renders completion and failures.
        panel.request_stop()
        return _render()

    # Background poll every 3 s: a failure is logged once per distinct error and the page keeps its last good state.
    @_guard("Status refresh", fallback=lambda: tuple(gr.update() for _ in card_outs))
    def _tick():
        panel.initial_check()  # the first tick verifies the real connection once, so the badge is never just 'unchecked'
        return _render()

    check_btn.click(_check_begin, outputs=card_outs).then(_check, inputs=gpu, outputs=[*card_outs, gpu])
    signin_btn.click(_signin, outputs=[*card_outs, code_row, code_box])
    submit_btn.click(_submit, inputs=code_box, outputs=[*card_outs, code_row, code_box])
    code_box.submit(_submit, inputs=code_box, outputs=[*card_outs, code_row, code_box])
    cancel_signin_btn.click(_cancel_signin, outputs=[*card_outs, code_row, code_box])
    launch_btn.click(_launch, inputs=[app_code, reqs, gpu, user, pw, target], outputs=card_outs)
    stop_btn.click(_stop, outputs=card_outs)
    # Copy runs in the browser and reads the link from the box itself, so it can only ever copy what is displayed there.
    copy_btn.click(None, js="() => { const a = document.querySelector('.m2g-urlcard a.m2g-url'); if (!a) { return; } "
                            "const u = a.getAttribute('href'); "
                            "(navigator.clipboard ? navigator.clipboard.writeText(u) : Promise.reject())"
                            ".catch(() => window.prompt('Copy this URL:', u)); }")
    timer.tick(_tick, outputs=card_outs, show_progress="hidden")

    def reset_card():
        """Instant: drop old log lines / a finished launch's result, then re-render the card from the real state."""
        panel.reset_view()
        return _render()

    def on_load():
        """A page load starts clean: no old logs or notices; the sign-in code row matches the real sign-in state."""
        panel.reset_view()
        return (*_render(), gr.update(visible=panel.card_view().signing_in), "")

    return SimpleNamespace(gpu=gpu, card_outs=card_outs, reset_card=reset_card, on_load=on_load,
                           on_load_outs=[*card_outs, code_row, code_box])


def build_ui(settings: Optional[Settings] = None, panel: Optional[ColabPanel] = None):
    import gradio as gr  # lazy: keeps the core importable (and testable) without Gradio

    panel = panel or ColabPanel()
    local_panel = LocalPanel()
    tracker = RunTracker()

    @guarded("Analyze and generate", error_cls=gr.Error)
    def _analyze(ticket):
        view = finish_analysis(ticket, tracker, settings)
        if view is None:  # superseded by a newer Analyze click or a reload: leave the page exactly as it is
            return tuple(gr.update() for _ in range(5))
        update, summary, res = gr.update(), view.summary, view.result
        if res is not None and res.ok and res.analysis is not None:
            try:
                pick, why = panel.suggestion(res.analysis.hardware)
                names, _ = panel.gpu_choices()
                if pick in names:
                    update = gr.update(value=pick)
                summary += f"\n\n**Suggested Colab runtime:** {pick} - {why}"
            except Exception:  # the suggestion is a nicety: never lose the result over it
                logging.getLogger("m2g.ui").exception("Colab runtime suggestion failed")
        return summary, view.app_code, view.requirements, view.files, update

    with gr.Blocks(title="Model to Gradio") as demo:
        gr.HTML(f"<style>{CSS}</style>")
        gr.Markdown(HEADER_MD, elem_classes=["m2g-header"])

        _step(gr, "1. Repository", "A Hugging Face model URL or a GitHub AI repository URL.")
        with gr.Row():
            url = gr.Textbox(label="Repository URL", show_label=False, container=False, scale=5,
                             placeholder="https://huggingface.co/owner/model")
            go = gr.Button("Analyze and generate", variant="primary", scale=1, min_width=170)
        gr.Examples(EXAMPLES, inputs=url, label="Examples")

        _step(gr, "2. Analysis")
        summary = gr.Markdown(RESULTS_PLACEHOLDER)
        run = gr.State(None)  # the RunTicket of this browser session's newest Analyze click

        _step(gr, "3. Generated files", "Saved to the output folder. The app code and requirements are what gets sent to Colab.")
        with gr.Tabs():
            with gr.Tab("Downloads"):
                files = gr.File(label="Generated files (app, requirements, Colab notebook, analysis)", file_count="multiple")
            with gr.Tab("app.py"):
                app_code = gr.Code(language="python", label="Generated Gradio app", interactive=False, lines=12,
                                   max_lines=24)
            with gr.Tab("requirements.txt"):
                reqs = gr.Code(label="Requirements", interactive=False, lines=6, max_lines=14)

        _step(gr, "3. Deployment target", "Choose one target explicitly. The platform never switches between Local and Colab automatically.")
        target = gr.Radio(["Run on My System (Local)", "Run on Google Colab"], value="Run on My System (Local)",
                          label="Deployment target")
        
        local = _build_local_panel(gr, app_code, reqs, files, target, local_panel)
        colab = _build_colab_panel(gr, app_code, reqs, target, panel)
        gpu = colab.gpu

        @guarded("Analyze and generate", error_cls=gr.Error)
        def _begin(u, previous):
            # Instant and separate from the slow analysis: the old model's results, files, logs and progress disappear at once.
            ticket, v = begin_analysis(tracker, previous, u)
            return v.summary, v.app_code, v.requirements, v.files, ticket, *colab.reset_card()

        begin_outs = [summary, app_code, reqs, files, run, *colab.card_outs]
        final_outs = [summary, app_code, reqs, files, gpu]
        # concurrency_limit: a new click must not queue behind a slow older analysis (whose result is then dropped).
        go.click(_begin, inputs=[url, run], outputs=begin_outs).then(_analyze, inputs=run, outputs=final_outs,
                                                                     concurrency_limit=4)
        url.submit(_begin, inputs=[url, run], outputs=begin_outs).then(_analyze, inputs=run, outputs=final_outs,
                                                                       concurrency_limit=4)

        @guarded("Page load", error_cls=gr.Error,
                 fallback=lambda: (RESULTS_PLACEHOLDER, "", "", [], None, *(gr.update() for _ in colab.on_load_outs)))
        def _page_load(previous):
            # A (re)loaded page starts clean. Only what is on screen is reset: files already written stay on disk.
            tracker.invalidate(previous)
            return RESULTS_PLACEHOLDER, "", "", [], None, *colab.on_load()

        demo.load(_page_load, inputs=run, outputs=[summary, app_code, reqs, files, run, *colab.on_load_outs])
    return demo


DEFAULT_PORT = 7860


def port_is_free(host: str, port: int) -> bool:
    """Whether host:port can host a new server: it can be bound and nothing is already listening on it.

    SO_REUSEADDR is deliberately NOT set. On Windows it lets a bind succeed while another server is listening, which
    would make us pass a port that Gradio then fails to use; the extra connect check catches that case as well.
    """
    bind_host = "127.0.0.1" if host in ("", "0.0.0.0") else host
    probe = socket.socket()
    try:
        probe.bind((bind_host, port))
    except OSError:
        return False
    finally:
        probe.close()
    try:
        with socket.socket() as client:
            client.settimeout(0.25)
            return client.connect_ex((bind_host, port)) != 0  # something listener there? then it is not free
    except OSError:
        return True


def choose_server_port(preferred: Optional[int], host: str = "127.0.0.1") -> Optional[int]:
    """The value to pass as Gradio's ``server_port``: the preferred port when it is free, otherwise ``None``.

    An explicit ``server_port`` means "this port only", so a taken port makes Gradio raise
    ``OSError: Cannot find empty port in range``. ``server_port=None`` is Gradio's supported auto-selection: it tries
    7860, 7861, ... until one is free. So 7860 (or ``M2G_PORT``) is honoured when available, and an occupied port falls
    back to Gradio's own scan instead of failing the launch.
    """
    if preferred is None or not 0 < int(preferred) < 65536:
        return None
    return int(preferred) if port_is_free(host, int(preferred)) else None


def main() -> None:
    global REA_ENABLED
    configure_logging()  # make m2g.* log records (and the tracebacks of failed callbacks) reach this console window
    REA_ENABLED = os.environ.get("M2G_REA_ENABLED", "1").strip().lower() not in {"0", "false", "no", "off"}
    settings = Settings.from_env()
    host = os.environ.get("M2G_HOST", "127.0.0.1")
    port = int(os.environ.get("M2G_PORT", str(DEFAULT_PORT)))
    demo = build_ui(settings)
    demo.launch(server_name=host, server_port=choose_server_port(port, host),
                allowed_paths=[str(settings.output_dir)], prevent_thread_lock=True, show_error=True)
    print(f"Model to Gradio is running at {demo.local_url}", flush=True)  # the port Gradio actually bound
    demo.block_thread()


if __name__ == "__main__":
    main()
