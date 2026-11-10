"""Real (non-mocked) Colab smoke test, run from PowerShell:

    .\\.venv\\Scripts\\python.exe scripts\\real_colab_smoke.py                    # read-only checks, creates NOTHING
    .\\.venv\\Scripts\\python.exe scripts\\real_colab_smoke.py --url <HF model URL> --create-runtime [--gpu T4|CPU] [--keep]

Without ``--create-runtime`` it only (1) finds the `colab` command, (2) confirms it is Google's official CLI and
(3) runs `colab sessions` (a read-only call that never prompts: if you are not signed in it says so). It never signs
in for you and never creates a runtime. ``--create-runtime`` is your explicit authorization to create one runtime
(it uses Colab compute units), launch the generated app, print the verified public URL, and stop the runtime again
unless ``--keep`` is given.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from m2g.colab_cli import ColabCli  # noqa: E402
from m2g.colab_workflow import CPU_LABEL, RUNNING, ColabLauncher  # noqa: E402
from m2g.config import Settings  # noqa: E402
from m2g.pipeline import run_pipeline  # noqa: E402


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Real Colab CLI smoke test (creates a runtime only with --create-runtime).")
    p.add_argument("--url", help="Hugging Face model URL to generate and launch (needed with --create-runtime)")
    p.add_argument("--gpu", default="T4", help="T4, L4, ... or CPU (default T4). A request, not a guarantee.")
    p.add_argument("--create-runtime", action="store_true", help="explicit authorization to create ONE Colab runtime")
    p.add_argument("--keep", action="store_true", help="leave the runtime running afterwards")
    args = p.parse_args(argv)
    settings = Settings.from_env()  # also loads .env so COLAB_COMMAND / COLAB_AUTH apply
    cli = ColabCli()

    ok, msg = cli.identify()
    print(f"[1/3] CLI identity: {'OK' if ok else 'PROBLEM'} - {msg}")
    if not ok:
        return 1
    print(f"      command: {' '.join(cli.command or [])}  version: {cli.version() or 'unknown'}  auth mode: {cli.auth}")
    state, message = cli.check()
    print(f"[2/3] `colab sessions`: {state} - {message}")
    gpus = cli.supported_gpus()
    print(f"[3/3] Accelerators accepted by this CLI: {', '.join(gpus.names)} ({gpus.source})")
    if state != "ok":
        print("Not connected / not working. Nothing was created. In the app click 'Sign in with Google', or fix the error above.")
        return 2
    if not args.create_runtime:
        print("Read-only checks passed. Nothing was created. Add --url <model> --create-runtime to launch for real.")
        return 0
    if not args.url:
        print("--create-runtime needs --url <Hugging Face model URL>.", file=sys.stderr)
        return 1

    res = run_pipeline(args.url, settings, write=False)
    if not res.ok:
        print(f"Cannot launch: {res.error or '; '.join(res.notes) or 'model not supported'}", file=sys.stderr)
        return 1
    launcher = ColabLauncher(cli)
    gpu = CPU_LABEL if args.gpu.upper() == "CPU" else args.gpu
    print(f"Launching {res.analysis.source.repo_id} on a {gpu} runtime (this can take many minutes)...")
    try:
        snap = launcher.start(res.app_code, res.requirements, gpu, settings=settings)
        print("\n".join(snap.logs[-40:]))
        if snap.state == RUNNING and snap.share_url:
            print(f"\nVERIFIED public URL: {snap.share_url}\n  {snap.url_check}\n  self-test: {snap.selftest}")
            if not args.keep:
                input("Press Enter to stop the runtime (the link then stops working)... ")
            return 0
        print(f"\nFAILED ({snap.error_kind}): {snap.message}", file=sys.stderr)
        return 3
    finally:
        if not args.keep and launcher.snapshot().session:
            launcher.stop()
            print("Runtime stopped.")


if __name__ == "__main__":
    sys.exit(main())
