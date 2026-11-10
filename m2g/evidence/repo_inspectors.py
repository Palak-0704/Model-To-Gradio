"""Static, side-effect-free inspectors for repository files. Each takes bytes or text that the caller already fetched and returns plain
JSON-safe facts. Nothing here touches the network, imports repository code, evaluates it, unpickles it or starts a process.

Every inspector is tolerant: malformed input yields ``{"parse_error": ...}`` or a ``problems`` list, never an exception, so one bad
file can never stop an investigation. Facts are direct observations ("this call is on line N of this file"); deciding what they mean
for a model is the job of the layers above."""
from __future__ import annotations

import ast
import configparser
import io
import json
import math
import pickletools
import re
import struct
import zlib
from typing import Any, Dict, Iterator, List, Optional, Tuple

try:  # Python 3.11+
    import tomllib  # type: ignore
except ImportError:  # pragma: no cover
    tomllib = None  # type: ignore
try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore

MAX_FACTS = 200  # entries kept per fact list in one file; a longer list is cut and named in ``truncated``

# --------------------------------------------------------------------------- classification (never excludes anything)

_EXT: Dict[str, str] = {}
for _cat, _exts in {
    "python": (".py", ".pyi", ".pyx"), "notebook": (".ipynb",), "json": (".json",), "jsonl": (".jsonl", ".ndjson"),
    "yaml": (".yaml", ".yml"), "toml": (".toml",), "ini": (".ini", ".cfg", ".conf"), "markdown": (".md", ".markdown", ".mdx"),
    "text": (".txt", ".rst", ".log", ".tsv", ".csv", ".tpl", ".jinja", ".j2"), "shell": (".sh", ".bash", ".bat", ".cmd", ".ps1"),
    "source": (".c", ".cc", ".cpp", ".cxx", ".h", ".hpp", ".cu", ".cuh", ".js", ".mjs", ".ts", ".tsx", ".jsx", ".rs", ".go", ".java",
               ".kt", ".swift", ".cs", ".lua", ".r", ".jl", ".m", ".scala", ".rb", ".php", ".proto", ".cmake", ".html", ".css"),
    "safetensors": (".safetensors",), "gguf": (".gguf", ".ggml"), "onnx": (".onnx",),
    "pickle_weights": (".pt", ".pth", ".ckpt", ".bin", ".pkl", ".pickle", ".joblib", ".mar"),
    "other_weights": (".h5", ".hdf5", ".keras", ".tflite", ".pb", ".msgpack", ".npz", ".npy", ".ot", ".mlmodel", ".engine", ".trt",
                      ".pdparams", ".pdmodel", ".mlpackage", ".params", ".caffemodel", ".nemo", ".tar"),
    "media": (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".wav", ".mp3", ".flac", ".ogg", ".mp4", ".webm", ".mov", ".avi"),
    "archive": (".zip", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar", ".whl"),
    "data": (".parquet", ".arrow", ".feather", ".db", ".sqlite"),
}.items():
    for _e in _exts:
        _EXT[_e] = _cat
_NAMES = {"dockerfile": "text", "makefile": "text", "license": "text", "licence": "text", "notice": "text", "procfile": "text",
          "tokenizer.model": "tokenizer_asset", "spiece.model": "tokenizer_asset", "sentencepiece.bpe.model": "tokenizer_asset",
          "vocab.txt": "tokenizer_asset", "merges.txt": "tokenizer_asset", "vocab.json": "tokenizer_asset", "added_tokens.json": "tokenizer_asset"}


def classify(path: str) -> str:
    """A category for ANY path. Unfamiliar names/extensions are ``unknown``: they are still inspected (by magic bytes), never dropped."""
    name = path.rsplit("/", 1)[-1].lower()
    if name in _NAMES:
        return _NAMES[name]
    if re.fullmatch(r"requirements[\w.\-]*\.txt|constraints[\w.\-]*\.txt", name):
        return "requirements"
    if name.endswith(".tiktoken"):
        return "tokenizer_asset"
    for ext in (".tar.gz", ".tar.bz2", ".tar.xz"):
        if name.endswith(ext):
            return "archive"
    dot = name.rfind(".")
    return _EXT.get(name[dot:], "unknown") if dot >= 0 else "unknown"


# --------------------------------------------------------------------------- helpers


def _short(v: Any, limit: int = 200, depth: int = 0) -> Any:
    if isinstance(v, str):
        return v if len(v) <= limit else v[:limit] + "..."
    if isinstance(v, (bool, int, float)) or v is None:
        return v
    if depth >= 3:
        return "..."
    if isinstance(v, (list, tuple)):
        return [_short(x, limit, depth + 1) for x in list(v)[:12]]
    if isinstance(v, dict):
        return {str(k)[:60]: _short(x, limit, depth + 1) for k, x in list(v.items())[:12]}
    return str(v)[:limit]


def _dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return ""


def _lit(node: Optional[ast.AST]) -> Any:
    if node is None:
        return None
    try:
        return _short(ast.literal_eval(node))
    except Exception:
        ref = _dotted(node)
        return {"ref": ref[:120]} if ref else {"expr": type(node).__name__}


# --------------------------------------------------------------------------- Python (AST only)

_LOAD = re.compile(r"(\.from_pretrained|\.from_single_file|\.from_checkpoint|\.from_config|^torch\.load|\.load_state_dict|InferenceSession$|"
                   r"(^|\.)load_model$|^joblib\.load|^pickle\.load|\.load_file$|hf_hub_download|snapshot_download|^torch\.jit\.load|"
                   r"^onnx\.load|(^|\.)pipeline$|^cv2\.dnn\.read\w*|(^|\.)Interpreter$|^tf\.saved_model\.load|^keras\.models\.load_model)")
_INFER = re.compile(r"\.(generate|predict|forward|transcribe|synthesize|detect|infer|inference|sample|stream|chat|encode_image|__call__)$")
_PRE = re.compile(r"(transforms\.\w+|(^|\.)(Resize|Normalize|ToTensor|CenterCrop|Compose)$|\.resize$|^cv2\.(resize|cvtColor|imread)|"
                  r"Image\.(open|fromarray)|librosa\.load|torchaudio\.load|(soundfile|sf)\.read|"
                  r"(^|\.)(tokenizer|tokenize|processor|feature_extractor|image_processor)$|\.apply_chat_template$)")
_POST = re.compile(r"((^|\.)(softmax|argmax|sigmoid|topk|batch_decode|decode|nms|non_max_suppression|detokenize)$|(soundfile|sf)\.write$|"
                   r"^cv2\.imwrite$|\.save$)")
_CALLABLE_NAMES = {"model", "net", "network", "pipe", "pipeline", "session", "sess", "engine", "predictor", "detector", "generator"}
_SESSION_HINT = ("sess", "ort", "onnx", "engine", "interpreter")
_SDK_SERVICES = {"openai", "anthropic", "cohere", "replicate", "boto3", "google", "azure", "elevenlabs", "stability_sdk", "together", "groq",
                 "mistralai", "huggingface_hub"}
_DTYPE = re.compile(r"^(torch|jnp|np|numpy|tf)\.(float16|bfloat16|float32|float64|int8|uint8|int4|half|float)$")
_DEFS_OF_INTEREST = {"predict", "infer", "inference", "forward", "generate", "run", "main", "demo", "detect", "transcribe", "synthesize",
                     "load_model", "build_model", "get_model", "preprocess", "postprocess", "__call__", "evaluate", "chat", "stream"}


def _kwargs(node: ast.Call, limit: int = 8) -> Dict[str, Any]:
    return {k.arg: _lit(k.value) for k in node.keywords[:limit] if k.arg}


def analyze_python(text: str) -> Dict[str, Any]:
    """AST summary of one module: imports, classes, functions, load/inference/pre/post-processing calls with literal arguments, CLI
    arguments, UI components, environment variable NAMES, external services, device/dtype hints. Nothing is executed."""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError, RecursionError, MemoryError) as exc:
        return {"parse_error": f"{type(exc).__name__}: {str(exc)[:160]}", "lines": text.count("\n") + 1}
    f: Dict[str, Any] = {"imports": [], "classes": [], "functions": [], "calls": {"load": [], "infer": [], "preprocess": [], "postprocess": []},
                         "env_vars": [], "cli_args": [], "ui": [], "trust_remote_code_lines": [], "device_hints": [], "dtype_hints": [],
                         "external_services": [], "lines": text.count("\n") + 1}
    tops, devices, dtypes, envs, services = set(), set(), set(), set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            f["imports"].append({"module": ", ".join(a.name for a in node.names), "level": 0, "line": node.lineno})
            tops.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            f["imports"].append({"module": node.module or "", "names": [a.name for a in node.names][:20], "level": node.level, "line": node.lineno})
            if node.level == 0 and node.module:
                tops.add(node.module.split(".")[0])
        elif isinstance(node, ast.ClassDef):
            methods = [n.name for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
            f["classes"].append({"name": node.name, "bases": [b for b in (_dotted(x) for x in node.bases) if b], "methods": methods[:40],
                                 "line": node.lineno})
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            a = node.args
            names = [x.arg for x in [*a.posonlyargs, *a.args, *a.kwonlyargs]] + ([a.vararg.arg] if a.vararg else []) + ([a.kwarg.arg] if a.kwarg else [])
            defaults = {x.arg: _lit(d) for x, d in zip(reversed([*a.posonlyargs, *a.args]), reversed(a.defaults))}
            defaults.update({x.arg: _lit(d) for x, d in zip(a.kwonlyargs, a.kw_defaults) if d is not None})
            doc = ast.get_docstring(node) or ""
            f["functions"].append({"name": node.name, "args": names[:24], "defaults": dict(list(defaults.items())[:12]), "line": node.lineno,
                                   "returns": _dotted(node.returns) if node.returns is not None else "",
                                   "decorators": [d for d in (_dotted(x.func if isinstance(x, ast.Call) else x) for x in node.decorator_list) if d][:4],
                                   "doc": doc[:160], "of_interest": node.name in _DEFS_OF_INTEREST})
        elif isinstance(node, ast.Attribute):
            d = _dotted(node)
            if _DTYPE.match(d):
                dtypes.add(d.split(".", 1)[1])
        elif isinstance(node, ast.Subscript) and _dotted(node.value) in ("os.environ", "environ"):
            if isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str):
                envs.add(node.slice.value[:80])
        elif isinstance(node, ast.Call):
            name = _dotted(node.func) or (node.func.attr if isinstance(node.func, ast.Attribute) else "")
            kw = _kwargs(node)
            first = [_lit(x) for x in node.args[:2]]
            rec = {"call": name[:120], "line": node.lineno, "args": first, "kwargs": kw}
            if kw.get("trust_remote_code") is True:
                f["trust_remote_code_lines"].append(node.lineno)
            recv = name.rsplit(".", 1)[0].lower() if "." in name else ""
            if name in ("os.environ.get", "os.getenv", "environ.get", "getenv") and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                envs.add(node.args[0].value[:80])
            if name.endswith(".add_argument"):
                flags = [a.value for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]
                f["cli_args"].append({"flags": flags[:3], "line": node.lineno, **{k: kw.get(k) for k in ("type", "default", "help", "choices", "required", "action", "nargs") if k in kw}})
            elif name.startswith(("gr.", "gradio.")):
                f["ui"].append({"component": name.split(".", 1)[1][:60], "line": node.lineno, "kwargs": {k: v for k, v in kw.items() if k in ("fn", "inputs", "outputs", "label", "choices", "minimum", "maximum", "value", "type")}})
            elif _LOAD.search(name):
                f["calls"]["load"].append(rec)
            elif name.endswith(".run") and any(h in recv for h in _SESSION_HINT) or _INFER.search(name) or (isinstance(node.func, ast.Name) and node.func.id in _CALLABLE_NAMES):
                f["calls"]["infer"].append(rec)
            elif _PRE.search(name):
                f["calls"]["preprocess"].append(rec)
            elif _POST.search(name):
                f["calls"]["postprocess"].append(rec)
            if name.endswith(".cuda"):
                devices.add("cuda")
            for a in [*node.args[:2], *[k.value for k in node.keywords if k.arg in ("device", "map_location", "device_map")]]:
                if isinstance(a, ast.Constant) and isinstance(a.value, str) and re.match(r"^(cuda|mps|cpu|auto)", a.value):
                    devices.add(a.value.split(":")[0])
            if name.startswith(("requests.", "httpx.", "urllib.request.")) and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                m = re.match(r"https?://([^/\s]+)", node.args[0].value)
                if m:
                    services.add(m.group(1)[:80])
    services.update(t for t in tops if t in _SDK_SERVICES and t != "huggingface_hub")
    f["top_level_modules"] = sorted(tops)
    f["env_vars"], f["device_hints"], f["dtype_hints"], f["external_services"] = sorted(envs), sorted(devices), sorted(dtypes), sorted(services)
    cut = []
    for key in ("imports", "classes", "functions", "cli_args", "ui"):
        if len(f[key]) > MAX_FACTS:
            f[key], _ = f[key][:MAX_FACTS], cut.append(key)
    for key, rows in f["calls"].items():
        if len(rows) > MAX_FACTS:
            f["calls"][key], _ = rows[:MAX_FACTS], cut.append("calls." + key)
    if cut:
        f["truncated"] = cut
    return f


