"""Synthetic multi-version Gradio API for offline tests (companion of fake_gradio.py, which only records event wiring).

Here the point is the CONSTRUCTOR SIGNATURES: every class below really rejects unknown keywords / extra positional arguments with
the same ``TypeError`` text Python gives, so an app executed against it fails exactly where it would fail on a Colab runtime with
that Gradio version. The profiles are a hand-written SUBSET (the parameters the tests rely on, written from the Gradio 4/5/6
release behaviour that was observed or documented); they are not the real libraries and cannot prove a real Colab run works.
"""
import inspect
import sys
import types
from contextlib import contextmanager
from typing import Dict, Iterator, List, Optional, Tuple

VERSIONS = ("4.44.1", "5.49.0", "6.30.0", "6.31.0")


def _p(text: str) -> List[str]:
    return text.split()


_LAYOUT_OLD = "theme css js head"  # constructor styling arguments of Gradio 4/5 (moved to launch() in 6)
_COMMON_COMPONENT = "label info show_label container scale min_width interactive visible elem_id elem_classes"

# class -> (positional parameters, keyword-only parameters), per major version
_PROFILES: Dict[int, Dict[str, Tuple[List[str], List[str]]]] = {}


def _build_profiles() -> None:
    def comp(pos, extra=""):
        return _p(pos), _p(_COMMON_COMPONENT + " " + extra)

    for major in (4, 5, 6):
        old = major < 6
        flag = "allow_flagging" if major == 4 else "flagging_mode"
        _PROFILES[major] = {
            "Interface": (_p("fn inputs outputs"), _p(f"examples live title description article {flag} analytics_enabled batch "
                                                      "max_batch_size api_name show_progress concurrency_limit"
                                                      + (" " + _LAYOUT_OLD if old else ""))),
            "ChatInterface": (_p("fn"), _p(f"multimodal chatbot textbox additional_inputs examples cache_examples title description "
                                           f"{flag} editable" + (" type " + _LAYOUT_OLD if old else ""))),
            "TabbedInterface": (_p("interface_list tab_names title"), _p("analytics_enabled" + (" " + _LAYOUT_OLD if old else ""))),
            "Blocks": ([], _p("analytics_enabled mode title fill_height" + (" fill_width " + _LAYOUT_OLD + " css_paths head_paths"
                                                                           if old else " fill_width"))),
            "Tab": (_p("label"), _p("visible interactive id elem_id elem_classes")),
            "Row": ([], _p("equal_height variant visible elem_id elem_classes")),
            "Column": ([], _p("scale min_width variant visible elem_id elem_classes")),
            "Accordion": (_p("label"), _p("open visible elem_id elem_classes")),
            "Markdown": (_p("value"), _p("visible elem_id elem_classes label show_label container")),
            "Button": (_p("value"), _p("variant size visible interactive elem_id elem_classes scale min_width")),
            "Textbox": comp("value", "lines max_lines placeholder autofocus autoscroll text_align rtl show_copy_button type max_length"),
            "Audio": comp("value", "sources type streaming autoplay show_download_button"),
            "Image": comp("value", "type sources height width"),
            "Video": comp("value", "sources height width"),
            "File": comp("value", "file_count file_types type"),
            "Label": comp("value", "num_top_classes"),
            "Slider": comp("minimum maximum", "value step"),
            "Dropdown": comp("choices", "value multiselect allow_custom_value"),
            "Checkbox": comp("value"),
            "Number": comp("value", "precision minimum maximum"),
            "Chatbot": comp("value", "height placeholder show_copy_button"
                                     + (" type bubble_full_width avatar_images" if old else " avatar_images")),
            "MultimodalTextbox": comp("value", "file_types placeholder lines"),
            "Gallery": comp("value", "columns rows height"),
            "JSON": comp("value"),
            "HighlightedText": comp("value", "show_legend"),
        }


_build_profiles()


class _Base:
    """Context-manager + event-listener behaviour so ``with gr.Blocks() as demo: ...`` and ``btn.click(...)`` work."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return lambda *a, **k: self  # click / submit / change / load / launch ... all return the object

    def launch(self, **kwargs):
        self.launched_with = kwargs
        return (self, "http://127.0.0.1:7860/", None)


def _make_class(name: str, pos: List[str], kwonly: List[str]) -> type:
    parameters = [inspect.Parameter("self", inspect.Parameter.POSITIONAL_OR_KEYWORD)]
    parameters += [inspect.Parameter(p, inspect.Parameter.POSITIONAL_OR_KEYWORD, default=None) for p in pos]
    parameters += [inspect.Parameter(k, inspect.Parameter.KEYWORD_ONLY, default=None) for k in kwonly]
    signature = inspect.Signature(parameters)

    def __init__(self, *args, **kwargs):
        try:
            bound = signature.bind(self, *args, **kwargs)
        except TypeError as exc:  # the same wording Python uses for a real constructor
            raise TypeError(f"{name}.__init__() {exc}") from None
        self.params = {k: v for k, v in bound.arguments.items() if k != "self"}

    __init__.__signature__ = signature
    return type(name, (_Base,), {"__init__": __init__})


def make_gradio(version: str) -> types.ModuleType:
    """A module that behaves like ``import gradio`` of the given version (major 4, 5 or 6)."""
    major = int(version.split(".")[0])
    if major not in _PROFILES:
        raise ValueError(f"no synthetic profile for Gradio {version}")
    mod = types.ModuleType("gradio")
    mod.__version__ = version
    for name, (pos, kwonly) in _PROFILES[major].items():
        setattr(mod, name, _make_class(name, pos, kwonly))
    mod.Error = type("Error", (Exception,), {})
    mod.themes = types.SimpleNamespace(Soft=lambda *a, **k: object(), Default=lambda *a, **k: object())
    mod.update = lambda **kw: {"__update__": kw}
    return mod


@contextmanager
def fake_gradio_api(version: str) -> Iterator[types.ModuleType]:
    """Install the synthetic gradio as ``sys.modules['gradio']`` for the duration of the block."""
    saved: Optional[types.ModuleType] = sys.modules.get("gradio")
    mod = make_gradio(version)
    sys.modules["gradio"] = mod
    try:
        yield mod
    finally:
        if saved is None:
            sys.modules.pop("gradio", None)
        else:
            sys.modules["gradio"] = saved


def run_app(code: str, version: str) -> dict:
    """Execute generated app code against the synthetic Gradio of ``version`` and return its namespace. A constructor the
    real Gradio would reject raises TypeError here, which is how the tests prove the checker predicts real failures."""
    ns: dict = {"__name__": "generated_app"}
    with fake_gradio_api(version):
        exec(compile(code, "app.py", "exec"), ns)
    return ns
