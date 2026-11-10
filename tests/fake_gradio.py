"""A tiny stand-in for the parts of the Gradio API that ``m2g.web.build_ui`` uses.

It records every event handler (fn, inputs, outputs, options, and the event it follows via ``.then``) so tests can call the
registered functions exactly as Gradio would, without Gradio. It is NOT the real library: it cannot check how Gradio
schedules events or renders components, only that the handlers are wired with the right arity and order.
"""
import sys
import types
from contextlib import contextmanager
from typing import Any, Dict, List, Optional


class Event:
    def __init__(self, registry: List["Event"], fn: Any, inputs: Any, outputs: Any, options: Dict[str, Any], after: Optional["Event"]):
        self.fn, self.inputs, self.outputs, self.options, self.after = fn, inputs, outputs, options, after
        self._registry = registry
        registry.append(self)

    def then(self, fn, inputs=None, outputs=None, **options) -> "Event":
        return Event(self._registry, fn, inputs, outputs, options, self)


class Component:
    registry: List[Event] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.args, self.kwargs = args, kwargs

    def __enter__(self) -> "Component":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False

    def _on(self, fn=None, inputs=None, outputs=None, **options) -> Event:
        return Event(Component.registry, fn, inputs, outputs, options, None)

    click = submit = tick = load = change = _on


class FakeError(Exception):
    pass


def make_module() -> types.ModuleType:
    mod = types.ModuleType("gradio")
    mod.update = lambda **kw: {"__update__": kw}
    mod.Error = FakeError
    mod.__getattr__ = lambda name: Component if name[:1].isupper() else (_ for _ in ()).throw(AttributeError(name))
    return mod


@contextmanager
def fake_gradio():
    """Install the stub as ``gradio`` and yield the list of recorded events (fresh for each use)."""
    Component.registry = []
    saved = sys.modules.get("gradio")
    sys.modules["gradio"] = make_module()
    try:
        yield Component.registry
    finally:
        if saved is None:
            sys.modules.pop("gradio", None)
        else:
            sys.modules["gradio"] = saved


def events_named(events: List[Event], name: str) -> List[Event]:
    return [e for e in events if getattr(e.fn, "__name__", "") == name]
