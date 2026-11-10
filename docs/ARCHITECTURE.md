# Architecture

Model-to-Gradio is a single-process, local-only Python application that turns a Hugging Face model URL (or a
GitHub AI repository URL) into a set of validated artifacts. It behaves as a **metadata compiler**: it downloads
only small metadata files, never model weights, never repository code, and it never executes the code it generates.

Output per supported model: `app.py`, `requirements.txt`, `colab_notebook.ipynb`, `analysis.json`.
Unsupported input: `analysis.json` plus a plain-language reason; no app is generated.

## 1. Design principles

1. **Nothing fetched is ever executed.** Repositories are read as bytes/text over HTTPS only.
2. **Nothing generated is ever executed by the platform.** It is checked statically (`ast`, `json`) and then handed to
   the user, who runs it locally or in Colab.
3. **Every decision is evidence-backed.** `analysis.json` records the signals and weights behind the task, the library,
   the dependencies and the hardware estimate.
4. **Fail closed, fail visibly.** Security defaults are off (`trust_remote_code`, pickle weights). Expected failures
   become typed stages; unexpected bugs are allowed to raise rather than be hidden.
5. **Secrets are write-only.** Tokens are read from the environment, sent only to their own API host, excluded from
   `repr()`, redacted from logs/errors, and scanned for in generated artifacts.
6. **The core is testable without network or Gradio.** Network access and the UI are both at the edges.

## 2. Layer diagram

```
┌──────────────────────── entrypoints ─────────────────────────┐
│  m2g/web.py (Gradio UI, 127.0.0.1:7860)   m2g/cli.py (argparse)│
└──────────────────────────────┬───────────────────────────────┘
                               │ both call the same function
┌──────────────────────────────▼───────────────────────────────┐
│ pipeline.py  run_pipeline()  → BuildResult(stage, ok, status) │
│   parse → inspect → analyze → generate → validate → write     │
└───┬───────────┬──────────────┬──────────────┬────────────────┘
    │           │              │              │
┌───▼────┐ ┌────▼─────┐ ┌──────▼──────┐ ┌─────▼──────┐
│security│ │inspector │ │  analyzer   │ │ generators │
│        │ │          │ │             │ │            │
│parse_  │ │_inspect_ │ │detect_task  │ │gradio_gen  │
│source_ │ │hf / _gh  │ │assess_      │ │colab_gen   │
│url     │ │          │ │hardware     │ │            │
│Safe    │ │RepoInfo  │ │_dependencies│ │validate.py │
│Fetcher │ │          │ │Analysis     │ │(AST gate)  │
└───┬────┘ └────┬─────┘ └──────┬──────┘ └─────┬──────┘
    │           │              │              │
┌───▼───────────▼──────────────▼──────────────▼────────────────┐
│ config.py  Settings (frozen), host allow-lists, .env loader,  │
│            TRUSTED_HOSTS / *_TOKEN_HOSTS / redirect suffixes  │
└──────────────────────────────────────────────────────────────┘
```

Dependencies flow strictly downward. No module below `web.py` imports Gradio; no module below `security.py`
touches a socket directly.

`model_to_gradio/` (config, errors, logging_setup) is an abandoned early scaffold and is not imported anywhere.
It can be deleted.

## 3. Module responsibilities

