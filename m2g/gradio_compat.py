"""Gradio API compatibility checking for generated apps (standard library only, nothing generated is ever executed here).

Why this exists: Gradio changes constructor signatures between releases (Colab installed 6.30.0 where ``ChatInterface`` no
longer takes ``type`` and ``TabbedInterface`` no longer takes ``description``). Fixing one keyword per model does not scale, so
this module checks EVERY gradio call in a generated app against the signatures of the Gradio that will actually run it:

* ``snapshot_from_module(gr)``   reads the real signatures (``inspect``) of an imported gradio module into plain data.
* ``render_probe_script(names)``  is the same function as a tiny script to run on the Colab runtime; its single output line is
                                  parsed by ``parse_probe_output``. So the version that runs the app is the one that is checked.
* ``check_code(code, snapshot)``  finds unsupported keywords / too many positional arguments / missing classes (AST only).
* ``repair_code(code, findings)`` removes ONLY cosmetic keywords (see ``COSMETIC_KWARGS``) and ``type='messages'``; anything that
                                  could change behaviour is reported, never rewritten.

Without a snapshot only a small list of removals that are certain (``STATIC_REMOVALS``) is enforced, so a wrong guess about an
unknown Gradio version can never reject a valid app. Gradio is never downgraded.
"""
import ast
import inspect
import json
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

PROBE_MARKER = "M2G_GRADIO_API"

# (class, keyword) -> (first Gradio major version that rejects it, hint). Only removals that were observed on a real runtime.
STATIC_REMOVALS: Dict[Tuple[str, str], Tuple[int, str]] = {
    ("ChatInterface", "type"): (6, "Gradio 6 always uses the messages format; remove type= and write the callback for "
                                   "(message, history) with history as a list of {'role', 'content'} dicts"),
    ("Chatbot", "type"): (6, "Gradio 6 always uses the messages format; remove type="),
    ("TabbedInterface", "description"): (6, "put the description in a gr.Markdown above the tabs or in each tab's gr.Interface"),
}

# Keywords that only change looks, labels or hosting details. Dropping one cannot change what the model computes, so these are the
# ONLY keywords automatic repair may remove (plus type='messages', see _safe_to_drop).
COSMETIC_KWARGS = frozenset({
    "theme", "css", "css_paths", "js", "head", "head_paths", "title", "description", "article", "analytics_enabled",
    "flagging_mode", "allow_flagging", "flagging_options", "flagging_dir", "flagging_callback", "show_api",
    "show_copy_button", "show_copy_all_button", "show_share_button", "show_download_button", "show_fullscreen_button",
    "bubble_full_width", "avatar_images", "show_label", "label", "container", "scale", "min_width", "elem_id", "elem_classes",
    "height", "max_height", "min_height", "placeholder", "info", "autofocus", "autoscroll", "layout", "line_breaks",
    "render_markdown", "sanitize_html", "group_consecutive_messages", "example_labels", "example_icons", "fill_height",
    "fill_width", "text_align", "rtl",
})

_FINDING_KINDS = ("unsupported_kwarg", "too_many_positional", "missing_class")


def parse_version(text: Any) -> Tuple[int, ...]:
    """'6.30.0' -> (6, 30, 0); anything unparseable -> ()."""
    parts = re.findall(r"\d+", str(text or "").split("+")[0])[:3]
    return tuple(int(p) for p in parts)


@dataclass(frozen=True)
class ClassSig:
    params: Tuple[str, ...]  # parameters that can be passed by keyword (``self`` excluded)
    var_kw: bool  # accepts **kwargs: any keyword is fine
    max_positional: Optional[int]  # None = unlimited (*args)


@dataclass(frozen=True)
class ApiSnapshot:
    version: str
    classes: Dict[str, ClassSig]
    missing: Tuple[str, ...] = ()  # names that were asked for but do not exist in this Gradio

    @property
    def major(self) -> Optional[int]:
        v = parse_version(self.version)
        return v[0] if v else None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ApiSnapshot":
        if not isinstance(data, dict) or not isinstance(data.get("classes"), dict):
            raise ValueError("not a Gradio API snapshot")
        classes = {}
        for name, raw in data["classes"].items():
            mp = raw.get("max_positional")
            classes[str(name)] = ClassSig(tuple(str(p) for p in raw.get("params", [])), bool(raw.get("var_kw")),
                                          None if mp is None else int(mp))
        return cls(str(data.get("version") or "unknown"), classes, tuple(str(m) for m in data.get("missing", [])))


