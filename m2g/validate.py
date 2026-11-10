"""Static validation of generated artifacts (Gradio app source and Colab notebook).

Nothing here executes generated code. Everything is checked with ``ast`` / ``json`` and the secret scanner.
A generated artifact that fails these checks must never be written to disk or offered for download.
"""
from __future__ import annotations

import ast
import builtins
import json
import re
from typing import Any, Dict, List, Optional, Tuple

from .capabilities import is_inference_function_name, slug
from .config import Settings
from .gradio_compat import check_tree as _compat_check_tree
from .security import (
    SecurityError,
    assert_no_secrets,
    validate_pip_requirement,
    validate_repo_id,
    validate_revision,
)


class ValidationError(ValueError):
    """Generated code or notebook failed a safety or structure check."""


# Controlled allowlist: top-level import name -> pip requirement (None = standard library, nothing to install). It is the ONLY
# place where an import can become a pip package, so a generated app can never cause an arbitrary package to be installed.
IMPORT_PIP_ALLOWLIST: Dict[str, Optional[str]] = {
    "os": None, "tempfile": None, "json": None, "re": None, "io": None, "wave": None, "math": None, "typing": None,
    "inspect": None,  # inspect: only signature() in the diffusers template
    "gradio": "gradio>=5.0", "torch": "torch>=2.1.0", "numpy": "numpy", "transformers": "transformers>=4.44.0",
    "diffusers": "diffusers>=0.30.0", "sentence_transformers": "sentence-transformers>=3.0.0", "PIL": "pillow",
    "soundfile": "soundfile", "librosa": "librosa",  # local audio decoding/resampling (no ffmpeg needed for wav/flac/ogg)
}
# Modules a generated app may import (top-level package name).
APP_ALLOWED_IMPORTS = set(IMPORT_PIP_ALLOWLIST)
# The generated package's own module: app.py imports its model code from loader.py (same folder). It is not a pip package.
LOCAL_PACKAGE_MODULES = frozenset({"loader"})
# Notebook code cells may additionally import these (environment checks, Colab secrets).
NOTEBOOK_ALLOWED_IMPORTS = APP_ALLOWED_IMPORTS | {
    "sys", "platform", "shutil", "subprocess", "importlib", "pathlib", "textwrap", "google", "runpy",
}
FORBIDDEN_CALLS = {"eval", "exec", "compile", "__import__", "system", "popen", "spawn", "fork", "execv", "execl",
                   "loads", "load", "unlink", "rmtree", "remove", "rmdir", "urlopen", "urlretrieve"}
# The notebook legitimately runs one fixed `subprocess.run([...])` for nvidia-smi; handled separately.
NOTEBOOK_EXTRA_OK_CALLS = {"run"}
FORBIDDEN_NAMES = {"pickle", "marshal", "shelve", "ctypes", "socket", "requests", "urllib", "httpx", "paramiko"}
_MAGIC_LINE = re.compile(r"^\s*[%!]")

# Library names that are NOT pre-imported by the platform header (it only provides os, gr, torch). The generated sections must import
# them; when one is used bare, the undefined-name error names the module that really exports it. This table only improves the
# message: it never makes a name count as defined.
KNOWN_SYMBOL_MODULES: Dict[str, str] = {
    "BitsAndBytesConfig": "transformers", "AutoTokenizer": "transformers", "AutoProcessor": "transformers",
    "AutoModel": "transformers", "AutoModelForCausalLM": "transformers", "AutoModelForSeq2SeqLM": "transformers",
    "AutoModelForSequenceClassification": "transformers", "AutoFeatureExtractor": "transformers", "AutoImageProcessor": "transformers",
    "AutoConfig": "transformers", "pipeline": "transformers", "TextIteratorStreamer": "transformers",
    "DiffusionPipeline": "diffusers", "SentenceTransformer": "sentence_transformers",
}

# Names the platform-owned header (gradio_gen._HEADER) binds before any generated section runs, including the runtime consent constant
# LOAD_SAFETENSORS that every loader must use for use_safetensors. Generated sections cannot rebind them (gradio_gen._PROTECTED_CONSTANTS),
# and the header always precedes them in the assembled app, so using one is not an undefined name. MODEL_ID, REVISION and
# TRUST_REMOTE_CODE are not listed: validate_app_code requires them as literals itself. Every other name must still be defined or imported.
PLATFORM_HEADER_NAMES = frozenset({
    "TASK", "USE_SAFETENSORS", "PREQUANTIZED", "USE_CUDA", "LOAD_IN_4BIT", "HF_TOKEN", "DEVICE", "DTYPE",
    "WEIGHTS_SAFETENSORS_MISSING", "ALLOW_PICKLE_WEIGHTS", "LOAD_SAFETENSORS", "PICKLE_WEIGHTS_NOTICE", "weights_failure_hint",
})

# Symbols whose USE needs a pip package that the app never imports by name (transformers loads bitsandbytes lazily), so the
# import-based requirement derivation cannot see it. symbol or keyword name -> (module, pip requirement).
SYMBOL_PIP_REQUIREMENTS: Dict[str, Tuple[str, str]] = {
    "BitsAndBytesConfig": ("bitsandbytes", "bitsandbytes>=0.43.0"),
    "load_in_4bit": ("bitsandbytes", "bitsandbytes>=0.43.0"),
    "load_in_8bit": ("bitsandbytes", "bitsandbytes>=0.43.0"),
    # transformers refuses to load with device_map unless accelerate is installed
    "device_map": ("accelerate", "accelerate>=0.30.0"),
}