# --------------------------------------------------------------------------- structured text


def summarize_json(obj: Any, path: str = "") -> Dict[str, Any]:
    name = path.rsplit("/", 1)[-1].lower()
    out: Dict[str, Any] = {"type": type(obj).__name__}
    if not isinstance(obj, dict):
        out["length"] = len(obj) if hasattr(obj, "__len__") else None
        if isinstance(obj, list):
            out["sample"] = _short(obj[:3])
        return out
    out["keys"] = [str(k)[:60] for k in list(obj)[:80]]
    for k in ("model_type", "architectures", "auto_map", "torch_dtype", "tokenizer_class", "processor_class", "feature_extractor_type",
              "image_processor_type", "_class_name", "_diffusers_version", "pipeline_tag", "library_name", "task_specific_params",
              "id2label", "num_labels", "vocab_size", "max_position_embeddings", "hidden_size", "image_size", "sampling_rate",
              "chat_template", "eos_token_id", "bos_token_id", "pad_token_id", "max_length", "do_sample", "temperature", "quantization_config",
              "trust_remote_code", "base_model_name_or_path", "peft_type", "target_modules", "task_type"):
        if k in obj:
            out.setdefault("values", {})[k] = _short(obj[k], 160)
    if name == "model_index.json":
        out["components"] = {k: _short(v) for k, v in obj.items() if isinstance(v, list) and len(v) == 2 and not k.startswith("_")}
    if isinstance(obj.get("weight_map"), dict):  # *.index.json: which shard holds which tensors
        shards: Dict[str, int] = {}
        for shard in obj["weight_map"].values():
            shards[str(shard)] = shards.get(str(shard), 0) + 1
        out["weight_map"] = {"tensors": len(obj["weight_map"]), "shards": dict(list(sorted(shards.items()))[:200])}
        out["total_size"] = _short((obj.get("metadata") or {}).get("total_size") if isinstance(obj.get("metadata"), dict) else None)
    return out


