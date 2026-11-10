"""Regression tests for UI error visibility (the bare Gradio "Error" toast).

No Gradio, network or Colab is needed. ``test_web_wiring`` reads ``m2g/web.py`` as text/AST, so it also runs where
Gradio is not installed.
"""
import ast
import io
import logging
import os
import unittest
from pathlib import Path
from unittest import mock

from m2g import ui_errors
from m2g.ui_errors import configure_logging, format_exception, guarded, scrub, short_message

WEB_PY = Path(__file__).resolve().parent.parent / "m2g" / "web.py"
HF = "hf_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3"
GH = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4"
GOOGLE_ACCESS = "ya29.a0AfH6SMBx-example_token.value"
GOOGLE_CODE = "4/0AX4XfWh_exampleAuthorizationCode1234567890"


class UiError(Exception):
    """Stands in for gradio.Error."""


class ScrubTests(unittest.TestCase):
    def test_token_shapes_are_redacted(self):
        text = f"hf={HF} gh={GH} g={GOOGLE_ACCESS} code={GOOGLE_CODE} Authorization: Bearer abcdefghijklmnop123"
        out = scrub(text)
        for secret in (HF, GH, GOOGLE_ACCESS, GOOGLE_CODE, "abcdefghijklmnop123"):
            self.assertNotIn(secret, out)
        self.assertIn("[redacted]", out)

    def test_explicit_and_environment_secrets_are_redacted(self):
        with mock.patch.dict(os.environ, {"MY_SERVICE_TOKEN": "env-secret-value-123"}):
            out = scrub("password was hunter2-hunter2 and env env-secret-value-123", ["hunter2-hunter2"])
        self.assertNotIn("hunter2-hunter2", out)
        self.assertNotIn("env-secret-value-123", out)

    def test_key_value_pairs_keep_the_key(self):
        out = scrub("GET /x?token=abc123456&mode=1 password=swordfish")
        self.assertIn("token=[redacted]", out)
        self.assertIn("password=[redacted]", out)
        self.assertIn("mode=1", out)
        self.assertNotIn("swordfish", out)

    def test_ordinary_text_is_untouched(self):
        self.assertEqual(scrub("Colab CLI check failed: exit 1"), "Colab CLI check failed: exit 1")

    def test_traceback_is_complete_and_redacted(self):
        def inner():
            raise ValueError(f"bad credentials {HF}")

        try:
            inner()
        except ValueError as exc:
            text = format_exception(exc)
        self.assertIn("Traceback", text)
        self.assertIn("inner", text)
        self.assertIn("ValueError", text)
        self.assertNotIn(HF, text)


class GuardedTests(unittest.TestCase):
    def test_success_passes_through(self):
        @guarded("add", error_cls=UiError)
        def add(a, b):
            return a + b

        self.assertEqual(add(1, 2), 3)

    def test_failure_is_logged_with_traceback_and_raised_as_visible_error(self):
        @guarded("check", error_cls=UiError)
        def boom():
            raise OSError(f"cannot reach host with {HF}")

        with self.assertLogs("m2g.ui", level="ERROR") as logs:
            with self.assertRaises(UiError) as ctx:
                boom()
        message = str(ctx.exception)
        self.assertIn("check failed: OSError", message)
        self.assertIn("console", message)
        self.assertNotIn(HF, message)
        logged = "\n".join(logs.output)
        self.assertIn("Traceback", logged)
        self.assertIn("boom", logged)
        self.assertNotIn(HF, logged)

    def test_message_never_empty_or_bare_error(self):
        @guarded("launch", error_cls=UiError)
        def boom():
            raise KeyError()

        with self.assertLogs("m2g.ui", level="ERROR"):
            with self.assertRaises(UiError) as ctx:
                boom()
        self.assertNotEqual(str(ctx.exception).strip(), "Error")
        self.assertIn("KeyError", str(ctx.exception))

    def test_deliberate_ui_error_passes_through_unlogged(self):
        @guarded("submit", error_cls=UiError)
        def fn():
            raise UiError("Please enter a URL.")

        with self.assertRaises(UiError) as ctx:
            fn()
        self.assertEqual(str(ctx.exception), "Please enter a URL.")

    def test_instance_secrets_are_scrubbed_from_log_and_message(self):
        @guarded("login", error_cls=UiError, secrets=lambda: ["pw-9999-secret"])
        def boom():
            raise RuntimeError("rejected pw-9999-secret")

        with self.assertLogs("m2g.ui", level="ERROR") as logs:
            with self.assertRaises(UiError) as ctx:
                boom()
        self.assertNotIn("pw-9999-secret", str(ctx.exception))
        self.assertNotIn("pw-9999-secret", "\n".join(logs.output))

    def test_fallback_returns_value_and_logs_each_distinct_failure_once(self):
        calls = {"n": 0}

        @guarded("tick", fallback=lambda: ("keep", "keep"))
        def tick():
            calls["n"] += 1
            raise RuntimeError("same failure")

        with self.assertLogs("m2g.ui", level="ERROR") as logs:
            self.assertEqual(tick(), ("keep", "keep"))
            self.assertEqual(tick(), ("keep", "keep"))
            self.assertEqual(tick(), ("keep", "keep"))
        self.assertEqual(calls["n"], 3)
        self.assertEqual(len(logs.records), 1)  # a 3-second timer must not flood the console

    def test_fallback_logs_again_when_the_failure_changes(self):
        errors = iter([RuntimeError("one"), RuntimeError("two")])

        @guarded("tick", fallback=lambda: None)
        def tick():
            raise next(errors)

        with self.assertLogs("m2g.ui", level="ERROR") as logs:
            tick()
            tick()
        self.assertEqual(len(logs.records), 2)

    def test_keyboard_interrupt_is_not_swallowed(self):
        @guarded("x", error_cls=UiError)
        def fn():
            raise KeyboardInterrupt()

        with self.assertRaises(KeyboardInterrupt):
            fn()

    def test_short_message_truncates(self):
        msg = short_message(ValueError("x" * 1000), "step")
        self.assertLess(len(msg), 450)


