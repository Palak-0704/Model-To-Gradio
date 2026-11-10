"""Library-version compatibility of generated apps with the runtime that will run them (standard library only; nothing generated is
ever executed here).

Why this exists: the Colab runtime installs the NEWEST release of every unpinned library, and libraries remove or rename APIs between
releases (a transformers release dropped a pipeline task that an app used; a class moved; a keyword vanished). ``gradio_compat``
already does this for Gradio. This module does the same for the model libraries, with the same three parts:

* ``library_snapshot(libs, symbols, want_tasks)``  runs ON THE RUNTIME (``render_library_probe_script`` ships its source): it reads the
  installed versions, which of the names the app imports really exist, and which transformers pipeline tasks are registered.
  One output line, ``M2G_LIB_API {json}``, parsed by ``parse_library_probe_output``.
* ``library_uses(code)`` / ``library_probe_arguments(code)``  find, with ``ast`` only, what the app takes from transformers,
  diffusers and sentence_transformers (``from x import A``, ``x.A``) and which pipeline task it asks for (``TASK`` or a literal).
* ``check_library_compat(code, snapshot)``  reports every name or task the installed version does not have.

Rules that keep it honest:
* The check is made against the versions that will RUN the app, read there, never against a table of guesses.
* No library is downgraded and no version is pinned: an incompatible app is reported with the cause and a fix, before anything is
  deployed. Without a usable snapshot (probe failed, tasks not readable) nothing is enforced, so a valid app is never rejected
  because the probe could not see something.
* Messages are phrased so ``error_classify.compat_error`` recognises them: the same cause gets the same label wherever it surfaces.
"""
import ast
import inspect
import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

LIB_PROBE_MARKER = "M2G_LIB_API"

# Libraries whose imported names are checked against the runtime.
CHECKED_LIBRARIES: Tuple[str, ...] = ("transformers", "diffusers", "sentence_transformers")
# Libraries whose installed version is reported in the preflight log (it is what makes a compatibility failure explainable).
REPORTED_LIBRARIES: Tuple[str, ...] = ("transformers", "diffusers", "sentence_transformers", "torch", "accelerate", "bitsandbytes",
                                       "huggingface_hub", "gradio")

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def library_snapshot(libs, symbols, want_tasks, calls=None):
    """What the runtime has: installed versions of ``libs``, the names in ``symbols`` ({library: [name, ...]}) that do NOT exist, and
    (with ``want_tasks``) the transformers pipeline tasks. ``calls`` ({library: {dotted name: [keyword, ...]}}) are the keywords the
    app passes to library callables: the ones the installed signature rejects come back in ``bad_kwargs``. A callable that takes
    **kwargs, or whose signature cannot be read, is never reported (it cannot be verified, so it is not guessed).
    Self-contained on purpose: render_library_probe_script() ships this
    function's source to the runtime, so keep it free of references to the rest of this module."""
    import importlib
    from importlib import metadata as _md
    dist = {"sentence_transformers": "sentence-transformers"}
    versions = {}
    for lib in libs:
        try:
            # Prefer the version exposed by the module that this interpreter will
            # import. Metadata can belong to a different installation when a
            # venv, PYTHONPATH overlay, editable package, or local wheel shadows
            # the distribution recorded by importlib.metadata.
            _module = importlib.import_module(lib)
            _version = getattr(_module, "__version__", None)
            versions[lib] = str(_version) if _version else _md.version(dist.get(lib, lib))
        except Exception:
            try:
                versions[lib] = _md.version(dist.get(lib, lib))
            except Exception:
                versions[lib] = None
    missing, errors = {}, []
    for lib, names in (symbols or {}).items():
        try:
            mod = importlib.import_module(lib)
        except Exception as exc:
            errors.append(lib + ": " + type(exc).__name__)
            continue
        gone = []
        for name in names:
            try:
                getattr(mod, name)
            except AttributeError:
                gone.append(name)
            except Exception:
                pass  # the name exists but one of its own optional dependencies is missing: not a version problem
        missing[lib] = gone
    tasks, aliases = None, []
    if want_tasks:
        try:
            from transformers import pipelines as _p
            try:
                tasks = sorted(_p.PIPELINE_REGISTRY.get_supported_tasks())
            except Exception:
                tasks = sorted(_p.SUPPORTED_TASKS)
            aliases = sorted(getattr(_p, "TASK_ALIASES", None) or {})
        except Exception as exc:
            errors.append("pipeline tasks: " + type(exc).__name__)
    bad_kwargs = {}
    for lib, targets in (calls or {}).items():
        try:
            mod = importlib.import_module(lib)
        except Exception:
            continue
        for dotted, kws in targets.items():
            try:
                import inspect as _inspect
                obj = mod
                for part in dotted.split("."):
                    obj = getattr(obj, part)
                params = list(_inspect.signature(obj).parameters.values())
            except Exception:
                continue  # not readable here: unverifiable, never a finding
            if any(p.kind is p.VAR_KEYWORD for p in params):
                continue
            accepted = {p.name for p in params if p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)}
            gone = [k for k in kws if k not in accepted]
            if gone:
                bad_kwargs.setdefault(lib, {})[dotted] = gone
    return {"versions": versions, "missing": missing, "tasks": tasks, "aliases": aliases, "errors": errors,
            "bad_kwargs": bad_kwargs}


