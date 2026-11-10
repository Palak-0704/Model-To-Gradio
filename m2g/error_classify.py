"""Central error classification and retry policy for Colab deployments (standard library only).

One place decides, from the evidence in an error (its kind, its message, the original traceback text), WHAT went wrong, whether
it is worth retrying and what the person should do. The Colab launcher and the Jupyter runtime path both call it, so a rule is
written once instead of per model.

Principles
* Original errors are never replaced: callers keep the full traceback in their diagnostics; ``ErrorInfo`` only adds a label, a
  plain-language explanation and a next step.
* Retries are bounded by the PERMANENT-vs-TRANSIENT split here (``max_retries`` is 0 for every permanent error) and by the limits
  each caller passes in. Nothing in this module loops.
* Unknown errors are permanent (never retried): guessing that an unrecognised failure is transient is how infinite loops start.

Categories: dependency, preflight, startup, model_loading, inference, network, file_transfer, colab_runtime, auth, resource,
validation, cancelled, internal, unknown.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Sequence, Tuple

CATEGORIES = ("dependency", "preflight", "startup", "model_loading", "inference", "network", "file_transfer", "colab_runtime",
              "auth", "resource", "validation", "cancelled", "internal", "unknown", "compatibility")

# Hard ceiling for any automatic retry of a transient error, whatever a rule says.
MAX_RETRIES_CEILING = 3


@dataclass(frozen=True)
class ErrorInfo:
    category: str
    code: str
    transient: bool
    max_retries: int  # 0 for every permanent error
    title: str  # plain-language explanation
    recovery: str  # what the person (or a later launch) can do
    technical: str = ""  # the deciding line of the original error, redacted by the caller

    def label(self) -> str:
        return f"{self.category}/{self.code}"

    def user_text(self) -> str:
        return f"[{self.label()}] {self.title}" + (f" What to do: {self.recovery}" if self.recovery else "")


def _rule(needles: Tuple[str, ...], category: str, code: str, transient: bool, retries: int, title: str, recovery: str):
    return (needles, category, code, transient, min(retries, MAX_RETRIES_CEILING) if transient else 0, title, recovery)


# --------------------------------------------------------------------------- library / runtime compatibility
# Evidence that the app and the library versions installed on the runtime disagree: an API, class or pipeline task that was removed,
# renamed or is not released yet. It is recognised from the ORIGINAL error text, whichever layer reported it, so it is never
# mistaken for a sign-in or Colab CLI problem (those texts also contain words such as "token", "oauth" or "ImportError"). Only the
# libraries listed here count, so an ordinary missing package keeps its own dependency rule.
_COMPAT_LIBS = (r"(?:transformers|diffusers|sentence_transformers|huggingface_hub|accelerate|peft|timm|torchvision|torchaudio|torch|"
                r"gradio|safetensors|tokenizers)")
_TASK_RE = re.compile(r"unknown task[ :]+['\"`]?(?P<t1>[A-Za-z][\w-]*)(?=[^\n]{0,20}available tasks)"
                      r"|does not provide the pipeline task ['\"](?P<t2>[^'\"]+)['\"]", re.I)
_IMPORT_NAME_RE = re.compile(r"cannot import name ['\"](?P<name>[\w.]+)['\"] from ['\"](?P<lib>" + _COMPAT_LIBS + r"[\w.]*)['\"]", re.I)
_MODULE_ATTR_RE = re.compile(r"module ['\"](?P<lib>" + _COMPAT_LIBS + r"[\w.]*)['\"] has no attribute ['\"](?P<name>\w+)['\"]", re.I)


def compat_error(text: str) -> Optional[ErrorInfo]:
    """A library-compatibility ErrorInfo when ``text`` shows that the app uses something the installed library does not have
    (an unknown pipeline task, a name that cannot be imported, a module attribute that does not exist); otherwise None.
    Permanent by construction: replaying the same app against the same versions cannot succeed."""
    s = text or ""
    m = _TASK_RE.search(s)
    if m:
        task = m.group("t1") or m.group("t2")
        return ErrorInfo("compatibility", "pipeline_task_unsupported", False, 0,
                         f"The transformers version installed on the runtime does not provide the pipeline task '{task}' "
                         "(the task was removed or renamed in that release).",
                         f"Regenerate the app so it loads the model with its AutoModel class and tokenizer instead of "
                         f"pipeline('{task}'), or run it on a transformers release that still provides this task. The installed "
                         "versions are listed in the preflight lines of the log.", error_line(s))
    m = _IMPORT_NAME_RE.search(s) or _MODULE_ATTR_RE.search(s)
    if m:
        lib, name = m.group("lib").split(".")[0], m.group("name")
        return ErrorInfo("compatibility", "removed_or_renamed_api", False, 0,
                         f"'{name}' does not exist in the {lib} version installed on the runtime (it was removed, renamed or is "
                         "not released yet).",
                         f"Regenerate the app so it uses the current {lib} API, or install a {lib} release that provides "
                         f"'{name}'. The installed versions are listed in the preflight lines of the log.", error_line(s))
    return None


# Kinds that are assigned from the text of a CLI/launcher failure. When the original error text carries library-compatibility
# evidence, that evidence wins over these kinds (a removed pipeline task is never a Google sign-in problem).
_COMPAT_OVERRIDES = frozenset({"needs_auth", "cli_broken", "cli_failed", "preflight_failed", "start_failed", "hardware_unavailable",
                               "app_incompatible"})


# Ordered: the first rule with a matching needle wins, so specific, permanent causes come BEFORE the generic network rules (a
# 401 is never "network", a missing package is never "transient").
_RULES = (
    _rule(("cuda out of memory", "outofmemoryerror", "torch.cuda.outofmemory", "cublas_status_alloc_failed"), "resource", "gpu_oom",
          False, 0, "The GPU ran out of memory.", "Pick a larger GPU (L4/A100/H100) or a smaller model."),
    _rule(("no space left on device",), "resource", "disk_full", False, 0, "The runtime ran out of disk space.",
          "Pick a smaller model or a runtime with more disk."),
    _rule(("trust_remote_code",), "model_loading", "needs_remote_code", False, 0,
          "This model needs repository code to be executed, which this tool never does automatically.",
          "Choose a model that is supported natively by transformers."),
    _rule(("gated repo", "access to model", "is restricted", "401 client error", "403 client error", "invalid username or password",
           "cannot access gated"), "model_loading", "model_access_denied", False, 0,
          "Hugging Face refused the download (gated or private model, or access denied).",
          "This tool never sends your Hugging Face token to Colab, so gated models are not supported here."),
    _rule(("repository not found", "404 client error", "entry not found", "revision not found", "is not a local folder and is not a valid model identifier"),
          "model_loading", "model_not_found", False, 0, "The model repository or file does not exist on Hugging Face.",
          "Check the model id spelling and that the repository is public."),
    _rule(("does not recognize this architecture", "unrecognized configuration class", "unrecognized model", "model type"
           " `", "unknown model type"), "model_loading", "unsupported_architecture", False, 0,
          "The installed transformers/diffusers version does not know this model architecture.",
          "The model may be too new or unsupported; try another model."),
    _rule(("unexpected keyword argument",),
          "startup", "api_incompatible", False, 0, "A library call in the generated app does not match the installed version.",
          "Generate the app again; the preflight normally repairs this before launch."),
    _rule(("expected all tensors", "shapes cannot be multiplied", "index out of range in self",
           "sizes of tensors must match", "expected input"), "inference", "bad_input_or_shape", False, 0,
          "The model rejected the input or tensor shapes during prediction.", "Try a different input; the error is not retried."),
    _rule(("modulenotfounderror", "no module named", "cannot import name", "importerror"), "dependency", "missing_module", False,
          0, "A Python dependency is missing or incompatible on the runtime.", "Check the module name in the log; fix requirements.txt."),
    _rule(("no matching distribution", "could not find a version that satisfies", "resolutionimpossible", "conflicting dependencies",
           "requires a different python", "invalid requirement"), "dependency", "pip_unsatisfiable", False, 0,
          "pip cannot satisfy the requirements on this runtime.", "A pinned version does not exist or conflicts; relax the pins."),
    _rule(("could not create share link", "frpc", "share link"), "network", "share_tunnel", False, 0,
          "Gradio could not create the public gradio.live link (its tunnel server refused or did not answer; already retried).",
          "Check https://status.gradio.app and launch again in a few minutes."),
    _rule(("incompleteread", "unexpected eof", "unexpected end of file", "error while deserializing header",
           "incomplete download", "safetensorerror"), "model_loading", "partial_download", True, 1,
          "A model file was downloaded incompletely or is corrupt.", "The download is retried once; if it repeats, launch again later."),
    _rule(("invalid_grant", "unauthenticated", "refresherror", "defaultcredentialserror", "sign in again", "http 401"), "auth",
          "google_auth", False, 0, "Google rejected the Colab CLI's credentials.", "Sign in with Google again."),
    _rule(("quota", "compute unit", "usage limit", "resource_exhausted", "no capacity"), "colab_runtime", "quota", False, 0,
          "Colab cannot provide this runtime (quota, usage limit or no capacity).", "Try another GPU, wait, or check your compute units."),
    _rule(("connection was lost", "connectionclosed", "kernel died", "kernel is dead", "kernel restart", "websocket", "wsclient",
           "no close frame", "remote end closed", "bad gateway", "gateway timeout"), "colab_runtime", "kernel_connection", True, 2,
          "The connection to the Colab runtime dropped.", "It is retried automatically; if it keeps failing, launch again."),
    _rule(("read timed out", "readtimeout", "connecttimeout", "connection reset", "connectionreseterror", "connection aborted",
           "connectionabortederror", "max retries exceeded", "temporary failure in name resolution", "name or service not known",
           "network is unreachable", "could not fetch url", "sslerror", "ssl: ", "503 server error", "502 server error",
           "504 server error", "http error 503", "http error 502", "http error 504", "service unavailable", "429 client error",
           "too many requests", "connectionerror", "connection refused", "i/o timeout"), "network", "transient_network", True, 2,
          "A network request failed (timeout, reset, rate limit or a temporary server error).",
          "It is retried automatically a limited number of times; if it persists, check the connection and launch again."),
    _rule(("timeouterror", "timed out"), "network", "timeout", True, 1, "An operation timed out.",
          "It is retried once; if it repeats, launch again or pick a smaller model."),
)

# Failures whose KIND (set by the launcher / CLI classifier) already says everything; the text cannot improve on it.
_KIND_TABLE = {
    "cancelled": ("cancelled", "cancelled", "The launch was cancelled.", ""),
    "blocked": ("validation", "blocked", "The generated app or its requirements were refused before anything was sent.",
                "Fix the reported problem and generate the app again."),
    "needs_auth": ("auth", "google_auth", "Google rejected the Colab CLI's credentials.", "Sign in with Google again."),
    "cli_unavailable": ("colab_runtime", "cli_unavailable", "The Colab CLI could not be started.", "Install or repair the Colab CLI."),
    "cli_broken": ("colab_runtime", "cli_broken", "The Colab CLI crashed (broken install or unsupported platform).",
                   "Reinstall the Colab CLI; this is not a sign-in problem."),
    "unsupported_gpu": ("colab_runtime", "unsupported_gpu", "The installed Colab CLI does not offer that accelerator.",
                        "Pick one of the accelerators the CLI lists."),
    "no_gpu": ("colab_runtime", "no_gpu", "Colab did not assign the requested GPU.", "Try again later or choose another accelerator."),
    "hardware_unavailable": ("colab_runtime", "quota", "Colab cannot provide this runtime.", "Try another GPU or wait."),
    "runtime_unreachable": ("colab_runtime", "runtime_unreachable", "The runtime was created but did not answer.", "Launch again."),
    "runtime_lost": ("colab_runtime", "runtime_lost", "The runtime stopped answering while the app was starting.", "Launch again."),
    "launch_timeout": ("startup", "launch_timeout", "The app did not publish a public URL in time (very large model or slow download).",
                       "Pick a smaller model or a faster GPU."),
    "transfer_failed": ("file_transfer", "transfer_failed", "The files on the runtime did not match what was sent.",
                        "Launch again; the transfer is retried automatically for dropped connections."),
    "preflight_failed": ("preflight", "preflight_failed", "The app is not compatible with the Gradio installed on the runtime.",
                         "See the preflight lines in the log; generate the app again."),
    "url_unreachable": ("network", "url_unreachable", "The app started but its public URL did not answer from this computer.",
                        "Launch again; the runtime was released."),
    "internal": ("internal", "internal", "An unexpected internal error occurred.", "See the console log for the traceback."),
}

# Default category for a kind whose text matched no rule.
_KIND_DEFAULT = {
    "install_failed": ("dependency", "install_failed", "Installing dependencies on the runtime failed.",
                       "See the pip output in the diagnostics."),
    "start_failed": ("startup", "start_failed", "The app process could not be started on the runtime.", "Launch again."),
    "app_failed": ("startup", "app_failed", "The app failed while starting on the runtime.", "See the traceback in the diagnostics."),
    "app_exited": ("startup", "app_exited", "The app process exited without publishing a URL.", "See the end of run.log."),
    "create_failed": ("colab_runtime", "create_failed", "The Colab runtime could not be created.", "Launch again later."),
    "cli_failed": ("colab_runtime", "cli_failed", "The Colab CLI reported an error.", "See the diagnostics."),
    "app_incompatible": ("compatibility", "app_incompatible", "The app is not compatible with the library versions installed on the runtime.",
                         "See the original error in the diagnostics; regenerate the app or use a library release that provides the API."),
}

_STAGE_DEFAULT = {"install": "dependency", "preflight": "preflight", "start": "startup", "load": "model_loading",
                  "inference": "inference", "transfer": "file_transfer", "runtime": "colab_runtime"}


def error_line(text: str) -> str:
    """The deciding line of an error text: the last unindented line that is not a traceback frame (usually ``Type: message``)."""
    keep = [ln.strip() for ln in (text or "").splitlines()
            if ln.strip() and ln[0] not in " \t" and not ln.startswith(("Traceback", "File ", "During handling"))]
    return (keep[-1] if keep else "")[:400]


def classify_text(text: str, stage: str = "") -> ErrorInfo:
    """Classify raw error text (a traceback, pip output, CLI output). ``stage`` (install, preflight, start, load, inference,
    transfer, runtime) only decides the category of an error no rule recognises, and which generic stage label is used."""
    compat = compat_error(text)
    if compat is not None:  # checked first: such errors also contain words (ImportError, token ...) that other rules react to
        return compat
    low = (text or "").lower()
    technical = error_line(text)
    # A failure while the model was loading vs. while predicting is told apart by the caller's ``stage`` (see below).
    for needles, category, code, transient, retries, title, recovery in _RULES:
        if any(n in low for n in needles):
            return ErrorInfo(category, code, transient, retries, title, recovery, technical)
    category = _STAGE_DEFAULT.get(stage, "unknown")
    return ErrorInfo(category, "unrecognized", False, 0,
                     "The step failed for a reason this tool does not recognise.",
                     "The original error is kept in the diagnostics; it is not retried automatically.", technical)


def classify_failure(kind: str, message: str = "", detail: str = "", stage: str = "") -> ErrorInfo:
    """Classify a launcher failure from its kind plus the evidence. ``detail`` should be the ORIGINAL error text (the last
    traceback), ``message`` the user-facing message. A kind that is already definitive wins; otherwise text rules decide."""
    technical = error_line(detail) or error_line(message)
    if kind in _COMPAT_OVERRIDES:
        compat = compat_error((detail or "") + "\n" + (message or ""))
        if compat is not None:
            return ErrorInfo(compat.category, compat.code, False, 0, compat.title, compat.recovery, technical or compat.technical)
    if kind in _KIND_TABLE:
        category, code, title, recovery = _KIND_TABLE[kind]
        if kind == "preflight_failed" and "no module named 'gradio'" in (detail + message).lower():
            title, code = "Gradio is not importable on the runtime.", "gradio_not_importable"
        return ErrorInfo(category, code, False, 0, title, recovery, technical)
    info = classify_text((detail or "") + "\n" + (message or ""), stage)
    if info.code == "unrecognized" and kind in _KIND_DEFAULT:
        category, code, title, recovery = _KIND_DEFAULT[kind]
        return ErrorInfo(category, code, False, 0, title, recovery, technical or info.technical)
    # Model weights fail at start-up: label a TRANSIENT network error for what it is. A permanent network cause that has its own
    # specific rule (the Gradio share tunnel) keeps its own label: it is not a model-download problem and is never retried.
    if kind in ("app_failed", "app_exited") and info.category == "network" and info.transient:
        return ErrorInfo("model_loading", "download_network_error", info.transient, info.max_retries,
                         "The model download failed because of a network error.", info.recovery, technical or info.technical)
    return ErrorInfo(info.category, info.code, info.transient, info.max_retries, info.title, info.recovery, technical or info.technical)


def retry_delay(attempt: int, base: float = 8.0, cap: float = 60.0) -> float:
    """Growing, capped wait before retry number ``attempt`` (1-based): base, 2*base, 3*base ... never above ``cap``."""
    return float(min(cap, base * max(1, int(attempt))))


def may_retry(info: ErrorInfo, attempts_made: int, limit: int) -> bool:
    """True only for a transient error with retries left under BOTH the rule's own bound and the caller's ``limit``
    (``attempts_made`` counts attempts already tried, including the first)."""
    retries_done = max(0, attempts_made - 1)
    return bool(info.transient) and retries_done < min(info.max_retries, max(0, limit), MAX_RETRIES_CEILING)


def explain(text: str, stage: str = "") -> str:
    """One-line user text for a raw error (used where only text is available)."""
    info = classify_text(text, stage)
    return info.user_text()
