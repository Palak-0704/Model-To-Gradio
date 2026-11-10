"""Acceptance gate for a genuine independent-repository inference application.

This deliberately rejects UI-only code, session-only checks, synthetic inputs and model
initialisation without inference.  Framework adapters may call it only after a real,
evidence-backed fixture has travelled through the loaded model.
"""
from __future__ import annotations

import ast
from typing import Any, Iterator, Mapping, Optional


class NativeReadinessError(ValueError):
    pass


def require_code_generation_prerequisites(evidence: Mapping[str, Any]) -> None:
    """Static facts required before OpenCode may write native integration.

    This deliberately does *not* require acquisition, a checksum, a local file,
    or a fixture execution.  Those facts prove that code may load and run a
    particular artifact and therefore belong exclusively to ``require_end_to_end``.
    The candidate identity still has to be pinned so generated code cannot pick
    an arbitrary repository file at runtime.
    """
    required = ("adapter_verified", "contract_verified", "preprocessing_evidence",
                "postprocessing_evidence", "artifact_path", "revision")
    missing = [key for key in required if evidence.get(key) in (None, "", False)]
    if missing:
        raise NativeReadinessError("Native code-generation evidence is incomplete: " + ", ".join(missing))


def require_generation_prerequisites(validation: Mapping[str, Any]) -> None:
    """Backward-compatible name for the static code-generation gate."""
    require_code_generation_prerequisites(validation)


def require_end_to_end(validation: Mapping[str, Any]) -> None:
    required = ("artifact_verified", "model_loaded", "preprocessing_verified", "inference_executed",
                "postprocessing_verified", "real_outputs_verified", "fixture_evidence")
    missing = [key for key in required if validation.get(key) is not True]
    if missing:
        raise NativeReadinessError("Working inference is not established: " + ", ".join(missing))
    if validation.get("fixture_kind") not in ("documented", "repository_fixture", "user_provided"):
        raise NativeReadinessError("Inference fixture provenance is not evidence-backed.")
    if validation.get("synthetic_input"):
        raise NativeReadinessError("Synthetic or random tensors cannot establish working inference readiness.")


def readiness_from_validation(validation: Mapping[str, Any]) -> tuple[str, str]:
    try:
        require_end_to_end(validation)
    except NativeReadinessError as exc:
        return "scaffold", str(exc)
    return "ready", "Verified end-to-end inference completed with an evidence-backed fixture."


_LOADER_NAMES = ("get_model", "get_pipe")
SCAFFOLD_MARKER = "M2G_NATIVE_SCAFFOLD"


def _own_nodes(func: ast.AST) -> Iterator[ast.AST]:
    """Every node of a function's own body, not descending into nested functions, lambdas or classes."""
    stack = list(getattr(func, "body", []))
    while stack:
        node = stack.pop()
        yield node
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            stack.extend(ast.iter_child_nodes(node))


def _raises_not_implemented(node: ast.AST) -> bool:
    if not isinstance(node, ast.Raise) or node.exc is None:
        return False
    exc = node.exc.func if isinstance(node.exc, ast.Call) else node.exc
    return isinstance(exc, ast.Name) and exc.id == "NotImplementedError"


def incomplete_loader_reason(code: str) -> Optional[str]:
    """Why ``code`` (loader.py + app.py as one text, or either file) is a placeholder and not a working model loader; None when it is not.

    Static only: the code is parsed, never executed. Two things make a package a placeholder, whatever its manifest or analysis says:
    the scaffold marker (``M2G_NATIVE_SCAFFOLD = True``), and a model loader (``get_model``/``get_pipe``) that can never return because
    it unconditionally raises ``NotImplementedError``. Such a package must never be classified as inference-ready or be deployed.
    A syntax error is not this function's concern (the validators report it), so it answers None for unparsable code.
    """
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError):
        return None
    for node in tree.body:
        if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and node.value.value is True
                and any(isinstance(t, ast.Name) and t.id == SCAFFOLD_MARKER for t in node.targets)):
            return ("It is a scaffold (" + SCAFFOLD_MARKER + " = True): the repository's model architecture and inference API were not "
                    "established from its metadata, so no model loader or inference code was generated.")
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in _LOADER_NAMES:
            own = list(_own_nodes(node))
            if any(_raises_not_implemented(n) for n in node.body) and not any(isinstance(n, ast.Return) for n in own):
                return (f"The model loader {node.name}() is a placeholder: it raises NotImplementedError and never returns a model, "
                        "so there is no working inference to deploy.")
    return None
