"""Read-only repository inspection for Hugging Face models and GitHub repositories.

Only metadata and a fixed allow-list of small text files are fetched. Model weights are never downloaded,
and nothing fetched is ever imported, executed or evaluated.
"""
from __future__ import annotations

import ast
import configparser
import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .config import Settings
from .security import (
    FetchError,
    HTTPStatusError,
    NetworkError,
    SafeFetcher,
    SecurityError,
    SourceRef,
    parse_source_url,
    redact_secrets,
    validate_repo_id,
    validate_revision,
)

try:  # Python 3.11+
    import tomllib  # type: ignore
except ImportError:  # pragma: no cover
    tomllib = None  # type: ignore


class InspectionError(RuntimeError):
    """User-facing inspection failure (message is safe to display).

    ``code`` is an optional stable identifier of the failure (for example ``repository_not_found``) for callers that must react to
    the kind of failure rather than parse its message; it is None for errors that have not been given one."""

    def __init__(self, message: str = "", code: Optional[str] = None) -> None:
        super().__init__(message)
        self.code = code


WEIGHT_EXTS = (".safetensors", ".bin", ".pt", ".pth", ".ckpt", ".gguf", ".onnx", ".h5", ".msgpack", ".tflite",
               ".pkl", ".pickle", ".npz", ".pb", ".ot", ".keras", ".mlmodel")
PICKLE_EXTS = (".bin", ".pt", ".pth", ".ckpt", ".pkl", ".pickle")
SAFETENSORS_INDEX_SUFFIX = ".safetensors.index.json"
PICKLE_INDEX_SUFFIXES = (".bin.index.json", ".pt.index.json", ".pth.index.json", ".ckpt.index.json")
# The ONLY files ever fetched from a Hugging Face repo. Weight files can never be requested.
HF_FETCH_ALLOWLIST = ("config.json", "model_index.json", "tokenizer_config.json", "tokenizer.json", "vocab.json", "merges.txt",
                      "adapter_config.json", "preprocessor_config.json", "processor_config.json", "generation_config.json",
                      "requirements.txt", "pyproject.toml", "README.md")
GH_FETCH_CANDIDATES = ("requirements.txt", "pyproject.toml", "environment.yml", "setup.py", "setup.cfg")
# Small JSON files of a GitHub repository that describe a model (file name -> RepoInfo attribute). They are read as text and parsed as data
# only, never executed; the shallowest copy (at most one directory deep) of each name is used.
GH_MODEL_CONFIG_FILES = {"config.json": "config", "model_index.json": "model_index", "preprocessor_config.json": "preprocessor_config",
                         "tokenizer_config.json": "tokenizer_config", "generation_config.json": "generation_config"}
MAX_RELEASES = 5
MAX_RELEASE_ASSETS = 80
_MAX_SMALL_FILE = 1_500_000
# Python source files that may be fetched from a GitHub repo purely as TEXT for static (ast) scanning. They are never
# imported, executed or evaluated; the caps keep the scan small and bounded.
_MAX_SCAN_ITEMS = 80


@dataclass
class RepoInfo:
    source: SourceRef
    revision_sha: Optional[str] = None
    pipeline_tag: Optional[str] = None
    library_name: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    license: Optional[str] = None
    gated: bool = False
    private: bool = False
    downloads: Optional[int] = None
    likes: Optional[int] = None
    files: List[Tuple[str, Optional[int]]] = field(default_factory=list)
    config: Optional[Dict[str, Any]] = None
    tokenizer_config: Optional[Dict[str, Any]] = None
    model_index: Optional[Dict[str, Any]] = None
    adapter_config: Optional[Dict[str, Any]] = None
    safetensors_total_params: Optional[int] = None
    safetensors_dtypes: Dict[str, int] = field(default_factory=dict)
    readme: str = ""
    # GitHub specific
    default_branch: Optional[str] = None
    description: Optional[str] = None
    topics: List[str] = field(default_factory=list)
    language: Optional[str] = None
    requirements: List[Dict[str, str]] = field(default_factory=list)
    requirement_flags: List[str] = field(default_factory=list)
    linked_hf_models: List[str] = field(default_factory=list)
    # link ranking: which README-linked Hugging Face model (if any) is the repository's OWN model
    primary_hf_model: Optional[str] = None
    hf_link_roles: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    # metadata of the primary linked model, read through the same allow-listed Hub inspector (never weights)
    primary_model_info: Optional["RepoInfo"] = None
    # static (ast-only) facts about a few small source files; nothing here was executed
    source_scan: List[Dict[str, Any]] = field(default_factory=list)
    # Deep, repository-wide static analysis (m2g.source_analysis.SourceAnalysis): module graph, call graph, loading sites and an
    # evidence-backed I/O contract. Built from source text already fetched; nothing is imported or executed. None when not run.
    source_analysis: Optional[Any] = None
    # The bounded TEXT of the source/config files the static scan already read (path -> text). Kept so the REA stage can hand REA
    # a read-only copy of files this platform has already collected, without any extra fetch. Data only; never executed.
    source_text: Dict[str, str] = field(default_factory=dict)
    package_keywords: List[str] = field(default_factory=list)
    # bookkeeping
    warnings: List[str] = field(default_factory=list)
    fetched: List[Tuple[str, int]] = field(default_factory=list)
    # result of the REA inspection stage (m2g.rea_client.ReaReport); set by the pipeline between inspection and analysis
    rea: Optional[Any] = None
    # result of the evidence collection stage (m2g.evidence_bridge.EvidenceContext); set by the pipeline next to ``rea``. None: not collected.
    evidence: Optional[Any] = None
    # generation_config.json (parsed). It was already fetched but never kept; it declares task-conditioning tokens (task_to_id).
    generation_config: Optional[Dict[str, Any]] = None
    # Task identifiers the model card's own ``model-index`` declares for its evaluation results (from the Hub API's cardData, which was
    # already returned; nothing extra is fetched). Raw, bounded identifier strings: the analyzer decides which ones it recognises.
    card_tasks: List[str] = field(default_factory=list)
    # preprocessor_config.json (parsed). It was already fetched (allow-listed) but discarded; it states the input a feature extractor /
    # image processor expects (sampling_rate, size, ...). Data only; nothing in it is executed.
    preprocessor_config: Optional[Dict[str, Any]] = None
    processor_config: Optional[Dict[str, Any]] = None
    # ---- GitHub provenance (filled by _inspect_github; every field stays at its default for a Hugging Face repository)
    # The commit every GitHub file was read at; None when the revision could not be resolved to one (then files were read by branch name).
    commit_sha: Optional[str] = None
    requested_revision: Optional[str] = None  # the branch/tag/commit named in the URL (None: the default branch was used)
    revision_kind: Optional[str] = None  # commit | requested-ref | default-branch | head
    homepage: str = ""  # the repository's own 'homepage' field (a maintainer declaration, kept as plain text)
    canonical_repo_id: Optional[str] = None  # set when GitHub reports another owner/name than the URL used (renamed / transferred)
    repo_flags: Dict[str, Any] = field(default_factory=dict)  # archived / fork / fork_of / empty
    # which README/code location(s) each linked Hugging Face model id was found in: {"owner/name": ["README.md", "pkg/model.py"]}
    hf_link_sources: Dict[str, List[str]] = field(default_factory=dict)
    # Things that could not be read, as structured facts ({"code", "message"}). A gap means the content is UNKNOWN, never absent.
    inspection_gaps: List[Dict[str, str]] = field(default_factory=list)
    # GitHub model artifacts, metadata only. ``config_paths``: RepoInfo attribute name -> repository path of a small model config file that was
    # read as TEXT (config.json, model_index.json, ...). ``release_assets``: ("<tag>/<asset name>", size) from GitHub Releases; the assets
    # themselves are never downloaded.
    config_paths: Dict[str, str] = field(default_factory=dict)
    release_assets: List[Tuple[str, Optional[int]]] = field(default_factory=list)
    # Transparent static-inspection coverage.  Missing/skipped source is unknown,
    # never interpreted as absent functionality.
    inspection_coverage: Dict[str, Any] = field(default_factory=dict)
    evidence_ledger: List[Dict[str, Any]] = field(default_factory=list)

    # ---- file helpers
    def file_names(self) -> List[str]:
        return [n for n, _ in self.files]

    def _exts(self, exts) -> List[Tuple[str, Optional[int]]]:
        return [(n, s) for n, s in self.files if n.lower().endswith(exts)]

    @property
    def has_safetensors(self) -> bool:
        return bool(self.safetensors_files or self.safetensors_indexes)

    @property
    def safetensors_files(self) -> List[Tuple[str, Optional[int]]]:
        return self._exts((".safetensors",))

    @property
    def safetensors_indexes(self) -> List[Tuple[str, Optional[int]]]:
        return [(n, s) for n, s in self.files if n.lower().endswith(SAFETENSORS_INDEX_SUFFIX)]

    @property
    def safetensors_shards(self) -> List[Tuple[str, Optional[int]]]:
        return [(n, s) for n, s in self.safetensors_files if re.search(r"-\d{5}-of-\d{5}\.safetensors$", n.lower())]

    @property
    def has_verified_safetensors(self) -> bool:
        """Whether this repository advertises a complete safe weight set."""
        return bool(self.safetensors_files) or bool(self.safetensors_indexes and self.safetensors_shards)

    @property
    def pickle_indexes(self) -> List[Tuple[str, Optional[int]]]:
        return [(n, s) for n, s in self.files if n.lower().endswith(PICKLE_INDEX_SUFFIXES)]

    def weight_inventory(self) -> Dict[str, Any]:
        """Return bounded, read-only format evidence from the Hub file listing."""
        safe = self.safetensors_files + self.safetensors_indexes
        pickle = self._exts(PICKLE_EXTS) + self.pickle_indexes
        return {
            "safetensors": [n for n, _ in safe][:80],
            "safetensors_shards": [n for n, _ in self.safetensors_shards][:80],
            "safetensors_indexed": bool(self.safetensors_indexes),
            "pickle": [n for n, _ in pickle][:80],
            "pickle_indexed": bool(self.pickle_indexes),
            "safe_format_available": self.has_verified_safetensors,
            "safe_format_same_repository": self.has_verified_safetensors,
        }

    @property
    def has_pickle_weights(self) -> bool:
        return bool(self._exts(PICKLE_EXTS))

    @property
    def has_gguf(self) -> bool:
        return bool(self._exts((".gguf",)))

    @property
    def has_onnx(self) -> bool:
        return bool(self._exts((".onnx",)))

    def weights_bytes(self) -> Optional[int]:
        """Total size of the preferred weight format present (safetensors > pickle > gguf > onnx)."""
        for exts in ((".safetensors",), PICKLE_EXTS, (".gguf",), (".onnx",)):
            sel = self._exts(exts)
            sizes = [s for _, s in sel if s]
            if sizes:
                return int(sum(sizes))
        return None