| Module | Responsibility | Key symbols |
|---|---|---|
| `m2g/config.py` | Frozen settings, host/redirect allow-lists, minimal `.env` loader, write probe | `Settings`, `TRUSTED_HOSTS`, `REDIRECT_HOST_SUFFIXES`, `HF_TOKEN_HOSTS`, `GITHUB_TOKEN_HOSTS`, `check_write_access` |
| `m2g/security.py` | Input URL parsing, SSRF-safe GET client, DNS pinning, secret scanning/redaction, identifier and path safety | `parse_source_url`, `SourceRef`, `SafeFetcher`, `validate_outbound_url`, `is_public_ip`, `redact_secrets`, `assert_no_secrets`, `validate_pip_requirement`, `safe_slug`, `ensure_within` |
| `m2g/inspector.py` | Metadata-only inspection of HF and GitHub repos | `inspect_source`, `RepoInfo`, `HF_FETCH_ALLOWLIST`, `parse_requirements` |
| `m2g/analyzer.py` | Evidence-based task, I/O, dependency, hardware and support analysis | `TASK_IO`, `detect_task`, `assess_hardware`, `analyze_hf`, `analyze_github`, `Analysis`, `Evidence` |
| `m2g/gradio_gen.py` | Per-task Gradio app templates, loader selection and code-derived requirements | `generate_app`, `generate_requirements`, `_BODIES`, `_PIPE_LOADER`, `_DIFFUSERS_LOADER`, `_ST_LOADER` |
| `m2g/runtime_compat.py` | Runtime-installed library/API probe and static compatibility checks | `render_library_probe_script`, `library_uses`, `check_library_compat` |
| `m2g/preflight.py` | Fail-closed runtime preflight for Gradio and model libraries before deployment | `render_probe_runner`, `run_preflight` |
| `m2g/colab_gen.py` | Colab notebook assembly (runtime check → install → token → app → load → launch) | `generate_notebook`, `notebook_to_json` |
| `m2g/validate.py` | Static validation of generated app source and notebook | `validate_app_code`, `validate_requirements_text`, `validate_notebook` |
| `m2g/pipeline.py` | End-to-end orchestration and result reporting | `run_pipeline`, `BuildResult`, `summarize` |
| `m2g/web.py` | Platform UI; thin adapter over the pipeline | `handle_submit`, `build_ui`, `next_steps` |
| `m2g/cli.py` | Command line with exit codes 0 / 2 / 1 | `main` |

## 4. Runtime flow

```
url ──parse_source_url──► SourceRef
         │
         ├─ huggingface ─► _inspect_hf ──► RepoInfo (API JSON, allow-listed config files, README, file sizes)
         └─ github ─────► _inspect_github ─► RepoInfo (API JSON, tree, README, requirements)
                                                │
                                    analyze(info) ─► Analysis
                                                │
                    generatable? ── no ──► write analysis.json only, stage=done
                            │ yes
                            ├─ generate_app ──► validate_app_code ──► code-derived requirements
                            ├─ generate_requirements ──► validate_requirements_text
                            ├─ generate_notebook ──► validate_notebook (re-validates embedded app)
                            └─ _write (path confinement + read-back) ──► 4 files, stage=done
```

`BuildResult.stage` moves monotonically through `parse → inspect → analyze → generate → write → done`.
`BuildResult.ok` means app **and** notebook were produced. `BuildResult.status` mirrors `Analysis.status`.

### Runtime compatibility flow

Generated code is never assumed to match the environment that will execute it. The Colab launchers install the
validated requirements, start a fresh interpreter, and probe the runtime versions and APIs actually referenced by
`app.py` (including imported names and transformer pipeline tasks). The shared preflight blocks deployment when a
referenced API is missing or cannot be inspected; it only applies repairs that are proven safe for Gradio keyword
compatibility. The same original runtime evidence is passed to the central error classifier, so model/library
compatibility failures remain distinct from Google authentication failures. Requirements are augmented from the
generated code's allow-listed imports and loader features such as quantization or `device_map`; arbitrary imports are
rejected rather than installed.

### Failure model

`EXPECTED_ERRORS = (SecurityError, InspectionError, GenerationError, ValidationError, FetchError)` plus `OSError`
during writes are converted into `BuildResult.error` at the current `stage`, passed through `redact_secrets`.
Anything else raises so it surfaces as a bug instead of being reported as a clean "unsupported".

### CLI exit codes

| Code | Meaning |
|---|---|
| `0` | app + notebook generated |
| `2` | analysed but unsupported/partial — no app |
| `1` | error (including unwritable output directory) |

## 5. Data model

