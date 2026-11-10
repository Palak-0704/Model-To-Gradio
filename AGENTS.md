# AGENTS.md — Model To Gradio (m2g)

Repository knowledge for agents working in this codebase. Keep it short and factual.

## What this project is

A **metadata compiler**: it accepts a public Hugging Face model URL or a public GitHub AI/ML repository URL and emits
`app.py` (Gradio), `requirements.txt`, `colab_notebook.ipynb` and `analysis.json`. It fetches **small metadata files only** —
it **never downloads weights and never executes repository code**. `trust_remote_code` is never enabled automatically.

## Test suite

- Runner: `pytest` (see `pytest.ini`; `addopts = -q`, `testpaths = tests`).
- Full suite baseline: **1418 passed, 9 skipped, 388 subtests passed** (~60 s), offline (fake transports).
- All tests run against fakes — no network, no REA server, no OpenCode, no credits:
  - `tests/fakehub.py` — fake HF Hub + GitHub transport (`FakeWeb`, `hf`, `add_github_repo`, `RangeWeb`).
  - `tests/fake_rea_server.py` — mock REA MCP server.
  - `tests/fake_opencode.py` — fake OpenCode adapter.
- Run everything: `python -m pytest`. Do not weaken tests to make the suite pass.

## Analysis pipeline (the part this repo cares about)

`m2g/pipeline.py` orchestrates: `inspector` (source inspection, metadata only) → `analyzer` (`analyze_hf` /
`analyze_github`) → optional REA investigation (`rea_client`, `rea_capabilities`) → `build_combined_analysis`
(`m2g/analyzer.py`) → `gradio_gen` (OpenCode) → `validate` → `colab_gen`.

### Key modules

- `m2g/inspector.py` — fetches bounded metadata for HF and GitHub. `_inspect_github_model_configs` **does** read a repo's
  own `config.json` into `RepoInfo.config` / `config_paths` (the older test note claiming it does not is stale).
- `m2g/analyzer.py` — the core: `analyze_hf`, `analyze_github`, `build_combined_analysis`. The combined report is the
  single structured handoff consumed by generation and written to `analysis.json` under `combined_analysis`.
- `m2g/capabilities.py` — capability detection + `build_model_evidence` (bounded config digest).
- `m2g/inference_contract.py` — loader/contract selection.
- `m2g/rea_client.py`, `m2g/rea_capabilities.py` — REA MCP integration: tool discovery, adaptive question planning. The
  planner also resolves the platform's OWN source-analysis gaps (a `source_text`-tagged tool is handed the already-collected
  source staged read-only in the scratch dir; no extra download, nothing executed). Questions no eligible capability can answer
  are recorded as open gaps (`rea_investigation.source_questions`). MCP prompt templates are discovered read-only via
  `prompts/list` and surfaced (`prompts`), never rendered or auto-applied.
- `m2g/evidence/` — evidence collection (repo inspectors, tensors, files, guards, context).
- `m2g/claims.py` — claim/validation engine. Claims are derived from REA findings too (`refs_from_rea`), with conflicts kept.
- `m2g/source_analysis.py` — static (AST-only) GitHub source analysis; the same analysis runs on a Hub repo's shipped custom
  code (`config.auto_map` / `custom_code` tag) via `inspector._hf_custom_code_source`.

### `combined_analysis` report (PART M, 25 areas)

Top-level keys (order): `schema_version, python_analysis, rea_analysis, repository, model, inference_contract,
dependencies, safety, unsupported_components, claims, parameter_comparison, weight_format, rea_investigation, conflicts,
confidence, evidence, warnings, uncertainties, recommendations, limitations`. `COMBINED_SCHEMA_VERSION = 1` lives in
`m2g/analyzer.py` and is imported by `m2g/gradio_gen.py`. Keep additions backward-compatible; bump the version only for
breaking changes and update every consumer.

Mapping of the 25 PART M areas: identity/provider/revision + classification + task/subtask + architecture + frameworks →
`model`; loading/input/output/preprocess/inference/postprocess → `inference_contract`; dependencies → `dependencies`;
runtime + hardware + artifacts → `python_analysis` / `repository`; conclusions/confidence/conflicts/unknowns/coverage/REA →
`evidence`/`confidence`/`conflicts`/`uncertainties`/`repository.coverage`/`rea_investigation`; safety exclusions → `safety`;
unsupported/inaccessible → `unsupported_components`; remaining limitations → `limitations`.

## Engineering rules observed here

- **Evidence over guessing.** Never invent a task, architecture or inference contract from a model's name. Fields the
  evidence did not establish stay `None`/empty and are listed as unknown.
- **Hardware is advisory (PART N).** Insufficient VRAM / CPU-only must never block metadata analysis; report it as a warning.
- **REA is additive.** A failed/partial/absent REA result must leave the Python analysis unchanged and keep going.
- **Static safety (PART I).** No weight downloads, no repo-code execution, no auto `trust_remote_code`, no installs.
- GitHub-only AI/ML repos are first-class inputs (`origin == "github-native"`); they yield an honest scaffold, never a fake
  loader. A repo's **own** `config.json` architecture is structural evidence and drives the native task.
- Write comments that explain *why* (invariants, trust boundaries), not *what*.

## Conventions

- No new dependencies without justification; reuse `SafeFetcher`, `Settings`, `requests`.
- Lint signal in this repo is minimal (`ruff` not pinned); the one tracked warning was `F841` in `m2g/inspector.py` (fixed).
- Do not touch generation/Colab/deploy/frontend modules unless an analysis integration truly requires it.