class LoggingTests(unittest.TestCase):
    def setUp(self):
        self.logger = logging.getLogger("m2g")
        self._handlers, self._level = list(self.logger.handlers), self.logger.level
        # Start from a logger with no console handler: configure_logging() skips adding one when any already exists
        # (e.g. left by another test that ran web.main()), which made the stream below receive nothing.
        self.logger.handlers[:] = [h for h in self._handlers if not getattr(h, "_m2g_console", False)]
        ui_errors._configured["done"] = False

    def tearDown(self):
        self.logger.handlers[:] = self._handlers
        self.logger.setLevel(self._level)
        ui_errors._configured["done"] = False

    def test_configure_logging_reaches_the_stream_once(self):
        stream = io.StringIO()
        configure_logging(stream=stream)
        configure_logging(stream=stream)  # idempotent
        logging.getLogger("m2g.colab").info("hello from colab")
        self.assertEqual(stream.getvalue().count("hello from colab"), 1)


class WebWiringTests(unittest.TestCase):
    """``m2g/web.py`` must keep error visibility switched on; read as source so Gradio is not required."""

    @classmethod
    def setUpClass(cls):
        cls.source = WEB_PY.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def test_launch_enables_show_error(self):
        launches = [n for n in ast.walk(self.tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and n.func.attr == "launch" and isinstance(n.func.value, ast.Name) and n.func.value.id == "demo"]
        self.assertTrue(launches, "web.py no longer calls demo.launch()")
        for call in launches:
            kw = {k.arg: k.value for k in call.keywords}
            self.assertIn("show_error", kw)
            self.assertTrue(isinstance(kw["show_error"], ast.Constant) and kw["show_error"].value is True)

    def test_logging_is_configured_before_launch(self):
        self.assertIn("configure_logging(", self.source)

    def test_every_panel_callback_is_guarded(self):
        build_ui = next(n for n in self.tree.body if isinstance(n, ast.FunctionDef) and n.name == "build_ui")
        panel = next(n for n in self.tree.body if isinstance(n, ast.FunctionDef) and n.name == "_build_colab_panel")
        for fn in (build_ui, panel):
            handlers = {c.args[0].id for c in ast.walk(fn) if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
                        and c.func.attr in ("click", "submit", "tick") and c.args and isinstance(c.args[0], ast.Name)}
            self.assertTrue(handlers, f"no event handlers found in {fn.name}")
            for name in handlers:
                local = [n for n in ast.walk(fn) if isinstance(n, ast.FunctionDef) and n.name == name]
                self.assertTrue(local, f"{name} is not a local function in {fn.name}")
                decorators = [ast.unparse(d) for d in local[0].decorator_list]
                self.assertTrue(any("guard" in d for d in decorators), f"{name} in {fn.name} is not guarded: {decorators}")

    def test_no_lambda_event_handlers_bypass_the_guard(self):
        for call in ast.walk(self.tree):
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute) and call.func.attr in ("click", "tick"):
                if call.args:
                    self.assertNotIsInstance(call.args[0], ast.Lambda, "an un-guarded lambda handler was added")


if __name__ == "__main__":
    unittest.main()