def analyze_notebook(text: str) -> Dict[str, Any]:
    try:
        nb = json.loads(text)
        cells = nb["cells"]
        assert isinstance(cells, list)
    except Exception as exc:
        return {"parse_error": f"not a readable notebook: {type(exc).__name__}"}
    code, installs, md = [], [], 0
    for c in cells:
        if not isinstance(c, dict):
            continue
        src = c.get("source")
        src = "".join(src) if isinstance(src, list) else str(src or "")
        if c.get("cell_type") == "code":
            lines = []
            for ln in src.splitlines():
                s = ln.strip()
                if re.match(r"^[!%]\s*pip\s+install", s):
                    installs.append(re.sub(r"^[!%]\s*pip\s+install\s*", "", s)[:200])
                lines.append("pass" if s.startswith(("!", "%")) else ln)
            code.append("\n".join(lines))
        elif c.get("cell_type") == "markdown":
            md += 1
    facts = analyze_python("\n\n".join(code)) if code else {"calls": {}}
    facts.update({"notebook_cells": len(cells), "code_cells": len(code), "markdown_cells": md, "pip_installs": installs[:40]})
    return facts


_FENCE = re.compile(r"^```[ \t]*([\w+-]*)[^\n]*\n(.*?)^```", re.S | re.M)


