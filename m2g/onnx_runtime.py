"""Approved, data-only ONNX Runtime access used by native-model adapters.

No repository module is imported.  Callers supply a local, regular ``.onnx`` path that
has already been provenance-checked by their deployment mechanism.  The adapter is kept
separate from inspection: inspection never opens model artifacts.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence


class OnnxRuntimeError(RuntimeError):
    pass


@dataclass(frozen=True)
class OnnxArtifact:
    """An explicit, immutable artifact declaration; discovery alone is not one."""
    path: str
    revision: str
    size: int
    sha256: str


def select_artifact(candidates: Sequence[Mapping[str, Any]], *, path: str, revision: str) -> OnnxArtifact:
    """Select one declared artifact exactly; ambiguous filename matching is forbidden."""
    matches = [c for c in candidates if isinstance(c, Mapping) and c.get("path") == path and c.get("revision") == revision]
    if len(matches) != 1:
        raise OnnxRuntimeError("Select exactly one ONNX artifact by its declared path and pinned revision.")
    item = matches[0]
    size, digest = item.get("size"), item.get("sha256")
    if not isinstance(size, int) or size <= 0 or not isinstance(digest, str) or len(digest) != 64:
        raise OnnxRuntimeError("The selected ONNX artifact requires a positive declared size and SHA-256 digest.")
    return OnnxArtifact(path, revision, size, digest.lower())


def verify_artifact(artifact: OnnxArtifact, local_path: Any) -> Path:
    """Verify a user-provided/acquired artifact before any runtime opens it."""
    target = Path(local_path)
    if target.suffix.lower() != ".onnx" or target.is_symlink() or not target.is_file():
        raise OnnxRuntimeError("Resolved artifact must be a regular .onnx file.")
    if target.stat().st_size != artifact.size:
        raise OnnxRuntimeError("Resolved ONNX artifact size does not match its declaration.")
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    if digest != artifact.sha256:
        raise OnnxRuntimeError("Resolved ONNX artifact SHA-256 does not match its declaration.")
    return target


@dataclass(frozen=True)
class TensorSpec:
    name: str
    type: str
    shape: List[Optional[int]]


def _shape(value: Any) -> List[Optional[int]]:
    out: List[Optional[int]] = []
    for dim in value or ():
        out.append(dim if isinstance(dim, int) and dim >= 0 else None)
    return out


def tensor_specs(nodes: Sequence[Any]) -> List[TensorSpec]:
    return [TensorSpec(str(node.name), str(node.type), _shape(getattr(node, "shape", ()))) for node in nodes]


def providers(ort: Any, prefer_gpu: bool = False) -> List[str]:
    available = set(ort.get_available_providers())
    chosen: List[str] = []
    if prefer_gpu and "CUDAExecutionProvider" in available:
        chosen.append("CUDAExecutionProvider")
    if "CPUExecutionProvider" in available:
        chosen.append("CPUExecutionProvider")
    if not chosen:
        raise OnnxRuntimeError("ONNX Runtime exposes neither CUDAExecutionProvider nor CPUExecutionProvider.")
    return chosen


class OnnxSession:
    """Lazy ONNX session with metadata inspection and exact named-tensor execution."""
    def __init__(self, model_path: Any, *, prefer_gpu: bool = False, ort_module: Any = None):
        self.path = Path(model_path)
        self.prefer_gpu, self._ort, self._session = prefer_gpu, ort_module, None

    def _runtime(self) -> Any:
        if self._ort is not None:
            return self._ort
        try:
            import onnxruntime as ort  # type: ignore
        except ImportError as exc:
            raise OnnxRuntimeError("onnxruntime is required to load this approved ONNX artifact.") from exc
        self._ort = ort
        return ort

    def load(self) -> "OnnxSession":
        if self._session is not None:
            return self
        if self.path.suffix.lower() != ".onnx" or not self.path.is_file() or self.path.is_symlink():
            raise OnnxRuntimeError("ONNX model path must be an existing regular .onnx file.")
        try:
            self._session = self._runtime().InferenceSession(str(self.path), providers=providers(self._runtime(), self.prefer_gpu))
        except Exception as exc:
            raise OnnxRuntimeError(f"Could not load ONNX model: {type(exc).__name__}: {exc}") from exc
        return self

    @property
    def inputs(self) -> List[TensorSpec]:
        return tensor_specs(self.load()._session.get_inputs())

    @property
    def outputs(self) -> List[TensorSpec]:
        return tensor_specs(self.load()._session.get_outputs())

    def run(self, tensors: Dict[str, Any]) -> Dict[str, Any]:
        expected = {spec.name for spec in self.inputs}
        got = set(tensors)
        if got != expected:
            raise OnnxRuntimeError("Inputs must exactly match the model input names; missing=" + ",".join(sorted(expected - got)) +
                                   " unexpected=" + ",".join(sorted(got - expected)))
        try:
            values = self.load()._session.run([spec.name for spec in self.outputs], tensors)
        except Exception as exc:
            raise OnnxRuntimeError(f"ONNX inference failed: {type(exc).__name__}: {exc}") from exc
        return {spec.name: value for spec, value in zip(self.outputs, values)}
