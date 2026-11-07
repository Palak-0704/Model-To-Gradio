# IMPLEMENTATION_PROGRESS.md - universal Colab reliability

Goal: stop per-model Gradio start-up crashes on Colab (e.g. `ChatInterface.__init__() got an unexpected keyword argument 'type'`
on Gradio 6.30.0) with one universal mechanism: check -> preflight -> bounded recovery -> clean up -> verify.
Gradio is never downgraded.

## How tests were run (read this first)
Phase 1 was verified by the user on Windows (host `.venv`, pytest): `tests/test_gradio_compat.py` 16 passed, `tests/test_gradio6_compat.py`
8 passed, total 24 passed, 0 failed. The assistant cannot execute anything on the Windows host (the process-capture tool refuses native
Windows); its own runs happen in a Linux container with `python -m unittest` (no pytest/gradio/network there), so for later phases the
assistant's container result is provisional until the user runs pytest on the host. **The full existing pytest suite has NOT been run yet.**

## Phase 1 - Gradio compatibility and tests: DONE (new tests green in container)
Files created
- `m2g/gradio_compat.py` - stdlib-only checker. `snapshot_from_module` (real signatures via `inspect`), `render_probe_script` /
  `parse_probe_output` (runs on the Colab runtime so the installed Gradio is what is checked), `check_code` / `check_tree`
  (unsupported keyword, too many positional args, missing class), `repair_code` (removes ONLY cosmetic keywords and
  `type='messages'`; behaviour-changing keywords are reported, never rewritten), `STATIC_REMOVALS` (certain removals, used when no
  snapshot exists).
- `tests/fake_gradio_api.py` - synthetic Gradio 4.44.1 / 5.49.0 / 6.30.0 / 6.31.0 whose classes really raise `TypeError` for bad
  keywords. Hand-written SUBSET of parameters, not the real libraries.
- `tests/test_gradio_compat.py` - 16 tests: ChatInterface, TabbedInterface, Blocks, Interface x 4 versions, checker-vs-runtime
  agreement, repair shapes, probe round trip, validate hook.
Files modified
- `m2g/validate.py` - `_gradio_compat_issues` now also reports the certain removals from `gradio_compat` (ChatInterface/Chatbot `type`).
- `m2g/gradio_gen.py` - generator prompt forbids `type=` on ChatInterface/Chatbot and theme/css/js/head on Blocks/Interface/ChatInterface.

Result: `tests/test_gradio_compat.py` 16 passed and `tests/test_gradio6_compat.py` 8 passed on Windows/pytest (user-verified):
24 passed, 0 failed.

## Phase 2 - Runtime preflight validation: IMPLEMENTED; tests written; Windows run of the full suite PENDING (user)
Files created
- `m2g/preflight.py` - orchestrates the existing Phase 1 pieces only (no new rules). `render_probe_runner(app_code, marker)` builds ASCII
  code that starts a FRESH interpreter on the runtime (so the Gradio pip just installed is the one read), runs
  `gradio_compat.render_probe_script` for the classes the app uses and prints `<marker>{rc,out,err}`. `run_preflight(code, probe,
  revalidate)` -> parse snapshot -> `check_code` -> bounded `repair_code` loop (max 3 passes) -> re-check -> verdict
  (`passed` / `repaired` / `blocked` / `probe_failed`). Fail-closed: unreadable runtime API or any unrepairable finding => `ok=False`.
  Repaired code is re-run through the project's `validate_app_code` (callback) before it may be deployed.
