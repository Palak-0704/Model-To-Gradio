"""A unified, provider-neutral repository representation.

The Hugging Face inspector and the GitHub inspector each produce an :class:`~m2g.inspector.RepoInfo` with
provider-specific fields. This module normalizes the two into one :class:`RepositoryModel` so every layer above (analysis,
evidence, claims, REA planning, the final report) can talk about "the repository" without branching on the provider.

Normalization never invents facts and never loses provenance: a value that the provider did not supply stays ``None``, the
original ``RepoInfo`` is kept as ``raw``, and every file/artifact carries a stable id derived from its path so the same
thing has the same id across collection paths. ``gaps`` and ``missing`` keep the honest distinction between "the content is
unknown" (a gap: it could not be read) and "the content is absent" (missing).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

# File categories used across the platform. Kept small and provider-neutral.
CAT_SOURCE = "source"
CAT_CONFIG = "config"
CAT_DEPENDENCY = "dependency"
CAT_WEIGHT = "weight"
CAT_README = "readme"
CAT_NOTEBOOK = "notebook"
CAT_DATA = "data"
CAT_OTHER = "other"

_WEIGHT_EXTS = (".safetensors", ".bin", ".pt", ".pth", ".ckpt", ".gguf", ".onnx", ".h5", ".msgpack", ".tflite", ".keras",
                ".pb", ".pkl", ".pickle", ".joblib", ".npy", ".npz", ".ot", ".engine", ".trt", ".pdparams", ".params", ".mar")
_CONFIG_NAMES = ("config.json", "model_index.json", "tokenizer_config.json", "preprocessor_config.json", "processor_config.json",
                 "generation_config.json", "adapter_config.json", "config.yaml", "config.yml", "model.yaml", "arch.json")
_DEPENDENCY_NAMES = ("requirements.txt", "requirements-dev.txt", "pyproject.toml", "setup.py", "setup.cfg", "environment.yml",
                     "environment.yaml", "pipfile", "poetry.lock", "conda.yml", "conda.yaml")


def stable_id(kind: str, *parts: str) -> str:
    """A stable, path-based identifier (``file:pkg/model.py``, ``artifact:weights.safetensors``). Deterministic across runs."""
    tail = "/".join(str(p).strip("/") for p in parts if p is not None and str(p) != "")
    return f"{kind}:{tail}" if tail else f"{kind}:"


def categorize(path: str) -> str:
    """The category of one repository path. Never excludes anything: an unknown file is ``other``."""
    low = path.lower()
    name = low.rsplit("/", 1)[-1]
    if name in ("readme.md", "readme.rst", "readme.txt", "readme") or name.startswith("readme."):
        return CAT_README
    if name.endswith(".ipynb"):
        return CAT_NOTEBOOK
    if low.endswith((".py", ".pyi", ".pyx")):
        return CAT_SOURCE
    if name in _CONFIG_NAMES or (name.endswith((".json", ".yaml", ".yml")) and any(k in name for k in ("config", "model", "arch"))):
        return CAT_CONFIG
    if name in _DEPENDENCY_NAMES or name.startswith(("requirements", "constraints")):
        return CAT_DEPENDENCY
    if low.endswith(_WEIGHT_EXTS):
        return CAT_WEIGHT
    if low.endswith((".csv", ".tsv", ".parquet", ".arrow", ".jsonl", ".ndjson", ".npy", ".npz")):
        return CAT_DATA
    return CAT_OTHER


@dataclass
class FileEntry:
    """One file in the repository, with a stable id and the provider's own size when it reported one."""

    id: str
    path: str
    category: str
    size: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "path": self.path, "category": self.category, "size": self.size}


@dataclass
class RepositoryModel:
    """The normalized repository. Every field is derived from the inspector's ``RepoInfo``; nothing is fetched or executed."""

    provider: str  # huggingface | github
    repo_id: str
    owner: str = ""
    name: str = ""
    requested_revision: Optional[str] = None
    resolved_revision: Optional[str] = None
    revision_kind: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    files: List[FileEntry] = field(default_factory=list)
    source_files: List[str] = field(default_factory=list)
    config_files: List[str] = field(default_factory=list)
    dependency_files: List[str] = field(default_factory=list)
    artifact_files: List[str] = field(default_factory=list)
    readme_files: List[str] = field(default_factory=list)
    entrypoints: List[str] = field(default_factory=list)
    local_modules: List[str] = field(default_factory=list)
    dependencies: List[Dict[str, str]] = field(default_factory=list)
    artifact_inventory: List[Dict[str, Any]] = field(default_factory=list)
    coverage: Dict[str, Any] = field(default_factory=dict)
    gaps: List[Dict[str, str]] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    raw: Any = field(default=None, repr=False, compare=False)

    def by_category(self, category: str) -> List[FileEntry]:
        return [f for f in self.files if f.category == category]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "provider": self.provider, "repo_id": self.repo_id, "owner": self.owner, "name": self.name,
            "requested_revision": self.requested_revision, "resolved_revision": self.resolved_revision,
            "revision_kind": self.revision_kind, "metadata": dict(self.metadata),
            "files": [f.to_dict() for f in self.files], "source_files": list(self.source_files),
            "config_files": list(self.config_files), "dependency_files": list(self.dependency_files),
            "artifact_files": list(self.artifact_files), "readme_files": list(self.readme_files),
            "entrypoints": list(self.entrypoints), "local_modules": list(self.local_modules),
            "dependencies": [dict(d) for d in self.dependencies], "artifact_inventory": [dict(a) for a in self.artifact_inventory],
            "coverage": dict(self.coverage), "gaps": [dict(g) for g in self.gaps], "missing": list(self.missing),
            "warnings": list(self.warnings),
        }