def snapshot_from_module(gr, names=None):
    """Signatures of the gradio classes in module ``gr`` as JSON-ready data. Self-contained on purpose: render_probe_script()
    ships this function's source to the Colab runtime, so keep it free of references to the rest of this module."""
    import inspect as _inspect
    version = str(getattr(gr, "__version__", "") or "")
    if not version:
        try:
            from importlib import metadata as _md
            version = _md.version("gradio")
        except Exception:
            version = "unknown"
    wanted = sorted(set(names)) if names else sorted(n for n in dir(gr) if n[:1].isupper())
    classes, missing = {}, []
    for name in wanted:
        obj = getattr(gr, name, None)
        if obj is None:
            missing.append(name)
            continue
        if not _inspect.isclass(obj):
            continue
        try:
            sig = _inspect.signature(obj.__init__)
        except (TypeError, ValueError):
            continue
        params, var_kw, unlimited, n_pos = [], False, False, 0
        for i, p in enumerate(sig.parameters.values()):
            if i == 0:
                continue  # self
            if p.kind is p.VAR_KEYWORD:
                var_kw = True
            elif p.kind is p.VAR_POSITIONAL:
                unlimited = True
            else:
                if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD):
                    n_pos += 1
                if p.kind is not p.POSITIONAL_ONLY:
                    params.append(p.name)
        classes[name] = {"params": params, "var_kw": var_kw, "max_positional": None if unlimited else n_pos}
    return {"version": version, "classes": classes, "missing": missing}


def render_probe_script(names: Iterable[str]) -> str:
    """Python source that prints one ``M2G_GRADIO_API {json}`` line describing the installed gradio. Run it on the runtime that
    will run the app (``python -c`` / a cell); it only imports gradio and reads signatures."""
    wanted = sorted({n for n in names if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(n))})
    return ("import json\n" + inspect.getsource(snapshot_from_module) + "\nimport gradio as gr\n"
            f"print({PROBE_MARKER!r} + ' ' + json.dumps(snapshot_from_module(gr, {wanted!r})))\n")


def parse_probe_output(text: str) -> Optional[ApiSnapshot]:
    """The snapshot from the last well-formed probe line in ``text`` (other output is ignored), or None."""
    for line in reversed((text or "").splitlines()):
        line = line.strip()
        if line.startswith(PROBE_MARKER + " "):
            try:
                return ApiSnapshot.from_dict(json.loads(line[len(PROBE_MARKER) + 1:]))
            except (ValueError, TypeError, AttributeError):
                return None
    return None


@dataclass(frozen=True)
class Finding:
    line: int
    col: int
    class_name: str
    kind: str  # one of _FINDING_KINDS
    kwarg: Optional[str]
    message: str
    repairable: bool = False
    span: Optional[Tuple[int, int, int, int]] = None  # keyword node (line, col, end_line, end_col), col in UTF-8 bytes

    def render(self, filename: str = "app.py") -> str:
        return f"{filename}:{self.line}:{self.col}: {self.message}"


def _gradio_names(tree: ast.AST) -> Tuple[Set[str], Dict[str, str]]:
    """(module aliases such as 'gr', bare imported class name -> real class name)."""
    aliases: Set[str] = set()
    bare: Dict[str, str] = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            aliases.update(a.asname or a.name.split(".")[0] for a in n.names if a.name.split(".")[0] == "gradio")
        elif isinstance(n, ast.ImportFrom) and (n.module or "").split(".")[0] == "gradio" and not n.level:
            bare.update({(a.asname or a.name): a.name for a in n.names if a.name[:1].isupper()})
    return aliases, bare


def _gradio_calls(tree: ast.AST) -> List[Tuple[ast.Call, str]]:
    aliases, bare = _gradio_names(tree)
    out = []
    for call in ast.walk(tree):
        if not isinstance(call, ast.Call):
            continue
        f = call.func
        if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id in aliases and f.attr[:1].isupper():
            out.append((call, f.attr))
        elif isinstance(f, ast.Name) and f.id in bare:
            out.append((call, bare[f.id]))
    return out


def gradio_names_used(code: str) -> List[str]:
    """Gradio classes the code calls, for the runtime probe. Unparseable code -> []."""
    try:
        return sorted({name for _, name in _gradio_calls(ast.parse(code))})
    except SyntaxError:
        return []


def _safe_to_drop(class_name: str, kw: ast.keyword) -> bool:
    if kw.arg in COSMETIC_KWARGS:
        return True
    return (kw.arg == "type" and class_name in ("ChatInterface", "Chatbot")
            and isinstance(kw.value, ast.Constant) and kw.value.value == "messages")


def check_code(code: str, snapshot: Optional[ApiSnapshot] = None) -> List[Finding]:
    """Compatibility findings for ``code``. With a snapshot every keyword of every gradio call is checked against the real
    signature; without one only STATIC_REMOVALS are. Raises SyntaxError for unparseable code (callers report that separately)."""
    return check_tree(ast.parse(code), snapshot)


