"""Execution of approved native ONNX contracts after artifact authorization.

The executor has no repository import path.  It accepts only named tensors prepared by
evidence-backed preprocessing and returns raw runtime outputs for evidence-backed
postprocessing to consume.
"""
from __future__ import annotations

from typing import Any, Dict, Mapping

from .onnx_runtime import OnnxRuntimeError, OnnxSession, verify_artifact


class NativeExecutionError(RuntimeError):
    pass


def execute_verified_onnx(artifact: Any, local_path: Any, tensors: Mapping[str, Any], *, prefer_gpu: bool = False,
                          ort_module: Any = None) -> Dict[str, Any]:
    """Verify, load and run an approved artifact; preprocessing is deliberately external."""
    try:
        path = verify_artifact(artifact, local_path)
        return OnnxSession(path, prefer_gpu=prefer_gpu, ort_module=ort_module).run(dict(tensors))
    except OnnxRuntimeError as exc:
        raise NativeExecutionError(str(exc)) from exc