def render_library_probe_script(libs: Sequence[str], symbols: Dict[str, Sequence[str]], want_tasks: bool,
                                calls: Optional[Dict[str, Dict[str, Sequence[str]]]] = None) -> str:
    """Python source that prints one ``M2G_LIB_API {json}`` line. It only imports the libraries the app uses and reads attributes;
    a failure inside it is reported in the line, never raised. Pure ASCII (every name is validated as an identifier)."""
    wanted = sorted({str(x) for x in libs if _IDENT.fullmatch(str(x))})
    syms = {str(lib): sorted({str(n) for n in names if _IDENT.fullmatch(str(n))})
            for lib, names in (symbols or {}).items() if _IDENT.fullmatch(str(lib))}
    dotted = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*")
    calls_ok = {str(lib): {str(d): sorted({str(k) for k in kws if _IDENT.fullmatch(str(k))})
                           for d, kws in targets.items() if dotted.fullmatch(str(d))}
                for lib, targets in (calls or {}).items() if _IDENT.fullmatch(str(lib))}
    return ("import json\n" + inspect.getsource(library_snapshot) + "\n"
            "try:\n"
            f"    print({LIB_PROBE_MARKER!r} + ' ' + json.dumps(library_snapshot({wanted!r}, {syms!r}, {bool(want_tasks)!r}, "
            f"{calls_ok!r})))\n"
            "except Exception as _e:\n"
            f"    print({LIB_PROBE_MARKER!r} + ' ' + json.dumps({{'error': type(_e).__name__}}))\n")


@dataclass(frozen=True)
class LibrarySnapshot:
    versions: Dict[str, Optional[str]]
    missing: Dict[str, Tuple[str, ...]]  # library -> imported names that do not exist in the installed version
    tasks: Optional[Tuple[str, ...]]  # registered transformers pipeline tasks, None when they could not be read
    aliases: Tuple[str, ...] = ()
    errors: Tuple[str, ...] = ()
    bad_kwargs: Dict[str, Dict[str, Tuple[str, ...]]] = field(default_factory=dict)  # library -> callable -> rejected keywords

    def version(self, lib: str) -> str:
        return self.versions.get(lib) or "unknown"

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "LibrarySnapshot":
        if not isinstance(data, dict) or not isinstance(data.get("versions"), dict):
            raise ValueError("not a library snapshot")
        tasks = data.get("tasks")
        return cls({str(k): (None if v is None else str(v)) for k, v in data["versions"].items()},
                   {str(k): tuple(str(n) for n in v) for k, v in (data.get("missing") or {}).items()},
                   tuple(str(t) for t in tasks) if isinstance(tasks, list) else None,
                   tuple(str(a) for a in (data.get("aliases") or [])), tuple(str(e) for e in (data.get("errors") or [])),
                   {str(lib): {str(d): tuple(str(k) for k in kws) for d, kws in targets.items()}
                    for lib, targets in (data.get("bad_kwargs") or {}).items()})