def _imports(tree: ast.AST) -> set:
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                raise ValidationError("Relative imports are not allowed in generated code.")
            found.add((node.module or "").split(".")[0])
    return found


def combine_package_source(app_code: str, loader_code: Optional[str]) -> str:
    """One program text for the whole-program static checks of a generated package: loader.py followed by app.py. Without a loader
    (a single-file app) the app code is returned unchanged."""
    return app_code if not loader_code else loader_code.rstrip("\n") + "\n\n\n" + app_code


def require_package_link(app_code: str, loader_code: Optional[str]) -> None:
    """app.py and loader.py are one package: when a loader.py exists app.py must import from it, and an app.py that imports it needs
    the file. Without this a package whose loader.py is missing (or ignored) could pass the whole-program checks."""
    try:
        tree = ast.parse(app_code)
    except SyntaxError as exc:
        raise ValidationError(f"Generated app is not valid Python: {exc}")
    imports_loader = any((isinstance(n, ast.ImportFrom) and n.level == 0 and (n.module or "").split(".")[0] in LOCAL_PACKAGE_MODULES)
                         or (isinstance(n, ast.Import) and any(a.name.split(".")[0] in LOCAL_PACKAGE_MODULES for a in n.names))
                         for n in ast.walk(tree))
    has_loader = bool(loader_code and loader_code.strip())
    if imports_loader and not has_loader:
        raise ValidationError("app.py imports loader.py, but loader.py is missing from the package.")
    if has_loader and not imports_loader:
        raise ValidationError("loader.py exists but app.py does not import it; app.py must use the model code in loader.py.")


def imports_to_requirements(code: str) -> List[Any]:
    """[(module, pip requirement)] for the third-party imports the generated app really uses, via IMPORT_PIP_ALLOWLIST.
    Any import outside the allowlist is rejected (ValidationError): it is never mapped to a package or installed."""
    try:
        mods = _imports(ast.parse(code))
    except SyntaxError as exc:
        raise ValidationError(f"Generated app is not valid Python: {exc}")
    bad = mods - APP_ALLOWED_IMPORTS
    if bad:
        raise ValidationError(f"Generated app imports unsupported modules (no pip package is mapped for them): {sorted(bad)}")
    return sorted((m, IMPORT_PIP_ALLOWLIST[m]) for m in mods if IMPORT_PIP_ALLOWLIST.get(m))


def symbols_to_requirements(code: str) -> List[Any]:
    """[(module, pip requirement)] for packages the generated app needs because of the symbols/keywords it uses (for example
    ``BitsAndBytesConfig`` or ``load_in_4bit=`` need ``bitsandbytes``), as listed in SYMBOL_PIP_REQUIREMENTS. Complements
    ``imports_to_requirements``, which only sees import statements."""
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        raise ValidationError(f"Generated app is not valid Python: {exc}")
    used = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Name):
            used.add(n.id)
        elif isinstance(n, ast.Attribute):
            used.add(n.attr)
        elif isinstance(n, ast.keyword) and n.arg:
            used.add(n.arg)
        elif isinstance(n, ast.alias):
            used.add(n.name.split(".")[-1])
        elif isinstance(n, ast.Constant) and isinstance(n.value, str):
            used.add(n.value)  # loader options are often passed as dict keys: load_kwargs["device_map"] = "auto"
    return sorted({SYMBOL_PIP_REQUIREMENTS[s] for s in used if s in SYMBOL_PIP_REQUIREMENTS})


_TAB_ATTRS = frozenset({"Tabs", "Tab", "TabItem", "TabbedInterface"})