def analyze_markdown(text: str) -> Dict[str, Any]:
    heads = [m.group(0).strip()[:100] for m in re.finditer(r"^#{1,4}\s+.+$", text, re.M)][:40]
    snippets, installs, langs = [], [], {}
    for m in _FENCE.finditer(text):
        lang, body = m.group(1).lower(), m.group(2)
        langs[lang or "none"] = langs.get(lang or "none", 0) + 1
        line = text.count("\n", 0, m.start()) + 1
        for ln in body.splitlines():
            if re.match(r"^\s*[$!]?\s*(pip|pip3|uv pip|conda)\s+install\b", ln):
                installs.append(re.sub(r"^\s*[$!]?\s*", "", ln)[:200])
        if lang in ("python", "py", "python3") or (lang in ("", "none") and re.search(r"^\s*(import|from)\s+\w+", body, re.M)):
            facts = analyze_python(body)
            if "parse_error" not in facts and len(snippets) < 10 and any(facts["calls"].get(k) for k in ("load", "infer")):
                snippets.append({"line": line, "calls": {k: v[:6] for k, v in facts["calls"].items() if v}, "imports": facts["top_level_modules"][:12]})
    front = {}
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end > 0 and yaml is not None:
            try:
                data = yaml.safe_load(text[3:end])
                if isinstance(data, dict):
                    front = {k: _short(data[k], 120) for k in ("pipeline_tag", "library_name", "license", "base_model", "tags", "language", "datasets") if k in data}
            except Exception:
                front = {}
    return {"headings": heads, "code_fence_languages": langs, "usage_snippets": snippets, "install_commands": installs[:30], "front_matter": front,
            "chars": len(text)}


def analyze_other_source(text: str) -> Dict[str, Any]:
    """Regex scan for non-Python sources (no parser is available): includes/imports and top-level definitions. Marked as a scan."""
    imps = re.findall(r"^\s*(?:#include\s*[<\"]([^>\"]+)|import\s+(?:[\w*{}\s,]+\s+from\s+)?['\"]([^'\"]+)|(?:const|let|var)\s+\w+\s*=\s*require\(['\"]([^'\"]+)|use\s+([\w:]+);|import\s+\"?([\w./]+)\"?)",
                      text, re.M)
    defs = re.findall(r"^\s*(?:export\s+)?(?:async\s+)?(?:function|fn|func|def|class|struct|interface|template\s*<[^>]*>\s*\w+)\s+([A-Za-z_]\w*)", text, re.M)
    return {"scan": "regex", "includes_or_imports": sorted({next(x for x in t if x) for t in imps})[:60], "definitions": defs[:80],
            "lines": text.count("\n") + 1}


def analyze_text_file(category: str, text: str, path: str) -> Dict[str, Any]:
    if category == "python":
        return analyze_python(text)
    if category == "notebook":
        return analyze_notebook(text)
    if category == "markdown":
        return analyze_markdown(text)
    if category in ("json", "tokenizer_asset") and path.lower().endswith(".json"):
        try:
            return summarize_json(json.loads(text), path)
        except (ValueError, RecursionError):
            return {"parse_error": "not valid JSON (or truncated by the read cap)"}
    if category == "jsonl":
        rows = [ln for ln in text.splitlines() if ln.strip()]
        return {"records_in_read_window": len(rows), "first_record_keys": _first_keys(rows)}
    if category == "yaml":
        if yaml is None:
            return {"scan": "unparsed", "note": "PyYAML is not installed; YAML content is kept as text only", "head": text[:600]}
        try:
            data = yaml.safe_load(text)
            return summarize_json(data, path) if isinstance(data, (dict, list)) else {"type": type(data).__name__}
        except Exception as exc:
            return {"parse_error": f"{type(exc).__name__}: {str(exc)[:120]}"}
    if category == "toml":
        if tomllib is None:
            return {"scan": "unparsed", "head": text[:600]}
        try:
            return summarize_json(tomllib.loads(text), path)
        except Exception as exc:
            return {"parse_error": f"{type(exc).__name__}: {str(exc)[:120]}"}
    if category == "ini":
        cp = configparser.ConfigParser(interpolation=None)
        try:
            cp.read_string(text)
            return {"sections": {s: list(cp[s])[:30] for s in cp.sections()[:30]}}
        except configparser.Error as exc:
            return {"parse_error": f"{type(exc).__name__}"}
    if category == "requirements":
        from ..inspector import parse_requirements
        reqs, flags = parse_requirements(text)
        return {"requirements": reqs[:200], "flags": flags}
    if category in ("source", "shell"):
        return analyze_other_source(text)
    return {"lines": text.count("\n") + 1, "head": text[:300]}


def _first_keys(rows: List[str]) -> List[str]:
    for r in rows[:3]:
        try:
            v = json.loads(r)
            if isinstance(v, dict):
                return [str(k)[:60] for k in list(v)[:30]]
        except ValueError:
            continue
    return []


# --------------------------------------------------------------------------- magic bytes

