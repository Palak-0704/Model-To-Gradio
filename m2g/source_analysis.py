"""Deep, static source analysis for a repository: module graph, call graph, loading sites and an I/O contract.

This is the repository-intelligence layer the Analyzer was missing. It is deliberately separate from
:mod:`m2g.evidence.repo_inspectors` (which parses ONE file) because it works across the whole repository: it resolves
imports between the repository's own modules, relates the functions that call each other, finds the model-loading and
inference sites, and reconstructs an input/output contract with provenance.

Everything here is static and side-effect-free. Repository text is parsed with the standard-library ``ast`` module; no
repository module is ever imported, executed or installed, and no weight is downloaded. The per-file facts come from the
already-bounded :func:`m2g.evidence.repo_inspectors.analyze_python` so there is exactly one Python scanner in the project.

Resolution states are explicit and never faked: an import is ``resolved_local`` only when it names a file that really is in
the repository, ``stdlib`` only when the name is in ``sys.stdlib_module_names``, and ``unresolved`` otherwise. A call edge
is ``resolved_local`` only when its target is a symbol this analysis actually found; anything else stays ``possible`` or
``unresolved``. The I/O contract carries the same discipline: an input or output is recorded only with the evidence that
produced it, and everything not established is listed under ``unknown``.
"""
from __future__ import annotations


import re
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from .evidence.repo_inspectors import analyze_python

# --------------------------------------------------------------------------- budgets (bounded by default; overridable by the caller)

MAX_MODULES = 60  # repository modules parsed per run
MAX_MODULE_BYTES = 400_000  # one module larger than this is recorded as skipped, never parsed
MAX_CALL_EDGES = 400
MAX_LOAD_SITES = 40
MAX_IMPORT_EDGES = 600
MAX_FACTS = 200

_STDLIB = frozenset(getattr(sys, "stdlib_module_names", ()))

# Import roots that identify a third-party ML stack. Used only to label an import edge, never to decide support.
_KNOWN_STACK = frozenset({
    "torch", "torchvision", "torchaudio", "tensorflow", "tf_keras", "keras", "jax", "flax", "numpy", "scipy", "sklearn",
    "transformers", "diffusers", "sentence_transformers", "sentencepiece", "tokenizers", "datasets", "accelerate", "peft",
    "bitsandbytes", "onnxruntime", "onnx", "cv2", "PIL", "pillow", "soundfile", "librosa", "torchaudio", "spacy", "gensim",
    "timm", "ultralytics", "safetensors", "gradio", "huggingface_hub", "einops", "triton", "xgboost", "lightgbm", "catboost",
    "open_clip", "clip", "sentence_transformers", "fastai", "paddle", "paddlepaddle", "mxnet", "theano", "pytorch_lightning",
})

# Loading-call signatures (framework, family). A call name is matched with ``re.search`` against the dotted call name.
_LOAD_SIGNATURES: Tuple[Tuple[str, str, "re.Pattern[str]"], ...] = (
    ("transformers", "from_pretrained", re.compile(r"\.from_pretrained$")),
    ("transformers", "pipeline", re.compile(r"(^|\.)pipeline$")),
    ("transformers", "from_config", re.compile(r"\.from_config$")),
    ("diffusers", "from_single_file", re.compile(r"\.from_single_file$")),
    ("diffusers", "from_pretrained", re.compile(r"\.from_pretrained$")),
    ("sentence-transformers", "SentenceTransformer", re.compile(r"(^|\.)SentenceTransformer$")),
    ("onnxruntime", "InferenceSession", re.compile(r"(^|\.)InferenceSession$")),
    ("pytorch", "torch.load", re.compile(r"^torch\.load$")),
    ("pytorch", "load_state_dict", re.compile(r"\.load_state_dict$")),
    ("tensorflow", "load_model", re.compile(r"(^|\.)load_model$")),
    ("tensorflow", "keras.models.load_model", re.compile(r"^keras\.models\.load_model$")),
    ("peft", "PeftModel.from_pretrained", re.compile(r"(^|\.)PeftModel\.from_pretrained$")),
    ("llama.cpp", "Llama", re.compile(r"(^|\.)Llama$")),
    ("llama.cpp", "gguf", re.compile(r"(^|\.)gguf\.")),
)

