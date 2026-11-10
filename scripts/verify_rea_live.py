"""LIVE REA verification: the real rea.cmd server, driven through m2g.rea_client. Not part of pytest (needs REA and the network).

Usage (PowerShell, project root):
    .\.venv\Scripts\python.exe scripts\verify_rea_live.py [HUGGING_FACE_MODEL_URL]

Three independent checks, so a failure points at the right layer. Nothing here is simulated or mocked.

  A  synthetic: build a valid SPARSE safetensors file locally (header at the start, logical size = header + tensor data, the data
     region is a hole) and call the real REA tools on it. As a negative control, the same header WITHOUT the logical size (a plain
     header-only file) is submitted too: the real REA rejected it ("range ... is outside the data buffer"), which is why sparse
     files are used. Answers: does the real inspect_model_artifact accept a sparse file with the right logical size?
  B  end-to-end: fetch only the real safetensors header of a Hugging Face model with HTTP range requests and run the same
     stage the pipeline runs (run_rea_inspection). Answers: do the real tools run, how many bytes were downloaded (expect two
     range requests, a few KB), is the scratch cleaned up?
  C  payload: does REA read the hole? A 3 GiB sparse file (no disk space used) is inspected with hash="header" while the read
     counters of REA's whole process tree are sampled (Windows I/O counters via PowerShell/CIM). A 256 MiB sparse CONTROL file is
     inspected with hash="full", which streams every byte: the counter must see ~256 MiB for that, otherwise the measurement is
     not trustworthy and the check fails as "inconclusive" instead of passing. The control only reads a local synthetic file.

Writes diagnostics\rea_live_verification.json and prints a verdict per check.
Exit code: 0 = all checks passed, 1 = at least one did not.
"""
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from m2g import rea_client as rc  # noqa: E402
from m2g.config import PROJECT_ROOT, Settings  # noqa: E402
from m2g.inspector import inspect_source  # noqa: E402
from m2g.security import SafeFetcher, parse_source_url  # noqa: E402

DEFAULT_URL = "https://huggingface.co/distilbert/distilbert-base-uncased-finetuned-sst-2-english"
HEADER = {"w": {"dtype": "F32", "shape": [2, 3], "data_offsets": [0, 24]}, "b": {"dtype": "F16", "shape": [4], "data_offsets": [24, 32]}}
BIG_BYTES = 3 << 30  # logical size of the big sparse file (a hole: uses no disk space)
CONTROL_BYTES = 256 << 20  # logical size of the control file inspected with hash="full"
WARMUP_BYTES = 1 << 20
MAX_HEADER_MODE_READ = 64 << 20  # a header-only inspection of the BIG file must read far less than this (it is 3 GiB)


def leftovers(project_dir: Path):
    base = project_dir.joinpath(*rc.SCRATCH_SUBDIR)
    return sorted(p.name for p in base.glob("hdr_*")) if base.exists() else []


def synthetic_header(total: int) -> bytes:
    """8 length bytes + JSON header of ONE uint8 tensor that fills a file of exactly ``total`` bytes."""
    for n in range(32, 512):
        k = total - 8 - n
        js = json.dumps({"payload": {"dtype": "U8", "shape": [k], "data_offsets": [0, k]}}, separators=(",", ":")).encode("utf-8")
        if len(js) == n:
            return n.to_bytes(8, "little") + js
    raise ValueError("no header length is consistent with this total size")


def tree_read_bytes(root_pid: int):
    """(sum of ReadTransferCount over ``root_pid`` and all its descendants, number of processes in the tree). Windows only."""
    query = "Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,ReadTransferCount | ConvertTo-Json -Compress"
    res = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", query], capture_output=True, text=True,
                         timeout=60, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    rows = json.loads(res.stdout)
    rows = rows if isinstance(rows, list) else [rows]
    children, reads = {}, {}
    for row in rows:
        pid = int(row["ProcessId"])
        children.setdefault(int(row["ParentProcessId"]), []).append(pid)
        reads[pid] = int(row.get("ReadTransferCount") or 0)
    seen, stack = set(), [root_pid]
    while stack:
        pid = stack.pop()
        if pid not in seen:
            seen.add(pid)
            stack.extend(children.get(pid, []))
    return sum(reads.get(pid, 0) for pid in seen), len(seen)