def validate_capability_interfaces(code: str, supported: List[Any], uncertain: List[str] = ()) -> None:
    """One interface per evidence-supported capability, none for uncertain ones. ``supported`` is [(capability name, chat flag)].
    A single capability needs no tabs. Several need one ``gr.Blocks`` app with a horizontal ``gr.Tabs`` container and one separate
    ``gr.Tab`` per capability; each tab holds its own ``predict_<name>`` / ``chat_fn_<name>`` handler, wired inside that tab."""
    tree = ast.parse(code)
    funcs = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
    uses_tabs = any(isinstance(n, ast.Attribute) and n.attr in _TAB_ATTRS for n in ast.walk(tree))
    uses_blocks = any(
        isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "Blocks"
        for n in ast.walk(tree)
    )
    if len(supported) <= 1:
        if uses_tabs:
            raise ValidationError("A single-capability model gets a simple interface; tabs are not needed.")
    else:
        if not uses_blocks:
            raise ValidationError("Multiple supported capabilities require a gr.Blocks app with horizontal gr.Tabs, "
                                  "not a single gr.Interface.")
        interface_bodies = []
        expected_handlers = set()
        for name, _chat in supported:
            expected = {f"predict_{slug(name)}", f"chat_fn_{slug(name)}"}
            matches = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in expected]
            if not matches:
                raise ValidationError(f"Capability '{name}' needs its own inference function predict_{slug(name)}.")
            expected_handlers.add(matches[0].name)
            # Distinct function names alone can hide a copy/paste UI that sends
            # every task through identical inference. Keep the check structural:
            # task-specific prompts, controls, or dispatch arguments produce a
            # different AST without executing generated code.
            interface_bodies.append(
                tuple(ast.dump(statement, annotate_fields=True, include_attributes=False)
                      for statement in matches[0].body)
            )
        if len(set(interface_bodies)) != len(interface_bodies):
            raise ValidationError("Each supported capability needs distinct task-specific inference logic.")
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)]
        withs = [n for n in ast.walk(tree) if isinstance(n, ast.With)]

        def _with_uses(node: ast.With, names: set) -> bool:
            return any(isinstance(i.context_expr, ast.Call) and _call_name(i.context_expr) in names for i in node.items)

        if not any(_with_uses(w, {"Tabs"}) for w in withs):
            raise ValidationError("Multiple supported capabilities need a horizontal `with gr.Tabs():` container.")
        tab_blocks = [w for w in withs if _with_uses(w, {"Tab", "TabItem"})]
        if len(tab_blocks) < len(supported):
            raise ValidationError("Each supported capability needs its own `with gr.Tab(...)` section.")
        # The retired layout: Column/Row sections switched by navigation buttons through gr.update(visible=...). Capabilities
        # must be real tabs instead. (A few conditional controls inside a tab are fine; only the section-switching pattern is.)
        switched = [n for n in calls if _call_name(n) in {"Column", "Row", "Group"} and any(k.arg == "visible" for k in n.keywords)]
        updates = [n for n in calls if _call_name(n) == "update" and any(k.arg == "visible" for k in n.keywords)]
        if len(switched) >= len(supported) and len(updates) >= len(supported):
            raise ValidationError("Capabilities must be separate gr.Tab sections, not visibility-switched sections behind "
                                  "navigation buttons.")
        funcs_by_name = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}

        def _wired(node: ast.AST) -> set:
            """Capability handlers connected (``fn=`` or the first argument of an event/Interface call) inside ``node``, following
            calls to non-handler module-level helper functions (for example a ``build_tab_x()`` helper)."""
            found, helpers, queue = set(), set(), [node]
            while queue:
                for c in ast.walk(queue.pop()):
                    if not isinstance(c, ast.Call):
                        continue
                    found.update(k.value.id for k in c.keywords if k.arg == "fn" and isinstance(k.value, ast.Name))
                    if isinstance(c.func, ast.Attribute) and c.args and isinstance(c.args[0], ast.Name):
                        found.add(c.args[0].id)
                    callee = c.func.id if isinstance(c.func, ast.Name) else None
                    if callee in funcs_by_name and callee not in expected_handlers and callee not in helpers:
                        helpers.add(callee)
                        queue.append(funcs_by_name[callee])
            return found & expected_handlers

        per_tab = [_wired(t) for t in tab_blocks]
        for handlers in per_tab:
            if len(handlers) > 1:
                raise ValidationError("Each gr.Tab must hold exactly one capability's inference handler; one tab wires "
                                      + ", ".join(sorted(handlers)) + ".")
        missing = sorted(expected_handlers - set().union(*per_tab))
        if missing:
            raise ValidationError("Each capability needs a real inference handler connected inside its own gr.Tab: "
                                  + ", ".join(missing))
    for name in uncertain:
        if {f"predict_{slug(name)}", f"chat_fn_{slug(name)}"} & funcs:
            raise ValidationError(f"Capability '{name}' is uncertain and must not get an interface.")


def _call_name(node: ast.Call) -> str:
    f = node.func
    return f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")


# `eval` is both Python's dynamic-code builtin and PyTorch's ``Module.eval()`` (switch a model to evaluation mode). Only the second is
# allowed: a METHOD call written ``<expr>.eval()`` with no arguments. The builtin always needs a source argument, so a bare
# ``eval(src)`` and ``builtins.eval(src)`` stay forbidden; anything reached through a builtins module is rejected even with no argument.
_BUILTINS_MODULE_NAMES = frozenset({"builtins", "__builtins__", "__builtin__"})


def _is_eval_mode_call(node: ast.Call) -> bool:
    f = node.func
    if not (isinstance(f, ast.Attribute) and f.attr == "eval") or node.args or node.keywords:
        return False
    return not any((isinstance(n, ast.Name) and n.id in _BUILTINS_MODULE_NAMES)
                   or (isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value in _BUILTINS_MODULE_NAMES)
                   for n in ast.walk(f.value))


def _is_forbidden_call(node: ast.Call, allowed: frozenset = frozenset()) -> bool:
    """True when ``node`` calls one of FORBIDDEN_CALLS (by function or method name), except the names in ``allowed`` and PyTorch's
    no-argument ``.eval()``. The single rule behind both the app.py check and the notebook-cell check."""
    name = _call_name(node)
    return name in FORBIDDEN_CALLS and name not in allowed and not _is_eval_mode_call(node)


def _literal_assign(tree: ast.Module, name: str) -> Any:
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) \
                and node.targets[0].id == name:
            try:
                return ast.literal_eval(node.value)
            except ValueError:
                raise ValidationError(f"{name} must be a plain literal.")
    return _MISSING


