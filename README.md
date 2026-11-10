# Model to Gradio

Paste a **Hugging Face model URL** (or a GitHub AI repo URL). The platform inspects the repository's *metadata only*,
works out what the model does, and generates:

- a model-specific **Gradio app** (`app.py`) with real inference code (transformers / diffusers / sentence-transformers),
- a matching `requirements.txt`,
- a **Google Colab notebook** (`colab_notebook.ipynb`) that checks the runtime, installs dependencies and launches the app,
- an `analysis.json` with the evidence behind every decision.

If a model is unsupported, you get the analysis and a plain-language reason, and no app is generated.

## Quick start (Windows 11)

Requires Python 3.10+ from python.org (tick "Add python.exe to PATH").

```
cd "D:\Model To Gradio"
setup_windows.bat        :: creates .venv and installs gradio + test tools
run_platform.bat         :: opens the platform at http://127.0.0.1:7860
```

Then paste a URL, e.g. `https://huggingface.co/distilbert/distilbert-base-uncased-finetuned-sst-2-english`,
and click **Analyze and generate**. Output lands in `D:\Model To Gradio\output\<owner>__<model>\`.

Command line instead of the UI:

```
.venv\Scripts\python -m m2g.cli https://huggingface.co/openai/whisper-tiny
```
Exit code 0 = app and notebook generated, 2 = analysed but unsupported, 1 = error.

Run the tests: `run_tests.bat` (uses `unittest`; no network needed).

## Where models actually run

The platform itself is light: it only fetches small metadata files (config, model card, file list). It **never downloads
model weights and never runs repository code**.

- **Colab (recommended):** upload `colab_notebook.ipynb` at <https://colab.research.google.com> (File > Upload notebook),
  choose *Runtime > Change runtime type > T4 GPU*, then *Run all*. The first cell checks the real GPU/RAM and stops with a
  clear message if they are not enough. A Google login alone does **not** guarantee a GPU runtime.
- **Locally:** only sensible for small CPU-friendly models. The UI tells you when that is the case; then
  `cd output\<model>`, create a venv, `pip install -r requirements.txt`, `python app.py`. Weights download on first use.

Gated models: put your token in a `.env` file (copy `.env.example`) as `HF_TOKEN=...`. It is sent only to
huggingface.co, never printed, and never written into generated files. In Colab use *Secrets* (key icon) named `HF_TOKEN`.

## What is supported

Hugging Face models whose repo metadata clearly identifies one of these tasks (the analyzer cites its evidence:
`pipeline_tag`, config architectures, model_index, file names, model card):

| Area | Tasks |
|---|---|
| Text | text-classification, token-classification, question-answering, fill-mask, text-generation (incl. chat), summarization, translation, text2text-generation, zero-shot-classification, feature-extraction, sentence-similarity |
| Vision | image-classification, zero-shot-image-classification, object-detection, image-segmentation, image-to-text, visual-question-answering |
| Audio | automatic-speech-recognition, audio-classification, text-to-speech, text-to-audio |
| Generative | text-to-image (diffusers) |

## Limitations (by design)

- **Not supported -> no app is generated:** depth-estimation, document-QA, image-to-image, image-text-to-text,
  any-to-any, text-to-video, table-QA, zero-shot-object-detection, reinforcement learning; GGUF/ONNX/Keras/spaCy/
  scikit-learn/fastText/peft-adapter-only/ultralytics/speechbrain/espnet and similar non-transformers libraries;
  pre-quantized GPTQ/AWQ/FP8 and similar (bitsandbytes 4-bit is supported); models whose estimated size exceeds free Colab.
- **Pickle weights** (`.bin`, `.pt`, `.ckpt` without safetensors) are refused unless you set `M2G_ALLOW_PICKLE_WEIGHTS=1`.
  Loading pickle can execute arbitrary code.
- **`trust_remote_code` is off.** Models that need it are refused unless you set `M2G_ALLOW_REMOTE_CODE=1`; the generated app
  and notebook then show a prominent warning. Only do this for repositories you have reviewed.
- **GitHub repositories** are analysed (README, requirements, entry-point candidates, linked Hugging Face models) and the
  repository's own code is **never run, imported or installed**. An app is generated only *through the repository's own
  linked Hugging Face model*: that model must be clearly identified (README/code links ranked by name, owner and wording; a
  backbone or an ambiguous set of links is not guessed), its metadata must be readable, and it must pass exactly the same
  checks as a Hugging Face URL (supported task, safetensors, no `trust_remote_code`, size) with a firmly established task
  and no contradiction with what the repository's own code takes as input. The app loads that Hugging Face model at its
  Hugging Face revision; the repository's own `requirements.txt` is not used. Anything else stays `partial` with the reason
  and no app. Output goes to `output\github__<owner>__<repo>\`, never over a Hugging Face run's folder.
- **Hardware figures are estimates** derived from parameter counts and file sizes, not measurements. The Colab notebook
  measures the real runtime.
- **Task detection is evidence-based but can be wrong** for models with missing or contradictory metadata; such models are
  reported as unsupported or with warnings rather than guessed.
- The generated app is a generic UI for the task; custom prompts/preprocessing a particular model card requires are not inferred.

## Security model

- URLs: only `https://huggingface.co` and `https://github.com` repository URLs are accepted; userinfo, odd ports, path tricks
  and look-alike hosts are rejected before any request.
- Network: GET-only client with a host allow-list, public-IP check (blocks private/loopback/link-local/metadata addresses),
  DNS-pinned connections, manually validated redirects (CDN hosts only), response-size caps and timeouts.
- Credentials go only to their own API host and are redacted from errors and artifacts; generated code and notebooks are
  scanned for secrets.
- Generated code is validated with `ast` (allowed imports, no `eval/exec/system/subprocess/pickle/socket`, literal
  `MODEL_ID`/`REVISION`/flags, no hard-coded `share=True`) and is **never executed by the platform**. All metadata is
  inserted as escaped literals.
- Output files are written only inside the output folder, then read back to confirm.
- The platform UI binds to `127.0.0.1` only.

## Project layout

```
m2g/security.py    URL parsing, SSRF/redirect-safe fetcher, secret redaction, path safety
m2g/inspector.py   metadata-only inspection of Hugging Face / GitHub repos
m2g/analyzer.py    evidence-based task, I/O, dependency, hardware, compatibility analysis
m2g/gradio_gen.py  per-task Gradio app templates
m2g/colab_gen.py   Colab notebook generator
m2g/validate.py    static validation of generated apps and notebooks
m2g/pipeline.py    URL -> files orchestration
m2g/web.py, cli.py platform UI and command line
tests/             offline tests using a fake Hugging Face/GitHub transport (tests/fakehub.py)
```
`model_to_gradio/` is an abandoned early scaffold and is unused; it can be deleted.

## Verification status

Offline unit and end-to-end tests (fake network) cover inspection, analysis, generation, validation, notebook generation and
the pipeline. **Real model inference, the Gradio UI under a real Gradio install, live Hugging Face/GitHub access, and a real
Colab run have not been verified by the automated tests.** Try one small model end to end after setup (see `docs/FIRST_RUN.md`).
