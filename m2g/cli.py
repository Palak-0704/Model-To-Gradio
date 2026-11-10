"""Command line: ``python -m m2g.cli <hf-or-github-url> [--out DIR] [--no-write] [--no-rea]``.

Exit codes: 0 = app + notebook generated, 2 = analysed but unsupported/partial (no app), 1 = error.

The REA inspection stage runs between metadata inspection and analysis, exactly as in the web UI. It is switched off with
``--no-rea`` or ``M2G_REA_ENABLED=0``; the printed report always states whether any REA tool was really executed.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import List, Mapping, Optional

from .config import Settings, check_write_access
from .pipeline import BuildResult, run_pipeline, summarize
from .rea_client import make_runner as make_rea_runner

_OFF = {"0", "false", "no", "off"}


def rea_enabled(no_rea: bool = False, environ: Optional[Mapping[str, str]] = None) -> bool:
    """REA is on unless ``--no-rea`` was given or M2G_REA_ENABLED is set to a false value (same default as the web UI)."""
    env = os.environ if environ is None else environ
    return not no_rea and env.get("M2G_REA_ENABLED", "1").strip().lower() not in _OFF


def rea_report(res: BuildResult) -> str:
    """Plain-text REA outcome for the console: the status and the tools that were really called ('' if the stage never ran)."""
    rep = res.rea
    if rep is None:
        return ""
    tools = ", ".join(f"{c.tool}{'' if c.ok else ' (FAILED)'}" for c in rep.calls) or "none (no REA tool was called)"
    return f"REA inspection: {rep.label}" + (f" - {rep.reason}" if rep.reason else "") + f"\nREA tools executed: {tools}"


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="m2g", description="Turn a Hugging Face / GitHub model URL into a Gradio app + Colab notebook.")
    p.add_argument("url", help="https://huggingface.co/<owner>/<model> or https://github.com/<owner>/<repo>")
    p.add_argument("--out", help="output directory (default: ./output or M2G_OUTPUT_DIR)")
    p.add_argument("--no-write", action="store_true", help="analyse and generate in memory only")
    p.add_argument("--no-rea", action="store_true", help="skip the REA inspection stage (it is on by default)")
    args = p.parse_args(argv)

    settings = Settings.from_env()
    if args.out:
        settings = Settings(**{**settings.__dict__, "output_dir": Path(args.out)})
    if not args.no_write:
        ok, msg = check_write_access(settings.output_dir)
        if not ok:
            print(f"Cannot write to {settings.output_dir}: {msg}", file=sys.stderr)
            return 1
    # The runner is passed only when REA is enabled; without it the pipeline records the stage as 'not_invoked'.
    extra = {"rea": make_rea_runner(settings)} if rea_enabled(args.no_rea) else {}
    res = run_pipeline(args.url, settings, write=not args.no_write, **extra)
    print(summarize(res))
    report = rea_report(res)
    if report:
        print("\n" + report)
    if res.error:
        return 1
    return 0 if res.ok else 2


if __name__ == "__main__":
    sys.exit(main())