def check_synthetic(project_dir: Path) -> dict:
    out = {"check": "A synthetic SPARSE file -> real REA (plain header-only file as the negative control)", "ok": False}
    js = json.dumps(HEADER).encode("utf-8")
    blob = len(js).to_bytes(8, "little") + js  # 8-byte length + header; the 32 bytes of tensor data are NOT written
    total = len(blob) + 32
    folder = project_dir / ".m2g" / "rea_probe"
    folder.mkdir(parents=True, exist_ok=True)
    sparse, plain = folder / "synthetic_sparse.safetensors", folder / "synthetic_header_only.safetensors"
    sparse.unlink(missing_ok=True)
    plain.unlink(missing_ok=True)
    out["sparse_file_logical_bytes"], out["header_only_file_bytes"] = total, len(blob)
    try:
        out["sparse_file_allocated_bytes"] = rc.create_sparse_file(sparse, blob, total)
        plain.write_bytes(blob)
        cmd = rc.resolve_command()
        out["command"] = cmd
        with rc.ReaMcpClient(cmd, cwd=project_dir) as client:
            out["server"] = dict(client.server_info)
            out["tools_discovered"] = len(client.list_tools())
            out["advertises_required_tools"] = {t: t in client.tools for t in rc.ALLOWED_TOOLS}
            calls = [client.call_tool(rc.HEALTH_TOOL, dict(rc.FIXED_ARGS[rc.HEALTH_TOOL]), target="server status"),
                     client.call_tool(rc.MODEL_TOOL, {"path": str(sparse), **rc.FIXED_ARGS[rc.MODEL_TOOL]}, target=sparse.name),
                     client.call_tool(rc.MODEL_TOOL, {"path": str(plain), **rc.FIXED_ARGS[rc.MODEL_TOOL]},
                                      target=plain.name + " (negative control)")]
            out["stderr_tail"] = client.stderr_tail
        out["calls"] = [c.__dict__ for c in calls]
        out["control_rejected_as_before"] = not calls[2].ok
        out["ok"] = calls[0].ok and calls[1].ok
    except (rc.ReaError, OSError) as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        sparse.unlink(missing_ok=True)
        plain.unlink(missing_ok=True)
    out["probe_files_removed"] = not sparse.exists() and not plain.exists()
    return out


def check_end_to_end(project_dir: Path, url: str) -> dict:
    out = {"check": "B real header fetch + real REA through run_rea_inspection", "url": url, "ok": False}
    settings = Settings.from_env()
    fetcher = SafeFetcher(settings)
    info = inspect_source(parse_source_url(url), fetcher, settings)
    started = time.monotonic()
    report = rc.run_rea_inspection(info, fetcher, settings, project_dir=project_dir)
    out["seconds"] = round(time.monotonic() - started, 1)
    out["report"] = report.to_dict()
    out["tools_invoked"] = report.tools_invoked
    # every HTTP hop is logged (the Hub redirects to its CDN), so this list is longer than the number of range requests
    out["safetensors_http_hops"] = [u.split("?")[0] for u in fetcher.request_log if ".safetensors" in u or "cdn" in u]
    out["http_range_requests"], out["bytes_downloaded"] = report.http_requests, report.bytes_downloaded
    out["scratch_leftovers"] = leftovers(project_dir)
    inspected = len(report.header_summaries)
    out["ok"] = (report.status == rc.COMPLETED and not out["scratch_leftovers"] and inspected > 0
                 and report.http_requests == 2 * inspected)
    return out


