"""Mock REA MCP server (stdio, newline-delimited JSON-RPC) used by tests/test_rea_integration.py.

It speaks just enough of MCP for the platform's client: initialize, tools/list (two pages), tools/call. Its tool schemas mirror
the real REA 6.8.0 ones for the tools the platform may call, plus decoys the client must never call. Every tools/call is
appended to the JSON-lines file named by FAKE_REA_LOG, so a test can prove which tools were REALLY executed and with what arguments.

FAKE_REA_MODE: ok | tool_error | crash | hang | no_model_tool | view | view_error | no_view_tool | gguf_bad | capabilities
  view       the model-inspection result advertises a retained parent_evidence_id, so the client pages it with inspect_analysis_view
  view_error the parent id is advertised but the view call fails (it must stay optional and never change the verdict)
  no_view_tool  the server does not advertise inspect_analysis_view at all
  capabilities  the server advertises the safe capability tools (entropy, unknowns, bundle) plus decoys; the client must select
                the safe ones by policy and never call the decoys (open_binary is mutating, gdb_console is open-world,
                record_unknown writes to the ledger)
"""
import json
import math
import os
import struct
import sys
import time

MODE = os.environ.get("FAKE_REA_MODE", "ok")
LOG = os.environ.get("FAKE_REA_LOG")

TOOLS = [
    {"name": "binary_session", "description": "Report the active binary target and server status without starting analysis.",
     "inputSchema": {"type": "object", "properties": {"expected_package_version": {"type": "string"},
                                                       "expected_catalog_digest": {"type": "string"},
                                                       "expected_server_path": {"type": "string"}}, "additionalProperties": False}},
    {"name": "inspect_model_artifact", "description": "Inspect local GGUF/safetensors declarations.",
     "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}, "hash": {"type": "string"},
                                                       "detail": {"type": "string"}, "view": {"type": "object"}},
                     "required": ["path"], "additionalProperties": False}},
    {"name": "inspect_analysis_view", "description": "Page retained Evidence from a previous inspection.",
     "inputSchema": {"type": "object", "properties": {"source": {"type": "object"}, "view": {"type": "object"}},
                     "required": ["source", "view"], "additionalProperties": False}},
    {"name": "open_binary", "description": "decoy", "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}},
                                                                    "required": ["path"], "additionalProperties": False}},
    {"name": "gdb_console", "description": "decoy", "inputSchema": {"type": "object", "properties": {"command": {"type": "string"}},
                                                                    "required": ["command"], "additionalProperties": False}},
]
# Capability tools with the real REA 6.8.0 schemas (annotations included, since the policy reads them).
CAPABILITY_TOOLS = [
    {"name": "inspect_byte_entropy", "description": "Inspect Shannon byte entropy for one explicit local file offline.",
     "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
     "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}, "block_size": {"type": "integer"},
                                                       "offset": {"type": "integer"}, "length": {"type": "integer"},
                                                       "detail": {"type": "string"}},
                     "required": ["path"], "additionalProperties": False}},
    {"name": "list_unknowns", "description": "List current residual-unknown heads in deterministic ID order.",
     "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
     "inputSchema": {"type": "object", "properties": {"status": {"type": "string"}, "severity": {"type": "string"},
                                                       "domain": {"type": "string"}, "offset": {"type": "integer"},
                                                       "limit": {"type": "integer"}}, "additionalProperties": False}},
    {"name": "get_evidence_bundle", "description": "Return every retained Evidence record and residual unknown as a bundle.",
     "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
     "inputSchema": {"type": "object", "properties": {"detail": {"type": "string"}, "filters": {"type": "object"}},
                     "additionalProperties": False}},
    {"name": "record_unknown", "description": "decoy: writes to the unknown ledger (mutating)",
     "annotations": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False},
     "inputSchema": {"type": "object", "properties": {"domain": {"type": "string"}}, "additionalProperties": False}},
    {"name": "close_binary", "description": "decoy: mutating session close",
     "annotations": {"readOnlyHint": False, "destructiveHint": True, "openWorldHint": False},
     "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}}, "additionalProperties": False}},
]
if MODE == "capabilities":
    TOOLS = TOOLS + CAPABILITY_TOOLS
if MODE == "no_model_tool":
    TOOLS = [t for t in TOOLS if t["name"] != "inspect_model_artifact"]
if MODE == "no_view_tool":
    TOOLS = [t for t in TOOLS if t["name"] != "inspect_analysis_view"]

PARENT = "ev_" + "ab" * 32