_MISSING = object()

# Keyword arguments that loaders receive from platform constants; a second source for them is always a bug.
_GUARDED_KWARGS = frozenset({"revision", "token", "trust_remote_code", "use_safetensors"})


def _dict_keys_from(value: ast.AST) -> Optional[set]:
    """Constant string keys of a dict literal / dict(k=v) call, or None when they cannot be determined statically."""
    if isinstance(value, ast.Dict):
        keys = set()
        for k in value.keys:
            if not (isinstance(k, ast.Constant) and isinstance(k.value, str)):
                return None
            keys.add(k.value)
        return keys
    if isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id == "dict" and not value.args:
        if any(k.arg is None for k in value.keywords):
            return None
        return {k.arg for k in value.keywords}
    return None


def _scope_dict_keys(scope: ast.AST):
    """(known, unresolved): the keys a function-local dict variable is built with, and names whose keys are not knowable."""
    known: Dict[str, set] = {}
    unresolved: set = set()
    for node in ast.walk(scope):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            t = node.targets[0]
            if isinstance(t, ast.Name):
                ks = _dict_keys_from(node.value)
                if ks is None:
                    unresolved.add(t.id)
                else:
                    known.setdefault(t.id, set()).update(ks)
            elif isinstance(t, ast.Subscript) and isinstance(t.value, ast.Name):
                if isinstance(t.slice, ast.Constant) and isinstance(t.slice.value, str):
                    known.setdefault(t.value.id, set()).add(t.slice.value)
                else:
                    unresolved.add(t.value.id)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
            name, meth = node.func.value.id, node.func.attr
            if meth == "update":
                known.setdefault(name, set()).update(k.arg for k in node.keywords if k.arg)
                if any(k.arg is None for k in node.keywords):
                    unresolved.add(name)
                for arg in node.args:
                    ks = _dict_keys_from(arg)
                    if ks is None:
                        unresolved.add(name)
                    else:
                        known[name].update(ks)
            elif meth == "setdefault" and node.args and isinstance(node.args[0], ast.Constant) \
                    and isinstance(node.args[0].value, str):
                known.setdefault(name, set()).add(node.args[0].value)
    return known, unresolved


def check_call_arguments(tree: ast.AST) -> None:
    """Reject calls that would pass one keyword twice (explicitly, via a ``**dict`` splat, or via ``model_kwargs``) and
    ``**splats`` whose keys cannot be resolved in a call that also sets a guarded keyword such as ``revision``.
    Python only catches the literal ``f(a=1, a=2)`` case at parse time; the rest fails at run time with
    "got multiple values for keyword argument"."""
    scopes = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))] + [tree]  # locals first
    seen = set()
    for scope in scopes:
        known, unresolved = _scope_dict_keys(scope)
        for node in ast.walk(scope):
            if not isinstance(node, ast.Call) or id(node) in seen:
                continue
            seen.add(id(node))
            explicit = [k.arg for k in node.keywords if k.arg]
            dup = {a for a in explicit if explicit.count(a) > 1}
            if dup:
                raise ValidationError(f"Call passes keyword argument(s) {sorted(dup)} more than once.")
            taken = set(explicit)
            cannot_resolve = False
            for k in node.keywords:
                if k.arg is not None:
                    continue
                if isinstance(k.value, ast.Name) and k.value.id in known:
                    keys = known[k.value.id]
                    cannot_resolve = cannot_resolve or k.value.id in unresolved
                else:
                    keys = _dict_keys_from(k.value)
                    cannot_resolve = cannot_resolve or keys is None
                    keys = keys or set()
                clash = taken & keys
                if clash:
                    raise ValidationError(f"Call passes keyword argument(s) {sorted(clash)} twice (explicitly and through a ** splat).")
                taken |= keys
            if cannot_resolve and set(explicit) & _GUARDED_KWARGS:
                raise ValidationError("A ** splat with unresolvable keys is combined with explicit "
                                      f"{sorted(set(explicit) & _GUARDED_KWARGS)}; it could pass the same keyword twice.")
            for k in node.keywords:
                if k.arg == "model_kwargs":
                    mk = known.get(k.value.id) if isinstance(k.value, ast.Name) else _dict_keys_from(k.value)
                    inner = (mk or set()) & _GUARDED_KWARGS & taken
                    if inner:
                        raise ValidationError(f"Keyword(s) {sorted(inner)} are passed both directly and inside model_kwargs.")


# --------------------------------------------------------------------------- static code gate (nothing is executed or imported)

APP_FILENAME = "app.py"
Issue = Tuple[int, int, str]  # (line, column, message)
# Gradio event listeners whose first arguments are (fn, inputs, outputs)
_EVENT_METHODS = frozenset({"click", "submit", "change", "then", "success", "release", "upload"})


def check_syntax(code: str, filename: str = APP_FILENAME) -> None:
    """compile() only builds a code object; the generated code is never run."""
    try:
        compile(code, filename, "exec", dont_inherit=True)
    except SyntaxError as exc:
        text = f"  -> {exc.text.strip()}" if exc.text else ""
        raise ValidationError(f"Generated app is not valid Python: {filename}:{exc.lineno or '?'}:{exc.offset or 0}: {exc.msg}{text}")
    except ValueError as exc:  # e.g. null bytes in the source
        raise ValidationError(f"Generated app is not valid Python: {exc}")


