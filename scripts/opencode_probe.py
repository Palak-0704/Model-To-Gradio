"""Manual probe: run the real OpenCode CLI once through m2g.opencode_cli and show what actually came back.

Not part of the app and not run by the tests. It makes ONE tiny model request (no repository content, no code generation).

  python scripts\opencode_probe.py          # plain request: verifies flags, exit status and the JSON event format
  python scripts\opencode_probe.py --rea    # asks OpenCode to call one read-only REA tool: verifies tool-call events

It prints the event types seen, every event that looks like a tool call (shortened), and the final text. It does not
decide for you whether REA "worked": read the printed tool events. The full redacted stdout is saved under .m2g\\.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from m2g.opencode_cli import OpenCodeAdapter  # noqa: E402

PLAIN_REQUEST = "Reply with exactly the single word OK. Do not use any tools."
REA_REQUEST = ("Call the REA MCP tool list_architectures exactly once (it is read-only and takes no binary). "
               "Then reply with the single word DONE. Use no other tool.")


def short(obj, limit=700) -> str:
    text = json.dumps(obj, ensure_ascii=True, default=str)
    return text if len(text) <= limit else text[:limit] + "...[cut]"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rea", action="store_true", help="ask for one read-only REA tool call")
    args = ap.parse_args()

    adapter = OpenCodeAdapter()
    print(f"executable : {adapter.executable}")
    print(f"available  : {adapter.available}  (problem: {adapter.problem})")
    print(f"model      : {adapter.model}")
    print(f"project dir: {adapter.project_dir}")
    if not adapter.available:
        return 2

    res = adapter.run(REA_REQUEST if args.rea else PLAIN_REQUEST)
    print("\n--- result ---")
    print(f"ok={res.ok} error={res.error} returncode={res.returncode} duration={res.duration_s}s")
    if res.message:
        print(f"message    : {res.message}")
    for w in res.warnings:
        print(f"warning    : {w}")
    print("command    :", " ".join(res.command))

    counts = Counter(str(ev.get("type")) for ev in res.events)
    print(f"\n--- {len(res.events)} JSON events, types: {dict(counts)} ---")
    if res.events:
        print("first event keys:", sorted(res.events[0].keys()))
    tool_events = [ev for ev in res.events if "tool" in json.dumps(ev, default=str).lower()]
    print(f"\n--- events that mention 'tool': {len(tool_events)} ---")
    for ev in tool_events:
        print(short(ev))

    print("\n--- assistant text ---")
    print(res.text[:500] or "(none parsed)")
    if res.stderr.strip():
        print("\n--- stderr (tail) ---")
        print(res.stderr.strip()[-800:])

    out_dir = ROOT / ".m2g"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / ("probe_rea.jsonl" if args.rea else "probe_plain.jsonl")
    out.write_text(res.stdout, encoding="utf-8")
    print(f"\nraw (redacted) stdout saved to {out}")
    return 0 if res.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