def check_tree(tree: ast.AST, snapshot: Optional[ApiSnapshot] = None) -> List[Finding]:
    """check_code() for an already parsed module."""
    major = snapshot.major if snapshot else None
    findings: List[Finding] = []
    for call, cls in _gradio_calls(tree):
        sig = snapshot.classes.get(cls) if snapshot else None
        if snapshot and cls in snapshot.missing:
            findings.append(Finding(call.lineno, call.col_offset, cls, "missing_class", None,
                                    f"gr.{cls} does not exist in Gradio {snapshot.version}; use a component that does"))
            continue
        for kw in call.keywords:
            if kw.arg is None:
                continue  # **kwargs at the call site: unknowable statically
            removal = STATIC_REMOVALS.get((cls, kw.arg))
            rejected = bool(removal and (major is None or major >= removal[0]))
            if not rejected and sig and not sig.var_kw and kw.arg not in sig.params:
                rejected = True
            if not rejected:
                continue
            hint = f" ({removal[1]})" if removal else ""
            where = f"Gradio {snapshot.version}" if snapshot else f"Gradio {removal[0]}+"
            findings.append(Finding(
                kw.lineno, kw.col_offset, cls, "unsupported_kwarg", kw.arg,
                f"gr.{cls}() does not accept '{kw.arg}' in {where}{hint}", _safe_to_drop(cls, kw),
                (kw.lineno, kw.col_offset, kw.end_lineno, kw.end_col_offset)))
        if sig and sig.max_positional is not None and not any(isinstance(a, ast.Starred) for a in call.args) \
                and len(call.args) > sig.max_positional:
            findings.append(Finding(call.lineno, call.col_offset, cls, "too_many_positional", None,
                                    f"gr.{cls}() takes at most {sig.max_positional} positional argument(s) in Gradio "
                                    f"{snapshot.version} but {len(call.args)} were given; pass the rest by keyword"))
    return sorted(set(findings), key=lambda f: (f.line, f.col, f.message))


def format_findings(findings: Sequence[Finding], filename: str = "app.py", limit: int = 10) -> str:
    lines = [f"  {f.render(filename)}" for f in findings[:limit]]
    if len(findings) > limit:
        lines.append(f"  ... and {len(findings) - limit} more")
    return "\n".join(lines)


def _line_starts(data: bytes) -> List[int]:
    starts, pos = [0], 0
    for part in data.split(b"\n")[:-1]:
        pos += len(part) + 1
        starts.append(pos)
    return starts


def repair_code(code: str, findings: Sequence[Finding]) -> Tuple[str, List[Finding], List[Finding]]:
    """Remove the keywords of repairable ``unsupported_kwarg`` findings. Returns (new_code, applied, remaining). The result is
    re-parsed; if the edit would leave invalid Python nothing is changed and every finding stays in ``remaining``."""
    todo = [f for f in findings if f.repairable and f.kind == "unsupported_kwarg" and f.span]
    remaining = [f for f in findings if f not in todo]
    if not todo:
        return code, [], list(findings)
    data = code.encode("utf-8")
    starts = _line_starts(data)
    edits = []
    for f in todo:
        (l1, c1, l2, c2) = f.span
        edits.append((starts[l1 - 1] + c1, starts[l2 - 1] + c2, f))
    for s, e, _ in sorted(set((s, e, f) for s, e, f in edits), key=lambda t: t[0], reverse=True):
        j = e
        while j < len(data) and data[j:j + 1] in (b" ", b"\t", b"\r", b"\n"):
            j += 1
        if j < len(data) and data[j:j + 1] == b",":
            end = j + 1
            while end < len(data) and data[end:end + 1] in (b" ", b"\t"):
                end += 1
            ls = data.rfind(b"\n", 0, s) + 1
            own_line = not data[ls:s].strip() and (data[end:end + 1] == b"\n" or data[end:end + 2] == b"\r\n")
            if own_line:
                s = ls  # the keyword had its own line: drop the whole line, not just the text
                end += 2 if data[end:end + 2] == b"\r\n" else 1
            data = data[:s] + data[end:]
            continue
        k = s
        while k > 0 and data[k - 1:k] in (b" ", b"\t", b"\r", b"\n"):
            k -= 1
        data = data[:k - 1] + data[e:] if k > 0 and data[k - 1:k] == b"," else data[:s] + data[e:]
    try:
        new_code = data.decode("utf-8")
        ast.parse(new_code)
    except (SyntaxError, UnicodeDecodeError):
        return code, [], list(findings)
    return new_code, todo, remaining
