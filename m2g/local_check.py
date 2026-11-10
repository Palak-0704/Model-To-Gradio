"""Compatibility check of a generated app against the LOCAL Python environment (the counterpart of the Colab runtime preflight).

    python -m m2g.local_check <app.py | output folder> [--python PATH] [--fix]

The app is checked with exactly the same probe and rules as on Colab (``m2g.preflight``): a fresh interpreter of the chosen Python
reads the installed Gradio signatures, library versions, imported names, pipeline tasks and library keyword arguments, and the
verdict is fail-closed. ``--python`` selects the interpreter that will RUN the app (default: the one running this command), because
the result is only meaningful for the environment that executes the app. Nothing is installed, downgraded or started; the app code
is never executed (only its imports are looked up). ``--fix`` rewrites app.py ONLY when preflight proved a safe repair (cosmetic
Gradio keywords) and keeps the original next to it as ``app.py.m2g-backup``.

Exit codes: 0 compatible (or repaired), 1 blocked / could not verify, 2 usage error.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .error_classify import classify_text
from .preflight import PROBE_TIMEOUT_S, REPAIRED, PreflightResult, render_probe_runner, run_preflight

_MARK = "M2G_LOCAL_PREFLIGHT:"


def find_app(path: str) -> Path:
    """``path`` is app.py itself or a folder that contains it (the generator's output folder)."""
    p = Path(path)
    app = p / "app.py" if p.is_dir() else p
    if not app.is_file():
        raise FileNotFoundError(f"No app.py found at {p}")
    return app


def probe_local(app_code: str, python: Optional[str] = None, timeout: int = PROBE_TIMEOUT_S + 60) -> Dict[str, Any]:
    """Run the preflight probe with ``python`` and return the same ``{rc, out, err}`` reply the Colab runtime gives."""
    exe = python or sys.executable
    try:
        r = subprocess.run([exe, "-c", render_probe_runner(app_code, _MARK)], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"rc": -1, "out": "", "err": f"{type(exc).__name__}: {str(exc)[:300]}"}
    for line in reversed((r.stdout or "").splitlines()):
        if line.startswith(_MARK):
            try:
                reply = json.loads(line[len(_MARK):])
            except ValueError:
                break
            if isinstance(reply, dict):
                return reply
    return {"rc": r.returncode, "out": "", "err": ((r.stderr or "").strip()[-1500:]) or "the probe printed no result"}


def check_local_app(path: str, python: Optional[str] = None, fix: bool = False,
                    probe: Optional[Dict[str, Any]] = None,
                    revalidate: Optional[Callable[[str], Any]] = None) -> PreflightResult:
    """Preflight verdict for the app at ``path`` in the local environment. ``probe`` lets callers/tests supply the probe reply."""
    app = find_app(path)
    code = app.read_text(encoding="utf-8")
    if revalidate is None:
        def revalidate(c: str) -> None:  # the project's own validators, as on Colab
            from .config import Settings
            from .validate import validate_app_code
            validate_app_code(c, Settings.from_env())
    result = run_preflight(code, probe if probe is not None else probe_local(code, python), revalidate=revalidate)
    if fix and result.status == REPAIRED and result.code != code:
        backup = app.with_name(app.name + ".m2g-backup")
        if not backup.exists():
            backup.write_text(code, encoding="utf-8")
        app.write_text(result.code, encoding="utf-8")
        result.log_lines.append(f"Wrote the repaired app to {app} (original kept as {backup.name}).")
    return result


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="m2g.local_check", description="Check a generated app against the local Python environment.")
    p.add_argument("path", help="app.py, or the output folder that contains it")
    p.add_argument("--python", help="interpreter that will run the app (default: the one running this command)")
    p.add_argument("--fix", action="store_true", help="write the safely repaired app.py (a backup is kept)")
    args = p.parse_args(argv)
    try:
        result = check_local_app(args.path, args.python, args.fix)
    except (FileNotFoundError, UnicodeDecodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    for line in result.log_lines:
        print(line)
    print(("OK: " if result.ok else "BLOCKED: ") + result.message)
    if not result.ok:
        info = classify_text(result.message, "preflight")
        if info.code != "unrecognized":
            print(info.user_text())
    elif result.status == REPAIRED and not args.fix:
        print("Run again with --fix to write the repaired app.py.")
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
