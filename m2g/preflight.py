"""Runtime preflight: check a generated app against the Gradio that is ACTUALLY installed on the Colab runtime.

This module only orchestrates the existing Phase 1 pieces in ``m2g.gradio_compat`` (probe script, snapshot parser, checker,
safe repair); it adds no new compatibility rules. Nothing here talks to Colab: the callers (``colab_workflow`` for the CLI
launch, ``colab_runtime`` for the Jupyter path) run ``render_probe_runner()`` on the runtime and hand the decoded reply to
``run_preflight()``.

Flow: probe reply -> ApiSnapshot of the runtime's Gradio -> check_code -> (safe repair -> re-check, bounded) -> verdict.
The verdict is fail-closed: if the runtime's API could not be read, or any finding remains that is not safely repairable,
``ok`` is False and the caller must not start the app.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .gradio_compat import (ApiSnapshot, Finding, check_code, format_findings, gradio_names_used, parse_probe_output,
                            render_probe_script, repair_code)
from .runtime_compat import (check_library_compat, describe_versions, format_library_findings, library_probe_arguments,
                             library_probe_calls, library_uses, parse_library_probe_output, render_library_probe_script)

MAX_REPAIR_PASSES = 3
PROBE_TIMEOUT_S = 240  # inner timeout of the probe subprocess on the runtime (importing gradio can take a while)

# Status values: passed (no findings), repaired (findings fixed and re-checked clean), blocked (unresolved findings or
# unusable code), probe_failed (the runtime's Gradio API could not be read).
PASSED, REPAIRED, BLOCKED, PROBE_FAILED = "passed", "repaired", "blocked", "probe_failed"

_RUNNER = '''import json, subprocess, sys
_src = __SRC__
try:
    _r = subprocess.run([sys.executable, "-c", _src], capture_output=True, text=True, timeout=__TIMEOUT__)
    _d = {"rc": _r.returncode, "out": (_r.stdout or "")[-300000:], "err": (_r.stderr or "")[-1500:]}
except Exception as _e:
    _d = {"rc": -1, "out": "", "err": type(_e).__name__ + ": " + str(_e)[:300]}
print(__MARKER__ + json.dumps(_d), flush=True)
'''


def render_probe_runner(app_code: str, marker: str) -> str:
    """Code to execute on the runtime. It starts a FRESH interpreter (so the Gradio that pip just installed is the one read,
    not a copy already imported by the kernel), runs the Phase 1 probe for the classes ``app_code`` uses, and prints one line
    ``<marker><json {rc, out, err}>``. Pure ASCII."""
    # Two probes in the SAME fresh interpreter: the libraries the app takes names/tasks from (transformers, diffusers, ...), then
    # Gradio. The library probe never raises (a failure is reported in its own line) and runs first, so it still reports when
    # Gradio itself does not import.
    src = (render_library_probe_script(*library_probe_arguments(app_code), calls=library_probe_calls(app_code)) + "\n"
           + render_probe_script(gradio_names_used(app_code)))
    return (_RUNNER.replace("__MARKER__", json.dumps(marker)).replace("__TIMEOUT__", str(PROBE_TIMEOUT_S))
            .replace("__SRC__", json.dumps(src)))


@dataclass
class PreflightResult:
    status: str
    ok: bool
    gradio_version: str = "unknown"
    code: str = ""  # the app code to deploy: the repaired code if repairs were applied, else the input
    classes_checked: int = 0
    repaired: List[str] = field(default_factory=list)
    remaining: List[str] = field(default_factory=list)
    message: str = ""  # user-facing summary; for a blocked result it names every unresolved problem
    log_lines: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {"status": self.status, "ok": self.ok, "gradio_version": self.gradio_version,
                "classes_checked": self.classes_checked, "repaired": list(self.repaired), "remaining": list(self.remaining),
                "message": self.message}


def _blocked(code: str, snap: Optional[ApiSnapshot], applied: List[Finding], remaining: List[str], message: str,
             lines: List[str]) -> PreflightResult:
    lines.append("Preflight BLOCKED: " + message.splitlines()[0])
    return PreflightResult(BLOCKED, False, snap.version if snap else "unknown", code, len(snap.classes) if snap else 0,
                           [f.render() for f in applied], remaining, message, lines)


def run_preflight(app_code: str, probe: Optional[Dict[str, Any]],
                  revalidate: Optional[Callable[[str], Any]] = None) -> PreflightResult:
    """Decide whether ``app_code`` may start on the runtime whose probe reply is ``probe`` ({rc, out, err} or None).

    ``revalidate`` is called with repaired code and must raise if the repaired app no longer passes the project's own
    validators; that blocks the launch instead of deploying unvalidated code."""
    lines: List[str] = []
    snap = parse_probe_output(str((probe or {}).get("out") or "")) if isinstance(probe, dict) else None
    if snap is None:
        err = str((probe or {}).get("err") or "").strip() if isinstance(probe, dict) else ""
        rc = (probe or {}).get("rc") if isinstance(probe, dict) else None
        tail = err.splitlines()[-1] if err else "no output"
        msg = ("Preflight could not read the Gradio API installed on the runtime (probe exit code "
               f"{rc}). Gradio is probably not installed or does not import there. Last error: {tail}")
        lines.append("Preflight FAILED: " + msg)
        return PreflightResult(PROBE_FAILED, False, "unknown", app_code, 0, [], [msg], msg, lines)

    lines.append(f"Preflight: runtime Gradio {snap.version}; read {len(snap.classes)} component signature(s)"
                 + (f"; not present in this Gradio: {', '.join(snap.missing)}" if snap.missing else "") + ".")
    lib_snap = parse_library_probe_output(str((probe or {}).get("out") or ""))
    if lib_snap is None:  # fail closed: an app that depends on these libraries is never started on unverified versions
        try:
            wanted_symbols, wanted_tasks = library_uses(app_code)
        except SyntaxError:
            wanted_symbols, wanted_tasks = {}, []
        if wanted_symbols or wanted_tasks:
            used = ", ".join(sorted(wanted_symbols) or ["transformers"])
            message = (f"The runtime returned no library information although the app uses {used}, so compatibility could not "
                       "be verified; the app was not started. Check that the libraries import on the runtime and retry.")
            return _blocked(app_code, snap, [], [message], message, lines)
    if lib_snap is not None:
        lines.append(f"Preflight: runtime libraries: {describe_versions(lib_snap)}.")
        try:
            used_symbols, used_tasks = library_uses(app_code)
        except SyntaxError:
            used_symbols, used_tasks = {}, []
        used_libraries = set(used_symbols)
        if used_tasks:
            used_libraries.add("transformers")
        probe_errors = [error for error in lib_snap.errors
                        if error.startswith("pipeline tasks:") and "transformers" in used_libraries
                        or error.split(":", 1)[0] in used_libraries]
        if probe_errors:
            message = ("The runtime could not inspect the libraries used by the app, so compatibility could not be verified; "
                       "the app was not started:\n  " + "\n  ".join(probe_errors))
            return _blocked(app_code, snap, [], [message], message, lines)
        try:
            lib_findings = check_library_compat(app_code, lib_snap)
        except SyntaxError:
            lib_findings = []  # reported as a syntax error by the Gradio check below
        if lib_findings:  # never repaired automatically: a different API means different code, and no library is downgraded
            return _blocked(app_code, snap, [], [f.render() for f in lib_findings],
                            "The app uses a library API that the versions installed on the runtime do not have, so it was not "
                            f"started:\n{format_library_findings(lib_findings)}", lines)
    current, applied = app_code, []
    for _ in range(MAX_REPAIR_PASSES):
        try:
            findings = check_code(current, snap)
        except SyntaxError as exc:
            return _blocked(app_code, snap, [], [f"app.py: syntax error line {exc.lineno}"],
                            f"The generated app is not valid Python (line {exc.lineno}).", lines)
        if not findings or not any(f.repairable for f in findings):
            break
        new_code, done, _rest = repair_code(current, findings)
        if not done:
            break
        for f in done:
            lines.append("Preflight: removed unsupported argument -> " + f.render())
        applied += done
        current = new_code
    try:
        findings = check_code(current, snap)
    except SyntaxError as exc:
        return _blocked(app_code, snap, applied, [f"app.py: syntax error line {exc.lineno}"],
                        f"Automatic repair left invalid Python (line {exc.lineno}); nothing was deployed.", lines)
    if findings:
        rendered = [f.render() for f in findings]
        return _blocked(app_code, snap, applied, rendered,
                        f"The app is not compatible with Gradio {snap.version} on the runtime and cannot be repaired "
                        f"safely, so it was not started:\n{format_findings(findings)}", lines)
    if applied:
        if revalidate is not None:
            try:
                revalidate(current)
            except Exception as exc:  # the project's validators reject the repaired app
                return _blocked(app_code, snap, applied, [f"revalidation: {exc}"],
                                f"The repaired app no longer passes validation ({exc}); nothing was deployed.", lines)
        lines.append(f"Preflight repaired: {len(applied)} incompatible argument(s) removed, re-checked clean against "
                     f"Gradio {snap.version}.")
        return PreflightResult(REPAIRED, True, snap.version, current, len(snap.classes), [f.render() for f in applied], [],
                               f"Repaired {len(applied)} incompatibility(ies) for Gradio {snap.version}.", lines)
    lines.append(f"Preflight passed: no incompatibilities with Gradio {snap.version}.")
    return PreflightResult(PASSED, True, snap.version, app_code, len(snap.classes), [], [],
                           f"Compatible with Gradio {snap.version}.", lines)
