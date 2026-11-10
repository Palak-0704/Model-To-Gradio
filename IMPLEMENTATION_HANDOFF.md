# IMPLEMENTATION_HANDOFF (written from a chat session with file read/write only)

NOTHING BELOW HAS BEEN EXECUTED. No Git, pytest, OpenCode, REA, ONNX Runtime or Gradio could be run from that session.

## Changed (untested)
- `m2g/analyzer.py` (Task 5, partial):
  - `_DOC_TENSOR` regex + `tensors` in `_documented_native_contract`: explicitly labelled README tensor declarations
    ("Input name: x, shape: [..]") are stored as `contract["documented_tensors"]` with `verified: False`. They do NOT feed
    `inputs`/`outputs`, `contract_verified`, or eligibility.
  - `generation_evidence` gains `onnx_candidates` (<=10), `artifact_selection` (unique|ambiguous|none), `documented_tensor_count`.

## Observed already in the repo (not changed by me)
- Task 3: `analyze_hf` already turns pickle-only into a warning (not a reason), and the generated header keeps
  `ALLOW_PICKLE_WEIGHTS` / `LOAD_SAFETENSORS` consent at runtime. Still needs a regression test and a check that
  `native_scaffold` / loader paths never unpickle.

## Not started / blocked, with exact blocker
1. HF ONNX-only: `analyze_hf` still appends "ONNX-only repository; ONNX runtimes are not supported in v1."
   Removing it alone is UNSAFE: `generate_app` would then build a transformers loader for an ONNX repo.
   Needs an HF-ONNX generation path reusing `onnx_runtime.py`, `native_executor.py`, `native_readiness.py`,
   `select_loader`, SHA-256 verification, with `a.origin` handling (many consumers key on "github-native").
2. Weak-evidence scaffold: the reason "No capability is established by firm evidence..." in `analyze_hf` blocks generation.
   Needs a generic HF scaffold (reuse `native_scaffold.build_native_parts` generalised beyond GitHub) plus a status
   kept separate from readiness; `Analysis.generatable` must not weaken the supported-model gate.
3. Missing config.json: `analyze_hf` only warns. Fallback should extend `detect_capabilities` fallback modalities with
   preprocessor/tokenizer config and safetensors metadata (check `capabilities.config_modalities`). README text is
   deliberately not task evidence today.
4. Task 5 remainder: release-asset revision pinning/identity and README preprocessing parsing beyond the single-line regexes.

## Next steps (in order)
00. UPDATE (session 3, NOT executed): policy reconciled. Direct HF analysis of a pickle-only model = generatable CANDIDATE
   (runtime pickle consent unchanged; `generation_blocked_reasons` NOT used). Adoption through a GitHub link is refused in
   `analyzer._check_linked_model` (text contains 'cannot be generated', 'pickle', 'safetensors') unless pickle consent is set.
   The earlier session-2 `generation_blocked_reasons` change was REVERTED. The 8th file in the refresh test is
   `output/.m2g-locks/<slug>.lock`, retained on purpose by `package_store.model_lock`; the test now counts it apart.
   Tests edited (intentional policy change): `test_github_generation.py` (HF pickle generatable), `test_web_ui_state.py`
   (HF pickle URL skipped in the unavailable-message loop; lock file counted apart).
   Model-vs-platform classification (Phase 2/4) NOT implemented. Tasks 1, 2, 4 NOT implemented.
0. UPDATE (session 2, still NOT executed): root cause of the pickle/refusal regressions was the pre-existing pickle branch in
   `analyze_hf` (it only warned, so pickle-only models became generatable). Fixed: that branch now appends to
   `generation_blocked_reasons` (status stays supported_with_warnings; tests expect exactly that) and `gradio_gen.generate_app`
   now includes blocked reasons in its refusal text. Re-run the six affected test files. Refresh "8 vs 7 files" and UI
   outcome failures are UNVERIFIED but likely downstream (a pickle preset was being generated); if they persist, inspect
   `m2g/web_api.py` / `web_ui` result mapping and stale output dirs.
1. `git status`, `.venv\Scripts\python -m pytest tests/test_github_native.py tests/test_analyzer.py -q`; fix any break from the
   `generation_evidence` key additions.
2. Add a test for `_DOC_TENSOR` (README with and without labelled tensors; assert `verified` is False, eligibility unchanged).
3. Implement tasks 3 (tests), 4, 2, then 1, in that order; full suite once at the end.
4. One real OpenCode run on a small ONNX repo; then Gradio launch.