# --------------------------------------------------------------------------- helpers


def _net_msg(exc: Exception) -> str:
    return ("Network problem while inspecting the repository: " + redact_secrets(str(exc)) +
            " (DNS/proxy/firewall?). The request was not retried repeatedly; fix connectivity and run again.")


def _card_task_types(card: Any) -> List[str]:
    """Task identifiers declared in the model card's ``model-index`` evaluation results (``results[].task.type``). Pure data read of the
    card YAML the Hub API already returned. Only identifier-shaped strings are kept (deduplicated, bounded); whether one is a
    recognised task is the analyzer's decision, and a card that declares none yields an empty list."""
    index = card.get("model-index") if isinstance(card, dict) else None
    found: List[str] = []
    for entry in (index if isinstance(index, list) else [])[:20]:
        results = entry.get("results") if isinstance(entry, dict) else None
        for res in (results if isinstance(results, list) else [])[:40]:
            task = res.get("task") if isinstance(res, dict) else None
            t = task.get("type") if isinstance(task, dict) else None
            if isinstance(t, str) and re.fullmatch(r"[a-z0-9][a-z0-9_.:+-]{0,63}", t) and t not in found:
                found.append(t)
    return found[:16]


def _try_json(text: str) -> Optional[Dict[str, Any]]:
    try:
        v = json.loads(text)
        return v if isinstance(v, dict) else None
    except json.JSONDecodeError:
        return None


def parse_requirements(text: str) -> Tuple[List[Dict[str, str]], List[str]]:
    """Pure-text parse of a requirements.txt. Returns ([{'name','spec'}], flags)."""
    reqs: List[Dict[str, str]] = []
    flags: List[str] = []
    for raw in text.splitlines():
        line = re.sub(r"(^|\s)#.*$", "", raw).strip()
        if not line:
            continue
        low = line.lower()
        if low.startswith(("-r", "-c", "--requirement", "--constraint")):
            flags.append("requirements file includes other files (not followed)")
        elif low.startswith(("-e", "--editable")):
            flags.append("editable/VCS install present")
        elif low.startswith(("--index-url", "--extra-index-url", "-i ", "--find-links", "-f ", "--trusted-host")):
            flags.append("custom package index / find-links present")
        elif low.startswith("-"):
            flags.append(f"pip option present: {line.split()[0]}")
        elif "://" in line or low.startswith("git+") or " @ " in line:
            flags.append("direct URL/VCS dependency (can run arbitrary code at install time)")
        else:
            spec = line.split(";")[0].strip()
            m = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)", spec)
            if m:
                reqs.append({"name": m.group(1).lower().replace("_", "-"), "spec": spec})
    return reqs, sorted(set(flags))


def _parse_pyproject(text: str) -> List[Dict[str, str]]:
    deps: List[str] = []
    if tomllib is not None:
        try:
            data = tomllib.loads(text)
            deps = list(data.get("project", {}).get("dependencies", []) or [])
            poetry = data.get("tool", {}).get("poetry", {}).get("dependencies", {}) or {}
            deps += [k for k in poetry if k.lower() != "python"]
        except Exception:  # malformed toml: ignore
            return []
    out = []
    for d in deps:
        m = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)", str(d))
        if m:
            out.append({"name": m.group(1).lower().replace("_", "-"), "spec": str(d).split(";")[0].strip()})
    return out


# First path segments of huggingface.co links that are site pages, not model owners (e.g. huggingface.co/tasks/text-classification).
_HF_SITE_PAGES = frozenset({"datasets", "spaces", "docs", "blog", "papers", "models", "collections", "organizations", "api", "settings",
                            "join", "login", "new", "pricing", "tasks", "chat", "learn", "posts", "search", "welcome", "enterprise",
                            "support", "terms-of-service", "privacy", "sponsors"})


