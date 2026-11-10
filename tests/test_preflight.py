"""Offline tests for m2g.preflight (runtime Gradio preflight).

The runtime is simulated: ``run_runner_locally`` executes the REAL probe runner that is shipped to Colab, with the
subprocess it starts replaced by an in-process run of the real probe source against the synthetic Gradio of a chosen
version (tests/fake_gradio_api.py). So the probe script, the snapshot parser, the checker and the repair are the real code;
only the Colab runtime and the real Gradio libraries are replaced. This cannot prove a real Colab run works."""
import contextlib
import io
import json
import subprocess
import unittest
from types import SimpleNamespace
from unittest import mock

from fake_gradio_api import fake_gradio_api
from m2g import preflight as pf

MARKER = "__M2G_TEST__ PROBE "


def run_runner_locally(code: str, version: str, raise_timeout: bool = False) -> str:
    """Execute a probe runner (from render_probe_runner) and return everything it printed."""
    def fake_run(argv, **kw):
        if raise_timeout:
            raise subprocess.TimeoutExpired(argv, 1)
        buf = io.StringIO()
        with fake_gradio_api(version), contextlib.redirect_stdout(buf):
            exec(compile(argv[2], "probe", "exec"), {})
        return SimpleNamespace(returncode=0, stdout=buf.getvalue(), stderr="")

    out = io.StringIO()
    with mock.patch("subprocess.run", fake_run), contextlib.redirect_stdout(out):
        exec(compile(code, "remote_preflight", "exec"), {})
    return out.getvalue()


def probe_for(code: str, version: str, **kw) -> dict:
    printed = run_runner_locally(pf.render_probe_runner(code, MARKER), version, **kw)
    line = next(ln for ln in printed.splitlines() if ln.startswith(MARKER))
    return json.loads(line[len(MARKER):])


CHAT = 'import gradio as gr\n\ndef fn(message, history):\n    return message\n\ndemo = gr.ChatInterface(fn, type="messages")\n'
PLAIN = 'import gradio as gr\n\ndemo = gr.Blocks()\nwith demo:\n    gr.Markdown("hello")\n    gr.Button("go")\n'


class TestRunner(unittest.TestCase):
    def test_runner_is_ascii_valid_python_and_prints_one_marker_line(self):
        code = pf.render_probe_runner(CHAT, MARKER)
        code.encode("ascii")
        compile(code, "runner", "exec")
        probe = probe_for(CHAT, "6.30.0")
        self.assertEqual(probe["rc"], 0)
        self.assertIn("M2G_GRADIO_API", probe["out"])

    def test_the_runtime_version_reaches_the_checker(self):
        for version in ("5.49.0", "6.30.0"):
            res = pf.run_preflight(PLAIN, probe_for(PLAIN, version))
            self.assertEqual(res.gradio_version, version)
            self.assertGreater(res.classes_checked, 0)

    def test_a_timed_out_probe_is_reported_not_swallowed(self):
        res = pf.run_preflight(PLAIN, probe_for(PLAIN, "6.30.0", raise_timeout=True))
        self.assertFalse(res.ok)
        self.assertEqual(res.status, pf.PROBE_FAILED)
        self.assertIn("TimeoutExpired", res.message)


class TestVerdicts(unittest.TestCase):
    def test_compatible_app_passes_unchanged(self):
        res = pf.run_preflight(PLAIN, probe_for(PLAIN, "6.30.0"))
        self.assertEqual((res.status, res.ok, res.code), (pf.PASSED, True, PLAIN))

    def test_safe_repair_is_applied_and_rechecked(self):
        res = pf.run_preflight(CHAT, probe_for(CHAT, "6.30.0"))
        self.assertEqual((res.status, res.ok), (pf.REPAIRED, True))
        self.assertNotIn("type=", res.code)
        self.assertIn("gr.ChatInterface(fn)", res.code)
        self.assertEqual(len(res.repaired), 1)
        self.assertTrue(any("removed unsupported argument" in ln for ln in res.log_lines))

    def test_the_same_app_needs_no_repair_on_gradio_5(self):
        res = pf.run_preflight(CHAT, probe_for(CHAT, "5.49.0"))
        self.assertEqual((res.status, res.code), (pf.PASSED, CHAT))

    def test_unsafe_unsupported_argument_blocks(self):
        code = CHAT.replace('type="messages"', 'type="tuples"')
        res = pf.run_preflight(code, probe_for(code, "6.30.0"))
        self.assertFalse(res.ok)
        self.assertEqual(res.status, pf.BLOCKED)
        self.assertEqual(res.code, code)  # never a half-repaired app
        self.assertIn("'type'", res.message)

    def test_mixed_findings_block_and_name_every_remaining_problem(self):
        code = PLAIN + 'gr.Markdown("x", theme="soft", bogus=1)\n'
        res = pf.run_preflight(code, probe_for(code, "6.30.0"))
        self.assertFalse(res.ok)
        self.assertEqual(len(res.remaining), 1)
        self.assertIn("bogus", res.remaining[0])
        self.assertEqual(len(res.repaired), 1)  # the cosmetic one was removed, the unsafe one was not guessed

    def test_missing_component_blocks(self):
        code = PLAIN + "gr.NoSuchComponent()\n"
        res = pf.run_preflight(code, probe_for(code, "6.30.0"))
        self.assertEqual(res.status, pf.BLOCKED)
        self.assertIn("NoSuchComponent", res.message)

    def test_too_many_positional_arguments_block(self):
        code = PLAIN + 'gr.Button("a", "b", "c")\n'
        res = pf.run_preflight(code, probe_for(code, "6.30.0"))
        self.assertEqual(res.status, pf.BLOCKED)
        self.assertIn("positional", res.message)

    def test_unreadable_runtime_api_is_fail_closed(self):
        for probe in (None, {"rc": 1, "out": "", "err": "ModuleNotFoundError: No module named 'gradio'"},
                      {"rc": 0, "out": "garbage\nM2G_GRADIO_API {not json", "err": ""}, "not a dict"):
            res = pf.run_preflight(PLAIN, probe)
            self.assertEqual((res.status, res.ok), (pf.PROBE_FAILED, False), probe)
        res = pf.run_preflight(PLAIN, {"rc": 1, "out": "", "err": "x\nModuleNotFoundError: No module named 'gradio'"})
        self.assertIn("No module named 'gradio'", res.message)

    def test_repair_that_fails_revalidation_is_blocked_and_not_deployed(self):
        def reject(code):
            raise ValueError("nope")
        res = pf.run_preflight(CHAT, probe_for(CHAT, "6.30.0"), revalidate=reject)
        self.assertFalse(res.ok)
        self.assertEqual(res.code, CHAT)
        self.assertIn("nope", res.message)

    def test_revalidation_is_not_called_when_nothing_was_repaired(self):
        called = []
        pf.run_preflight(PLAIN, probe_for(PLAIN, "6.30.0"), revalidate=called.append)
        self.assertEqual(called, [])

    def test_invalid_python_is_blocked(self):
        res = pf.run_preflight("def (:\n", probe_for(PLAIN, "6.30.0"))
        self.assertEqual(res.status, pf.BLOCKED)

    def test_result_dict_is_json_serialisable(self):
        res = pf.run_preflight(CHAT, probe_for(CHAT, "6.30.0"))
        self.assertEqual(json.loads(json.dumps(res.to_dict()))["status"], pf.REPAIRED)


if __name__ == "__main__":
    unittest.main()