- `tests/test_preflight.py` - 15 tests. The real probe runner + real probe source run against the synthetic Gradio of a chosen version.
Files modified
- `m2g/colab_workflow.py` - new step `ColabLauncher._preflight` between `_install` and `_upload` (the app is checked against the Gradio
  that is installed on THAT runtime). `parse_markers` reads a new `PROBE` marker (this run's random prefix only). `LaunchSnapshot.preflight`
  (status, gradio_version, classes_checked, repaired, remaining, message) is copied by `snapshot()`. Preflight lines go to the existing log.
  Safe repairs replace `files["app.py"]` before the checksum-verified transfer. Block => `_Abort("preflight_failed")`, full message in
  `diagnostics`, runtime released by the existing failure cleanup; nothing is transferred or started.
- `m2g/colab_runtime.py` - `ColabManager.run_predict` (Jupyter path) now installs, runs `_preflight`, then writes the (possibly repaired)
  app and loads it. Skipped only when the app calls no gradio class. `ColabManager.preflight` + one status line in `status_markdown`.
- `tests/test_colab_workflow.py` - `FakeCli` answers the preflight probe (`gradio_version=`, `probe_result=` options; default is a
  permissive 5.49.0 snapshot), plus class `TestRuntimePreflight` (8 tests: step order install->preflight->write->start, probe failure
  and empty probe stop before upload and release the runtime, safe repair + revalidation, unresolved finding blocks and leaves app.py
  untouched, marker prefix check, ASCII payload).
Test results
- Container (Linux, Python only, `m2g.gradio_compat` + `m2g.preflight` + fake_gradio_api): `tests/test_preflight.py` 15 passed, 0 failed.
- NOT RUN here: `tests/test_colab_workflow.py` (incl. 8 new tests), `test_colab_live_logs.py`, `test_colab_panel_flow.py`,
  `test_colab_runtime.py` - they need the rest of the project, which was not copied into the container. Run on Windows:
  `python -m pytest tests/test_preflight.py tests/test_colab_workflow.py tests/test_colab_live_logs.py tests/test_colab_panel_flow.py tests/test_colab_runtime.py tests/test_gradio_compat.py tests/test_gradio6_compat.py`
  Possible fallout to check: any existing test that asserts the exact `FakeCli.exec_log` sequence (now contains `"preflight"`), and
  `test_colab_runtime.py::test_deploys_once_then_predicts` (its sample app uses no gradio call, so preflight is skipped by design).
Actual Colab preflight: NOT verified on a real runtime (no live Colab run was made).
Notes
- UI: preflight lines appear in the existing Logs panel; the panel itself (`colab_panel.py`) was not changed. The verdict is available as
  `LaunchSnapshot.preflight` for a later UI line.
- The state stays `installing dependencies` during preflight (no new stage constant, to avoid touching the panel's stage handling).

## Phase 3 - Error handling and bounded recovery: IMPLEMENTED; Windows run of the new + affected tests PENDING (user)
State found at the start of this session (2026-11-02): `m2g/error_classify.py` already existed on disk (created 02:27, 15.6 KB) and
`m2g/colab_workflow.py` / `m2g/colab_runtime.py` already imported and used it, although the hand-over said nothing was implemented. It
was verified, NOT rewritten. `tests/test_error_classify.py` was not touched.
Design (one mechanism, one place)
- `m2g/error_classify.py` (stdlib only): `classify_text(text, stage)` / `classify_failure(kind, message, detail, stage)` -> `ErrorInfo`
  (category, code, transient, max_retries, title, recovery, technical). Ordered rules: specific PERMANENT causes (OOM, gated/401/403/404,
  missing module, pip unsatisfiable, share tunnel, unexpected keyword) come before the generic network rules. Unknown => permanent.
  `may_retry(info, attempts_made, limit)` is true only for a transient error under BOTH the rule bound and the caller bound and
  `MAX_RETRIES_CEILING` (3). `retry_delay` grows and is capped. Nothing in the module loops.
- `ColabLauncher` (`colab_workflow.py`): `_install` replays pip ONLY if the pip output classifies as transient (`install_attempts`, default 3
  total tries); `_start_and_wait` restarts the app process ONLY for a transient start-up failure (`startup_attempts`, default 3 total,
  never past `launch_timeout`); runtime creation / transfer / kernel retries from earlier phases are unchanged. `_finish_failure` classifies
  every failure and fills `LaunchSnapshot.error_category/error_code/retryable/recovery/recovery_log`.
- `ColabManager.run_predict` (`colab_runtime.py`): `_execute_with_recovery` for install and model load (same rules); inference is never retried;
  `_runtime_error` returns the ORIGINAL error first, then `[category/code] ... What to do: ...`, and keeps `last_error`.
Changed in THIS session (gaps found by reading the code against the requirements)
- `m2g/colab_workflow.py`: `LaunchSnapshot.failed_stage` was declared but never set -> `_finish_failure` now records the stage that was active
  (also for a cancel); the classification diagnostics block now carries stage and retries made; an unexpected internal exception now keeps its
  full redacted traceback in `diagnostics` (it was only in the console); `refresh()` (app died after RUNNING) now classifies the failure and
  records `failed_stage=running`, and keeps the end of run.log in the diagnostics.
- `m2g/colab_panel.py`: the failed card now shows cause (`category/code`), failing stage, number of automatic retries and the "What to do" step
  (the old 200-character cut of the message used to drop the step); the View-logs text (`status_markdown`) shows cause, transient/permanent, and a
  collapsed block with every automatic retry and the ORIGINAL diagnostics (tracebacks, pip/CLI output). Runtime-supplied text only goes into the
  code block or is HTML-escaped; ``` inside diagnostics cannot close the block.
Files created: `tests/test_error_recovery_integration.py` (23 tests: pip/startup transient retried and bounded, permanent and unrecognised never
retried, `install_attempts=1` / `startup_attempts=1` disable retries, stage + traceback + retries preserved, secrets redacted, panel text, and the
Jupyter path: install / load / inference).
Test results (be precise)
- RAN, container (Linux, `python -m unittest`): `tests/test_error_classify.py` 21 passed, 0 failed, against a verbatim copy of the module on disk.
- NOT RUN: `tests/test_error_recovery_integration.py` (23 tests) and the edited `colab_workflow.py` / `colab_panel.py` - the container cannot see
  the project (the file-copy bridge failed) and cannot execute anything on Windows. Only a `py_compile` syntax check of the new test file was done.
  The tests were traced by hand against the real code paths, but they are UNVERIFIED until you run them. Report any failure and it will be fixed.
- REA: invoked (`open_binary` on `m2g/error_classify.py`); it refused with `unsupported binary format` (REA opens executables/archives, not
  Python source). The code was therefore verified by reading the source directly, not by REA analysis.
- Not verified on a real Colab runtime. Classification rules are matched against error TEXT, so real pip/transformers wording that differs from
  the sample strings in the tests would fall back to "unrecognised" (= permanent, never retried): safe, but less specific.
Fix after the first Windows run (2026-11-02): `tests/test_error_recovery_integration.py::TestStartupRecovery::test_permanent_startup_errors_are_never_restarted`
failed because a Gradio share-tunnel failure came back as `model_loading/download_network_error` instead of `network/share_tunnel`.
Cause (verified in source and reproduced): the rule ORDER was already right (`classify_text` returned `network/share_tunnel`, transient=False);
the bug was the start-up relabel at the end of `classify_failure`, which turned EVERY network-category result of an `app_failed`/`app_exited`
launch into `download_network_error`. Fix (`m2g/error_classify.py`, one condition): relabel only when the network error is TRANSIENT
(`... and info.transient`). Retry policy unchanged: share tunnel stays permanent (0 retries, app not restarted); a transient download blip
still becomes `model_loading/download_network_error` with its 2 retries. The test was not modified.
Checked in the container (not on Windows): reproduction before/after, other classifications unchanged (OOM, gated, missing module, unknown),
`tests/test_error_classify.py` 21 passed. REA was tried again on `error_classify.py` and again refused (unsupported binary format).
The failing integration test itself and the focused command have NOT been re-run on Windows by the assistant.
Verify on Windows (PowerShell, from the project folder)
`cd "D:\Model To Gradio"; .\.venv\Scripts\python.exe -m pytest tests/test_error_classify.py tests/test_error_recovery_integration.py tests/test_colab_workflow.py tests/test_colab_runtime.py tests/test_colab_panel_flow.py tests/test_colab_live_logs.py tests/test_preflight.py tests/test_gradio_compat.py tests/test_gradio6_compat.py tests/test_ui_errors.py -q`
then the full suite: `.\.venv\Scripts\python.exe -m pytest -q`
Exact remaining work: (1) run the commands above and fix any failure (most likely spots: assertions on exact wording in
`test_error_recovery_integration.py`, and any existing test that asserts the exact failed-card HTML or the exact `diagnostics` text);
(2) if all green, mark Phase 3 DONE here; (3) Phase 4 (cleanup and logging) and Phase 5 (full regression) are untouched.
## Phase 4 - Logging, diagnostics and resource cleanup: IMPLEMENTED (10 of 10 items); L1-R3 focused tests passed on Windows (user-reported); R2 tests WRITTEN, NOT RUN
Status reported by the user at the start of this session (2026-11-02): Phases 1-3 complete, full Windows regression suite passed (with skipped tests).
The assistant did NOT verify that result and cannot tell whether that run happened before or after the first Phase 4 edit of `m2g/colab_workflow.py`
(saved 02:50:47). The Phase 1-3 sections above were left as written; they predate that report.
The project has no written Phase 4 spec, so progress is measured against this checklist, derived from the code and from the gaps found by reading it:
| # | Item | Code | Test | Executed |
| L1 | timestamped, stage-tagged, redacted launcher log lines (`HH:MM:SS [stage] text`, `_in_stage`) | yes | yes | no |
| L2 | redaction of URL credentials and `password=`/`api_key=`/`token=` pairs (`code=` deliberately excluded) | yes | yes | no |
| L3 | complete redacted tracebacks kept in `diagnostics` (internal error, failure while stopping a runtime) | yes | yes | no |
| L4 | link password file overwritten with zeros and deleted on the runtime once the app is up | yes | yes | no |
| L5 | guaranteed runtime release; if it cannot be stopped: password removed + exact `colab stop` command shown | yes | yes | no |
| L6 | busy flag and CLI interrupt latch always reset (`_run_safely`) | yes | yes | no |
| L7 | log buffer capped at 600 lines | yes | yes | no |
| R1 | redacted console log lines in `ColabManager` (retry warning, preflight lines) | yes (this session) | yes | no |
| R3 | redacted `last_error` / returned runtime error text in `ColabManager` | yes (this session) | yes | no |
| R2 | explicit close of the Jupyter kernel on disconnect / failure (see "R2 update" below) | yes (2026-11-02, 2nd session) | yes | no |
By this checklist (updated after R2): implementation 10/10, tests written 10/10. Executed: L1-R3 by the user on Windows (focused run: passed, with skips, no visible failure; the
assistant did not see the output). R2 (`tests/test_kernel_cleanup.py`, 20 tests, and the edited `tests/test_colab_runtime.py`) has NOT been executed by anyone yet.
Files
- Modified before this session (found on disk, kept as is): `m2g/colab_workflow.py` (L1-L7).
- Modified this session: `m2g/colab_runtime.py` (additive only: `from .ui_errors import scrub` + `scrub()` around the retry warning, the preflight
  log lines, `last_error["original"]`, `last_error["stdout_tail"]` and the returned error text; control flow untouched).
- Created this session: `tests/test_logging_cleanup.py` (26 tests: password-file removal incl. the real script run locally; cleanup that never
  leaves the launcher busy or the interrupt latch set; stage labels; log cap; redaction in the launcher and the Jupyter path).
- NOT touched: every existing test file, `colab_panel.py`, `error_classify.py`, `colab_cli.py`, `security.py`, `ui_errors.py`, Gradio pins (Gradio is never downgraded).
Test results (be precise)
- NOT RUN. The assistant has file tools on `D:\Model To Gradio` only: no shell there, and copying files into its Linux container failed
  ("Failed to upload file"). The new tests were traced by hand against the real code paths; `py_compile` was not run either.
- REA: `open_binary` on `m2g/colab_workflow.py` was refused again ("unsupported binary format"; REA opens executables/archives, not Python source).
  REA analysis did NOT happen; the code was verified by reading the source.
- Possible fallout to check (hand analysis, unconfirmed): `FakeCli.exec_code` in `tests/test_colab_workflow.py` raises AssertionError for the new
  password-removal script; `_remove_remote_secrets` catches it and logs, so `test_link_password_is_transferred_but_never_logged` should still pass.
Verify on Windows (PowerShell, from the project folder)
`cd "D:\Model To Gradio"; .\.venv\Scripts\python.exe -m pytest tests/test_logging_cleanup.py tests/test_colab_workflow.py tests/test_colab_live_logs.py tests/test_error_recovery_integration.py tests/test_colab_runtime.py tests/test_colab_panel_flow.py tests/test_preflight.py -q`
then the full suite: `.\.venv\Scripts\python.exe -m pytest -q`
Exact remaining work: (1) run the commands above and fix any failure (most likely spots: assertions on `tcr`/`twf` fake-runtime helpers in the new
runtime tests, and the stage-label expectations); (2) R2: add an explicit kernel close to `JupyterClient`/`ColabManager.disconnect` together with a
fake-transport test (not done because the existing fake transport would see an unexpected DELETE request and needs a matching update); (3) if all
green, mark Phase 4 DONE here; Phase 5 (full regression) is the run in (1).
### R2 update (2026-11-02, second session): Jupyter kernel closure
Inspected first (file tools; REA `open_binary` on `m2g/colab_runtime.py` was refused: "unsupported binary format" - REA inspection did NOT succeed, the code was read directly).
Found: `JupyterClient._ensure_kernel` starts a kernel (`POST /api/kernels`) that was never deleted; `ColabManager.disconnect/_fail` only deleted the runtime.
Changed (additive; Phase 4 L1-R3 code untouched)
- `m2g/colab_runtime.py`
  - `JupyterClient.close() -> bool`: `DELETE /api/kernels/<id>` for the ONE kernel this client started, with the same proxy-token header as every other call.
    Idempotent (no kernel / already closed -> True, no request); 200/202/204/404 -> True and the id is forgotten; any other status or any exception -> False,
    the id is kept so it can be retried; never raises; the kernel id must match `[A-Za-z0-9_-]{1,64}` or nothing is sent; log lines carry only the HTTP status or
    exception type (no token, no runtime URL).
  - `ColabManager._close_kernel(jc)` + attribute `kernel_cleanup` (`'closed'` | `'failed'` | `None`). Called by `disconnect()` and by `_fail()` (which `_guard` uses for
    expected AND unexpected exceptions, including those during `connect`), always BEFORE the runtime is deleted. A failed close never blocks the release.
    `_fail` skips both for `kind == 'disconnected'` (the runtime is already gone), exactly as it already skipped the runtime delete.
- `tests/test_colab_runtime.py`: ONE added route in `make_transport` (`DELETE /api/kernels/k1 -> 204`). No assertion was changed.
  (Without it the old fake raised AssertionError for the new request; the manager would have swallowed it and the old tests would still pass, but the cleanup would never be exercised.)
- `tests/test_kernel_cleanup.py` (new, 20 tests): close before any kernel / deletes only its kernel / twice -> one request / 404 / refused then retried / transport error /
  token and URL never logged / unsafe id; manager: successful disconnect (kernel then runtime), refused close, raising close, failed close + failed release (manual instructions,
  no token), repeated disconnect sends nothing, only those two DELETE URLs are ever used, failed connect, expected runtime error, unexpected exception, already-disconnected
  runtime is not contacted, reconnect after cleanup starts a fresh kernel.
Cancellation: the Jupyter path has no cancel (cancel/interrupt belongs to the CLI launcher, covered by Phase 4 L5/L6); a cancelled or failed CLI launch releases its runtime, which ends its kernel.
Not user data: nothing here deletes files, notebooks or other kernels; the runtime release is unchanged.
Test results: NOT RUN by the assistant (no shell on the Windows host; REA refused). The new tests were traced by hand against the code.
Limitations: no live Colab run - that a real runtime accepts `DELETE /api/kernels/<id>` is the standard Jupyter API but is unverified here; a kernel started by a different
client is deliberately never touched.
Verify on Windows (PowerShell)
`cd "D:\Model To Gradio"; .\.venv\Scripts\python.exe -m pytest tests/test_kernel_cleanup.py tests/test_colab_runtime.py tests/test_logging_cleanup.py tests/test_error_recovery_integration.py tests/test_colab_workflow.py tests/test_colab_panel_flow.py -q`
## Phase 5 - Full regression testing: DONE

The expanded shared compatibility audit was completed on the Windows host. The
runtime probe now reports the version of the module actually imported by the
target interpreter before falling back to `importlib.metadata`. This prevents a
shadowed or overlaid installation from being reported as the active runtime
version, which previously produced inaccurate compatibility diagnostics.

Regression coverage added:
- A same-app/different-runtime test verifies that the installed module version
  appears in diagnostics and that an unrelated host distribution version does
  not leak into the result.
- Existing universal tests continue to cover removed pipeline tasks, model
  classes, loader keywords, imports, local checks, Colab preflight, and
  compatibility-error classification.

Validation completed:
- Focused runtime compatibility, universal runtime, preflight, and error
  classification tests passed.
- Full `pytest -q` suite passed on Windows.
- No dependency pins, downgrades, model-specific exceptions, or weakened
  validation were introduced.

The REA integration remains intentionally scoped to safe model-artifact
inspection. It does not provide authoritative installed-library API/version
evidence, so deployment-runtime probing remains the source of truth for this
compatibility decision.

## Live Colab deployment: NOT TESTED

## Phase 6 - Multi-capability generation and explicit deployment targets: IMPLEMENTED

Audit findings:
- Evidence-based multi-capability detection, capability provenance, and per-capability
  tab validation were already present in `m2g/capabilities.py`, `m2g/analyzer.py`,
  `m2g/gradio_gen.py`, and `m2g/validate.py`; no duplicate detector was added.
- The main web UI exposed only the Colab launch card. The existing local support was
  limited to `m2g.local_check` preflight and did not create an isolated environment
  or launch the generated application.
- Generated setup guidance still permitted a removed Transformers pipeline task for
  seq2seq and question-answering families.

Changes:
- Added `m2g/local_workflow.py` and `m2g/local_panel.py`. The Local path reports
  hardware, creates `.m2g-venv` under the generated output directory, installs the
  generated requirements, runs the same runtime preflight, and starts/stops only
  the local process.
- Added an explicit `Run on My System (Local)` / `Run on Google Colab` selector to
  `m2g.web`. Each launch handler verifies the selected target and refuses to launch
  when the other target is selected; no automatic fallback is possible.
- Added shared task-family loading guidance in `m2g.gradio_gen`: seq2seq,
  summarization, translation, and question-answering generation must prefer the
  documented AutoModel plus tokenizer/processor and direct inference instead of
  relying on a potentially removed pipeline registry entry. Other tasks may use a
  pipeline only when runtime evidence establishes it.
- Added local workflow and loading-strategy regression tests.

Focused validation passed:
`tests/test_capability_detection.py`, `tests/test_multi_capability_evidence.py`,
`tests/test_gradio_gen.py`, `tests/test_web_ui_state.py`,
`tests/test_local_workflow.py`, `tests/test_preflight.py`, and
`tests/test_runtime_compat.py`.

The complete pytest suite is the final acceptance gate and is run after this
implementation update. A live Colab deployment remains environment-dependent and
is not claimed as tested.

## Phase 7 - Task-specific inference validation and final verification: COMPLETE

- Strengthened `m2g.validate.validate_capability_interfaces` so supported
  capabilities must have structurally distinct inference bodies, not merely
  different function names and tabs. Copy-pasted multi-capability inference is
  rejected before deployment.
- Updated the synthetic OpenCode regression fixture to emit task-conditioned
  inference, preserving realistic multi-capability test coverage.
- Added a regression test proving identical inference bodies are blocked.

Validation results on the configured Windows environment:
- Focused compatibility, capability, generator, UI, local workflow, and
  preflight tests: **104 passed**.
- Complete pytest suite: **821 passed**.
- Changed Python modules and tests compile successfully with `py_compile`.

Live model startup and authenticated Colab deployment were not executed; those
remain environment-dependent acceptance checks and are not represented as
passing runtime evidence.

## Phase 8 - Colab deployment lifecycle repair: COMPLETE

Root cause:
- The Stop button was implemented as a two-callback Gradio chain. The first
  callback set `ColabPanel._stopping` and disabled the card; the second callback
  synchronously ran the potentially long `colab stop` plus session verification.
  If that chained callback was delayed, cancelled, or failed before a subsequent
  render, `_stopping` remained true. That made the card display `Stopping…`,
  disabled Check Status and Launch, and occupied the Gradio event queue.
- Authentication itself was not the cause: `card_view()` correctly treated
  authenticated-without-runtime as launchable. The blocked controls were a
  stale busy UI state.

Changes:
- Added `ColabPanel.request_stop()`, which owns a daemon stop worker and
  always releases `_stopping` in a `finally` path, including launcher
  exceptions.
- Rewired the Stop button to one non-blocking callback. The existing timer
  renders completion, stop failures, cancellation, and the restored launch
  state without chaining a long operation through the Gradio event queue.
- Added stop-generation tracking so an older stop worker cannot clear a newer
  stop request's state.
- Preserved the synchronous `stop_runtime()` API for scripts and tests, with
  explicit cleanup in all paths. Runtime ownership, URL verification, secret
  redaction, and failure diagnostics remain unchanged.

Focused validation:
- Colab panel/workflow, web UI state, and local workflow tests:
  **122 passed**.
- Added regression coverage for non-blocking stop completion and launcher
  exceptions, plus callback output arity after the wiring change.

Full validation:
- Complete pytest suite: **802 passed**.
- Updated modules and regression tests compile successfully.

No live Google authentication or Colab runtime was available in this test
environment. Real credentialed deployment remains the final environment-
dependent verification step; mocked tests do not claim that acceptance.

## Phase 9 - Dynamic multi-capability dashboard generation: COMPLETE

Changes:
- Extended the generic generator contract with a capability-derived
  `gr.Blocks` dashboard layout: a horizontal navigation row, one section per
  evidence-supported capability, and visibility switching so only the selected
  section is shown.
- Required separate task-specific inputs, outputs, controls, and inference
  handlers while retaining one shared lazy model/tokenizer loader.
- Added evidence-backed task instructions and loading feedback requirements to
  the generated-app prompt; uncertain capabilities remain excluded rather than
  being presented as supported features.
- Updated static validation to accept either the existing tabbed layout or the
  new `Blocks` dashboard layout for multi-capability applications.
- Updated the synthetic OpenCode fixture and generator regression assertions to
  cover navigation, hidden sections, separate handlers, shared loading, and
  dashboard structure.

Validation:
- Focused generator tests: **39 passed**.
- Complete pytest suite: **821 passed**.

The generated dashboard was validated structurally. Actual FLAN-T5 model
inference and credentialed Colab deployment were not available in this
environment and remain required for live acceptance.

## Phase 10 - Weight eligibility and dashboard validation hardening: COMPLETE

Root causes:
- Weight inspection only recognized literal `.safetensors` files. It did not
  distinguish safetensors indexes/shards from pickle indexes, so safe-format
  evidence could be missed or treated as complete without a verifiable shard
  set.
- Multi-capability validation previously accepted any `gr.Blocks` occurrence.
  A generated app could therefore contain a dashboard-looking title while
  omitting real navigation controls, visibility updates, or rendered handlers.

Changes:
- Added bounded weight inventory evidence for direct safetensors, indexed
  safetensors, shards, pickle files, and pickle indexes.
- A safetensors set is eligible only when it belongs to the inspected model
  revision and is complete according to the repository listing. Pickle
  loading remains disabled by default; no automatic pickle fallback was added.
- Separated architecture/task `reasons` from
  `generation_blocked_reasons`, so pickle-only models remain analyzable and
  are not mislabeled as unsupported while app generation is still refused.
- Propagated weight-security diagnostics through linked GitHub-model analysis,
  pipeline summaries, and generated-app integrity checks.
- Multi-capability validation now requires `gr.Blocks`, one navigation control
  and separately visible section per supported capability, connected
  visibility events, active-state styling, and a real rendered inference
  handler for every capability.
- Added regressions for indexed safetensors, pickle-only eligibility
  separation, and dashboards with missing navigation wiring.

Validation:
- Focused regression tests: **118 passed**.
- Complete pytest suite: **passed, 0 failed** (all collected tests).
- No live generated app or authenticated Colab deployment was launched in
  this environment.

## Phase 11 - Local startup lifecycle and Home page contract: COMPLETE

Root causes:
- Local launch previously marked the process `RUNNING` immediately after
  `Popen`, consumed output synchronously, and exposed no verified URL. A
  completed preparation command therefore looked successful even when the
  app had not started.
- The generated dashboard contract required navigation labels but did not
  require a dedicated Home section with navigation wiring, so generated
  output could omit the analysis page.

Changes:
- Local startup now reads managed process output in a background reader,
  detects a local Gradio URL, and only enters `RUNNING` after URL readiness.
- Startup crashes and timeout are reported as failures with captured logs.
- Stop requests are cancellation-aware, terminate and wait for the managed
  process, clear stale process state, and cannot be overwritten by a stale
  startup worker.
- Local status rendering exposes the verified URL as an escaped link.
- The generated dashboard fixture now includes a dedicated Home analysis and
  REA recommendations section with visibility navigation.

Validation:
- Focused local and generator tests: **44 passed**.
- Complete pytest suite: **passed, 0 failures** (all collected tests).
- Actual generated model inference, local dependency installation, and
  authenticated Colab deployment still require live environment verification.


## Phase 11 — Premium Web Frontend: COMPLETE

### Overview
Built a complete, premium AI developer platform frontend and REST API backend, replacing the Gradio-based web UI with a modern HTML/CSS/JavaScript application.

### Files Created
| File | Purpose |
|---|---|
| `frontend/index.html` | Main HTML entry point — full single-page app |
| `frontend/style.css` | Complete styling: dark navy theme, animations, responsive |
| `frontend/script.js` | Page rendering, navigation, all component logic |
| `frontend/api.js` | Backend API communication (fetch-based, abort controllers) |
| `frontend/state.js` | Shared reactive state store with subscriber pattern |
| `frontend/assets/icons/.gitkeep` | Assets folder placeholder |
| `m2g/web_api.py` | Lightweight HTTP REST API server (stdlib HTTPServer) |
| `run_frontend.bat` | Windows launcher script |

### Backend REST API (`m2g/web_api.py`)
The existing `m2g/web.py` is a Gradio app with no HTTP API. `web_api.py` adds a lightweight stdlib HTTPServer that:
- Serves the `frontend/` static files
- Delegates all analysis/generation/deployment to existing `m2g` code (no logic reimplemented)
- Runs on `127.0.0.1:8765` by default

**Endpoints:**
- `POST /api/analyze` → async job via `run_pipeline()`
- `GET  /api/analyze/<job_id>` → poll job status
- `GET  /api/apps` → scan `output/` directory
- `GET  /api/apps/<slug>` → app detail + analysis.json
- `GET  /api/apps/<slug>/files/<name>` → file content (secrets redacted)
- `POST /api/apps/<slug>/validate` → re-run `validate_app_code()`
- `POST /api/apps/<slug>/generate` → re-run pipeline (async job)
- `GET  /api/generate/<job_id>` → poll generation status
- `POST /api/local/<slug>/start` → generated `run_local.bat` via `LocalLauncher.launch_batch()` on Windows (legacy apps fall back to `launch()`)
- `GET  /api/local/<slug>/status` → `LocalSnapshot`
- `POST /api/local/<slug>/stop` → `LocalLauncher.stop()`
- `GET  /api/colab/status` → CLI availability check
- `POST /api/colab/check` → `ColabCli.check()`
- `GET  /api/colab/gpus` → available GPU options
- `POST /api/colab/<slug>/start` → `ColabLauncher.start_async()`
- `GET  /api/colab/<slug>/status` → `LaunchSnapshot`
- `POST /api/colab/<slug>/stop` → `ColabLauncher.stop()`
- `GET  /api/colab/<slug>/logs` → `ColabLauncher.fetch_logs()`
- `GET  /api/activity` → activity log

### Frontend Pages Implemented
| Page | Content |
|---|---|
| Dashboard | Welcome, stats, recent apps, recent activity |
| Model Analyzer | URL input, async progress, full analysis display |
| Model Insights | Strengths, limitations, hardware, fine-tuning, deps |
| App Generator | Capabilities display, generation progress, file listing |
| Generated Apps | Searchable list + detail with file viewer tabs |
| Deployment | Local (venv) + Colab (staged progress, URL display) |
| Activity & Logs | Filterable event log |

### Design System
- Colors: `#0B1020` background, `#6366F1` primary, `#8B5CF6` secondary, `#A78BFA` highlight
- Font: Inter (Google Fonts)
- Animations: page transitions, progress bars, status pulse, AI glow, toast notifications
- Fully responsive: desktop/tablet/mobile with collapsible sidebar

### Security Preserved
- `web_api.py` binds `127.0.0.1` only
- Slug/filename regex validation prevents path traversal
- `redact_secrets()` applied to all file content returned to frontend
- `trust_remote_code=False` default preserved in all pipeline calls
- No credentials returned in API responses
- `_within()` path containment check on all file access

### Integration Tests Passed
```
python -c "import m2g.web_api; print('imports OK')"  ✓
python -c "_list_apps(), _snap_local(), _snap_colab()"  ✓
HTTP server: /, /api/apps, /api/activity, /api/colab/status  ✓ (HTTP 200)
Frontend files: index.html, style.css, script.js, api.js, state.js  ✓
```

### How to Launch
```bat
run_frontend.bat
```
Or directly:
```
.venv\Scripts\python.exe -m m2g.web_api
```
Then open: `http://127.0.0.1:8765`

To change host/port:
```
set M2G_API_HOST=0.0.0.0
set M2G_API_PORT=8765
```

### Compatibility and Local Deployment Updates
- All REST output/file/deployment routes resolve `Settings.output_dir`, including `M2G_OUTPUT_DIR`.
- Generated requirements add tested API-family ceilings (`transformers<5`, `gradio<7`, `torch<3`, and related runtime packages) rather than selecting unbounded future major releases.
- Each generated app includes an app-specific `run_local.bat`; Windows API deployment executes that launcher asynchronously and verifies the announced Gradio URL before reporting `running`.
- The HTML Generated Apps detail view exposes BAT download and local-instructions actions, and the Local deployment log clear action is wired.

### Known Unverified Items
1. **Live model analysis**: requires HuggingFace network access and valid URL
2. **App generation**: requires OpenCode CLI configured and working
3. **Local deployment**: the API-backed BAT workflow was exercised, but the sample app's first dependency installation was still running after six minutes and was stopped; no completed generated-app URL was demonstrated in this run
4. **Colab deployment**: requires Google authentication and `colab` CLI installed
5. **Existing test suite**: complete pytest suite passes; focused output-directory and BAT-launch regression tests were added
6. **REA stage in web_api**: controlled by `M2G_REA_ENABLED` env var, same as `web.py`

### Existing Gradio UI
`m2g/web.py` and the existing `run_platform.bat` are **unchanged**. Both UIs can run simultaneously on different ports.

## Phase 4.1 - Backend Pipeline Orchestration (implemented, tests NOT yet run)
- `pipeline.required_artifacts(folder)`: one definition of a complete package (app.py, loader.py, requirements.txt, analysis.json, colab_notebook.ipynb + `run_<folder>.bat`); `web_api` analysis/generation jobs now check the launcher too.
- `web_api._new_or_active_job`: a repeated analyze/generate request for the same URL/slug joins the running job instead of starting a second pipeline run (response gains `reused`).
- Unexpected job exceptions are redacted before they are stored or logged.
- `local_runner.ensure_package_matches_analysis`: local start (and Colab start via the API) refuse a folder whose loader.py model/revision differs from analysis.json (stale files after a failed regeneration).
- REA vs OpenCode: REA stays a header-only inspection tool (2 allow-listed tools of 229, none generates code); OpenCode stays the generation engine. No provider change.
- Tests added: `tests/test_pipeline_orchestration.py`. Backend only; no UI files touched.
