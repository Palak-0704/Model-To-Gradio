"""Bounded, repository-neutral inference contracts and approved loader selection.

This module consumes metadata already inspected by :mod:`inspector`; it never fetches,
imports, evaluates, or executes repository content.  A contract is deliberately sparse:
unknown facts are omitted rather than guessed.  Loader adapters are capability based, not
repository based, and default to rejection.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Sequence


@dataclass(frozen=True)
class LoaderAdapter:
    name: str
    framework: str
    weight_formats: Sequence[str]
    required_fields: Sequence[str]
    security: Sequence[str]

    def supports(self, contract: Mapping[str, Any]) -> bool:
        if contract.get("framework") != self.framework:
            return False
        if not set(contract.get("weight_formats") or ()).intersection(self.weight_formats):
            return False
        return all(contract.get(key) for key in self.required_fields)


# ONNX is a data-only graph format.  This is intentionally the only native-repository
# adapter until another format has an equally bounded, non-code-executing loader contract.
_APPROVED = (
    LoaderAdapter("onnxruntime-data-only", "ONNX Runtime", (".onnx",), ("inputs", "outputs"),
                  ("never execute repository code", "never load pickle weights", "no trust_remote_code")),
)


def select_loader(contract: Mapping[str, Any]) -> Optional[LoaderAdapter]:
    """Return an approved capability adapter, or ``None`` when loading is unsupported."""
    for adapter in _APPROVED:
        if adapter.supports(contract):
            return adapter
    return None


def build_contract(*, frameworks: Sequence[str], task: Optional[str], inputs: Sequence[str], outputs: Sequence[str],
                   native: Mapping[str, Any], revision: Optional[str]) -> Dict[str, Any]:
    """Normalize static evidence into JSON-safe contract data with explicit provenance."""
    weight_files = list(native.get("weight_files") or []) + list(native.get("release_weight_assets") or [])
    formats = sorted({"." + p.rsplit(".", 1)[1].lower() for p in weight_files if "." in p})
    architecture = list(native.get("architecture_files") or [])
    entries = list(native.get("inference_entrypoints") or [])
    evidence = []
    for path in architecture:
        evidence.append({"type": "architecture_config", "source": path, "revision": revision,
                         "method": "repository_tree", "confidence": "structural"})
    for path in entries:
        evidence.append({"type": "inference_entrypoint", "source": path, "revision": revision,
                         "method": "repository_tree", "confidence": "structural"})
    framework = next((f for f in frameworks if f in ("ONNX Runtime", "PyTorch", "TensorFlow")), None)
    return {"architecture": architecture or None, "framework": framework, "task": task,
            "variants": [], "inputs": list(inputs), "outputs": list(outputs),
            "preprocessing": [], "postprocessing": [], "configuration": architecture,
            "weight_formats": formats, "weight_files": weight_files, "entrypoints": entries,
            "revision": revision, "dependencies": list(native.get("frameworks") or []),
            "security": ["repository code was not executed", "weights were not downloaded", "unsafe pickle is unsupported"],
            "evidence": evidence}