def parse_library_probe_output(text: str) -> Optional[LibrarySnapshot]:
    """The snapshot from the last well-formed probe line in ``text`` (other output is ignored), or None."""
    for line in reversed((text or "").splitlines()):
        line = line.strip()
        if line.startswith(LIB_PROBE_MARKER + " "):
            try:
                return LibrarySnapshot.from_dict(json.loads(line[len(LIB_PROBE_MARKER) + 1:]))
            except (ValueError, TypeError, AttributeError):
                return None
    return None


# --------------------------------------------------------------------------- what the app takes from the libraries

Position = Tuple[int, int]


def library_uses(code: str) -> Tuple[Dict[str, Dict[str, Position]], List[Tuple[str, Position]]]:
    """({library: {name: (line, col)}}, [(pipeline task, (line, col))]) for ``code``. Names come from ``from transformers import A``
    and from ``transformers.A`` (also through ``import transformers as tf``). A task is a literal first argument / ``task=`` of a
    transformers ``pipeline(...)`` call, a module-level string constant used there, or the platform's ``TASK`` constant when the app
    builds a pipeline and uses it. Raises SyntaxError for unparseable code."""
    tree = ast.parse(code)
    modules: Dict[str, str] = {}
    pipeline_names = set()
    symbols: Dict[str, Dict[str, Position]] = {}

    def note(lib: str, name: str, node: ast.AST) -> None:
        symbols.setdefault(lib, {}).setdefault(name, (node.lineno, node.col_offset))

    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                root = a.name.split(".", 1)[0]
                if root in CHECKED_LIBRARIES:
                    modules[a.asname or a.name] = root
        elif isinstance(n, ast.ImportFrom) and not n.level:
            root = (n.module or "").split(".", 1)[0]
            if root not in CHECKED_LIBRARIES:
                continue
            for a in n.names:
                if a.name != "*":
                    note(root, a.name, n)
                    if root == "transformers" and a.name == "pipeline":
                        pipeline_names.add(a.asname or a.name)
    for n in ast.walk(tree):
        if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id in modules:
            note(modules[n.value.id], n.attr, n)

    consts = {n.targets[0].id: n.value.value for n in tree.body
              if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name)
              and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str)}
    imported_pipeline = bool(pipeline_names or "pipeline" in symbols.get("transformers", {}))
    tasks: Dict[str, Position] = {}
    pipeline_called = False
    for c in ast.walk(tree):
        if not isinstance(c, ast.Call):
            continue
        f = c.func
        is_pipeline = (isinstance(f, ast.Name) and f.id in (pipeline_names or {"pipeline"}) and imported_pipeline) or \
                      (isinstance(f, ast.Attribute) and f.attr == "pipeline" and isinstance(f.value, ast.Name)
                       and modules.get(f.value.id) == "transformers")
        if not is_pipeline:
            continue
        pipeline_called = True
        arg = c.args[0] if c.args and not isinstance(c.args[0], ast.Starred) else next(
            (k.value for k in c.keywords if k.arg == "task"), None)

        # Resolve the small, data-only forms used by generated loaders.  This deliberately does not
        # execute code: dynamic task expressions remain unknown and are checked at runtime instead.
        dict_tasks = {}
        for assignment in ast.walk(tree):
            if not isinstance(assignment, ast.Assign) or len(assignment.targets) != 1 \
                    or not isinstance(assignment.targets[0], ast.Name) or not isinstance(assignment.value, ast.Dict):
                continue
            for key, value in zip(assignment.value.keys, assignment.value.values):
                if isinstance(key, ast.Constant) and key.value == "task":
                    if isinstance(value, ast.Constant) and isinstance(value.value, str):
                        dict_tasks[assignment.targets[0].id] = value.value
                    elif isinstance(value, ast.Name) and value.id in consts:
                        dict_tasks[assignment.targets[0].id] = consts[value.id]

        task = arg.value if isinstance(arg, ast.Constant) and isinstance(arg.value, str) else \
            consts.get(arg.id) if isinstance(arg, ast.Name) else None
        if task is None and c.keywords and any(k.arg is None for k in c.keywords):
            expanded = next((k.value for k in c.keywords if k.arg is None and isinstance(k.value, ast.Name)), None)
            task = dict_tasks.get(expanded.id) if expanded is not None else None
        if task:
            tasks.setdefault(task, (c.lineno, c.col_offset))
    if pipeline_called and "TASK" in consts:
        use = next((n for n in ast.walk(tree) if isinstance(n, ast.Name) and n.id == "TASK" and isinstance(n.ctx, ast.Load)), None)
        if use is not None:
            tasks.setdefault(consts["TASK"], (use.lineno, use.col_offset))
    return symbols, sorted(tasks.items(), key=lambda t: t[1])