def _bound_names(tree: ast.AST) -> set:
    """Every name bound anywhere in the module (conservative: used only when pyflakes is not installed)."""
    names = set(dir(builtins)) | {"__file__", "__name__", "__doc__", "__package__", "__spec__"}
    for n in ast.walk(tree):
        if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
            names.add(n.id)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(n.name)
        elif isinstance(n, ast.arg):
            names.add(n.arg)
        elif isinstance(n, ast.alias):
            names.add((n.asname or n.name).split(".")[0])
        elif isinstance(n, ast.ExceptHandler) and n.name:
            names.add(n.name)
        elif isinstance(n, (ast.Global, ast.Nonlocal)):
            names.update(n.names)
        elif type(n).__name__ in ("MatchAs", "MatchStar") and getattr(n, "name", None):
            names.add(n.name)
        elif type(n).__name__ == "MatchMapping" and getattr(n, "rest", None):
            names.add(n.rest)
    return names


def _undefined_name_issues(tree: ast.AST, filename: str) -> List[Issue]:
    """Names that are used but never defined (the NameError class of bug). Pyflakes does the scope analysis (nested functions,
    comprehensions, class bodies, globals); it is a validator-only dependency and is never part of a generated app's
    requirements. Without it a conservative check still catches any name that is bound nowhere in the module."""
    try:
        from pyflakes import messages as pf_messages
        from pyflakes.checker import Checker
    except ImportError:
        bound = _bound_names(tree) | PLATFORM_HEADER_NAMES
        return [(n.lineno, n.col_offset, f"undefined name {n.id!r}") for n in ast.walk(tree)
                if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load) and n.id not in bound]
    fatal = (pf_messages.UndefinedName, pf_messages.UndefinedLocal)
    return [(m.lineno, m.col, m.message % m.message_args) for m in Checker(tree, filename).messages
            if isinstance(m, fatal)
            and not (isinstance(m, pf_messages.UndefinedName) and m.message_args and m.message_args[0] in PLATFORM_HEADER_NAMES)]


def _with_import_hints(issues: List[Issue]) -> List[Issue]:
    """Make an undefined-name report actionable for a library name the platform does not import for the generated sections."""
    out: List[Issue] = []
    for ln, col, msg in issues:
        m = re.match(r"undefined name '([^']+)'", msg)
        mod = KNOWN_SYMBOL_MODULES.get(m.group(1)) if m else None
        if mod:
            name = m.group(1)
            msg += (f" ({name} is exported by `{mod}` and is not imported by the platform header: add `from {mod} import {name}` "
                    f"or `import {mod}` and use `{mod}.{name}`)")
        out.append((ln, col, msg))
    return out


def _star_import_issues(tree: ast.AST) -> List[Issue]:
    """A star import hides undefined names (pyflakes then only reports 'may be undefined'), so it is never accepted."""
    return [(n.lineno, n.col_offset, f"star import `from {n.module or '.'} import *` is not allowed; import each name explicitly")
            for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and any(a.name == "*" for a in n.names)]


def _component_count(node: Optional[ast.AST]) -> Optional[int]:
    if isinstance(node, (ast.List, ast.Tuple)):
        return None if any(isinstance(e, ast.Starred) for e in node.elts) else len(node.elts)
    return 1 if isinstance(node, ast.Call) else None  # one component; anything else cannot be counted statically


def _call_arg(call: ast.Call, name: str, pos: int) -> Optional[ast.AST]:
    for k in call.keywords:
        if k.arg == name:
            return k.value
    arg = call.args[pos] if len(call.args) > pos else None
    return None if isinstance(arg, ast.Starred) else arg


def _callback_issues(tree: ast.Module) -> List[Issue]:
    """A module-level function used as a Gradio callback must accept what its components send (gr.Interface / event listeners)
    or (message, history) for gr.ChatInterface. Functions with *args/**kwargs and inputs that cannot be counted are skipped."""
    funcs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    out: List[Issue] = []
    for call in ast.walk(tree):
        if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)):
            continue
        attr = call.func.attr
        if attr not in _EVENT_METHODS | {"Interface", "ChatInterface"}:
            continue
        fn = _call_arg(call, "fn", 0)
        if not (isinstance(fn, ast.Name) and fn.id in funcs):
            continue
        a = funcs[fn.id].args
        if a.vararg or a.kwarg:
            continue
        hi = len(a.posonlyargs) + len(a.args)
        lo = hi - len(a.defaults)
        if attr == "ChatInterface":
            if hi < 2:
                out.append((call.lineno, call.col_offset, f"gr.ChatInterface callback {fn.id}() must accept (message, history) but takes {hi} parameter(s)"))
            continue
        n = _component_count(_call_arg(call, "inputs", 1))
        if n is not None and not lo <= n <= hi:
            want = str(lo) if lo == hi else f"{lo}-{hi}"
            out.append((call.lineno, call.col_offset, f"{attr}() passes {n} input component(s) but {fn.id}() takes {want} parameter(s)"))
    return out


