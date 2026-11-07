# PART 1 PROGRESS - Independent GitHub model support

## Part 1 objective
A public GitHub AI model repository is independently eligible for analysis + Gradio/Colab artifact generation. A linked HF model is NOT mandatory. HF-originated and linked-HF GitHub flows keep working. No fabricated architecture/task/IO/inference. Security unchanged.

## Starting state recovered
- `m2g/inspector.py` already provides (DO NOT REDO): `RepoInfo.files` (tree), `config/model_index/preprocessor_config/tokenizer_config/generation_config` + `config_paths`, `release_assets`, `source_scan` (ast only), `requirements`/`requirement_flags`, `linked_hf_models`/`primary_hf_model`/`primary_model_info`, `inspection_gaps`, `commit_sha`. Constants `WEIGHT_EXTS`, `PICKLE_EXTS`.
- Original blockers: `analyze_github` always `partial` unless linked HF adopted; `gradio_gen.check_analysis_integrity` / `generate_app` required `model_source` HF.
- No Inspector correction needed.

## Design (extends existing pipeline)
- `Analysis.origin` ("huggingface" | "github-linked-hf" | "github-native") + `Analysis.native` digest (eligible, signals, confirmed/inferred/unknown, weights, configs, missing).
- `model_ref` stays the GitHub `source` for github-native (no fake HF id). MODEL_ID in the scaffold = GitHub owner/name.
- Eligibility (github-native): model artifact (weight file in tree/releases OR model-describing config with architectures/model_type/_class_name) AND a second signal (ML framework detected, or weights+config both). README keywords / task guess / entrypoint names never qualify. Otherwise `partial` with explicit `missing` reason.
- Generation for github-native = deterministic scaffold (no OpenCode). Decision: scaffold never loads weights; pickle gating enforced inside generated loader (refuses pickle suffixes unless M2G_ALLOW_PICKLE_WEIGHTS=1, and still raises NotImplementedError). Repo requirements are never put into generated requirements.txt (only scaffold imports: gradio).

## Checkpoint 0 status: COMPLETED
## Checkpoint 1 status: COMPLETED (analyzer.py saved)
## Checkpoint 2 status: COMPLETED (gradio_gen.py + native_scaffold.py saved; not executed)
## Checkpoint 3 status: COMPLETED (colab_gen.py, pipeline.py, package_store.py saved; not executed)
## Checkpoint 4 status: COMPLETED IN CODE (written, NOT executed, NOT even syntax-checked by a tool; the user runs the tests)
- 4A DONE: tests/test_github_native.py written (unittest style, reuses fakehub.py, test_analyzer.analyze_preset, test_github_generation.github_repo_for/readme_for).
- 4B DONE: cli.py, web_api.py, colab_workflow.py have NO HF-only gate on the native path (left untouched). web.py `generation_banner` said "A working Gradio app was generated" for a scaffold: fixed with a github-native branch (wording only).
- 4C DONE: native_scaffold.py no longer has M2G_ALLOW_PICKLE_WEIGHTS/ALLOW_PICKLE_WEIGHTS; get_model() refuses pickle suffixes outright (RuntimeError, no consent switch), otherwise raises NotImplementedError; M2G_MODEL_PATH is documented as informational only. `_t` collapses any run of '@' (was: '@@@' could survive as '@@' and make validation reject the repo). colab_gen.py `_NATIVE_LOAD_CELL` closing print text corrected (no longer implies M2G_MODEL_PATH enables loading).
- 4D DONE: this file.

## Files modified
- PART1_PROGRESS.md (new)
- m2g/analyzer.py
- m2g/gradio_gen.py
- m2g/colab_gen.py
- m2g/pipeline.py
- m2g/package_store.py
- m2g/native_scaffold.py (NEW; Checkpoint 4C edits: pickle consent removed, `_t` '@' fix)
- m2g/colab_gen.py (4C: one print text in _NATIVE_LOAD_CELL)
- m2g/web.py (4B: generation_banner github-native branch)
- tests/test_github_native.py (NEW, 4A)