def library_probe_arguments(code: str) -> Tuple[List[str], Dict[str, List[str]], bool]:
    """(libraries to report, {library: names to look up}, whether the pipeline tasks are needed) for the runtime probe."""
    try:
        symbols, tasks = library_uses(code)
    except SyntaxError:
        return list(REPORTED_LIBRARIES), {}, False
    return list(REPORTED_LIBRARIES), {lib: sorted(names) for lib, names in symbols.items()}, bool(tasks)


def library_call_keywords(code: str) -> Dict[str, Dict[str, Dict[str, Position]]]:
    """{library: {dotted callable: {keyword: (line, col)}}} for every call with keyword arguments whose callee is a name taken from a
    checked library: ``Name(...)``, ``Name.method(...)`` after ``from transformers import Name``, or ``tf.Name(...)`` /
    ``tf.Name.method(...)`` after ``import transformers as tf``. Only top-level library names are followed (``from
    transformers.x import Y`` is skipped), and ``**kwargs`` at the call site is ignored. Raises SyntaxError for unparseable code."""
    tree = ast.parse(code)
    modules: Dict[str, str] = {}
    names: Dict[str, Tuple[str, str]] = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                root = a.name.split(".", 1)[0]
                if root in CHECKED_LIBRARIES and (a.asname is None or "." not in a.name):
                    modules[a.asname or root] = root
        elif isinstance(n, ast.ImportFrom) and not n.level and n.module in CHECKED_LIBRARIES:
            for a in n.names:
                if a.name != "*":
                    names[a.asname or a.name] = (n.module, a.name)
    out: Dict[str, Dict[str, Dict[str, Position]]] = {}
    for c in ast.walk(tree):
        if not isinstance(c, ast.Call) or not any(k.arg for k in c.keywords):
            continue
        chain, f = [], c.func
        while isinstance(f, ast.Attribute):
            chain.append(f.attr)
            f = f.value
        if not isinstance(f, ast.Name):
            continue
        chain.reverse()
        if f.id in modules and chain:
            lib, dotted = modules[f.id], ".".join(chain)
        elif f.id in names:
            lib, dotted = names[f.id][0], ".".join([names[f.id][1]] + chain)
        else:
            continue
        for k in c.keywords:
            if k.arg:
                out.setdefault(lib, {}).setdefault(dotted, {}).setdefault(k.arg, (k.value.lineno, k.value.col_offset))
    return out