def _find_hf_links(text: str) -> List[str]:
    found: List[str] = []
    patterns = [r"huggingface\.co/([A-Za-z0-9][A-Za-z0-9._-]{0,95}/[A-Za-z0-9][A-Za-z0-9._-]{0,95})",
                r"from_pretrained\(\s*[\"']([A-Za-z0-9][A-Za-z0-9._-]{0,95}/[A-Za-z0-9][A-Za-z0-9._-]{0,95})[\"']"]
    skip = _HF_SITE_PAGES
    for pat in patterns:
        for m in re.finditer(pat, text):
            rid = m.group(1).rstrip(".")
            if rid.split("/")[0].lower() in skip:
                continue
            try:
                validate_repo_id(rid)
            except SecurityError:
                continue
            if rid not in found:
                found.append(rid)
    return found[:10]


# --------------------------------------------------------------------------- static packaging / source helpers
# Everything below works on TEXT only. `ast.parse` builds a syntax tree without running any code; `ast.literal_eval` only
# accepts literals. No setup.py, notebook or module is ever imported or executed.


def _call_name(node: ast.Call) -> str:
    f = node.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return ""


def parse_setup_py(text: str) -> Tuple[List[Dict[str, str]], List[str], Optional[str]]:
    """Statically read literal ``install_requires``/``keywords`` from a setup() call. Returns (reqs, keywords, problem)."""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return [], [], "setup.py could not be parsed"
    reqs: List[Dict[str, str]] = []
    keywords: List[str] = []
    problem: Optional[str] = None
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and _call_name(node) == "setup"):
            continue
        for kw in node.keywords:
            if kw.arg not in ("install_requires", "keywords"):
                continue
            try:
                val = ast.literal_eval(kw.value)
            except (ValueError, SyntaxError, TypeError, RecursionError, MemoryError):
                if kw.arg == "install_requires":
                    problem = "install_requires is computed, not a literal list"
                continue
            if isinstance(val, str):
                val = [v for v in re.split(r"[\n,]", val) if v.strip()] if kw.arg == "keywords" else val.splitlines()
            if not isinstance(val, (list, tuple)) or not all(isinstance(v, str) for v in val):
                continue
            if kw.arg == "install_requires":
                parsed, _flags = parse_requirements("\n".join(val))
                reqs += parsed
            else:
                keywords += [v.strip() for v in val if v.strip()][:20]
    return reqs, keywords, problem


def parse_setup_cfg(text: str) -> Tuple[List[Dict[str, str]], List[str]]:
    cp = configparser.ConfigParser(interpolation=None)
    try:
        cp.read_string(text)
        raw = cp.get("options", "install_requires", fallback="")
        kws = cp.get("metadata", "keywords", fallback="")
    except (configparser.Error, ValueError):
        return [], []
    reqs, _ = parse_requirements(raw)
    return reqs, [k.strip() for k in re.split(r"[\n,]", kws) if k.strip()][:20]


_SKIP_DIR_RE = re.compile(r"(^|/)(tests?|docs?|\.github|node_modules|venv|\.venv|build|dist|third_party|vendor|site-packages|"
                          r"__pycache__)(/|$)", re.I)
_SOURCE_HINT_RE = re.compile(r"(model|modeling|processor|pipeline|inference|infer|predict|detect|hubconf|generat|caption|demo|app|main|"
                             r"config|train|__init__)", re.I)


def pick_source_files(paths: List[Tuple[str, Optional[int]]], limit: Optional[int] = None,
                      max_file_bytes: Optional[int] = _MAX_SMALL_FILE) -> List[str]:
    """Choose a few small .py files (package modules, examples, entry points) worth a static scan."""
    scored: List[Tuple[int, str]] = []
    for path, size in paths:
        low = path.lower()
        if not low.endswith(".py") or _SKIP_DIR_RE.search(path) or low in ("setup.py", "conftest.py"):
            continue
        if max_file_bytes is not None and size is not None and size > max_file_bytes:
            continue
        parts = path.split("/")
        score = 0
        if parts[-1].lower() in ("detect.py", "predict.py", "inference.py", "hubconf.py"):
            score += 8  # conventional ML entry points; parsed as text only
        if _SOURCE_HINT_RE.search(parts[-1]):
            score += 2
        if len(parts) >= 2 and parts[0] not in ("examples", "example", "scripts"):
            score += 2  # inside a package directory
        if parts[0] in ("examples", "example", "demo"):
            score += 2
        if len(parts) <= 2:
            score += 1
        scored.append((-score, path))
    ordered = [p for _, p in sorted(scored)]
    return ordered if limit is None else ordered[:max(0, limit)]


def _dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return ""