| Type | Where | Role |
|---|---|---|
| `SourceRef` | `security.py` | Frozen repository identity: kind, owner/name, revision, canonical URL |
| `RepoInfo` | `inspector.py` | Raw evidence bag: API metadata, config/model_index/tokenizer/adapter JSON, README, file list with sizes, weight-format facts, warnings |
| `Evidence` | `analyzer.py` | One scored signal: `signal`, `source`, `detail`, `weight` |
| `HardwareAssessment` | `analyzer.py` | Params + source, fp16/4-bit VRAM estimates, runtime tier, CPU-local flag, flags |
| `Analysis` | `analyzer.py` | Single decision record consumed by both generators; `to_dict()` produces `analysis.json` |
| `BuildResult` | `pipeline.py` | Transport object for CLI/UI: stage, artifacts, paths, error, notes |

`Analysis` carries: `task`, `task_confidence`, `task_evidence`, `library`, `inputs`/`outputs`, `dependencies` +
`dependency_evidence`, `hardware`, `status`, `reasons`, `warnings`, `chat`, `trust_remote_code`, `quantization`,
`load_in_4bit_recommended`, `gated`, `ui_hints`, `license`, `min_transformers`, `use_safetensors`.

`Analysis.status ∈ {supported, supported_with_warnings, unsupported, partial}`;
`generatable` is true only for the first two.

## 6. Trust boundaries and invariants

| Boundary | Mechanism |
|---|---|
| Input URL | `parse_source_url`: https only; no userinfo, non-443 ports, `%`, `//`, `..`, control chars; host allow-list; reserved-segment lists for HF and GitHub; strict owner/name/revision regexes; length cap |
| Every HTTP hop | `validate_outbound_url`: scheme/host allow-list (exact trusted hosts or CDN suffixes), IP-literal hosts rejected, **all** resolved addresses must be globally routable |
| DNS rebinding | `_PinnedHTTPSConnection` connects to the pre-validated IP while keeping SNI and certificate verification for the hostname |
| Credentials | `HF_TOKEN` → only `huggingface.co`; `GITHUB_TOKEN` → only `api.github.com`; never in errors, artifacts or `repr()` |
| Response size | Per-file caps (`max_response_bytes`, `_MAX_SMALL_FILE`), streaming read cap, request timeout, redirect limit, per-session dead-host circuit breaker |
| Redirects | Handled manually; each hop is re-validated in full; only CDN suffixes may be redirected to |
| Fetched content | HF: only `config.json`, `model_index.json`, `tokenizer_config.json`, `adapter_config.json`, `preprocessor_config.json`, `generation_config.json`, `README.md`. Weight files can never be requested |
| Generated app | Fixed templates; repo-controlled values inserted with `repr()`; `ast.parse` self-check; unresolved `@@` sentinel is an error; `assert_no_secrets` |
| App validation | Import allow-list; forbidden calls (`eval`, `exec`, `compile`, `__import__`, `system`, `popen`, `spawn`, `fork`, `load(s)`, `unlink`, `rmtree`, `urlopen`, …); forbidden modules (`pickle`, `ctypes`, `socket`, `requests`, `urllib`, …); module-level statement allow-list; literal `MODEL_ID`, `REVISION`, `TRUST_REMOTE_CODE`, `USE_SAFETENSORS`; no hard-coded `trust_remote_code=True`; no hard-coded `share=True`; must define `predict`/`chat_fn` and `demo` |
| Notebook validation | nbformat 4.5; code cells ship with empty outputs; exactly one `%%writefile app.py` cell (re-validated as an app); only `%pip install` magics; `subprocess.run` only with a literal argv list and no `shell=True`; import allow-list extended for Colab |
| Disk | `ensure_within` confines every write to the output directory; `safe_slug` derives the folder name; each file is read back and compared after writing |
| Dangerous opt-ins | `M2G_ALLOW_REMOTE_CODE` and `M2G_ALLOW_PICKLE_WEIGHTS` are off by default and must be consistent across analysis, generation and validation |