# gr.TabbedInterface keywords that exist in both Gradio 5 and 6 (checked against gradio 6.31 source). Gradio 6 removed the other
# Interface-style keywords (description, article, theme, css, js, head ...), so passing one raises a TypeError at app start-up.
_TABBED_SAFE_KWARGS = frozenset({"interface_list", "tab_names", "title", "analytics_enabled"})
_TABBED_MAX_POSITIONAL = 3  # interface_list, tab_names, title


def _gradio_compat_issues(tree: ast.AST) -> List[Issue]:
    """Gradio-version compatibility of the generated UI. Static only: nothing is imported or run."""
    out: List[Issue] = []
    for call in ast.walk(tree):
        if not isinstance(call, ast.Call) or _call_name(call) != "TabbedInterface":
            continue
        bad = sorted({k.arg for k in call.keywords if k.arg and k.arg not in _TABBED_SAFE_KWARGS})
        if bad:
            out.append((call.lineno, call.col_offset,
                        f"gr.TabbedInterface() does not accept {bad} in current Gradio; it only takes "
                        f"{sorted(_TABBED_SAFE_KWARGS)}. Put a description in a gr.Markdown above the tabs (use gr.Blocks) "
                        "or in each tab's gr.Interface(description=...)"))
        if len(call.args) > _TABBED_MAX_POSITIONAL:
            out.append((call.lineno, call.col_offset,
                        f"gr.TabbedInterface() takes at most {_TABBED_MAX_POSITIONAL} positional arguments "
                        "(interface_list, tab_names, title)"))
    # every other removal that is certain (e.g. ChatInterface(type=...) on Gradio 6). TabbedInterface is reported above already.
    out.extend((f.line, f.col, f.message) for f in _compat_check_tree(tree) if f.class_name != "TabbedInterface")
    return out


def _structure_issues(tree: ast.Module) -> List[Issue]:
    out: List[Issue] = _gradio_compat_issues(tree)
    if "gradio" not in _imports(tree):
        out.append((1, 0, "gradio is never imported (expected `import gradio as gr`)"))
    for n in tree.body:
        if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "demo" for t in n.targets) \
                and isinstance(n.value, (ast.Constant, ast.List, ast.Tuple, ast.Dict)):
            out.append((n.lineno, n.col_offset, "`demo` must be a Gradio app (gr.Interface, gr.ChatInterface, gr.Blocks, gr.TabbedInterface ...), not a plain value"))
    return out + _callback_issues(tree)


def check_static_code(tree: ast.Module, filename: str = APP_FILENAME) -> None:
    """Undefined names / missing function references plus basic Gradio structure, reported with file:line:column."""
    issues = sorted(set(_with_import_hints(_undefined_name_issues(tree, filename)) + _star_import_issues(tree) + _structure_issues(tree)))
    if not issues:
        return
    lines = [f"  {filename}:{ln}:{col}: {msg}" for ln, col, msg in issues[:10]]
    more = [f"  ... and {len(issues) - 10} more"] if len(issues) > 10 else []
    raise ValidationError("Generated app failed static validation (nothing was executed):\n" + "\n".join(lines + more)
                          + "\nFix: define or import every name before it is used and keep callbacks consistent with their components.")


def _import_lines(tree: ast.AST, bad: set) -> List[int]:
    lines = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            mods = {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom):
            mods = {(n.module or "").split(".")[0]}
        else:
            continue
        if mods & bad:
            lines.add(n.lineno)
    return sorted(lines)


# Loading-only options. Forwarded to inference they fail at run time ("model_kwargs are not used by the model").
_LOAD_ONLY_KWARGS = frozenset({"use_safetensors", "revision", "token", "trust_remote_code"})
# Component arguments that cap what a user may submit. A generated UI must not invent such caps; a limit the evidence really
# establishes is enforced inside the inference function (gr.Error) instead, where it can state that evidence.
_LIMIT_KWARGS = {"Audio": {"max_length", "min_length"}, "Video": {"max_length", "min_length"}, "Textbox": {"max_length"},
                 "TextArea": {"max_length"}}


