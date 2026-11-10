"""Generate a Google Colab notebook (nbformat 4.5, plain JSON) for a supported Hugging Face model.

The notebook: (1) checks the runtime BEFORE installing or downloading anything, (2) installs validated pinned-by-need
requirements, (3) reads HF_TOKEN from Colab Secrets (never printed), (4) writes the generated Gradio app, (5) loads the
model with a clear error message on failure, and (6) launches the UI.

Nothing here runs the model. A Google login does not guarantee a live Colab GPU; the environment cell reports what the
runtime really has and fails with an actionable message if it is not enough.
"""
from __future__ import annotations

import ast
import json
import re
from typing import Any, Dict, List, Optional

from .analyzer import Analysis
from .config import Settings
from .gradio_gen import GenerationError, generate_app, generate_requirements, linked_pickle_refusal, split_app_code
from .validate import ValidationError, validate_app_code, validate_notebook, validate_requirements_text

NOTEBOOK_MINOR = 5


def _md_safe(text: Any, limit: int = 220) -> str:
    """Neutralise untrusted metadata before it is placed in a markdown cell."""
    s = re.sub(r"[\x00-\x1f\x7f]+", " ", str(text if text is not None else ""))
    s = s.replace("<", "&lt;").replace(">", "&gt;")
    s = re.sub(r"([\[\]\(\)`*_|#])", r"\\\1", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s if len(s) <= limit else s[: limit - 1] + "…"


def _md_code(text: Any, limit: int = 120) -> str:
    """Untrusted text (a file name) as an inline code span, so Markdown shows it literally (underscores are not escaped). Control characters
    and backticks are removed first, so the text cannot close the span."""
    s = re.sub(r"[\x00-\x1f\x7f`]+", " ", str(text if text is not None else ""))
    s = re.sub(r"\s+", " ", s).strip()
    return "`" + (s if len(s) <= limit else s[: limit - 1] + "…") + "`"


def _evidence_md(a: Analysis) -> str:
    """Evidence basis for the title cell: status counts plus any evidence that could not be read. Built from the analysis' evidence digest
    only; absent or unreadable evidence is stated as unknown, never as confirmed."""
    ev = a.evidence
    if not isinstance(ev, dict):
        return "**Evidence collection:** not run for this analysis; only the repository metadata read earlier is used.\n\n"
    by = (ev.get("summary") or {}).get("by_status") or {}
    counts = " · ".join(f"{by.get(s, 0)} {s}" for s in ("collected", "missing", "inaccessible", "invalid", "skipped"))
    md = (f"**Evidence collection:** {_md_safe(counts)} (metadata and safetensors headers only; no weights were downloaded). "
          "Anything not collected is unknown, not assumed.\n\n")
    items = ev.get("items") if isinstance(ev.get("items"), dict) else {}
    gaps = [(k, v) for k, v in items.items() if isinstance(v, dict) and v.get("status") in ("inaccessible", "invalid")][:3]
    if gaps:
        md += "**Evidence that could not be used:**\n" + "\n".join(
            f"- {_md_code(k)}: {_md_safe(v.get('status'))}" + (f" ({_md_safe(v.get('error'), 140)})" if v.get("error") else "") for k, v in gaps) + "\n\n"
    return md


def _lines(text: str) -> List[str]:
    return text.rstrip("\n").splitlines(keepends=True) or [""]


def _md(cell_id: str, text: str) -> Dict[str, Any]:
    return {"cell_type": "markdown", "id": cell_id, "metadata": {}, "source": _lines(text)}


def _code(cell_id: str, text: str) -> Dict[str, Any]:
    return {"cell_type": "code", "id": cell_id, "metadata": {}, "execution_count": None, "outputs": [],
            "source": _lines(text)}


def _num(x: Optional[float]) -> str:
    return "None" if x is None else repr(round(float(x), 2))


def _loader_name(app_code: str) -> str:
    names = {n.name for n in ast.parse(app_code).body if isinstance(n, ast.FunctionDef)}
    for candidate in ("get_pipe", "get_model"):
        if candidate in names:
            return candidate
    raise GenerationError("Generated app has no model loader function (get_pipe/get_model).")


_ENV_CELL = '''# Step 1 - check the runtime BEFORE installing or downloading anything.
import os
import platform
import subprocess
import sys

NEEDS_GPU = @@NEEDS_GPU@@          # from the repo analysis
EST_VRAM_GB = @@VRAM@@             # fp16 estimate from repo metadata (None = unknown)
EST_VRAM_4BIT_GB = @@VRAM4@@       # 4-bit estimate (None = unknown)
USE_4BIT = @@USE4@@                # analysis recommends 4-bit loading on a T4-class GPU

print("Python:", sys.version.split()[0], "|", platform.platform())
try:  # os.sysconf exists on Linux/macOS (Colab) but not on Windows
    ram_gb = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**30
    print(f"System RAM: {ram_gb:.1f} GiB")
except (AttributeError, ValueError, OSError):
    print("System RAM: unknown (this Python has no os.sysconf)")
try:
    import torch
    has_gpu = torch.cuda.is_available()
except ImportError:
    torch, has_gpu = None, False

gpu_gb = None
if has_gpu:
    props = torch.cuda.get_device_properties(0)
    gpu_gb = props.total_memory / 2**30
    print(f"GPU: {props.name} ({gpu_gb:.1f} GiB VRAM)")
    try:
        print(subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader"],
                             capture_output=True, text=True, timeout=20).stdout.strip())
    except (OSError, subprocess.SubprocessError):
        pass
else:
    print("GPU: none detected")

if NEEDS_GPU and not has_gpu:
    raise RuntimeError("This model needs a GPU. In Colab choose Runtime > Change runtime type > T4 GPU, "
                       "then Runtime > Run all. (A Google login alone does not guarantee a GPU runtime.)")
need = EST_VRAM_4BIT_GB if USE_4BIT else EST_VRAM_GB
if has_gpu and need and gpu_gb < need * 1.15:
    raise RuntimeError(f"Estimated {need} GiB VRAM needed but this GPU has {gpu_gb:.1f} GiB. "
                       "Use a larger GPU runtime or a smaller model.")
print("Environment check passed.")
'''

_TOKEN_CELL = '''# Step 3 - optional Hugging Face token (needed only for gated/private models).
# Add it under the key icon (Secrets) in the left sidebar as HF_TOKEN. It is never printed or written to disk.
import os

try:
    from google.colab import userdata
    _token = userdata.get("HF_TOKEN")
    if _token:
        os.environ["HF_TOKEN"] = _token
except Exception:  # secret missing, access not granted, or not running in Colab
    pass
print("HF_TOKEN:", "set" if os.environ.get("HF_TOKEN") else "not set")
'''

_LOAD_CELL = '''# Step 5 - load the model once and run a quick self-test. The first run downloads the weights.
import importlib
import os
import sys

LOADER = @@LOADER@@
MODEL_MODULE = @@MODEL_MODULE@@  # the generated loader.py (app.py imports from it); a single-file app holds the loader itself
USE_4BIT = @@USE4@@
if USE_4BIT:
    os.environ["M2G_LOAD_IN_4BIT"] = "1"  # read by loader.py at import time
@@PICKLE@@sys.path.insert(0, os.getcwd())
app = importlib.import_module("app")
try:
    getattr(importlib.import_module(MODEL_MODULE), LOADER)()
except Exception as exc:
    raise RuntimeError(f"Model failed to load: {type(exc).__name__}: {exc}. See 'Troubleshooting' at the bottom.") from exc
print("Model loaded OK.")

example = None
examples = getattr(app.demo, "examples", None)
if isinstance(examples, list) and examples and isinstance(examples[0], list):
    example = examples[0]
if example is not None and hasattr(app, "predict"):
    try:
        print("Self-test result:", str(app.predict(*example))[:500])
    except Exception as exc:
        print(f"Self-test could not run ({type(exc).__name__}: {exc}). Try the UI below.")
else:
    print("No built-in example for this task; test it in the UI below.")
'''

_NATIVE_LOAD_CELL = '''# Step 5 - SCAFFOLD for an independent GitHub model repository. Nothing is loaded here: the repository's architecture and inference
# API are not established by its metadata, so no loader was invented. No repository code is run, imported or installed, and no weights
# are downloaded.
import importlib
import os
import sys

sys.path.insert(0, os.getcwd())
app = importlib.import_module("app")
loader = importlib.import_module("loader")
print(loader.SCAFFOLD_REPORT)
print()
print("No model was loaded. The UI below is a scaffold: it implements no loading and no inference. Write real loading code in get_model()")
print("in loader.py yourself, after reading the repository's own documentation and reviewing its code, before expecting any inference.")
'''

_PICKLE_CONSENT_BLOCK = '''# This repository has NO safetensors weights, only pickle-based ones (.bin/.pt/.ckpt). Unpickling can execute arbitrary code and a
# public model is not automatically safe, so loading them is OFF and the model will fail to load with an explanation. Set this to True
# ONLY if you have reviewed the repository and trust it; then restart the session and run the cells again (loader.py reads it at import).
ALLOW_PICKLE_WEIGHTS = False
if ALLOW_PICKLE_WEIGHTS:
    os.environ["M2G_ALLOW_PICKLE_WEIGHTS"] = "1"  # read by loader.py at import time
'''

_LAUNCH_CELL = '''# Step 6 - launch the Gradio UI (ONE launch, ONE server).
# SHARE_PUBLIC_LINK=True asks Gradio for a temporary public gradio.live link: anyone who has it can use your Colab
# GPU until you stop this cell. Set it to False to keep the app private.
# If the tunnel cannot be created, the server that is already running is KEPT (it is not closed or relaunched); a
# second launch happens only if the first one never started a server at all.
# Two different URLs can exist, and they are never mixed up:
#   PUBLIC_URL         https://<id>.gradio.live            public, from Gradio's tunnel
#   COLAB_PREVIEW_URL  https://7860-<id>.colab.dev/        private preview through Colab's own proxy (needs your
#                                                          Google session); it is NOT a public deployment link
import json
import re

SHARE_PUBLIC_LINK = True
PUBLIC_URL = None
COLAB_PREVIEW_URL = None
LOCAL_URL = None
TUNNEL_ERROR = None
_PUBLIC_RE = r"https://[A-Za-z0-9-]+[.]gradio[.](?:live|app)/?"
_PREVIEW_RE = r"https://[0-9]+-[A-Za-z0-9.-]+[.]colab[.]dev/?"


def classify_url(url):
    """'public' for a gradio.live/gradio.app link, 'colab_preview' for a Colab proxy link, else None."""
    if not isinstance(url, str):
        return None
    if re.fullmatch(_PUBLIC_RE, url):
        return "public"
    if re.fullmatch(_PREVIEW_RE, url):
        return "colab_preview"
    return None


_launch_error = None
_share_url = None
try:
    _res = app.demo.launch(share=SHARE_PUBLIC_LINK, prevent_thread_lock=True)
    if isinstance(_res, tuple) and len(_res) > 2:
        LOCAL_URL, _share_url = _res[1], _res[2]
except Exception as exc:
    _launch_error = f"{type(exc).__name__}: {exc}"

_running = bool(getattr(app.demo, "is_running", False))
if _launch_error and not _running:
    # The first launch never started a server (so there is nothing to duplicate): start the app privately instead.
    TUNNEL_ERROR = _launch_error
    _res = app.demo.launch(share=False, prevent_thread_lock=True)
    if isinstance(_res, tuple) and len(_res) > 1:
        LOCAL_URL = _res[1]
    _running = bool(getattr(app.demo, "is_running", True))
elif _launch_error:
    TUNNEL_ERROR = _launch_error  # the server is up and stays up; only the tunnel failed
LOCAL_URL = LOCAL_URL or getattr(app.demo, "local_url", None)

if SHARE_PUBLIC_LINK and not TUNNEL_ERROR:
    _share_url = _share_url or getattr(app.demo, "share_url", None)
    if classify_url(_share_url) == "public":
        PUBLIC_URL = _share_url
    else:
        TUNNEL_ERROR = ("Gradio returned no public gradio.live URL (see its message above). "
                        "Check https://status.gradio.app for Share Links outages.")

# The URL Colab shows in the embedded view comes from Colab's own proxy for the port the app really listens on.
if _running:
    try:
        _port = int(re.search(r":([0-9]+)", str(LOCAL_URL)).group(1))
        from google.colab.output import eval_js
        _proxy = eval_js("google.colab.kernel.proxyPort(%d)" % _port)
        if classify_url(_proxy) == "colab_preview":
            COLAB_PREVIEW_URL = _proxy
    except Exception:
        pass  # not in Colab, or no browser attached: no preview URL (none is guessed)

if PUBLIC_URL:
    MODE = "public"
elif _running and COLAB_PREVIEW_URL:
    MODE = "colab_preview"
elif _running:
    MODE = "local_only"
else:
    MODE = "failed"

print("=" * 70)
if PUBLIC_URL:
    print("PUBLIC URL (temporary, anyone with the link can use this app):")
    print(PUBLIC_URL)
elif SHARE_PUBLIC_LINK:
    print("PUBLIC ACCESS UNAVAILABLE: Gradio's gradio.live tunnel could not be created.")
    print("Reason:", TUNNEL_ERROR)
    print("The app itself is not affected. To retry the public link: re-run this cell later or check https://status.gradio.app")
else:
    print("Public link disabled (SHARE_PUBLIC_LINK = False): the app is private to this Colab session.")
if COLAB_PREVIEW_URL:
    print("COLAB PREVIEW AVAILABLE (private to your Google session, NOT a public link):")
    print(COLAB_PREVIEW_URL)
if not PUBLIC_URL and _running and not COLAB_PREVIEW_URL:
    print("The app is running inside Colab only; no public URL and no preview URL could be determined.")
if not _running:
    print("The app did not start.")
print("=" * 70)
# Machine-readable result (one line) for tools that read this cell's output.
print("M2G_LAUNCH_RESULT " + json.dumps({"mode": MODE, "public_url": PUBLIC_URL, "colab_preview_url": COLAB_PREVIEW_URL,
                                         "tunnel_error": TUNNEL_ERROR}))

if _running:
    try:
        app.demo.block_thread()  # keep the app running until this cell is stopped
    except KeyboardInterrupt:
        print("Stopped.")
'''


def generate_notebook(a: Analysis, app_code: Optional[str] = None, settings: Optional[Settings] = None) -> Dict[str, Any]:
    """Return a validated Colab notebook dict for a generatable analysis. Raises GenerationError otherwise."""
    settings = settings or Settings()
    if not a.generatable:
        raise GenerationError(f"Cannot generate a notebook for status '{a.status}': " + "; ".join([*a.reasons, *a.generation_blocked_reasons]))
    refusal = linked_pickle_refusal(a)  # enforced here too: a notebook never embeds an app the generator would refuse
    if refusal:
        raise GenerationError("Cannot generate a notebook: " + refusal)
    code = app_code if app_code is not None else generate_app(a, settings)
    try:
        report = validate_app_code(code, settings)
        if report["model_id"] != a.model_ref.repo_id or report["revision"] != a.revision:
            raise GenerationError("Notebook app does not match the analysis (model id / revision).")
        reqs = validate_requirements_text(generate_requirements(a))
    except ValidationError as exc:
        raise GenerationError(f"Refusing to build notebook: {exc}")

    hw = a.hardware
    use4 = bool(a.load_in_4bit_recommended and a.quantization != "bitsandbytes")
    needs_gpu = bool(hw.needs_gpu or a.quantization == "bitsandbytes" or use4)

    ev = sorted(a.task_evidence, key=lambda e: -e.weight)[:4]
    ev_md = "\n".join(f"- {_md_safe(e.signal)}: {_md_safe(e.detail)} _(source: {_md_safe(e.source)})_" for e in ev) \
        or "- (no task evidence recorded)"
    warn_md = "\n".join(f"- {_md_safe(w)}" for w in a.warnings[:8]) or "- none"
    vram = f"{hw.est_vram_gb_fp16} GiB (fp16 estimate)" if hw.est_vram_gb_fp16 else "unknown"
    ref = a.model_ref  # the Hugging Face model that is loaded (for a GitHub analysis: its linked model, never the GitHub repo)
    via_github = (f"**Found via GitHub repository:** {_md_safe(a.source.canonical_url)} (its code was not run or installed)  \n"
                  if a.source.kind == "github" and a.model_source is not None else "")
    title = (
        f"# {_md_safe(ref.repo_id)} - Gradio demo on Colab\n\n"
        f"**Source:** {_md_safe(ref.canonical_url)}  \n"
        + via_github +
        f"**Task:** {_md_safe(a.task)} (confidence {a.task_confidence:.2f}) · **Library:** {_md_safe(a.library)} · "
        f"**Revision:** {_md_safe(a.revision or 'unpinned')}  \n"
        f"**Estimated VRAM:** {vram} · **Runtime:** {_md_safe(hw.recommended_runtime)}\n\n"
        f"**Why this task (evidence):**\n{ev_md}\n\n**Warnings:**\n{warn_md}\n\n"
        + _evidence_md(a) +
        "**How to use:** choose *Runtime > Change runtime type > T4 GPU* if a GPU is recommended, then *Runtime > Run all*. "
        "`trust_remote_code` is " + ("**ENABLED** (you opted in - this runs code from the model repo)" if a.trust_remote_code
                                    else "disabled") + ". Weights download on first run."
    )
    native = a.origin == "github-native"
    if native:  # independent GitHub model: say what this notebook really is; nothing is downloaded and no repository code is run
        title = title.replace("Weights download on first run.", "This is a scaffold: no weights are downloaded and no repository code is run or installed.")
        title += ("\n\n**Independent GitHub model repository (no Hugging Face model is used).** The architecture and inference contract are not "
                  "established, so no inference code was invented. Only the scaffold's own requirements are installed; the repository's "
                  "requirements are never installed.")
    troubleshooting = (
        "## Troubleshooting\n"
        "- **CUDA out of memory / environment check failed:** pick a larger GPU runtime or a smaller model"
        + (" (4-bit loading is already enabled for this model)." if use4 else ".") + "\n"
        "- **401/403 or 'gated repo':** accept the model licence on Hugging Face, then add `HF_TOKEN` in Colab Secrets.\n"
        "- **Dependency conflict after install:** *Runtime > Restart session*, then run the cells again from Step 1.\n"
        "- **Unsupported architecture:** this notebook only covers the pipeline task shown above; see the project README.\n"
        "- **No GPU assigned:** free-tier GPU availability is not guaranteed; try again later."
    )

    def fill(tpl: str, **kw: str) -> str:
        for k, v in kw.items():
            tpl = tpl.replace(f"@@{k}@@", v)
        return tpl

    pip_line = "%pip install -q " + " ".join(f"'{r}'" for r in reqs)
    try:  # the same loader.py / app.py slices the local package is written from (nothing is regenerated for Colab)
        loader_py, app_py = split_app_code(code)
        package_cells = [_code("m2g-06-loader", "%%writefile loader.py\n" + loader_py), _code("m2g-06-app", "%%writefile app.py\n" + app_py)]
    except GenerationError:  # code that was not produced by generate_app(): kept as one file
        package_cells = [_code("m2g-06-app", "%%writefile app.py\n" + code)]
    cells = [
        _md("m2g-00-title", title),
        _code("m2g-01-env", fill(_ENV_CELL, NEEDS_GPU=repr(needs_gpu), VRAM=_num(hw.est_vram_gb_fp16),
                                 VRAM4=_num(hw.est_vram_gb_4bit), USE4=repr(use4))),
        _md("m2g-02-md", "## Step 2 - install dependencies\nRequirements come from the repo analysis and are validated; "
                         "versions are minimums, not pins."),
        _code("m2g-03-pip", pip_line),
        _code("m2g-04-token", _TOKEN_CELL),
        _md("m2g-05-md", "## Step 4 - write the generated model code and Gradio app\nThe code below is exactly what the platform generated "
                         "and validated (the same loader.py and app.py as the local package); read it before running."),
        *package_cells,
        _code("m2g-07-load", fill(_LOAD_CELL, LOADER=repr(_loader_name(code)), USE4=repr(use4),
                                MODEL_MODULE=repr("loader" if len(package_cells) == 2 else "app"),
                                PICKLE=_PICKLE_CONSENT_BLOCK if a.ui_hints.get("safetensors_unavailable") else "")),
        _code("m2g-08-launch", _LAUNCH_CELL),
        _md("m2g-09-trouble", troubleshooting),
    ]
    if native:  # the scaffold's loader would (rightly) refuse to load anything, so the load step reports instead of failing the notebook
        cells = [_code("m2g-07-load", _NATIVE_LOAD_CELL) if c.get("id") == "m2g-07-load" else c for c in cells]
    nb = {
        "nbformat": 4,
        "nbformat_minor": NOTEBOOK_MINOR,
        "metadata": {
            "colab": {"provenance": [], "gpuType": "T4" if needs_gpu else None, "name": f"{ref.repo_id}.ipynb"},
            "kernelspec": {"name": "python3", "display_name": "Python 3"},
            "language_info": {"name": "python"},
            "accelerator": "GPU" if needs_gpu else "None",
        },
        "cells": cells,
    }
    try:
        validate_notebook(nb, settings)
    except ValidationError as exc:
        raise GenerationError(f"Generated notebook failed validation: {exc}")
    return nb


def notebook_to_json(nb: Dict[str, Any]) -> str:
    return json.dumps(nb, indent=1, ensure_ascii=False) + "\n"