# Inference-call signatures (how a model is invoked).
_INFER_SIGNATURES: Tuple[Tuple[str, "re.Pattern[str]"], ...] = (
    ("generate", re.compile(r"\.generate$")),
    ("forward", re.compile(r"\.forward$")),
    ("predict", re.compile(r"\.predict$")),
    ("predict_proba", re.compile(r"\.predict_proba$")),
    ("transcribe", re.compile(r"\.transcribe$")),
    ("synthesize", re.compile(r"\.synthesize$")),
    ("detect", re.compile(r"\.detect$")),
    ("encode", re.compile(r"\.encode$")),
    ("embed", re.compile(r"\.embed")),
    ("__call__", re.compile(r"^(model|net|network|pipe|pipeline|session|sess|engine|predictor|detector|generator|self\.model|self\.net)$")),
)

# Postprocessing signatures.
_POST_SIGNATURES: Tuple[str, ...] = (
    "softmax", "argmax", "sigmoid", "topk", "batch_decode", "decode", "nms", "non_max_suppression", "detokenize", "top_k",
)
# What a postprocessing call turns the model output into: a label/class, text, boxes, or a probability score. Only a mapping
# with real evidence is used; an unrecognised call contributes nothing.
_OUTPUT_KIND: Dict[str, str] = {
    "argmax": "label", "topk": "label", "top_k": "label", "nms": "boxes", "non_max_suppression": "boxes",
    "batch_decode": "text", "decode": "text", "detokenize": "text",
    "softmax": "scores", "sigmoid": "scores",
}

# Tensor/array-producing preprocessing signatures (modality hints only).
_PRE_MODALITY: Tuple[Tuple[str, "re.Pattern[str]"], ...] = (
    ("image", re.compile(r"(image|img|pixel|cv2|vision|vae|resize|ToTensor|Normalize|CenterCrop|Compose)")),
    ("audio", re.compile(r"(audio|speech|wave|wav|mel|spectrogram|soundfile|librosa|sample_rate|sampling_rate)")),
    ("text", re.compile(r"(text|token|prompt|input_ids|attention_mask|encode|decode|tokenizer)")),
)