def check_payload_not_read(project_dir: Path) -> dict:
    out = {"check": "C REA does not read the sparse payload (read counters of REA's process tree)", "ok": False}
    folder = project_dir / ".m2g" / "rea_probe"
    folder.mkdir(parents=True, exist_ok=True)
    warm, big, control = folder / "warmup.safetensors", folder / "big_sparse.safetensors", folder / "control_sparse.safetensors"
    for p in (warm, big, control):
        p.unlink(missing_ok=True)
    try:
        for path, size in ((warm, WARMUP_BYTES), (big, BIG_BYTES), (control, CONTROL_BYTES)):
            rc.create_sparse_file(path, synthetic_header(size), size)
        out["big_logical_bytes"], out["big_allocated_bytes"] = big.stat().st_size, rc.allocated_bytes(big)
        cmd = rc.resolve_command()
        with rc.ReaMcpClient(cmd, cwd=project_dir) as client:
            client.list_tools()
            pid = client._proc.pid  # root of REA's process tree (cmd.exe -> node ...)

            def measure(path: Path, hash_mode: str) -> dict:
                before, _ = tree_read_bytes(pid)
                started = time.monotonic()
                call = client.call_tool(rc.MODEL_TOOL, {"path": str(path), "hash": hash_mode, "detail": "summary"}, target=path.name)
                seconds = round(time.monotonic() - started, 2)
                after, processes = tree_read_bytes(pid)
                return {"file": path.name, "logical_bytes": path.stat().st_size, "hash": hash_mode, "ok": call.ok,
                        "error": call.error, "seconds": seconds, "bytes_read_delta": after - before, "processes_in_tree": processes}

            # the warm-up call lets REA load its own code first, so the measured calls only show what the file itself costs
            out["runs"] = [measure(warm, "header"), measure(big, "header"), measure(control, "full")]
        _, big_run, control_run = out["runs"]
        out["counter_sees_reads"] = bool(control_run["ok"] and control_run["bytes_read_delta"] >= 0.9 * CONTROL_BYTES)
        out["payload_not_read"] = bool(big_run["ok"] and big_run["bytes_read_delta"] < MAX_HEADER_MODE_READ)
        if not out["counter_sees_reads"]:
            out["verdict"] = "INCONCLUSIVE: the control (hash=full) was not seen by the counter, so a small number proves nothing"
        elif not big_run["ok"]:
            out["verdict"] = "REA refused the big sparse file: " + str(big_run["error"])[:300]
        elif out["payload_not_read"]:
            out["verdict"] = (f"PASS: hash=header on a {BIG_BYTES >> 30} GiB sparse file read {big_run['bytes_read_delta']:,} bytes; "
                              f"hash=full on a {CONTROL_BYTES >> 20} MiB file read {control_run['bytes_read_delta']:,} bytes")
        else:
            out["verdict"] = f"FAIL: hash=header read {big_run['bytes_read_delta']:,} bytes of a {BIG_BYTES >> 30} GiB file"
        out["ok"] = out["counter_sees_reads"] and out["payload_not_read"]
    except (rc.ReaError, OSError, ValueError, subprocess.SubprocessError) as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        for p in (warm, big, control):
            p.unlink(missing_ok=True)
    out["probe_files_removed"] = not any(p.exists() for p in (warm, big, control))
    return out


def main() -> int:
    url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL
    project_dir = Path(PROJECT_ROOT)
    results = []
    for fn, args in ((check_synthetic, (project_dir,)), (check_end_to_end, (project_dir, url)), (check_payload_not_read, (project_dir,))):
        try:
            res = fn(*args)
        except Exception as exc:  # report, never hide
            res = {"check": fn.__name__, "ok": False, "error": f"{type(exc).__name__}: {exc}"}
        results.append(res)
        print(f"[{'PASS' if res['ok'] else 'FAIL'}] {res['check']}")
        if res.get("error"):
            print("       error:", res["error"])
        for c in res.get("calls", []):
            print(f"       {c['tool']} [{c.get('target')}]: {'ok' if c['ok'] else 'FAILED ' + str(c.get('error'))[:300]}")
            if c["tool"] == rc.MODEL_TOOL and c["ok"]:
                print("       output:", (c.get("output") or "")[:600].replace("\n", " "))
        if "report" in res:
            print(f"       status={res['report']['status']} tools={res['tools_invoked']} seconds={res['seconds']}")
            print(f"       reason={res['report']['reason']}")
            print(f"       scratch leftovers={res['scratch_leftovers']} range requests={res['http_range_requests']} "
                  f"bytes downloaded={res['bytes_downloaded']:,}")
        for run in res.get("runs", []):
            print(f"       {run['file']} hash={run['hash']} logical={run['logical_bytes']:,} ok={run['ok']} "
                  f"read delta={run['bytes_read_delta']:,} in {run['seconds']}s (processes in tree: {run['processes_in_tree']})")
        if res.get("verdict"):
            print("       " + res["verdict"])
    target = project_dir / "diagnostics" / "rea_live_verification.json"
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    print("Full evidence written to", target)
    return 0 if all(r["ok"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
