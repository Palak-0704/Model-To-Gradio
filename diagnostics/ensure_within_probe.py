"""Diagnostic only; changes no behaviour and can be deleted.

Reproduces tests/test_package_integrity.py::TestProcessModelLocks::test_concurrent_process_pipelines_share_one_safe_package and,
whenever m2g.security.ensure_within rejects a path, prints exactly which paths were compared and how Windows resolved them.

Run from the repository root:
    .venv\\Scripts\\python.exe diagnostics\\ensure_within_probe.py            # two processes at once (the failing scenario)
    .venv\\Scripts\\python.exe diagnostics\\ensure_within_probe.py serial      # the same two runs one after the other
"""
import multiprocessing
import os
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _install_probe():
    sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
    from m2g import package_store, pipeline, security

    real = security.ensure_within

    def probe(base, target):
        try:
            return real(base, target)
        except security.SecurityError:
            print(f"[pid {os.getpid()}] ensure_within REJECTED a path", flush=True)
            for label, value in (("base", base), ("target", target)):
                p = Path(value)
                print(f"  {label}: raw={str(value)!r}\n    Path.resolve()={str(p.resolve())!r}\n"
                      f"    os.path.realpath={os.path.realpath(p)!r}\n    exists={p.exists()}", flush=True)
            print("  call stack:\n" + "".join(traceback.format_stack(limit=6)), flush=True)
            raise

    package_store.ensure_within = probe  # both modules imported the function by name
    pipeline.ensure_within = probe


def worker(base, start, result):
    _install_probe()
    from fakehub import FakeWeb, hf, PRESETS
    from m2g.config import Settings
    from m2g.pipeline import run_pipeline

    start.wait(30)
    web = FakeWeb()
    hf(web, "sst2")
    res = run_pipeline("https://huggingface.co/" + PRESETS["sst2"]["repo_id"], Settings(output_dir=Path(base)),
                       web.fetcher(), evidence=False)
    result.put((os.getpid(), res.ok, res.error, res.reused_package))


def main():
    serial = len(sys.argv) > 1 and sys.argv[1] == "serial"
    ctx = multiprocessing.get_context("spawn")
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp).resolve() / "out"
        print(f"python {sys.version.split()[0]} | os.name={os.name} | base={base}", flush=True)
        start, result = ctx.Event(), ctx.Queue()
        procs = [ctx.Process(target=worker, args=(str(base), start, result)) for _ in range(2)]
        outcomes = []
        if serial:
            start.set()
            for p in procs:
                p.start()
                outcomes.append(result.get(timeout=120))
                p.join(60)
        else:
            for p in procs:
                p.start()
            start.set()
            outcomes = [result.get(timeout=120) for _ in procs]
            for p in procs:
                p.join(60)
        for outcome in outcomes:
            print("RESULT (pid, ok, error, reused):", outcome, flush=True)


if __name__ == "__main__":
    main()