## Completed implementation
- analyzer.py: `Analysis.origin`/`native`, `generatable` native branch, `_model_like_config`, `_github_native_evidence`, `_adopt_github_native`, `analyze_github` else-branch, `_adopt_linked_model` sets origin=github-linked-hf.
- native_scaffold.py: templates + `build_native_parts` (confirmed/inferred/unknown facts, safe get_model, honest predict, torch-free app header).
- gradio_gen.py: `_check_native_integrity`, `build_native_scaffold`, `finalize_native_scaffold`, dispatch in `check_analysis_integrity` / `generate_app` / `finalize_generated_code`; `split_app_code`/`join_package_code` accept NATIVE_APP_HEADER.

- colab_gen.py: for origin github-native the title states scaffold/no download, and the load cell (`m2g-07-load`) is replaced by `_NATIVE_LOAD_CELL` that prints SCAFFOLD_REPORT instead of failing on get_model(). Env/GPU cells untouched.
- pipeline.py: generation step label + summary lines for native (confirmed/inferred/unknown). analysis.json is still written before generation (partial output preserved).
- package_store.py `fingerprints_for`: native uses scaffold text as fingerprint input.
## Remaining work
(Checkpoint 3 done.) Not inspected/verified: web.py / web_api.py / cli.py / colab_workflow.py / local_* may gate on `model_source` or HF refs (out of Part 1 scope unless they block native generation).

## Known limitations
- SCAFFOLD ONLY: github-native yields a scaffold. get_model() always raises (no weights / pickle refused / NotImplementedError) and predict() always raises gr.Error. NO working inference exists for independent GitHub repos.
- Native generation is scaffold-only by design: no working inference for unknown architectures.
- Nothing executed or tested in this session (user runs tests).

## Tests added but not run
- tests/test_github_native.py (NEW, never executed): classes TestNativeEligibility, TestNativeRejection, TestNativeIntegrity, TestNativeScaffold, TestNativeNeverTouchesWeightsOrRepositoryCode, TestNativePipeline, TestExistingFlowsAreUnchanged. Nothing in Part 1 has been run. Expect some first-run failures from assumptions made without execution (see 'Assumptions to verify').

## Assumptions to verify on first test run
- Analysis.to_dict() includes `origin` and `native` (analysis.json assertions in TestNativePipeline).
- Analysis.native defaults to a falsy value for HF / github-linked-hf analyses.
- Exact summary/banner/notebook strings asserted ("scaffold only, no inference is implemented", "implements no loading and no inference").
- run_scaffold() execs the generated scaffold with a stub gradio, socket.socket and subprocess.Popen patched.

## Newly found problem (NOT fixed, Inspector was out of scope)
- inspector.py declares RepoInfo.config/model_index/.../config_paths/release_assets and GH_MODEL_CONFIG_FILES, but `_inspect_github` never fetches config files or GitHub Releases, and evidence_bridge only fills metadata for a linked HF model. So through the real inspector a github-native repo can only qualify via a weight file in the git tree (plus a framework from requirements/imports); the config and release-asset eligibility paths are reachable only when RepoInfo is built by hand (tests use dataclasses.replace). The earlier claim 'Inspector already supports GitHub config/release metadata' was wrong.
- Unverified: local_runner.ensure_package_matches_analysis / verify_generated_app (Part 2 area, not touched) may assume an HF model for github-native packages; colab_workflow validates app code only and has no HF gate.

## Exact next action if interrupted
Future session: (1) user runs the commands in the final report and pastes failures; fix test or code per failure. (2) Decide whether to add GitHub config/release fetching to inspector._inspect_github (fetch GH_MODEL_CONFIG_FILES at fetch_ref via raw.githubusercontent.com, GET {api}/releases with MAX_RELEASES/MAX_RELEASE_ASSETS, fill config/config_paths/release_assets; text/metadata only, never download assets), then replace the dataclasses.replace workarounds in tests. (3) Part 2/3 untouched.

## (superseded) next action from before Checkpoint 4
Checkpoint 3: read colab_gen.py ~290-360 and pipeline.py ~440-450; adjust only HF-specific assumptions for origin == "github-native". Leave Colab GPU/hardware checks untouched.