_MAGIC = ((b"GGUF", "gguf"), (b"PK\x03\x04", "zip"), (b"PK\x05\x06", "zip (empty)"), (b"\x1f\x8b", "gzip"), (b"BZh", "bzip2"),
          (b"\xfd7zXZ\x00", "xz"), (b"7z\xbc\xaf\x27\x1c", "7z"), (b"\x7fELF", "ELF executable/library"), (b"MZ", "PE executable (Windows)"),
          (b"\xcf\xfa\xed\xfe", "Mach-O"), (b"\x89HDF\r\n\x1a\n", "HDF5 (Keras/h5)"), (b"\x93NUMPY", "NumPy .npy"), (b"TFL3", "TFLite flatbuffer"),
          (b"\x89PNG", "PNG image"), (b"\xff\xd8\xff", "JPEG image"), (b"GIF8", "GIF image"), (b"RIFF", "RIFF (WAV/WebP/AVI)"), (b"OggS", "Ogg"),
          (b"fLaC", "FLAC"), (b"ID3", "MP3"), (b"%PDF", "PDF"), (b"SQLite format 3", "SQLite database"), (b"PAR1", "Parquet"),
          (b"ARROW1", "Arrow IPC"), (b"\x00asm", "WebAssembly"), (b"dex\n", "Android DEX"), (b"\xca\xfe\xba\xbe", "Java class / Mach-O fat"))


def sniff(head: bytes) -> Dict[str, Any]:
    """Identify a file from its first bytes and say how text-like / compressed it looks. Identification only, never an inspection."""
    fmt = next((name for magic, name in _MAGIC if head.startswith(magic)), None)
    if fmt is None and len(head) >= 10 and head[8:9] == b"{":
        n = int.from_bytes(head[:8], "little")
        if 2 <= n < 1 << 32:
            fmt = "safetensors-like (8-byte length + JSON header)"
    if fmt is None and head[:1] == b"\x80" and len(head) > 1 and 2 <= head[1] <= 5:
        fmt = f"Python pickle (protocol {head[1]}) - not unpickled"
    printable = sum(1 for b in head if 32 <= b < 127 or b in (9, 10, 13)) / max(1, len(head))
    counts = [0] * 256
    for b in head:
        counts[b] += 1
    ent = -sum(c / len(head) * math.log2(c / len(head)) for c in counts if c) if head else 0.0
    if fmt is None and head.startswith(b"version https://git-lfs.github.com/spec/"):
        fmt = "Git LFS pointer (content is not on this host)"
    return {"format": fmt or ("text-like" if printable > 0.95 else "unidentified binary"), "printable_ratio": round(printable, 3),
            "entropy_bits_per_byte": round(ent, 2), "sample_bytes": len(head)}


def parse_lfs_pointer(head: bytes) -> Optional[Dict[str, Any]]:
    if not head.startswith(b"version https://git-lfs.github.com/spec/"):
        return None
    t = head[:512].decode("ascii", "replace")
    size = re.search(r"^size (\d+)$", t, re.M)
    oid = re.search(r"^oid sha256:([0-9a-f]{64})$", t, re.M)
    return {"lfs_size": int(size.group(1)) if size else None, "lfs_oid": oid.group(1) if oid else None}


# --------------------------------------------------------------------------- safetensors


def _pattern(name: str) -> str:
    return re.sub(r"\d+", "{n}", name)


def inspect_safetensors(header_json: bytes, header_len: int, total_size: Optional[int]) -> Dict[str, Any]:
    out: Dict[str, Any] = {"header_bytes": header_len}
    try:
        obj = json.loads(header_json.decode("utf-8"))
        assert isinstance(obj, dict)
    except Exception:
        return {**out, "parse_error": "the safetensors header is not valid JSON"}
    meta = obj.pop("__metadata__", None)
    if isinstance(meta, dict):
        out["metadata"] = {str(k)[:60]: _short(v, 120) for k, v in list(meta.items())[:30]}
    params, dtypes, patterns, spans, problems = 0, {}, {}, [], []
    for name, t in obj.items():
        shape, dtype, offs = (t.get("shape"), t.get("dtype"), t.get("data_offsets")) if isinstance(t, dict) else (None, None, None)
        if not (isinstance(shape, list) and isinstance(dtype, str) and isinstance(offs, list) and len(offs) == 2
                and all(isinstance(x, int) and not isinstance(x, bool) for x in [*shape, *offs])):
            problems.append(f"malformed entry for tensor {str(name)[:60]!r}")
            continue
        n = 1
        for d in shape:
            n *= max(d, 0)
        params += n
        dtypes[dtype] = dtypes.get(dtype, 0) + n
        p = patterns.setdefault(_pattern(str(name)), {"count": 0, "dtype": dtype, "example_shape": shape[:6], "example": str(name)[:100]})
        p["count"] += 1
        spans.append(tuple(offs))
    cursor = 0
    for a, b in sorted(spans):
        if a != cursor:
            problems.append("tensor data_offsets are not contiguous (gap or overlap)")
            break
        cursor = b
    if total_size is not None and spans and cursor != total_size - 8 - header_len:
        problems.append(f"the header declares {cursor} data bytes but the file holds {max(total_size - 8 - header_len, 0)} after its header")
    prefixes: Dict[str, int] = {}
    for name in obj:
        key = ".".join(str(name).split(".")[:2])
        prefixes[key] = prefixes.get(key, 0) + 1
    out.update({"tensor_count": len(spans), "parameters": params, "dtypes": dtypes, "data_bytes": cursor,
                "name_patterns": sorted(patterns.values(), key=lambda p: -p["count"])[:60], "prefix_counts": dict(sorted(prefixes.items(), key=lambda kv: -kv[1])[:40]),
                "consistent": not problems})
    if problems:
        out["problems"] = problems[:5]
    return out


# --------------------------------------------------------------------------- GGUF