def _size_map(listing: Sequence[Tuple[str, Optional[int]]]) -> Dict[str, Optional[int]]:
    return {p: s for p, s in listing}


def _artifact_entry(path: str, size: Optional[int], fmt: Optional[str]) -> Dict[str, Any]:
    return {"id": stable_id("artifact", path), "path": path, "size": size, "format": fmt,
            "inspection_status": "listed" if fmt is None else "listed", "metadata": {}}


def _format_of(path: str) -> Optional[str]:
    low = path.lower()
    for ext, fmt in ((".safetensors", "safetensors"), (".gguf", "gguf"), (".ggml", "gguf"), (".onnx", "onnx"),
                     (".bin", "pytorch-pickle"), (".pt", "pytorch"), (".pth", "pytorch"), (".ckpt", "pytorch"),
                     (".h5", "keras/tf"), (".keras", "keras"), (".tflite", "tflite"), (".pb", "tensorflow-graph"),
                     (".msgpack", "flax-msgpack"), (".pkl", "pickle"), (".pickle", "pickle"), (".joblib", "joblib"),
                     (".npz", "numpy"), (".npy", "numpy"), (".ot", "openvino")):
        if low.endswith(ext):
            return fmt
    return None


def from_info(info: Any, *, source_analysis: Optional[Any] = None) -> RepositoryModel:
    """Build the normalized model from an inspector ``RepoInfo`` (Hugging Face or GitHub). Nothing is fetched or executed.

    ``source_analysis`` (a :class:`m2g.source_analysis.SourceAnalysis`) is optional and, when given, fills the entry points,
    local modules and a source-derived artifact/coverage picture; the caller runs it from text it already fetched."""
    ref = info.source
    provider = str(getattr(ref, "kind", "") or "unknown")
    listing: List[Tuple[str, Optional[int]]] = list(getattr(info, "files", []) or [])
    sizes = _size_map(listing)
    files: List[FileEntry] = []
    for path, size in listing:
        files.append(FileEntry(stable_id("file", path), path, categorize(path), size))
    by_cat: Dict[str, List[str]] = {}
    for f in files:
        by_cat.setdefault(f.category, []).append(f.path)

    release_assets: List[Tuple[str, Optional[int]]] = list(getattr(info, "release_assets", []) or [])
    artifact_paths = list(by_cat.get(CAT_WEIGHT, []))
    inventory = [_artifact_entry(p, sizes.get(p), _format_of(p)) for p in artifact_paths]
    for asset, size in release_assets:
        if str(asset).lower().endswith(_WEIGHT_EXTS):
            inventory.append(_artifact_entry(f"release:{asset}", size, _format_of(asset)))

    dependencies = [dict(r) for r in (getattr(info, "requirements", []) or [])]
    entrypoints: List[str] = []
    local_modules: List[str] = []
    coverage: Dict[str, Any] = {}
    if source_analysis is not None:
        entrypoints = [f"{e['module']}:{e['function']}" for e in getattr(source_analysis, "entrypoints", [])][:60]
        local_modules = [m.module for m in getattr(source_analysis, "modules", [])][:120]
        coverage = dict(getattr(source_analysis, "coverage", {}) or {})

    missing: List[str] = []
    if not files:
        missing.append("file listing (the provider did not return one)")
    if not by_cat.get(CAT_README):
        missing.append("README / documentation")
    if not by_cat.get(CAT_CONFIG) and not artifact_paths:
        missing.append("model configuration or weight artifact")

    metadata = {
        "description": getattr(info, "description", None),
        "topics": list(getattr(info, "topics", []) or []),
        "language": getattr(info, "language", None),
        "license": getattr(info, "license", None),
        "pipeline_tag": getattr(info, "pipeline_tag", None),
        "library_name": getattr(info, "library_name", None),
        "downloads": getattr(info, "downloads", None),
        "likes": getattr(info, "likes", None),
        "private": bool(getattr(info, "private", False)),
        "gated": bool(getattr(info, "gated", False)),
        "default_branch": getattr(info, "default_branch", None),
        "homepage": getattr(info, "homepage", "") or None,
    }
    metadata = {k: v for k, v in metadata.items() if v not in (None, [], "")}

    # Provider-specific provenance: GitHub pins a commit and records gaps; Hugging Face pins a revision_sha and has neither.
    is_github = provider == "github"
    resolved = (getattr(info, "commit_sha", None) if is_github else getattr(info, "revision_sha", None))
    gaps = [dict(g) for g in (getattr(info, "inspection_gaps", []) or [])] if is_github else []

    return RepositoryModel(
        provider=provider,
        repo_id=str(getattr(ref, "repo_id", "") or ""),
        owner=str(getattr(ref, "owner", "") or ""),
        name=str(getattr(ref, "name", "") or ""),
        requested_revision=(getattr(info, "requested_revision", None) if is_github else None) or getattr(ref, "revision", None),
        resolved_revision=resolved,
        revision_kind=getattr(info, "revision_kind", None) if is_github else None,
        metadata=metadata,
        files=files,
        source_files=by_cat.get(CAT_SOURCE, []),
        config_files=by_cat.get(CAT_CONFIG, []),
        dependency_files=by_cat.get(CAT_DEPENDENCY, []),
        artifact_files=artifact_paths,
        readme_files=by_cat.get(CAT_README, []),
        entrypoints=entrypoints,
        local_modules=local_modules,
        dependencies=dependencies,
        artifact_inventory=inventory,
        coverage=coverage,
        gaps=gaps,
        missing=missing,
        warnings=[str(w) for w in (getattr(info, "warnings", []) or [])][:40],
        raw=info,
    )