def send(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def record(entry):
    if LOG:
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")


def _gguf_text(path):
    """Minimal GGUF read for the mock: magic, version, tensor count. It never reads tensor data."""
    with open(path, "rb") as fh:
        if fh.read(4) != b"GGUF":
            return None
        ver = struct.unpack("<I", fh.read(4))[0]
        if ver >= 2:
            tcount = struct.unpack("<Q", fh.read(8))[0]
        else:
            tcount = struct.unpack("<I", fh.read(4))[0]
    return {"format": "gguf", "version": ver, "tensor_count": tcount}


def _model_text(path, args):
    """The text a model-inspection call returns. GGUF files are detected by magic; otherwise safetensors is assumed."""
    g = _gguf_text(path)
    if g is not None:
        if MODE == "gguf_bad":
            return json.dumps({"error": {"code": "target_unavailable", "message": "GGUF header rejected"}}), True
        return json.dumps(g), False
    size = os.path.getsize(path)  # the LOGICAL size: what the real REA 6.8.0 validates the declared tensor offsets against
    with open(path, "rb") as fh:
        n = int.from_bytes(fh.read(8), "little")
        header = json.loads(fh.read(n))  # like hash=header: only the header is read, never the tensor data
    blocks = getattr(os.stat(path), "st_blocks", None)  # allocated 512-byte blocks (POSIX only; None on Windows)
    record({"file_existed": True, "file_size": size, "header_bytes": n, "bytes_read": 8 + n, "hash": args.get("hash"),
            "allocated_bytes": None if blocks is None else blocks * 512})
    data_len = size - 8 - n
    for key, tensor in header.items():
        if key == "__metadata__":
            continue
        begin, end = tensor["data_offsets"]
        if end > data_len:  # the failure recorded from the real REA for header-only files (diagnostics/rea_live_verification.json)
            msg = f"Cannot open artifact: Safetensors tensor {key} range [{begin},{end}) is outside the data buffer."
            return json.dumps({"error": {"code": "target_unavailable", "message": msg}}), True
    text = {"format": "safetensors", "tensor_count": len([k for k in header if k != "__metadata__"]), "header_bytes": n,
            "hash": args.get("hash"), "file_size": size}
    return json.dumps(text), False


def call(name, args):
    if name == "binary_session":
        return {"content": [{"type": "text", "text": json.dumps({"active_target": None, "server": "fake-rea"})}]}
    if name == "inspect_model_artifact":
        if MODE == "tool_error":
            return {"isError": True, "content": [{"type": "text", "text": "refused: file is not a complete safetensors file"}]}
        if MODE == "crash":
            sys.exit(3)
        if MODE == "hang":
            time.sleep(60)
        text, is_err = _model_text(args["path"], args)
        if is_err:
            return {"isError": True, "content": [{"type": "text", "text": text}]}
        body = json.loads(text)
        if MODE in ("view", "view_error", "capabilities"):
            body = {"normalized_result": {"parent_evidence_id": PARENT, "summary": body}, "confidence": "derived"}
        return {"content": [{"type": "text", "text": json.dumps(body)}]}
    if name == "inspect_byte_entropy":
        path = args["path"]
        length = int(args.get("length") or 0)
        with open(path, "rb") as fh:  # never reads more than the caller bounded: a sparse hole is not streamed
            data = fh.read(length) if length else fh.read(65536)
        counts = [0] * 256
        for b in data:
            counts[b] += 1
        total = max(len(data), 1)
        ent = 0.0
        for c in counts:
            if c:
                p = c / total
                ent -= p * math.log2(p)
        return {"content": [{"type": "text", "text": json.dumps({"file_bytes_read": len(data), "entropy": round(ent, 4)})}]}
    if name == "list_unknowns":
        return {"content": [{"type": "text", "text": json.dumps({"total": 1, "next_offset": None,
                                                                  "unknowns": [{"unknown_id": "unk_" + "cd" * 32,
                                                                                "status": "open", "severity": "low",
                                                                                "domain": "model-artifact"}]})}]}
    if name == "get_evidence_bundle":
        return {"content": [{"type": "text", "text": json.dumps({"detail": args.get("detail", "complete"),
                                                                  "evidence_count": 1, "unknown_count": 1})}]}
    if name == "inspect_analysis_view":
        if MODE == "view_error":
            return {"isError": True, "content": [{"type": "text", "text": "view unavailable"}]}
        src = args.get("source") or {}
        rows = {"kind": "summary", "collection": "metadata", "parent_evidence_id": src.get("evidence_id"),
                "rows": [{"key": "general.architecture", "value": "llama"}, {"key": "general.name", "value": "TinyLlama"}]}
        return {"content": [{"type": "text", "text": json.dumps(rows)}]}
    return {"isError": True, "content": [{"type": "text", "text": "unexpected tool " + name}]}


def main():
    sys.stdout.write("fake-rea banner (not JSON, must be ignored)\n")
    sys.stdout.flush()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        msg = json.loads(line)
        method, mid = msg.get("method"), msg.get("id")
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": mid, "result": {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                                                          "serverInfo": {"name": "rea", "version": "6.8.0"}}})
        elif method == "tools/list":
            cursor = (msg.get("params") or {}).get("cursor")
            page = TOOLS[:2] if not cursor else TOOLS[2:]
            result = {"tools": page}
            if not cursor:
                result["nextCursor"] = "page2"
            send({"jsonrpc": "2.0", "id": mid, "result": result})
        elif method == "tools/call":
            params = msg.get("params") or {}
            entry = {"tool": params.get("name"), "arguments": params.get("arguments")}
            record(entry)
            send({"jsonrpc": "2.0", "id": mid, "result": call(params.get("name"), params.get("arguments") or {})})
        elif mid is not None:
            send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "unknown method"}})


if __name__ == "__main__":
    main()