@dataclass(frozen=True)
class ModuleRef:
    """One repository module: its path and the facts a static parse produced for it."""

    path: str
    module: str  # dotted module name derived from the path (``pkg/mod.py`` -> ``pkg.mod``)
    is_package: bool
    facts: Mapping[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {"path": self.path, "module": self.module, "is_package": self.is_package}


@dataclass
class ImportEdge:
    """A resolved (or explicitly unresolved) import edge between a module and a target."""

    source: str
    target: str
    state: str  # resolved_local | relative_local | stdlib | third_party | unresolved
    line: int
    names: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {"source": self.source, "target": self.target, "state": self.state, "line": self.line, "names": list(self.names)}


@dataclass
class CallEdge:
    """A call relationship, tagged with how it was established (never stronger than the evidence allows)."""

    caller: str  # "module:function" or "module:<module>"
    callee: str  # dotted call name as written
    kind: str  # resolved_local | direct | possible | inferred | unresolved
    line: int
    role: str = "call"  # call | load | infer | preprocess | postprocess

    def to_dict(self) -> Dict[str, Any]:
        return {"caller": self.caller, "callee": self.callee, "kind": self.kind, "line": self.line, "role": self.role}


@dataclass
class LoadSite:
    """A model-loading site found statically, with the arguments the code literally passes."""

    module: str
    line: int
    call: str
    framework: str
    family: str
    model_id: Optional[str]
    args: List[Any]
    kwargs: Mapping[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {"module": self.module, "line": self.line, "call": self.call, "framework": self.framework, "family": self.family,
                "model_id": self.model_id, "args": list(self.args), "kwargs": dict(self.kwargs)}


@dataclass
class SourceAnalysis:
    """The repository-wide static analysis: modules, graphs, loading sites and the evidence-backed I/O contract."""

    modules: List[ModuleRef] = field(default_factory=list)
    import_edges: List[ImportEdge] = field(default_factory=list)
    call_edges: List[CallEdge] = field(default_factory=list)
    load_sites: List[LoadSite] = field(default_factory=list)
    infer_sites: List[Dict[str, Any]] = field(default_factory=list)
    entrypoints: List[Dict[str, Any]] = field(default_factory=list)
    frameworks: List[str] = field(default_factory=list)
    io_contract: Dict[str, Any] = field(default_factory=dict)
    unresolved_imports: List[str] = field(default_factory=list)
    skipped: List[Dict[str, Any]] = field(default_factory=list)
    coverage: Dict[str, Any] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "modules": [m.to_dict() for m in self.modules],
            "import_edges": [e.to_dict() for e in self.import_edges],
            "call_edges": [e.to_dict() for e in self.call_edges],
            "load_sites": [s.to_dict() for s in self.load_sites],
            "infer_sites": [dict(s) for s in self.infer_sites],
            "entrypoints": [dict(e) for e in self.entrypoints],
            "frameworks": list(self.frameworks),
            "io_contract": dict(self.io_contract),
            "unresolved_imports": list(self.unresolved_imports),
            "skipped": [dict(s) for s in self.skipped],
            "coverage": dict(self.coverage),
            "notes": list(self.notes),
        }


# --------------------------------------------------------------------------- module naming / selection


def module_name_for(path: str) -> Tuple[str, bool]:
    """Dotted module name for a repository path, and whether it is a package ``__init__``. Never raises."""
    p = path.strip("/")
    is_pkg = p.endswith("/__init__.py") or p == "__init__.py"
    if p.endswith(".py"):
        p = p[:-3]
    if p.endswith("/__init__"):
        p = p[: -len("/__init__")]
    elif p == "__init__":
        p = ""
    parts = [seg for seg in p.split("/") if seg and seg != "."]
    return ".".join(parts), is_pkg


def _looks_like_source(path: str) -> bool:
    return path.endswith((".py", ".pyi"))


def select_modules(paths: Sequence[Tuple[str, Optional[int]]], *, limit: int = MAX_MODULES) -> Tuple[List[str], List[Dict[str, Any]]]:
    """Choose the repository's own Python modules worth analysing, shallowest/entry-point first. Returns (paths, skipped)."""
    scored: List[Tuple[int, str, Optional[int]]] = []
    skipped: List[Dict[str, Any]] = []
    for path, size in paths:
        low = path.lower()
        if not _looks_like_source(path):
            continue
        if any(seg in low.split("/") for seg in ("tests", "test", "docs", "examples", "example", "scripts", "venv", ".venv",
                                                 "node_modules", "build", "dist", "third_party", "vendor", "site-packages")):
            # not skipped silently: recorded, but not analysed (they are not the model implementation)
            continue
        if size is not None and size > MAX_MODULE_BYTES:
            skipped.append({"path": path, "size": size, "reason": "larger than the per-module analysis cap"})
            continue
        depth = path.count("/")
        bonus = 0
        base = path.rsplit("/", 1)[-1].lower()
        if base in ("model.py", "models.py", "modeling.py", "inference.py", "predict.py", "detect.py", "main.py", "app.py",
                    "run.py", "pipeline.py", "hubconf.py", "demo.py", "infer.py"):
            bonus -= 6  # conventional implementation files first
        if base == "__init__.py":
            bonus += 3
        scored.append((depth + bonus, path, size))
    chosen = [p for _s, p, _z in sorted(scored)[:limit]]
    if len(scored) > limit:
        for _s, p, size in sorted(scored)[limit:]:
            skipped.append({"path": p, "size": size, "reason": "module-count budget exhausted"})
    return chosen, skipped


# --------------------------------------------------------------------------- import resolution


def _resolve_import(source_module: str, is_package: bool, module: str, level: int,
                    module_index: Mapping[str, str], packages: Set[str]) -> Tuple[str, str]:
    """Resolve one import to (target, state). ``state`` is explicit and never claims a resolution that did not happen."""
    if level and level > 0:  # relative import
        base_parts = source_module.split(".")[:-1] if not is_package else source_module.split(".")
        up = level - 1
        if up:
            base_parts = base_parts[:-up] if up <= len(base_parts) else []
        tail = [p for p in (module or "").split(".") if p]
        target = ".".join([*base_parts, *tail])
        if target in module_index or target in packages:
            return target, "relative_local"
        return (target or "."), "unresolved"
    root = (module or "").split(".")[0]
    if module and (module in module_index or module in packages):
        return module, "resolved_local"
    # A package directory may be local even when its ``__init__.py`` was not fetched (namespace package or budget): treat the
    # root as local when any analysed module lives under it.
    root_is_local = root in module_index or root in packages or any(k.startswith(root + ".") for k in module_index)
    if root_is_local:
        # The top package is local but the full dotted path is not a module we saw: keep the specific target and say so.
        return (module, "unresolved") if module and "." in module else (root, "resolved_local")
    if root in _STDLIB:
        return root, "stdlib"
    if root in _KNOWN_STACK:
        return root, "third_party"
    if not root:
        return module or "?", "unresolved"
    return root, "unresolved"


def _module_graph(modules: List[ModuleRef]) -> Tuple[List[ImportEdge], List[str], List[str]]:
    module_index = {m.module: m.path for m in modules if m.module}
    packages = {m.module for m in modules if m.is_package}
    edges: List[ImportEdge] = []
    unresolved: List[str] = []
    third_party: Set[str] = set()
    for m in modules:
        for imp in m.facts.get("imports") or []:
            module = str(imp.get("module") or "")
            level = int(imp.get("level") or 0)
            target, state = _resolve_import(m.module, m.is_package, module, level, module_index, packages)
            names = [str(n) for n in (imp.get("names") or [])][:20]
            edges.append(ImportEdge(m.module, target, state, int(imp.get("line") or 0), names))
            if state == "unresolved":
                unresolved.append(f"{m.module}: {module or '.'} (line {imp.get('line')})")
            elif state == "third_party":
                third_party.add(target)
    return edges[:MAX_IMPORT_EDGES], sorted(set(unresolved))[:MAX_FACTS], sorted(third_party)


# --------------------------------------------------------------------------- call graph


def _defined_symbols(modules: List[ModuleRef]) -> Dict[str, Set[str]]:
    """module -> the top-level symbol names it defines (functions, classes, assigned constants)."""
    out: Dict[str, Set[str]] = {}
    for m in modules:
        names: Set[str] = set()
        for fn in m.facts.get("functions") or []:
            if isinstance(fn, dict) and fn.get("name"):
                names.add(str(fn["name"]))
        for cl in m.facts.get("classes") or []:
            if isinstance(cl, dict) and cl.get("name"):
                names.add(str(cl["name"]))
        out[m.module] = names
    return out


def _call_graph(modules: List[ModuleRef], import_edges: List[ImportEdge]) -> Tuple[List[CallEdge], List[Dict[str, Any]], List[str]]:
    """Call edges plus the inference sites, and the set of frameworks implied by loading/inference call names."""
    # imported local symbols: module -> {symbol: origin_module}
    imported_local: Dict[str, Dict[str, str]] = {}
    for e in import_edges:
        if e.state in ("resolved_local", "relative_local"):
            for n in e.names:
                imported_local.setdefault(e.source, {})[n] = e.target
    defined = _defined_symbols(modules)
    edges: List[CallEdge] = []
    infer_sites: List[Dict[str, Any]] = []
    frameworks: Set[str] = set()
    for m in modules:
        local_names = defined.get(m.module, set())
        imported = imported_local.get(m.module, {})
        for role, rows in (m.facts.get("calls") or {}).items():
            for row in rows or []:
                if not isinstance(row, dict):
                    continue
                call = str(row.get("call") or "")
                line = int(row.get("line") or 0)
                root = call.split(".")[0]
                if call in local_names or root in local_names or root in imported:
                    kind = "resolved_local"
                elif root:
                    kind = "possible"
                else:
                    kind = "unresolved"
                if call:
                    edges.append(CallEdge(f"{m.module}:<module>", call, kind, line, role))
                if role == "load":
                    sig = next(((fw for fw, _fam, rx in _LOAD_SIGNATURES if rx.search(call))), None)
                    if sig:
                        frameworks.add(sig)
                if role == "infer":
                    fam = next((name for name, rx in _INFER_SIGNATURES if rx.search(call)), None)
                    if fam:
                        infer_sites.append({"module": m.module, "line": line, "call": call, "kind": fam,
                                            "args": list(row.get("args") or []), "kwargs": dict(row.get("kwargs") or {})})
    return edges[:MAX_CALL_EDGES], infer_sites[:MAX_FACTS], sorted(frameworks)


# --------------------------------------------------------------------------- loading sites


def _load_sites(modules: List[ModuleRef]) -> Tuple[List[LoadSite], List[str]]:
    sites: List[LoadSite] = []
    frameworks: Set[str] = set()
    for m in modules:
        for row in (m.facts.get("calls") or {}).get("load") or []:
            if not isinstance(row, dict):
                continue
            call = str(row.get("call") or "")
            sig = next(((fw, fam) for fw, fam, rx in _LOAD_SIGNATURES if rx.search(call)), None)
            if sig is None:
                continue
            fw, fam = sig
            frameworks.add(fw)
            args = list(row.get("args") or [])
            kwargs = dict(row.get("kwargs") or {})
            model_id = next((a for a in args if isinstance(a, str) and re.fullmatch(r"[\w.\-]+/[\w.\-]+", a)), None)
            if model_id is None:
                model_id = next((v for k, v in kwargs.items() if k in ("pretrained_model_name_or_path", "model_name_or_path",
                                                                       "model_id", "model", "repo_id") and isinstance(v, str)), None)
            sites.append(LoadSite(m.module, int(row.get("line") or 0), call, fw, fam, model_id, args, kwargs))
    return sites[:MAX_LOAD_SITES], sorted(frameworks)


def _entrypoints(modules: List[ModuleRef]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for m in modules:
        for fn in m.facts.get("functions") or []:
            if not isinstance(fn, dict):
                continue
            name = str(fn.get("name") or "")
            if fn.get("of_interest") or name in ("main", "run", "predict", "infer", "generate", "detect", "transcribe", "__call__"):
                out.append({"module": m.module, "function": name, "line": int(fn.get("line") or 0),
                            "args": [str(a) for a in (fn.get("args") or [])][:16],
                            "decorators": [str(d) for d in (fn.get("decorators") or [])][:4],
                            "returns": str(fn.get("returns") or "")})
    return out[:MAX_FACTS]


# --------------------------------------------------------------------------- I/O contract


def _modality_of(text: str) -> Optional[str]:
    low = text.lower()
    for modality, rx in _PRE_MODALITY:
        if rx.search(low):
            return modality
    return None


def _io_contract(modules: List[ModuleRef], load_sites: List[LoadSite], infer_sites: List[Dict[str, Any]],
                 entrypoints: List[Dict[str, Any]]) -> Dict[str, Any]:
    """An evidence-backed input/output contract. Each field names the evidence that produced it; nothing is invented.

    The contract never asserts an input or output merely because a model task or architecture name suggests one: it records
    what the code and metadata actually show (a ``predict(self, image)`` signature, a ``tokenizer(...)`` preprocessing call,
    a ``softmax`` postprocessing call) and lists everything else under ``unknown``."""
    inputs: List[Dict[str, Any]] = []
    outputs: List[Dict[str, Any]] = []
    preprocessing: List[Dict[str, Any]] = []
    postprocessing: List[Dict[str, Any]] = []
    unknown: List[str] = []

    def add_input(modality: str, source: str, detail: str, line: int = 0) -> None:
        if any(i["modality"] == modality for i in inputs):
            return
        inputs.append({"modality": modality, "source": source, "detail": detail[:160], "line": line})

    # 1. From the signature of an inference entry point (direct, structural evidence).
    for ep in entrypoints:
        if ep["function"] in ("predict", "infer", "inference", "forward", "detect", "transcribe", "__call__", "generate", "run"):
            for arg in ep.get("args") or []:
                if arg in ("self", "cls", "kwargs", "args"):
                    continue
                mod = _modality_of(arg)
                if mod:
                    add_input(mod, f"{ep['module']}:{ep['function']}()", f"parameter '{arg}'", ep.get("line", 0))

    # 2. From preprocessing calls (what the code really transforms).
    for m in modules:
        for row in (m.facts.get("calls") or {}).get("preprocess") or []:
            if not isinstance(row, dict):
                continue
            call = str(row.get("call") or "")
            mod = _modality_of(call) or _modality_of(" ".join(str(a) for a in (row.get("args") or [])))
            preprocessing.append({"call": call, "module": m.module, "line": int(row.get("line") or 0), "modality": mod})
            if mod:
                add_input(mod, f"{m.module}:{call}()", "preprocessing call", int(row.get("line") or 0))

    # 3. From postprocessing calls (what the code turns the model output into).
    for m in modules:
        for row in (m.facts.get("calls") or {}).get("postprocess") or []:
            if not isinstance(row, dict):
                continue
            call = str(row.get("call") or "")
            kind = next((k for k in _POST_SIGNATURES if k in call), "postprocess")
            postprocessing.append({"call": call, "module": m.module, "line": int(row.get("line") or 0), "kind": kind})
            out_kind = _OUTPUT_KIND.get(kind)
            if out_kind and not any(o["kind"] == out_kind for o in outputs):
                outputs.append({"kind": out_kind, "source": f"{m.module}:{call}()", "detail": f"{kind} postprocessing call",
                                "line": int(row.get("line") or 0)})
        for row in (m.facts.get("calls") or {}).get("infer") or []:
            if not isinstance(row, dict):
                continue
            call = str(row.get("call") or "")
            if "generate" in call or "decode" in call:
                if not any(o["kind"] == "text" for o in outputs):
                    outputs.append({"kind": "text", "source": f"{m.module}:{call}()", "detail": "generation/decoding call",
                                    "line": int(row.get("line") or 0)})

    if not inputs:
        unknown.append("input fields (no inference signature or preprocessing call established one statically)")
    if not outputs:
        unknown.append("output fields (no postprocessing or decoding call established one statically)")
    if not load_sites:
        unknown.append("model loading (no recognised loading call was found in the analysed modules)")
    if not infer_sites:
        unknown.append("inference call (no recognised model invocation was found in the analysed modules)")

    return {
        "inputs": inputs[:12],
        "outputs": outputs[:12],
        "preprocessing": preprocessing[:20],
        "postprocessing": postprocessing[:20],
        "load_frameworks": sorted({s.framework for s in load_sites}),
        "model_ids": sorted({s.model_id for s in load_sites if s.model_id}),
        "device_hints": sorted({str(d) for m in modules for d in (m.facts.get("device_hints") or [])}),
        "dtype_hints": sorted({str(d) for m in modules for d in (m.facts.get("dtype_hints") or [])}),
        "unknown": unknown,
        "contract_verified": bool(inputs and outputs),
    }


# --------------------------------------------------------------------------- entry point


def analyze_repository(files: Sequence[Tuple[str, str]], *, listed: Optional[Sequence[Tuple[str, Optional[int]]]] = None,
                       limits: Optional[Mapping[str, int]] = None) -> SourceAnalysis:
    """Analyse a repository's Python source statically.

    ``files`` is ``[(path, text)]`` for the modules the caller already fetched (nothing is fetched here). ``listed`` is the
    full repository file listing, used only to report coverage and to resolve which local modules exist even when they were
    not fetched. Nothing is imported, executed or installed; a module that does not parse is recorded as ``skipped`` and the
    analysis continues."""
    max_modules = int((limits or {}).get("max_modules", MAX_MODULES))
    listed = listed or []
    skipped: List[Dict[str, Any]] = []
    modules: List[ModuleRef] = []
    for path, text in files[:max_modules]:
        if not isinstance(text, str):
            skipped.append({"path": path, "reason": "content unavailable"})
            continue
        facts = analyze_python(text)
        if facts.get("parse_error"):
            skipped.append({"path": path, "reason": f"did not parse: {facts['parse_error']}"})
            continue
        mod, is_pkg = module_name_for(path)
        modules.append(ModuleRef(path, mod, is_pkg, facts))
    if len(files) > max_modules:
        for path, _ in files[max_modules:]:
            skipped.append({"path": path, "reason": "module-count budget exhausted"})

    import_edges, unresolved, third_party = _module_graph(modules)
    call_edges, infer_sites, call_frameworks = _call_graph(modules, import_edges)
    load_sites, load_frameworks = _load_sites(modules)
    entrypoints = _entrypoints(modules)
    io_contract = _io_contract(modules, load_sites, infer_sites, entrypoints)
    frameworks = sorted({*load_frameworks, *call_frameworks})

    analysed = {m.path for m in modules}
    skipped_paths = {s.get("path") for s in skipped}
    source_paths = [p for p, _ in listed if _looks_like_source(p)]
    not_analysed = [p for p in source_paths if p not in analysed and p not in skipped_paths]
    coverage = {
        "modules_listed": len(source_paths),
        "modules_analysed": len(modules),
        "modules_skipped": len(skipped),
        "modules_not_analysed": len(not_analysed),
        "complete": not not_analysed and not skipped,
        "not_analysed": sorted(not_analysed)[:MAX_FACTS],
    }
    notes: List[str] = []
    if third_party:
        notes.append("third-party imports (declared, not installed): " + ", ".join(third_party[:12]))
    if unresolved:
        notes.append(f"{len(unresolved)} import(s) could not be resolved to a local module or a standard-library module")
    return SourceAnalysis(modules=modules, import_edges=import_edges, call_edges=call_edges, load_sites=load_sites,
                          infer_sites=infer_sites, entrypoints=entrypoints, frameworks=frameworks, io_contract=io_contract,
                          unresolved_imports=unresolved, skipped=skipped, coverage=coverage, notes=notes)