def library_probe_calls(code: str) -> Dict[str, Dict[str, List[str]]]:
    """``library_call_keywords`` reduced to what the runtime probe needs: {library: {callable: [keyword, ...]}}."""
    try:
        found = library_call_keywords(code)
    except SyntaxError:
        return {}
    return {lib: {d: sorted(kws) for d, kws in targets.items()} for lib, targets in found.items()}


# --------------------------------------------------------------------------- checking

@dataclass(frozen=True)
class LibFinding:
    line: int
    col: int
    library: str
    kind: str  # "missing_name" | "unsupported_task" | "unsupported_kwarg"
    name: str
    message: str

    def render(self, filename: str = "app.py") -> str:
        return f"{filename}:{self.line}:{self.col}: {self.message}"


def _task_known(task: str, snap: LibrarySnapshot) -> bool:
    known = set(snap.tasks or ()) | set(snap.aliases)
    return task in known or (task.startswith("translation_") and "translation" in known)


def check_library_compat(code: str, snapshot: Optional[LibrarySnapshot]) -> List[LibFinding]:
    """Findings for names and pipeline tasks the runtime's libraries do not have. No snapshot, or tasks that could not be read,
    means nothing is enforced for that part. Raises SyntaxError for unparseable code (callers report that separately)."""
    if snapshot is None:
        return []
    symbols, tasks = library_uses(code)
    findings: List[LibFinding] = []
    for lib, names in sorted(symbols.items()):
        gone = set(snapshot.missing.get(lib, ()))
        for name, (line, col) in sorted(names.items(), key=lambda t: t[1]):
            if name in gone:
                findings.append(LibFinding(
                    line, col, lib, "missing_name", name,
                    f"{lib} {snapshot.version(lib)} installed on the runtime has no '{name}' (cannot import name '{name}' from "
                    f"'{lib}'): it was removed, renamed or is not released yet. Use the current API name, or a {lib} release "
                    "that provides it"))
    if snapshot.bad_kwargs:  # only keywords the installed signature itself rejected (callables with **kwargs never get here)
        for lib, targets in sorted(library_call_keywords(code).items()):
            for dotted, kws in sorted(targets.items()):
                rejected = set(snapshot.bad_kwargs.get(lib, {}).get(dotted, ()))
                for kw, (line, col) in sorted(kws.items(), key=lambda t: t[1]):
                    if kw in rejected:
                        findings.append(LibFinding(
                            line, col, lib, "unsupported_kwarg", kw,
                            f"{lib}.{dotted}() in {lib} {snapshot.version(lib)} installed on the runtime does not accept the "
                            f"keyword argument '{kw}' (unexpected keyword argument '{kw}'): it was removed or renamed. Use the "
                            f"argument name that this version documents, or a {lib} release that has '{kw}'"))
    if snapshot.tasks:  # only when the registered tasks could be read
        for task, (line, col) in tasks:
            if not _task_known(task, snapshot):
                findings.append(LibFinding(
                    line, col, "transformers", "unsupported_task", task,
                    f"transformers {snapshot.version('transformers')} installed on the runtime does not provide the pipeline task "
                    f"'{task}'. Load the model with its AutoModel class and tokenizer instead of pipeline('{task}'), or use a "
                    "transformers release that provides the task"))
    return sorted(findings, key=lambda f: (f.line, f.col, f.message))


def format_library_findings(findings: Sequence[LibFinding], filename: str = "app.py", limit: int = 10) -> str:
    lines = [f"  {f.render(filename)}" for f in findings[:limit]]
    if len(findings) > limit:
        lines.append(f"  ... and {len(findings) - limit} more")
    return "\n".join(lines)


def describe_versions(snapshot: Optional[LibrarySnapshot]) -> str:
    """'transformers 4.x, torch 2.x' for the preflight log (only libraries that are installed)."""
    if snapshot is None:
        return "not read"
    shown = [f"{lib} {ver}" for lib, ver in snapshot.versions.items() if ver]
    return ", ".join(shown) or "none of the tracked libraries is installed"