def check_loading_and_limits(tree: ast.AST) -> None:
    """Static checks for two classes of generated-UI bug. (1) Loading options leaking into inference: ``use_safetensors`` passed
    directly to a transformers ``pipeline(...)`` call (it must be inside ``model_kwargs``), or any loading option inside
    ``generate_kwargs``. (2) Invented restrictions: duration/length caps on components, or ``max_file_size``."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node)
        if name == "pipeline" and any(k.arg == "use_safetensors" for k in node.keywords):
            raise ValidationError("pipeline(...) must not receive use_safetensors directly (it reaches generation and fails with "
                                  "'model_kwargs are not used by the model'); pass it inside model_kwargs={'use_safetensors': ...} "
                                  "or load the model with from_pretrained() and pass the model to pipeline().")
        for k in node.keywords:
            if k.arg == "generate_kwargs":
                keys = _dict_keys_from(k.value) or set()
                bad = sorted(keys & _LOAD_ONLY_KWARGS)
                if bad:
                    raise ValidationError(f"generate_kwargs must not carry loading option(s) {bad}; they apply only when the model is loaded.")
            if k.arg == "max_file_size":
                raise ValidationError("Generated UI must not invent a file-size limit (max_file_size).")
            if k.arg in _LIMIT_KWARGS.get(name, ()):
                raise ValidationError(f"Generated UI must not invent an input limit: {name}({k.arg}=...). Handle the requirement in the "
                                      "backend; enforce only a limit the model's evidence really establishes, inside the inference function.")


def check_no_hardcoded_pickle_loading(tree: ast.AST, settings: Settings) -> None:
    """Pickle weights are loaded only with the person's runtime consent (the platform constant LOAD_SAFETENSORS carries it). A generated
    loader that hard-codes ``use_safetensors=False`` or ``None`` (as a keyword, a dict entry or a subscript assignment) would bypass that
    consent, so it is rejected unless pickle weights are enabled in the settings (the existing explicit opt-in)."""
    if settings.allow_pickle_weights:
        return

    def unsafe(value: Optional[ast.AST]) -> bool:
        return isinstance(value, ast.Constant) and (value.value is False or value.value is None)

    def is_key(node: Optional[ast.AST]) -> bool:
        return isinstance(node, ast.Constant) and node.value == "use_safetensors"

    for node in ast.walk(tree):
        hit = False
        if isinstance(node, ast.keyword) and node.arg == "use_safetensors":
            hit = unsafe(node.value)
        elif isinstance(node, ast.Dict):
            hit = any(is_key(k) and unsafe(v) for k, v in zip(node.keys, node.values))
        elif isinstance(node, ast.Assign):
            hit = unsafe(node.value) and any(isinstance(t, ast.Subscript) and is_key(t.slice) for t in node.targets)
        if hit:
            raise ValidationError("use_safetensors must be passed as LOAD_SAFETENSORS: hard-coding False/None would load pickle weights "
                                  "(which can execute arbitrary code) without the person's explicit consent.")


def validate_app_code(code: str, settings: Optional[Settings] = None) -> Dict[str, Any]:
    """Validate a generated Gradio ``app.py``. Returns a small report; raises ValidationError on failure."""
    settings = settings or Settings()
    if not isinstance(code, str) or not code.strip():
        raise ValidationError("Generated app is empty.")
    if "@@" in code:
        raise ValidationError("Unresolved template placeholder in generated app.")
    check_syntax(code)
    tree = ast.parse(code)

    bad = _imports(tree) - APP_ALLOWED_IMPORTS - LOCAL_PACKAGE_MODULES
    if bad:
        raise ValidationError(f"Generated app imports disallowed modules: {sorted(bad)} ({APP_FILENAME} line(s) "
                              f"{', '.join(map(str, _import_lines(tree, bad)))}); allowed: {sorted(APP_ALLOWED_IMPORTS)}")
    check_call_arguments(tree)
    check_loading_and_limits(tree)
    check_no_hardcoded_pickle_loading(tree, settings)

    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _is_forbidden_call(node):
            raise ValidationError(f"Generated app calls forbidden function '{_call_name(node)}'.")
        if isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            raise ValidationError(f"Generated app references forbidden module '{node.id}'.")
        if isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_NAMES:
            raise ValidationError(f"Generated app references forbidden module '{node.attr}'.")
        # share=True hard-coded would publish the user's app to the internet without consent
        if isinstance(node, ast.keyword) and node.arg == "share" and isinstance(node.value, ast.Constant) \
                and node.value.value is True:
            raise ValidationError("Generated app must not hard-code share=True.")

    for node in tree.body:
        if not isinstance(node, (ast.Import, ast.ImportFrom, ast.Assign, ast.AnnAssign, ast.FunctionDef, ast.If, ast.Expr)):
            raise ValidationError(f"Unexpected module-level statement: {type(node).__name__}")
        if isinstance(node, ast.Expr) and not isinstance(node.value, ast.Constant):
            raise ValidationError("Only a docstring may be a bare module-level expression.")
    check_static_code(tree)

    model_id = _literal_assign(tree, "MODEL_ID")
    revision = _literal_assign(tree, "REVISION")
    trust = _literal_assign(tree, "TRUST_REMOTE_CODE")
    if model_id is _MISSING or not isinstance(model_id, str):
        raise ValidationError("Generated app is missing a literal MODEL_ID.")
    try:
        validate_repo_id(model_id)
        if revision not in (_MISSING, None):
            validate_revision(revision)
    except SecurityError as exc:
        raise ValidationError(str(exc))
    if trust is _MISSING or not isinstance(trust, bool):
        raise ValidationError("Generated app is missing a literal TRUST_REMOTE_CODE flag.")
    if trust and not settings.allow_remote_code:
        raise ValidationError("trust_remote_code=True is not allowed unless explicitly enabled in settings.")
    safet = _literal_assign(tree, "USE_SAFETENSORS")
    if safet is False and not settings.allow_pickle_weights:
        raise ValidationError("Pickle weights are not allowed unless explicitly enabled in settings.")

    # Every trust_remote_code keyword must come from the constant, never a literal True.
    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and node.arg == "trust_remote_code" and isinstance(node.value, ast.Constant) \
                and node.value.value is True and not settings.allow_remote_code:
            raise ValidationError("Hard-coded trust_remote_code=True found.")

    names = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
    if not any(is_inference_function_name(n) for n in names):
        raise ValidationError("Generated app has no predict/chat_fn inference function.")
    if not any(isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "demo" for t in n.targets)
               for n in tree.body):
        raise ValidationError("Generated app does not define a Gradio `demo`.")
    try:
        assert_no_secrets(code, settings)
    except SecurityError as exc:
        raise ValidationError(str(exc))
    return {"model_id": model_id, "revision": revision, "trust_remote_code": trust, "functions": sorted(names)}


def validate_requirements_text(text: str) -> List[str]:
    reqs = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not reqs:
        raise ValidationError("requirements are empty.")
    try:
        return [validate_pip_requirement(r) for r in reqs]
    except SecurityError as exc:
        raise ValidationError(str(exc))


def _cell_source(cell: Dict[str, Any]) -> str:
    src = cell.get("source", "")
    return "".join(src) if isinstance(src, list) else str(src)


def validate_notebook(nb: Dict[str, Any], settings: Optional[Settings] = None) -> Dict[str, Any]:
    """Validate a generated Colab notebook (nbformat 4 dict). Raises ValidationError on failure."""
    settings = settings or Settings()
    if not isinstance(nb, dict) or nb.get("nbformat") != 4 or not isinstance(nb.get("nbformat_minor"), int):
        raise ValidationError("Notebook must be nbformat 4.")
    cells = nb.get("cells")
    if not isinstance(cells, list) or not cells:
        raise ValidationError("Notebook has no cells.")
    json.dumps(nb)  # must be serializable
    n_code = 0
    writefile_cells = 0
    loader_cells = 0
    app_src = loader_src = None
    pip_lines: List[str] = []
    for i, cell in enumerate(cells):
        ctype = cell.get("cell_type")
        if ctype not in ("markdown", "code"):
            raise ValidationError(f"Cell {i}: invalid cell_type {ctype!r}.")
        if not isinstance(cell.get("source"), (list, str)) or not isinstance(cell.get("metadata"), dict):
            raise ValidationError(f"Cell {i}: malformed source/metadata.")
        if ctype == "code":
            n_code += 1
            if cell.get("outputs") != [] or cell.get("execution_count") is not None:
                raise ValidationError(f"Cell {i}: code cells must ship without outputs.")
            src = _cell_source(cell)
            first_line, _, rest = src.partition("\n")
            if first_line.strip() == "%%writefile loader.py":  # the generated model code (loader.py), shared with the local package
                loader_src, loader_cells = rest, loader_cells + 1
                continue
            if first_line.strip() == "%%writefile app.py":
                app_src, writefile_cells = rest, writefile_cells + 1
                continue
            if first_line.lstrip().startswith("%%"):
                raise ValidationError(f"Cell {i}: cell magic {first_line.strip()[:30]!r} is not allowed.")
            plain = []
            for line in src.splitlines():
                if _MAGIC_LINE.match(line):
                    stripped = line.strip()
                    if stripped.startswith("%pip install"):
                        pip_lines.append(stripped)
                        continue
                    raise ValidationError(f"Cell {i}: only '%pip install' magics are allowed, got {stripped[:40]!r}.")
                plain.append(line)
            try:
                tree = ast.parse("\n".join(plain))
            except SyntaxError as exc:
                raise ValidationError(f"Cell {i}: invalid Python: {exc}")
            bad = _imports(tree) - NOTEBOOK_ALLOWED_IMPORTS
            if bad:
                raise ValidationError(f"Cell {i}: disallowed imports {sorted(bad)}")
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    name = _call_name(node)
                    if _is_forbidden_call(node, allowed=frozenset({"load", "loads", "remove"})):
                        raise ValidationError(f"Cell {i}: forbidden call '{name}'.")
                    if name == "run" and "subprocess" in ast.dump(node.func):
                        if not (node.args and isinstance(node.args[0], ast.List)
                                and all(isinstance(e, ast.Constant) for e in node.args[0].elts)):
                            raise ValidationError(f"Cell {i}: subprocess.run must use a fixed literal argument list.")
                        if any(k.arg == "shell" for k in node.keywords):
                            raise ValidationError(f"Cell {i}: shell=True is not allowed.")
    if n_code < 3:
        raise ValidationError("Notebook must contain environment-check, install and launch code cells.")
    if writefile_cells != 1:
        raise ValidationError("Notebook must contain exactly one '%%writefile app.py' cell.")
    if loader_cells > 1:
        raise ValidationError("Notebook must contain at most one '%%writefile loader.py' cell.")
    require_package_link(app_src, loader_src)  # a notebook without the loader.py cell (or one app.py ignores) cannot run on Colab
    validate_app_code(combine_package_source(app_src, loader_src), settings)  # the embedded package gets the full app-level checks
    for line in pip_lines:
        for tok in line[len("%pip install"):].split():
            if tok.startswith("-"):
                if tok not in ("-q", "--quiet"):
                    raise ValidationError(f"Unexpected pip option {tok!r}.")
                continue
            try:
                validate_pip_requirement(tok.strip("'\""))
            except SecurityError as exc:
                raise ValidationError(str(exc))
    try:
        assert_no_secrets(json.dumps(nb), settings)
    except SecurityError as exc:
        raise ValidationError(str(exc))
    return {"cells": len(cells), "code_cells": n_code, "pip_lines": len(pip_lines)}