_GGML = {0: "F32", 1: "F16", 2: "Q4_0", 3: "Q4_1", 6: "Q5_0", 7: "Q5_1", 8: "Q8_0", 9: "Q8_1", 10: "Q2_K", 11: "Q3_K", 12: "Q4_K", 13: "Q5_K",
         14: "Q6_K", 15: "Q8_K", 24: "I8", 25: "I16", 26: "I32", 27: "I64", 28: "F64", 30: "BF16"}
_GGUF_SCALAR = {0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i", 6: "<f", 7: "<?", 10: "<Q", 11: "<q", 12: "<d"}


class _Short(Exception):
    pass


class _Reader:
    def __init__(self, buf: bytes):
        self.b, self.i = buf, 0

    def take(self, n: int) -> bytes:
        if n < 0 or self.i + n > len(self.b):
            raise _Short()
        v = self.b[self.i:self.i + n]
        self.i += n
        return v

    def unpack(self, fmt: str) -> Any:
        return struct.unpack(fmt, self.take(struct.calcsize(fmt)))[0]

    def string(self, wide: bool) -> str:
        n = self.unpack("<Q" if wide else "<I")
        return self.take(n).decode("utf-8", "replace")


def _gguf_value(r: _Reader, t: int, wide: bool) -> Any:
    if t in _GGUF_SCALAR:
        return r.unpack(_GGUF_SCALAR[t])
    if t == 8:
        return r.string(wide)
    if t == 9:
        et, n = r.unpack("<I"), r.unpack("<Q" if wide else "<I")
        kept, n_str = [], 0
        for k in range(n):
            v = _gguf_value(r, et, wide)
            if k < 8:
                kept.append(v if not isinstance(v, str) else v[:40])
            n_str += 1
        return {"array_of": et, "length": n, "first": kept}
    raise ValueError(f"unknown GGUF value type {t}")


def inspect_gguf(buf: bytes, total_size: Optional[int]) -> Dict[str, Any]:
    """Parse a GGUF header (metadata + tensor table) from the leading bytes of the file.

    ``complete`` is False when the read window ended early (caller should fetch a larger window). When the tensor table is
    complete, ``header_end_offset`` is the exact byte offset where the (aligned) tensor data begins, ``data_offset`` is the
    alignment the file declares and ``parameters`` is the total element count of the tensor table."""
    r = _Reader(buf)
    out: Dict[str, Any] = {}
    try:
        if r.take(4) != b"GGUF":
            return {"parse_error": "missing GGUF magic"}
        ver = r.unpack("<I")
        wide = ver >= 2
        tcount, kcount = (r.unpack("<Q"), r.unpack("<Q")) if wide else (r.unpack("<I"), r.unpack("<I"))
        out.update({"version": ver, "tensor_count": tcount, "kv_count": kcount})
        if kcount > 1_000_000 or tcount > 10_000_000:
            return {**out, "parse_error": "implausible GGUF counts"}
        meta, complete = {}, True
        try:
            for _ in range(kcount):
                key, t = r.string(wide), r.unpack("<I")
                val = _gguf_value(r, t, wide)
                if len(meta) < 120:
                    meta[key[:80]] = _short(val, 120)
            out["metadata"] = meta
            out["architecture"] = meta.get("general.architecture")
            out["has_tokenizer"] = any(k.startswith("tokenizer.") for k in meta)
            out["has_chat_template"] = "tokenizer.chat_template" in meta
            types, patterns, params = {}, {}, 0
            for _ in range(tcount):
                name, nd = r.string(wide), r.unpack("<I")
                dims = [r.unpack("<Q" if wide else "<I") for _ in range(nd)]
                gt, _off = r.unpack("<I"), r.unpack("<Q")
                label = _GGML.get(gt, f"type_{gt}")
                types[label] = types.get(label, 0) + 1
                n = 1
                for d in dims:
                    n *= max(d, 0)
                params += n
                p = patterns.setdefault(_pattern(name), {"count": 0, "dtype": label, "example_shape": dims[:6], "example": name[:100]})
                p["count"] += 1
            out.update({"tensor_types": types, "name_patterns": sorted(patterns.values(), key=lambda p: -p["count"])[:60],
                        "parameters": params})
            align = meta.get("general.alignment")
            align = align if isinstance(align, int) and 0 < align <= 1 << 20 else 32
            out["data_offset"] = align
            out["header_end_offset"] = (r.i + align - 1) // align * align
        except _Short:
            complete = False
        out["complete"] = complete
    except _Short:
        return {**out, "parse_error": "the read window ended inside the GGUF preamble", "complete": False}
    except (ValueError, struct.error) as exc:
        return {**out, "parse_error": str(exc)[:120], "complete": False}
    return out


# --------------------------------------------------------------------------- ONNX (minimal protobuf wire reader)

_ONNX_DTYPE = {1: "float32", 2: "uint8", 3: "int8", 4: "uint16", 5: "int16", 6: "int32", 7: "int64", 8: "string", 9: "bool", 10: "float16", 11: "float64",
               12: "uint32", 13: "uint64", 14: "complex64", 15: "complex128", 16: "bfloat16"}


def _varint(buf: memoryview, i: int) -> Tuple[int, int]:
    shift = v = 0
    while True:
        if i >= len(buf) or shift > 63:
            raise ValueError("truncated or oversized varint")
        b = buf[i]
        i += 1
        v |= (b & 0x7F) << shift
        if not b & 0x80:
            return v, i
        shift += 7


def _pb(buf: memoryview) -> Iterator[Tuple[int, int, Any]]:
    i = 0
    while i < len(buf):
        key, i = _varint(buf, i)
        field, wire = key >> 3, key & 7
        if wire == 0:
            v, i = _varint(buf, i)
        elif wire == 2:
            n, i = _varint(buf, i)
            if i + n > len(buf):
                raise ValueError("length-delimited field runs past the buffer")
            v, i = buf[i:i + n], i + n
        elif wire in (1, 5):
            n = 8 if wire == 1 else 4
            if i + n > len(buf):
                raise ValueError("fixed field runs past the buffer")
            v, i = buf[i:i + n], i + n
        else:
            raise ValueError(f"unsupported protobuf wire type {wire}")
        yield field, wire, v


def _txt(v: Any) -> str:
    return bytes(v).decode("utf-8", "replace")[:120]


def _value_info(buf: memoryview) -> Dict[str, Any]:
    info: Dict[str, Any] = {"name": "", "dtype": None, "shape": None}
    for f, w, v in _pb(buf):
        if f == 1 and w == 2:
            info["name"] = _txt(v)
        elif f == 2 and w == 2:
            for tf, tw, tv in _pb(v):
                if tf == 1 and tw == 2:  # tensor_type
                    for xf, xw, xv in _pb(tv):
                        if xf == 1 and xw == 0:
                            info["dtype"] = _ONNX_DTYPE.get(xv, f"onnx_type_{xv}")
                        elif xf == 2 and xw == 2:
                            dims = []
                            for df, dw, dv in _pb(xv):
                                if df == 1 and dw == 2:
                                    d: Any = "?"
                                    for kf, kw, kv in _pb(dv):
                                        if kf == 1 and kw == 0:
                                            d = kv
                                        elif kf == 2 and kw == 2:
                                            d = _txt(kv)
                                    dims.append(d)
                            info["shape"] = dims[:12]
                elif tf in (4, 5, 9):
                    info["dtype"] = "non-tensor (sequence/map/optional)"
    return info


def inspect_onnx(buf: bytes) -> Dict[str, Any]:
    """Graph inputs/outputs (names, dtypes, shapes), opsets, operator histogram and initializer count of a complete ONNX file."""
    out: Dict[str, Any] = {"opsets": []}
    try:
        mv = memoryview(buf)
        ops: Dict[str, int] = {}
        inits, inputs, outputs, ext = set(), [], [], False
        n_init = 0
        for f, w, v in _pb(mv):
            if f == 1 and w == 0:
                out["ir_version"] = v
            elif f == 2 and w == 2:
                out["producer"] = _txt(v)
            elif f == 3 and w == 2:
                out["producer_version"] = _txt(v)
            elif f == 8 and w == 2:
                dom, ver = "", None
                for of, ow, ov in _pb(v):
                    if of == 1 and ow == 2:
                        dom = _txt(ov)
                    elif of == 2 and ow == 0:
                        ver = ov
                out["opsets"].append({"domain": dom or "ai.onnx", "version": ver})
            elif f == 7 and w == 2:
                for gf, gw, gv in _pb(v):
                    if gf == 1 and gw == 2:
                        for nf, nw, nv in _pb(gv):
                            if nf == 4 and nw == 2:
                                op = _txt(nv)
                                ops[op] = ops.get(op, 0) + 1
                    elif gf == 2 and gw == 2:
                        out["graph_name"] = _txt(gv)
                    elif gf == 5 and gw == 2:
                        n_init += 1
                        for tf, tw, tv in _pb(gv):
                            if tf == 8 and tw == 2:
                                inits.add(_txt(tv))
                            elif tf in (13, 14) and tw in (0, 2):
                                ext = ext or tf == 13 or tv == 1
                    elif gf == 11 and gw == 2:
                        inputs.append(_value_info(gv))
                    elif gf == 12 and gw == 2:
                        outputs.append(_value_info(gv))
        out.update({"inputs": [i for i in inputs if i["name"] not in inits][:40], "outputs": outputs[:40], "initializer_count": n_init,
                    "operators": dict(sorted(ops.items(), key=lambda kv: -kv[1])[:40]), "node_count": sum(ops.values()),
                    "uses_external_data": ext})
    except (ValueError, RecursionError, MemoryError) as exc:
        out["parse_error"] = f"{type(exc).__name__}: {str(exc)[:120]}"
    return out


# --------------------------------------------------------------------------- pickle (opcode scan only) and zip directories

_BENIGN_PICKLE = ("torch.", "collections.", "numpy.", "_codecs.", "copyreg.", "builtins.set", "builtins.dict", "builtins.list", "builtins.tuple",
                  "builtins.slice", "builtins.complex", "builtins.bytearray", "builtins.frozenset", "numpy.core.", "numpy._core.")
_DANGEROUS_MODULES = {"os", "posix", "nt", "subprocess", "sys", "socket", "shutil", "runpy", "importlib", "pty", "ctypes", "webbrowser", "pickle",
                      "requests", "urllib", "http", "commands", "popen2", "asyncio", "multiprocessing", "code", "codeop", "marshal", "types"}
_DANGEROUS_BUILTINS = {"eval", "exec", "compile", "__import__", "open", "getattr", "setattr", "input", "breakpoint"}
_STR_OPS = {"UNICODE", "BINUNICODE", "SHORT_BINUNICODE", "BINUNICODE8", "STRING", "BINSTRING", "SHORT_BINSTRING"}


def scan_pickle(data: bytes, max_ops: int = 400_000) -> Dict[str, Any]:
    """Walk the opcodes of a pickle with ``pickletools.genops`` (a parser: it never executes an opcode) and report which globals it would
    import, how many call opcodes it holds and which dictionary keys / strings it contains. Nothing is unpickled."""
    globs, strings, calls, protocol, last_strs, n, err = [], [], 0, None, [], 0, None
    try:
        for op, arg, _pos in pickletools.genops(data):
            n += 1
            if n > max_ops:
                err = "opcode budget reached"
                break
            if op.name == "PROTO":
                protocol = arg
            elif op.name in _STR_OPS:
                strings.append(arg)
                last_strs = (last_strs + [arg])[-2:]
            elif op.name == "GLOBAL":
                globs.append(str(arg).replace(" ", ".", 1))
            elif op.name == "STACK_GLOBAL" and len(last_strs) == 2:
                globs.append(f"{last_strs[0]}.{last_strs[1]}")
            elif op.name in ("REDUCE", "BUILD", "INST", "OBJ", "NEWOBJ", "NEWOBJ_EX"):
                calls += 1
            elif op.name == "STOP":
                break
    except Exception as exc:  # ValueError for truncated/invalid streams
        err = f"{type(exc).__name__}: {str(exc)[:100]}"
    uniq = sorted(set(globs))
    danger = [g for g in uniq if g.split(".")[0] in _DANGEROUS_MODULES or (g.startswith("builtins.") and g.split(".", 1)[1] in _DANGEROUS_BUILTINS)]
    custom = [g for g in uniq if not g.startswith(_BENIGN_PICKLE) and g not in danger]
    keys = [s for s in strings if re.fullmatch(r"[\w.\-/]{3,120}", s) and "." in s][:400]
    pats: Dict[str, int] = {}
    for k in keys:
        pats[_pattern(k)] = pats.get(_pattern(k), 0) + 1
    return {"protocol": protocol, "opcodes_scanned": n, "globals": uniq[:100], "dangerous_globals": danger[:30], "custom_globals": custom[:60],
            "call_opcodes": calls, "string_count": len(strings), "key_patterns": dict(sorted(pats.items(), key=lambda kv: -kv[1])[:40]),
            "executes_code_when_loaded": bool(calls), "scan_error": err, "unpickled": False}


def find_eocd(tail: bytes) -> Optional[Dict[str, int]]:
    i = tail.rfind(b"PK\x05\x06")
    if i < 0 or len(tail) - i < 22:
        return None
    entries, cd_size, cd_off = struct.unpack("<HII", tail[i + 10:i + 20])
    z64 = tail.rfind(b"PK\x06\x07", 0, i)
    zip64 = None
    if z64 >= 0 and len(tail) - z64 >= 20:
        zip64 = struct.unpack("<Q", tail[z64 + 8:z64 + 16])[0]
    return {"entries": entries, "cd_size": cd_size, "cd_offset": cd_off, "eocd_pos_in_tail": i, "zip64_eocd_offset": zip64 or 0}


def parse_zip64_eocd(blob: bytes) -> Optional[Dict[str, int]]:
    if len(blob) < 56 or blob[:4] != b"PK\x06\x06":
        return None
    entries, cd_size, cd_off = struct.unpack("<QQQ", blob[32:56])
    return {"entries": entries, "cd_size": cd_size, "cd_offset": cd_off}


def parse_central_directory(cd: bytes, limit: int = 20_000) -> List[Dict[str, Any]]:
    members, i = [], 0
    while i + 46 <= len(cd) and cd[i:i + 4] == b"PK\x01\x02" and len(members) < limit:
        method, = struct.unpack("<H", cd[i + 10:i + 12])
        csize, usize, nlen, xlen, clen = struct.unpack("<IIHHH", cd[i + 20:i + 22 + 8 + 2] if False else cd[i + 20:i + 24] + cd[i + 24:i + 28] + cd[i + 28:i + 34])
        off, = struct.unpack("<I", cd[i + 42:i + 46])
        name = cd[i + 46:i + 46 + nlen].decode("utf-8", "replace")
        members.append({"name": name, "method": method, "compressed": csize, "size": usize, "offset": off})
        i += 46 + nlen + xlen + clen
    return members


def summarize_zip_members(members: List[Dict[str, Any]]) -> Dict[str, Any]:
    names = [m["name"] for m in members]
    pkl = next((m for m in members if m["name"].endswith("data.pkl")), None)
    storage = [n for n in names if "/data/" in n and not n.endswith(("/", ".pkl"))]
    return {"members": len(members), "total_uncompressed": sum(m["size"] for m in members),
            "torch_zip_checkpoint": pkl is not None, "has_pickle_member": any(n.endswith(".pkl") for n in names),
            "storage_entries": len(storage), "npy_members": sum(1 for n in names if n.endswith(".npy")),
            "member_names_sample": names[:40], "data_pkl": {"name": pkl["name"], "size": pkl["size"]} if pkl else None}


def read_zip_member(blob: bytes, method: int, compressed: int, cap: int) -> Optional[bytes]:
    """Bytes of one zip member from [local header + data]; None when the layout is not understood. Decompression is capped."""
    if len(blob) < 30 or blob[:4] != b"PK\x03\x04":
        return None
    nlen, xlen = struct.unpack("<HH", blob[26:30])
    data = blob[30 + nlen + xlen:30 + nlen + xlen + compressed]
    if method == 0:
        return data[:cap]
    if method == 8:
        d = zlib.decompressobj(-15)
        return d.decompress(data, cap)
    return None