## 7. Decision engines

### 7.1 Task detection (weighted evidence voting)

`detect_task(info)` accumulates scores in a candidate map:

| Signal | Weight |
|---|---|
| `pipeline_tag` from the Hub API | 1.0 |
| diffusers `_class_name` in `model_index.json` | 0.9 |
| library/tag `sentence-transformers` | 0.8 |
| config `architectures[0]` suffix mapping | 0.7 (0.4 for a bare `*Model` head) |
| tags that name a task | 0.3 |

Architecture mapping special-cases `*ForConditionalGeneration` (voice/caption/text2text by `model_type`),
`*ForQuestionAnswering` (VQA vs QA), speech and caption model types, TTS classes, CLIP/Siglip, and
`VisionEncoderDecoderModel`. Equivalent task pairs (e.g. `summarization ≡ text2text-generation`,
`feature-extraction ≡ sentence-similarity`) are normalised so they do not count as conflict. A genuine conflict
(second candidate ≥ 0.6) lowers confidence by ×0.6 and records a note.

### 7.2 Blocking gates (`analyze_hf`)

Unknown task; known-unsupported task; unsupported task; adapter-only PEFT repo; unsupported library
(`_UNSUPPORTED_LIBS`); GGUF-only or ONNX-only; no weight files; non-`bitsandbytes` quantization; `auto_map` /
`custom_code` without the opt-in; pickle-only weights without the opt-in; diffusers task without `model_index.json`;
private repo; estimated VRAM exceeding a single Colab GPU. Any reason → `unsupported`.

### 7.3 Hardware estimation

1. Parameter count: Hub `safetensors` metadata → weight-file bytes assuming 16-bit → `12·layers·hidden² + vocab·hidden`.
2. `weights_gb_fp16 = params·2/1e9`; `est_vram_gb_fp16 = weights·1.2 + 1`; `est_vram_gb_4bit = params·0.5/1e9·1.2 + 1`.
3. Tiering: CPU-friendly (≤1B, non-image) → free T4 (≤13.5 GB) → T4 with 4-bit → L4 → A100 40 GB → A100 4-bit →
   exceeds Colab.

All figures are labelled estimates; the notebook measures the real runtime before installing anything.

### 7.4 Dependency derivation

Starts from a fixed base (`gradio`, `torch`, `safetensors`, `numpy`, `transformers`/`diffusers`, `accelerate`,
`sentence-transformers`) and adds `pillow`, `timm`, `soundfile`+`librosa`, `sentencepiece`+`protobuf`, `sacremoses`
and `bitsandbytes` when evidence requires it. Each entry keeps a human-readable reason in `dependency_evidence`,
and every requirement is re-validated with `validate_pip_requirement` before it can reach a file.

### 7.5 GitHub branch

Always `partial` (no app is generated: that would require running unreviewed repository code). `Analysis.host` is
`github`; `Analysis.library` is the detected ML stack (never the hosting platform). Task evidence is tiered:

1. **authoritative** - the repo's own linked Hugging Face model (`RepoInfo.primary_hf_model`, ranked by
   `inspector.rank_hf_links`), analysed from allow-listed Hub metadata (no weights);
2. **structural** - ast-only facts from a few small source files (`source_scan`) and statically parsed dependencies
   (`requirements.txt`, `pyproject.toml`, literal `setup.py`/`setup.cfg` `install_requires`), giving frameworks and
   input/output modalities;
3. **heuristic** - README/topic keywords; ignored when they contradict detected modalities and capped at confidence 0.35.

Hardware is estimated only from the primary model's Hub metadata; otherwise it is reported as unknown and recorded in
`Analysis.uncertainties`. No GPU tier is assumed.

## 8. Generation

### 8.1 Gradio app

`generate_app` selects:

- **body** by `_body_key`: the task, `text-generation:chat`, or `embed:sentence-transformers` / `embed:transformers`;
- **loader** by library: `transformers.pipeline`, `DiffusionPipeline`, or `SentenceTransformer`.

Shared header defines `MODEL_ID`, `REVISION`, `TASK`, `TRUST_REMOTE_CODE`, `USE_SAFETENSORS`, `PREQUANTIZED`,
`LOAD_IN_4BIT`, `HF_TOKEN`, `DEVICE`, `DTYPE`. Models load lazily on first request. Launch binds to
`GRADIO_SERVER_NAME` (default `127.0.0.1`) and `share` only when `GRADIO_SHARE=1`.

Repo-derived header text passes through `_clean` (no quotes/backslashes/newlines); values inserted into code use
`repr()`. `generate_requirements` emits a sorted, de-duplicated, validated list.

### 8.2 Colab notebook

Ten cells, nbformat 4.5, no outputs:

1. title + evidence + warnings (markdown, sanitised with `_md_safe`);
2. **runtime check first** (Python, RAM, GPU name/VRAM, `nvidia-smi`; fails with actionable advice if the GPU is
   missing or too small);
3. install note; 4. `%pip install -q …`;
5. `HF_TOKEN` from Colab Secrets;
6. `%%writefile app.py`;
7. import the app, call `get_pipe`/`get_model`, run one example as a self-test;
8. launch with `share=SHARE_PUBLIC_LINK`;
9. troubleshooting (markdown).

Metadata sets `accelerator: GPU` and `gpuType: T4` only when analysis says a GPU is needed.

## 9. Testability design

Dependency injection at the network edge — `SafeFetcher(settings, resolver, transport)` — lets
`tests/fakehub.py` simulate both APIs with no sockets. Generation tests stub `gradio`, `torch` and pipeline
objects and include explicit template-injection resistance cases. `web.py` imports Gradio lazily, so the whole
core runs under `unittest`/`pytest` offline.

| Test module | Covers |
|---|---|
| `test_security.py` | URL parsing, IP classification, allow-list, fetcher redirects/caps, identifier safety |
| `test_inspector.py` | HF and GitHub inspection, requirements parsing |
| `test_analyzer.py` | task detection, dependencies, hardware, safety gates, GitHub analysis |
| `test_gradio_gen.py` | generated code shape, injection resistance, logic with a stubbed backend |
| `test_pipeline_e2e.py` | app validator, notebook validator, end-to-end pipeline, entry points |

Not covered by automated tests (documented in the README): real model inference, Gradio under a real install,
live Hugging Face/GitHub access, and a real Colab run.

## 10. Extension points

- **New task**: add an entry to `TASK_IO`, a body to `_BODIES` (plus `_body_key` handling if it has variants), and a
  loader if it needs a new library. Add a test.
- **New library**: extend `Analysis.library`, the loader selection in `generate_app`, `_dependencies`, and the
  `validate.py` import allow-list together — the three must agree or validation will reject the app.
- **New source host**: extend `TRUSTED_HOSTS` / redirect suffixes / token hosts in `config.py` and add a parser
  branch in `security.parse_source_url`; `inspector.inspect_source` dispatch follows.
- **Policy change**: dangerous opt-ins are `Settings` fields threaded through `analyze_hf`, `generate_app` and
  `validate_app_code`; changing a default in one place only is a bug.

## 11. Known gaps

1. No packaging: there is no `pyproject.toml`, so `m2g` is importable only via `PYTHONPATH` (set by the `.bat`
   scripts and `pytest.ini`).
2. Validation is split: the pipeline validates app and requirements directly but relies on `generate_notebook`
   to validate the notebook internally.
3. `HF_TOKEN` and `GITHUB_TOKEN` are mutually exclusive per request (`security.py`, `elif`), harmless today because
   the host sets never overlap.
4. `model_to_gradio/` is dead code.
5. Task detection, dependency derivation and hardware figures are heuristics; they are reported as such in
   `analysis.json` and in every warning path.