def scan_python_source(path: str, text: str) -> Optional[Dict[str, Any]]:
    """ast-only summary of one module: classes (+bases), defs, imports and literal from_pretrained() arguments."""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return None
    classes: Dict[str, List[str]] = {}
    defs: List[str] = []
    imports: List[str] = []
    imported_names: List[str] = []
    local_imports: List[str] = []
    pretrained_ids: List[str] = []
    pretrained_classes: List[str] = []
    calls: List[Dict[str, Any]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and len(classes) < _MAX_SCAN_ITEMS:
            classes[node.name] = [b for b in (_dotted(x) for x in node.bases) if b]
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and len(defs) < _MAX_SCAN_ITEMS:
            defs.append(node.name)
        elif isinstance(node, ast.Import):
            imports += [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            top = node.module.split(".")[0]
            imports.append(top)
            imported_names += [f"{top}:{a.name}" for a in node.names if len(imported_names) < _MAX_SCAN_ITEMS]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level > 0:
            local_imports.append(node.module.replace(".", "/") + ".py")
        elif isinstance(node, ast.Call) and _call_name(node) == "from_pretrained":
            owner = _dotted(node.func.value) if isinstance(node.func, ast.Attribute) else ""
            if owner and owner not in pretrained_classes and len(pretrained_classes) < _MAX_SCAN_ITEMS:
                pretrained_classes.append(owner.split(".")[-1])
            if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                v = node.args[0].value
                if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}/[A-Za-z0-9][A-Za-z0-9._-]{0,95}", v) and v not in pretrained_ids:
                    pretrained_ids.append(v)
        elif isinstance(node, ast.Call) and len(calls) < _MAX_SCAN_ITEMS:
            name, low = _call_name(node), _call_name(node).lower()
            role = ("loading" if "load" in low else "inference" if any(x in low for x in ("predict", "infer", "generate", "forward"))
                    else "preprocessing" if any(x in low for x in ("tokeniz", "preprocess", "transform"))
                    else "postprocessing" if any(x in low for x in ("decode", "postprocess", "format")) else "")
            if role:
                calls.append({"name": name, "role": role, "line": getattr(node, "lineno", 0)})
    return {"path": path, "classes": classes, "defs": defs, "imports": sorted(set(imports)),
            "imported_names": imported_names[:_MAX_SCAN_ITEMS], "pretrained_ids": pretrained_ids[:10],
            "pretrained_classes": pretrained_classes, "local_imports": sorted(set(local_imports))[:_MAX_SCAN_ITEMS], "calls": calls}


# README context cues. A link is the repository's OWN model when it carries the repo's name/owner or sits next to wording like
# "pretrained model is available at"; it is a dependency when it sits under "based on/utilizes/acknowledgements" wording.
_PRIMARY_CUES = ("pretrained model", "pre-trained model", "our model", "available at", "model weights", "weights", "checkpoint",
                 "released model", "model card", "hosted at", "download")
_DEPENDENCY_CUES = ("acknowledg", "based on", "utiliz", "backbone", "tested with", "vision encoder", "language model",
                    "possible values", "credits", "thanks", "inspired", "built on", "builds on", "lm:")


def _norm_name(v: str) -> str:
    return re.sub(r"[^a-z0-9]", "", v.lower())


def rank_hf_links(readme: str, ref: SourceRef, links: List[str],
                  extra_evidence: Optional[Dict[str, Dict[str, Any]]] = None) -> Tuple[Optional[str], Dict[str, Dict[str, Any]]]:
    """Decide which README-linked Hugging Face model (if any) is the repository's primary model. Returns (id, roles).

    ``extra_evidence`` maps a model id to ``{"score": int, "reason": str}`` for evidence that is not in the README text (for example the
    repository's own homepage field); it is added to that link's score and recorded in its reasons."""
    roles: Dict[str, Dict[str, Any]] = {}
    low = readme.lower()
    for rid in links:
        score, reasons = 0, []
        owner, _, name = rid.partition("/")
        if _norm_name(name) == _norm_name(ref.name):
            score += 3
            reasons.append("model name equals the repository name")
        if ref.owner and owner.lower() == ref.owner.lower():
            score += 2
            reasons.append("same owner as the repository")
        extra = (extra_evidence or {}).get(rid)
        if extra:
            score += int(extra.get("score", 0))
            reasons.append(str(extra.get("reason", "declared by the repository")))
        windows = [low[max(0, m.start() - 160):m.start()] for m in re.finditer(re.escape(rid.lower()), low)]
        if any(c in w for w in windows for c in _PRIMARY_CUES):
            score += 2
            reasons.append("README describes it as the available/pretrained model")
        dep = bool(windows) and all(any(c in w for c in _DEPENDENCY_CUES) for w in windows)
        if dep:
            score -= 2
            reasons.append("README mentions it as a backbone/dependency")
        role = "dependency" if dep and score < 3 else ("candidate" if score >= 3 else "referenced")
        roles[rid] = {"role": role, "score": score, "reasons": reasons}
    ranked = sorted(roles.items(), key=lambda kv: -kv[1]["score"])
    primary = None
    if ranked and ranked[0][1]["score"] >= 3 and (len(ranked) == 1 or ranked[0][1]["score"] - ranked[1][1]["score"] >= 2):
        primary = ranked[0][0]
        roles[primary]["role"] = "primary"
    return primary, roles


# --------------------------------------------------------------------------- Hugging Face


def _inspect_hf(ref: SourceRef, fetcher: SafeFetcher, settings: Settings) -> RepoInfo:
    info = RepoInfo(source=ref)
    base = f"https://huggingface.co/api/models/{ref.repo_id}"
    url = (f"{base}/revision/{ref.revision}" if ref.revision else base) + "?blobs=true"
    try:
        meta = fetcher.get_json(url, max_bytes=4_000_000)
    except HTTPStatusError as exc:
        if exc.status in (401, 403):
            raise InspectionError("Hugging Face denied access (gated or private model). Set HF_TOKEN for an account that "
                                  "accepted the model's terms.")
        if exc.status == 404:
            raise InspectionError("Model not found on Hugging Face (wrong name, or it is private).")
        raise InspectionError(f"Hugging Face API returned HTTP {exc.status}.")
    except NetworkError as exc:
        raise InspectionError(_net_msg(exc))
    except FetchError as exc:
        raise InspectionError(redact_secrets(str(exc)))
    if not isinstance(meta, dict):
        raise InspectionError("Unexpected response from Hugging Face API.")

    info.revision_sha = meta.get("sha") if re.fullmatch(r"[0-9a-f]{7,64}", str(meta.get("sha", ""))) else None
    info.pipeline_tag = meta.get("pipeline_tag")
    info.library_name = meta.get("library_name")
    info.tags = [str(t) for t in (meta.get("tags") or [])][:200]
    card = meta.get("cardData") if isinstance(meta.get("cardData"), dict) else {}
    info.card_tasks = _card_task_types(card)
    lic = card.get("license") or next((t.split(":", 1)[1] for t in info.tags if t.startswith("license:")), None)
    info.license = str(lic) if lic else None
    info.gated = bool(meta.get("gated"))
    info.private = bool(meta.get("private"))
    info.downloads, info.likes = meta.get("downloads"), meta.get("likes")
    for s in meta.get("siblings") or []:
        if isinstance(s, dict) and isinstance(s.get("rfilename"), str):
            info.files.append((s["rfilename"], s.get("size") if isinstance(s.get("size"), int) else None))
    st = meta.get("safetensors")
    if isinstance(st, dict):
        if isinstance(st.get("total"), int):
            info.safetensors_total_params = st["total"]
        if isinstance(st.get("parameters"), dict):
            info.safetensors_dtypes = {str(k): int(v) for k, v in st["parameters"].items() if isinstance(v, int)}
    inline_cfg = meta.get("config") if isinstance(meta.get("config"), dict) else None

    pin = info.revision_sha or ref.revision or "main"
    try:
        validate_revision(pin)
    except SecurityError:
        pin = "main"
    sizes = dict(info.files)
    inspected, skipped, inaccessible = [], [], []
    # Metadata is read first; small Python modules are then selected from the
    # advertised repository listing under the same configurable static budget.
    hf_sources = [n for n, s in info.files if n.endswith(".py") and s is not None and s <= settings.github_max_source_file_bytes]
    wanted_hf = list(HF_FETCH_ALLOWLIST) + sorted(hf_sources, key=lambda n: (n.count("/"), n))[:settings.github_max_source_files]
    for fname in wanted_hf:
        if fname not in sizes:
            continue
        if sizes[fname] and sizes[fname] > _MAX_SMALL_FILE:
            skipped.append({"path": fname, "reason": "response budget"})
            info.warnings.append(f"{fname} skipped (larger than {_MAX_SMALL_FILE} bytes).")
            continue
        try:
            res = fetcher.fetch(f"https://huggingface.co/{ref.repo_id}/resolve/{pin}/{fname}", max_bytes=_MAX_SMALL_FILE)
        except HTTPStatusError as exc:
            inaccessible.append({"path": fname, "reason": f"HTTP {exc.status}"})
            info.warnings.append(f"Could not fetch {fname} (HTTP {exc.status}).")
            continue
        except NetworkError as exc:
            info.warnings.append(f"Network problem fetching {fname}; remaining files skipped: {redact_secrets(str(exc))[:100]}")
            break
        except FetchError as exc:
            info.warnings.append(f"Could not fetch {fname}: {redact_secrets(str(exc))[:100]}")
            continue
        text = res.body.decode("utf-8", errors="replace")
        info.fetched.append((fname, len(res.body)))
        inspected.append(fname)
        if fname == "README.md":
            info.readme = text[: settings.max_readme_chars]
        elif fname.endswith(".py"):
            info.source_text[fname] = text  # kept for the static custom-code analysis and the REA source stage (no re-fetch)
            scanned = scan_python_source(fname, text)
            if scanned:
                info.source_scan.append(scanned)
                info.evidence_ledger.append({"source": fname, "location": "AST", "finding": "static Hugging Face source summary",
                                             "provenance": "huggingface static scan", "confidence": 0.8})
        elif fname == "requirements.txt":
            reqs, flags = parse_requirements(text)
            info.requirements += [{**r, "source": fname} for r in reqs]
            info.requirement_flags += flags
        elif fname == "pyproject.toml":
            info.requirements += [{**r, "source": fname} for r in _parse_pyproject(text)]
        else:
            parsed = _try_json(text)
            if parsed is None:
                info.warnings.append(f"{fname} is not valid JSON.")
                continue
            attr = {"config.json": "config", "model_index.json": "model_index", "tokenizer_config.json": "tokenizer_config",
                    "adapter_config.json": "adapter_config", "generation_config.json": "generation_config",
                    "preprocessor_config.json": "preprocessor_config", "processor_config.json": "processor_config"}.get(fname)
            if attr:
                setattr(info, attr, parsed)
    if info.config is None and inline_cfg is not None:
        info.config = inline_cfg
        info.warnings.append("config.json not fetched; using the Hub API's config summary.")
    if info.tokenizer_config is None and inline_cfg and isinstance(inline_cfg.get("tokenizer_config"), dict):
        info.tokenizer_config = inline_cfg["tokenizer_config"]
    info.inspection_coverage = {"host": "huggingface", "discovered": len(info.files), "inspected": inspected,
                                "skipped": skipped, "inaccessible": inaccessible,
                                "remaining": [n for n in hf_sources if n not in inspected],
                                "budget": {"max_source_files": settings.github_max_source_files,
                                           "max_source_file_bytes": settings.github_max_source_file_bytes}}
    _hf_custom_code_source(info, ref, fetcher, settings, pin)
    return info


def _hf_custom_code_source(info: RepoInfo, ref: SourceRef, fetcher: SafeFetcher, settings: Settings, pin: str) -> None:
    """Statically analyse the Python code a Hugging Face repo SHIPS (custom modelling code), without executing any of it.

    A repository with ``config.auto_map`` (or the ``custom_code`` tag) ships its own modelling/tokenizer code. The same
    repository-wide static analysis the GitHub path uses (module graph, loading sites, I/O contract) is run over those files,
    read from the Hub's raw-file endpoint. Only files the Hub listing shows, under a strict per-file and total byte bound, are
    fetched; nothing is imported, executed or installed, and ``trust_remote_code`` is never enabled. Absent custom code (the
    common case) makes this a no-op, so a metadata-only Hugging Face analysis is unchanged."""
    cfg = info.config or {}
    auto_map = cfg.get("auto_map") if isinstance(cfg, dict) else None
    ships_code = bool(auto_map) or "custom_code" in (info.tags or [])
    if not ships_code:
        return
    from .source_analysis import MAX_MODULES, analyze_repository, select_modules
    limit = int(getattr(settings, "max_source_modules", 0) or MAX_MODULES)
    listed = info.files
    modules, skipped = select_modules(listed, limit=limit)
    # Auto-map may name files the heuristic selection skipped (a custom model in an unconventional path): add them explicitly.
    for path in _auto_map_paths(auto_map):
        if path not in modules and any(p == path for p, _ in listed):
            modules.append(path)
    sizes = dict(listed)
    text: Dict[str, str] = {}
    total = 0
    for path in modules[: settings.hf_custom_code_max_files]:
        size = sizes.get(path)
        if size is not None and size > settings.hf_custom_code_max_file_bytes:
            info.warnings.append(f"{path} skipped from the custom-code scan (larger than {settings.hf_custom_code_max_file_bytes} bytes).")
            continue
        if total >= settings.hf_custom_code_max_bytes:
            skipped.append({"path": path, "reason": "custom-code byte budget exhausted"})
            continue
        if path in info.source_text:  # the shallow Hub scan already fetched it: reuse, never download twice
            text[path] = info.source_text[path]
            total += len(text[path])
            continue
        try:
            res = fetcher.fetch(f"https://huggingface.co/{ref.repo_id}/resolve/{pin}/{path}",
                                max_bytes=settings.hf_custom_code_max_file_bytes)
        except (HTTPStatusError, NetworkError, FetchError) as exc:
            info.warnings.append(f"Could not fetch custom code {path}: {redact_secrets(str(exc))[:80]}")
            continue
        info.fetched.append((path, len(res.body)))
        body = res.body.decode("utf-8", errors="replace")
        text[path] = body
        info.source_text[path] = body
        total += len(body)
    if not text:
        return
    try:
        src = analyze_repository(sorted(text.items()), listed=listed,
                                 limits={"max_modules": settings.hf_custom_code_max_files})
        for s in skipped:
            if s not in src.skipped:
                src.skipped.append(s)
        info.source_analysis = src
    except Exception as exc:  # a static-analysis failure must never break inspection; the metadata analysis still stands
        info.warnings.append(f"The custom-code static analysis did not complete ({type(exc).__name__}); the metadata analysis is unaffected.")


def _auto_map_paths(auto_map: Any) -> List[str]:
    """The distinct repository paths named by a ``config.auto_map`` mapping.

    A value is a dotted ``module.Class`` reference (optionally ``pkg.module.Class``); the last segment is the class, the rest is
    the module path, so ``"a.b.Class"`` names ``a/b.py``."""
    out: List[str] = []
    if not isinstance(auto_map, dict):
        return out
    for value in auto_map.values():
        for entry in (value if isinstance(value, list) else [value]):
            if not isinstance(entry, str) or "." not in entry or "/" in entry:
                continue
            module = entry.rsplit(".", 1)[0]
            path = module.replace(".", "/") + ".py"
            if path not in out:
                out.append(path)
    return out


# --------------------------------------------------------------------------- GitHub

_SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
_REPO_PATH_SEG = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
_GH_SHA_HEADERS = {"Accept": "application/vnd.github.sha", "X-GitHub-Api-Version": "2022-11-28"}
# Codes of RepoInfo.inspection_gaps. A gap always means the content is UNKNOWN (it could not be read), never that it is absent.
# LINK_BLINDING_GAPS could hide a link to a Hugging Face model; CODE_BLIND_GAPS could hide repository code that contradicts a linked model.
LINK_BLINDING_GAPS = ("api_unavailable", "readme_unavailable", "network_interrupted")
CODE_BLIND_GAPS = ("api_unavailable", "file_tree_unavailable", "source_scan_interrupted", "network_interrupted")


def _gap(info: RepoInfo, code: str, message: str) -> None:
    """Record one thing that could not be read (once per code) as a structured fact; the analysis reasons about what is therefore unknown."""
    if not any(g["code"] == code for g in info.inspection_gaps):
        info.inspection_gaps.append({"code": code, "message": redact_secrets(message)[:240]})


def _printable(text: Any, limit: int = 80) -> str:
    return re.sub(r"[^\x20-\x7e]", "?", str(text))[:limit]


def _safe_repo_path(path: Any) -> bool:
    """True for a repository path that can go into a raw.githubusercontent.com URL without changing its meaning: no query, fragment,
    escape, space, empty or dot segment. Paths come from the repository's own file list, so they are untrusted text."""
    if not isinstance(path, str) or not path or len(path) > 240:
        return False
    return all(_REPO_PATH_SEG.match(part) and part not in (".", "..") for part in path.split("/"))


def _rate_limited(exc: HTTPStatusError) -> bool:
    """Does this refusal look like GitHub's rate limit (rather than an access refusal)? Decided from the response headers only."""
    headers = getattr(exc, "headers", None) or {}
    return exc.status == 429 or headers.get("x-ratelimit-remaining") == "0" or "retry-after" in headers


def _resolve_github_commit(api: str, branch: str, fetcher: SafeFetcher) -> Tuple[Optional[str], str]:
    """(commit sha, "") for the branch / tag / commit named ``branch``, or (None, why it could not be resolved). Only the commit id is
    read: GitHub's ``sha`` media type answers with the 40-character id alone, so no commit metadata or file list is transferred."""
    if _SHA40_RE.fullmatch(branch):
        return branch, ""
    try:
        res = fetcher.fetch(f"{api}/commits/{branch}", _GH_SHA_HEADERS, max_bytes=4096)
    except HTTPStatusError as exc:
        return None, f"HTTP {exc.status}"
    except NetworkError as exc:
        raise InspectionError(_net_msg(exc), code="network_error")
    except FetchError as exc:
        return None, redact_secrets(str(exc))[:80]
    text = res.body.decode("ascii", errors="replace").strip()
    return (text, "") if _SHA40_RE.fullmatch(text) else (None, "the answer was not a commit id")


def _read_github_flags(info: RepoInfo, meta: Dict[str, Any], ref: SourceRef) -> None:
    """Repository facts that decide how the rest of the inspection is read: disabled, fork, homepage, renamed. Raises for a disabled repository."""
    if meta.get("disabled"):
        raise InspectionError("This GitHub repository is disabled, so it cannot be inspected.", code="repository_disabled")
    flags: Dict[str, Any] = {"archived": bool(meta.get("archived")), "fork": bool(meta.get("fork"))}
    parent = meta.get("parent")
    if flags["fork"] and isinstance(parent, dict) and isinstance(parent.get("full_name"), str):
        flags["fork_of"] = _printable(parent["full_name"], 120)
    info.repo_flags = flags
    homepage = meta.get("homepage")
    if isinstance(homepage, str):
        info.homepage = homepage.strip()[:300]
    full = meta.get("full_name")
    if isinstance(full, str) and full.lower() != ref.repo_id.lower():
        try:
            validate_repo_id(full)
        except SecurityError:
            return
        info.canonical_repo_id = full
        info.warnings.append(f"GitHub reports this repository as '{full}' (it was renamed or transferred); the files analysed are from there.")



def _inspect_github_model_configs(
    info: RepoInfo,
    paths: List[Tuple[str, Optional[int]]],
    fetcher: SafeFetcher,
    repo_id: str,
    fetch_ref: str,
) -> None:
    """Read bounded GitHub model config JSON files as data only."""
    candidates: Dict[str, List[Tuple[int, str, Optional[int]]]] = {}

    for path, size in paths:
        if not _safe_repo_path(path):
            continue

        parts = path.split("/")
        if len(parts) > 2:
            continue

        filename = parts[-1].lower()
        attr = GH_MODEL_CONFIG_FILES.get(filename)
        if not attr:
            continue

        candidates.setdefault(attr, []).append((len(parts), path, size))

    for attr, entries in candidates.items():
        # Prefer the root copy; use a deterministic path if multiple copies exist.
        _, path, size = sorted(entries, key=lambda item: (item[0], item[1].lower()))[0]

        if size is not None and size > _MAX_SMALL_FILE:
            info.warnings.append(f"{path} skipped (larger than {_MAX_SMALL_FILE} bytes).")
            continue

        url = f"https://raw.githubusercontent.com/{repo_id}/{fetch_ref}/{path}"

        try:
            res = fetcher.fetch(url, max_bytes=_MAX_SMALL_FILE)
        except HTTPStatusError as exc:
            info.warnings.append(f"Could not fetch model config {path} (HTTP {exc.status}).")
            continue
        except NetworkError as exc:
            info.warnings.append(
                f"Network problem fetching model config {path}: {redact_secrets(str(exc))[:100]}"
            )
            _gap(info, "github_config_metadata_unavailable",
                 "One or more GitHub model config files could not be read")
            break
        except FetchError as exc:
            info.warnings.append(
                f"Could not fetch model config {path}: {redact_secrets(str(exc))[:100]}"
            )
            continue

        info.fetched.append((path, len(res.body)))
        parsed = _try_json(res.body.decode("utf-8", errors="replace"))

        if parsed is None:
            info.warnings.append(f"{path} is not a valid JSON object.")
            continue

        setattr(info, attr, parsed)
        info.config_paths[attr] = path


def _inspect_github_releases(
    info: RepoInfo,
    api: str,
    fetcher: SafeFetcher,
    headers: Dict[str, str],
) -> None:
    """Collect bounded GitHub release asset metadata; never download assets."""
    url = f"{api}/releases?per_page={MAX_RELEASES}"

    try:
        releases = fetcher.get_json(url, headers, max_bytes=1_000_000)
    except HTTPStatusError as exc:
        info.warnings.append(f"Could not inspect GitHub releases (HTTP {exc.status}).")
        return
    except NetworkError as exc:
        info.warnings.append(
            f"Network problem inspecting GitHub releases: {redact_secrets(str(exc))[:100]}"
        )
        return
    except FetchError as exc:
        info.warnings.append(
            f"Could not inspect GitHub releases: {redact_secrets(str(exc))[:100]}"
        )
        return

    if not isinstance(releases, list):
        info.warnings.append("GitHub releases API returned an unexpected response.")
        return

    for release in releases[:MAX_RELEASES]:
        if not isinstance(release, dict):
            continue

        tag = release.get("tag_name")
        if not isinstance(tag, str) or not tag.strip():
            tag = "untagged"
        tag = _printable(tag, 100)

        assets = release.get("assets")
        if not isinstance(assets, list):
            continue

        for asset in assets:
            if len(info.release_assets) >= MAX_RELEASE_ASSETS:
                return
            if not isinstance(asset, dict):
                continue

            name = asset.get("name")
            if not isinstance(name, str) or not name.strip():
                continue

            name = _printable(name, 180)
            size = asset.get("size")
            if not isinstance(size, int) or isinstance(size, bool) or size < 0:
                size = None

            info.release_assets.append((f"{tag}/{name}", size))


def _inspect_github(ref: SourceRef, fetcher: SafeFetcher, settings: Settings) -> RepoInfo:
    info = RepoInfo(source=ref)
    api = f"https://api.github.com/repos/{ref.repo_id}"
    gh_headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    api_ok = True
    try:
        meta = fetcher.get_json(api, gh_headers)
        if not isinstance(meta, dict):
            raise InspectionError("Unexpected response from the GitHub API.", code="unexpected_response")
        default_branch = meta.get("default_branch")
        info.default_branch = default_branch if isinstance(default_branch, str) else None
        info.description = meta.get("description")
        info.topics = [str(t) for t in (meta.get("topics") or [])][:30]
        info.language = meta.get("language")
        lic = meta.get("license")
        info.license = lic.get("spdx_id") if isinstance(lic, dict) else None
        info.private = bool(meta.get("private"))
        _read_github_flags(info, meta, ref)
        if meta.get("archived"):
            info.warnings.append("Repository is archived.")
    except HTTPStatusError as exc:
        if exc.status == 404:
            raise InspectionError("GitHub repository not found (wrong name, or it is private). It may also have been deleted or renamed; "
                                  "check the URL.", code="repository_not_found")
        if exc.status == 401:
            raise InspectionError("GitHub rejected the configured GITHUB_TOKEN (HTTP 401). Remove or replace the token and run again.",
                                  code="github_token_rejected")
        if exc.status == 451:
            raise InspectionError("GitHub reports that this repository is unavailable for legal reasons (HTTP 451).", code="repository_blocked")
        if exc.status == 410:
            raise InspectionError("GitHub reports that this repository is no longer available (HTTP 410).", code="repository_gone")
        if exc.status in (403, 429):
            api_ok = False
            info.warnings.append(f"GitHub API returned HTTP {exc.status} (rate limit or blocked). Falling back to "
                                 "raw.githubusercontent.com for README/requirements only; set GITHUB_TOKEN to lift the limit.")
            _gap(info, "api_unavailable", ("GitHub's API rate limit was reached" if _rate_limited(exc) else
                                          f"GitHub refused the API request (HTTP {exc.status})")
                 + ", so branch, file list and repository metadata are unknown")
        else:
            raise InspectionError(f"GitHub API returned HTTP {exc.status}." + (" GitHub may be having problems; try again later." if exc.status >= 500 else ""),
                                  code="github_api_error")
    except NetworkError as exc:
        raise InspectionError(_net_msg(exc), code="network_error")
    except FetchError as exc:
        raise InspectionError(redact_secrets(str(exc)))

    # Which revision is read: the one named in the URL, else the default branch. It is resolved to a commit once, and the file list and
    # every file are then read at that commit, so they all describe the same state of the repository even if the branch moves meanwhile.
    info.requested_revision = ref.revision
    branch = ref.revision or info.default_branch or "HEAD"
    try:
        validate_revision(branch)
    except SecurityError:
        info.warnings.append("Default branch name is unusual; using HEAD.")
        branch = "HEAD"
    if _SHA40_RE.fullmatch(branch):
        info.revision_kind = "commit"
    elif ref.revision:
        info.revision_kind = "requested-ref"
    elif info.default_branch and branch == info.default_branch:
        info.revision_kind = "default-branch"
    else:
        info.revision_kind = "head"
    fetch_ref = branch  # the pinned commit once resolved; otherwise the branch name (the files then come from the branch tip)
    if api_ok:
        sha, why = _resolve_github_commit(api, branch, fetcher)
        if sha:
            info.commit_sha = fetch_ref = sha
        else:
            _gap(info, "revision_unpinned", f"the GitHub revision '{branch}' could not be resolved to a commit ({why})")

    paths: List[Tuple[str, Optional[int]]] = []
    if api_ok:
        try:
            tree = fetcher.get_json(f"{api}/git/trees/{fetch_ref}?recursive=1", gh_headers, max_bytes=4_000_000)
            if tree.get("truncated"):
                # GitHub's recursive tree endpoint signals truncation rather than
                # offering an offset. Page through top-level subtrees explicitly,
                # retaining a bounded, deduplicated aggregate.
                info.warnings.append("GitHub recursive tree was truncated; continuing through top-level subtrees within the tree budget.")
                root = fetcher.get_json(f"{api}/git/trees/{fetch_ref}", gh_headers, max_bytes=1_500_000)
                extra = []
                for entry in root.get("tree") or []:
                    if len(extra) >= settings.max_tree_entries or not isinstance(entry, dict) or entry.get("type") != "tree":
                        continue
                    sha = entry.get("sha")
                    prefix = entry.get("path")
                    if not isinstance(sha, str) or not isinstance(prefix, str):
                        continue
                    page = fetcher.get_json(f"{api}/git/trees/{sha}?recursive=1", gh_headers, max_bytes=4_000_000)
                    for child in page.get("tree") or []:
                        if isinstance(child, dict) and isinstance(child.get("path"), str):
                            extra.append({**child, "path": prefix + "/" + child["path"]})
                            if len(extra) >= settings.max_tree_entries:
                                break
                tree = {**tree, "tree": list(tree.get("tree") or []) + extra}
            seen_paths = set()
            for e in (tree.get("tree") or []):
                if isinstance(e, dict) and e.get("type") == "blob" and isinstance(e.get("path"), str):
                    if e["path"] not in seen_paths and len(paths) < settings.max_tree_entries:
                        seen_paths.add(e["path"])
                        paths.append((e["path"], e.get("size") if isinstance(e.get("size"), int) else None))
        except HTTPStatusError as exc:
            info.warnings.append(f"Could not list repository files (HTTP {exc.status}).")
        except NetworkError as exc:
            raise InspectionError(_net_msg(exc))
        except FetchError as exc:
            info.warnings.append(f"Could not list repository files: {redact_secrets(str(exc))[:100]}")
    info.files = paths
    names = {p.lower(): p for p, _ in paths}

    # Read small model config JSON files as data; never fetch weights or execute repository code.
    if api_ok and paths:
        _inspect_github_model_configs(info, paths, fetcher, ref.repo_id, fetch_ref)

    # Release assets are metadata only; the asset URLs and contents are never fetched.
    if api_ok:
        _inspect_github_releases(info, api, fetcher, gh_headers)

    wanted: List[str] = []
    readme = next((names[k] for k in ("readme.md", "readme.rst", "readme.txt", "readme") if k in names), None)
    if paths:
        wanted = ([readme] if readme else []) + [c for c in GH_FETCH_CANDIDATES if c in names]
    else:  # no tree (API blocked/failed): probe the common root files
        wanted = ["README.md", "requirements.txt", "pyproject.toml"]
    sizes = dict(paths)
    readme_path = "README"  # the path the README text was really read from (for hf_link_sources)
    for path in wanted[:6]:
        if sizes.get(path) and sizes[path] > _MAX_SMALL_FILE:
            info.warnings.append(f"{path} skipped (too large).")
            continue
        try:
            res = fetcher.fetch(f"https://raw.githubusercontent.com/{ref.repo_id}/{fetch_ref}/{path}", max_bytes=_MAX_SMALL_FILE)
        except HTTPStatusError as exc:
            if exc.status != 404 or paths:
                info.warnings.append(f"Could not fetch {path} (HTTP {exc.status}).")
            continue
        except NetworkError as exc:
            info.warnings.append(f"Network problem fetching {path}: {redact_secrets(str(exc))[:100]}")
            break
        except FetchError:
            continue
        text = res.body.decode("utf-8", errors="replace")
        info.fetched.append((path, len(res.body)))
        low = path.lower()
        if low.startswith("readme"):
            info.readme = text[: settings.max_readme_chars]
            readme_path = path
        elif low == "requirements.txt":
            reqs, flags = parse_requirements(text)
            info.requirements += [{**r, "source": path} for r in reqs]
            info.requirement_flags += flags
        elif low == "pyproject.toml":
            info.requirements += [{**r, "source": path} for r in _parse_pyproject(text)]
        elif low in ("setup.py", "setup.cfg"):
            # Static text parse only: literal install_requires/keywords are read via ast / configparser, never executed.
            if low == "setup.py":
                reqs, kws, problem = parse_setup_py(text)
            else:
                (reqs, kws), problem = parse_setup_cfg(text), None
            info.requirements += [{**r, "source": f"{path} install_requires"} for r in reqs]
            info.package_keywords += kws
            if not reqs:
                info.warnings.append(f"{path} present; dependencies in it were not evaluated (it is not executed)"
                                     + (f" ({problem})." if problem else "."))
        elif low == "environment.yml":
            info.warnings.append(f"{path} present; dependencies in it were not evaluated (it is not executed).")
    seen, uniq = set(), []
    for r in info.requirements:
        if r["name"] not in seen:
            seen.add(r["name"])
            uniq.append(r)
    info.requirements = uniq
    info.requirement_flags = sorted(set(info.requirement_flags))

    # Static source scan: prioritized text-only modules under configurable budgets.
    # Import relationships are recorded from AST nodes; nothing is imported or
    # executed.  We record every omission so callers never mistake it
    # for negative evidence.
    scanned_text: Dict[str, str] = {}
    source_candidates = pick_source_files(paths) if paths else []
    inspected, skipped, inaccessible, remaining = [], [], [], []
    source_bytes = 0
    for path in source_candidates:
        if len(inspected) >= settings.github_max_source_files or source_bytes >= settings.github_max_source_bytes:
            remaining.append(path)
            continue
        size = sizes.get(path)
        if size is not None and size > settings.github_max_source_file_bytes:
            skipped.append({"path": path, "reason": "per-file budget"})
            continue
        try:
            allowed = min(settings.github_max_source_file_bytes, settings.github_max_source_bytes - source_bytes)
            res = fetcher.fetch(f"https://raw.githubusercontent.com/{ref.repo_id}/{fetch_ref}/{path}", max_bytes=allowed)
        except HTTPStatusError as exc:
            inaccessible.append({"path": path, "reason": f"HTTP {exc.status}"})
            continue
        except NetworkError as exc:
            info.warnings.append(f"Network problem fetching {path}; remaining source files skipped: {redact_secrets(str(exc))[:100]}")
            inaccessible.append({"path": path, "reason": "network"})
            remaining.extend(p for p in source_candidates if p != path and p not in inspected)
            break
        except FetchError:
            inaccessible.append({"path": path, "reason": "fetch failed"})
            continue
        info.fetched.append((path, len(res.body)))
        inspected.append(path)
        source_bytes += len(res.body)
        text = res.body.decode("utf-8", errors="replace")
        scanned_text[path] = text
        scanned = scan_python_source(path, text)
        if scanned:
            info.source_scan.append(scanned)
            info.evidence_ledger.append({"source": path, "location": "AST", "finding": "static source summary",
                                         "provenance": "github static scan", "confidence": 0.8})
    info.inspection_coverage = {"tree_entries": len(paths), "source_candidates": len(source_candidates),
                                "inspected": inspected, "skipped": skipped, "inaccessible": inaccessible,
                                "remaining": remaining, "bytes_read": source_bytes,
                                "budget": {"max_files": settings.github_max_source_files,
                                           "max_bytes": settings.github_max_source_bytes,
                                           "max_file_bytes": settings.github_max_source_file_bytes}}

    # Deep repository-wide source analysis (module graph, call graph, loading sites, I/O contract). Same fetched text, ast only.
    if scanned_text or paths:
        try:
            from .source_analysis import MAX_MODULES, analyze_repository, select_modules
            limit = int(getattr(settings, "max_source_modules", 0) or MAX_MODULES)
            extra_modules, module_skips = select_modules(paths, limit=limit) if paths else ([], [])
            for path in extra_modules:
                if path in scanned_text:
                    continue
                try:
                    res = fetcher.fetch(f"https://raw.githubusercontent.com/{ref.repo_id}/{fetch_ref}/{path}",
                                        max_bytes=settings.github_max_source_file_bytes)
                except (HTTPStatusError, NetworkError, FetchError):
                    continue
                info.fetched.append((path, len(res.body)))
                scanned_text[path] = res.body.decode("utf-8", errors="replace")
            src = analyze_repository(sorted(scanned_text.items()), listed=paths, limits={"max_modules": limit})
            for s in module_skips:
                if s not in src.skipped:
                    src.skipped.append(s)
            info.source_analysis = src
            # Keep the bounded text of the files the static scan already read, so the REA stage can give REA a read-only copy
            # of them (no extra fetch). Bounded per file and in total; only text/config files that were really fetched.
            budget = settings.github_max_source_bytes
            kept = 0
            for path, text in sorted(scanned_text.items()):
                if kept >= budget or len(text) > settings.github_max_source_file_bytes:
                    continue
                info.source_text[path] = text
                kept += len(text)
        except Exception as exc:  # a static-analysis failure must never break inspection; the shallow scan still stands
            info.warnings.append(f"Deep source analysis did not complete ({type(exc).__name__}); the basic source scan is unaffected.")

    info.linked_hf_models = _find_hf_links(info.readme)
    for rid in info.linked_hf_models:  # where each link was found (provenance only; it changes no ranking or decision)
        info.hf_link_sources.setdefault(rid, []).append(readme_path)
    for sc in info.source_scan:  # from_pretrained("owner/name") literals found in code count as links too
        for rid in sc["pretrained_ids"]:
            if rid not in info.linked_hf_models and len(info.linked_hf_models) < 10:
                try:
                    validate_repo_id(rid)
                except SecurityError:
                    continue
                info.linked_hf_models.append(rid)
            if rid in info.linked_hf_models and sc["path"] not in info.hf_link_sources.setdefault(rid, []):
                info.hf_link_sources[rid].append(sc["path"])
    info.primary_hf_model, info.hf_link_roles = rank_hf_links(info.readme, ref, info.linked_hf_models)
    if info.primary_hf_model:
        # Authoritative metadata for the repo's own model, read through the same allow-listed Hub inspector (no weights).
        try:
            info.primary_model_info = _inspect_hf(parse_source_url(f"https://huggingface.co/{info.primary_hf_model}"), fetcher, settings)
        except (InspectionError, SecurityError) as exc:
            info.warnings.append(f"Metadata for the primary linked model '{info.primary_hf_model}' could not be read: "
                                 f"{redact_secrets(str(exc))[:140]}")
    return info


# --------------------------------------------------------------------------- entry points


def inspect_source(ref: SourceRef, fetcher: Optional[SafeFetcher] = None, settings: Optional[Settings] = None) -> RepoInfo:
    settings = settings or Settings()
    fetcher = fetcher or SafeFetcher(settings)
    if ref.kind == "huggingface":
        return _inspect_hf(ref, fetcher, settings)
    if ref.kind == "github":
        return _inspect_github(ref, fetcher, settings)
    raise InspectionError("Unsupported source kind.")


def inspect_url(url: str, fetcher: Optional[SafeFetcher] = None, settings: Optional[Settings] = None) -> RepoInfo:
    try:
        ref = parse_source_url(url)
    except SecurityError as exc:
        raise InspectionError(f"Rejected URL: {exc}")
    return inspect_source(ref, fetcher, settings)

