"""Regression tests for the generated Colab launch cell.

Covers: exactly one Gradio launch / one server, correct URL classification (public gradio.live vs Colab proxy), and
accurate status text. The cell is taken from a notebook that was generated AND round-tripped through JSON, then executed
against a fake ``app.demo``; no Gradio, network or Colab is needed.
"""
import contextlib
import io
import json
import sys
import types
import unittest
from unittest import mock

from m2g.colab_gen import generate_notebook, notebook_to_json
from m2g.colab_workflow import verify_share_url
from m2g.validate import validate_notebook
from test_analyzer import analyze_preset

PUBLIC = "https://abc123.gradio.live"
PREVIEW = "https://7860-gpu-t4-s-abc123xyz-a.us-west4-0.prod.colab.dev/"
LOCAL = "http://127.0.0.1:7860/"


class FakeDemo:
    """Scripted ``launch`` results. Each step is ('ok', tuple, share_url_attr) or ('raise', exc, server_started)."""

    def __init__(self, steps):
        self.steps = list(steps)
        self.launch_calls = []
        self.closed = 0
        self.blocked = 0
        self.is_running = False
        self.local_url = None
        self.share_url = None

    def launch(self, **kwargs):
        self.launch_calls.append(kwargs)
        step = self.steps.pop(0)
        if step[0] == "raise":
            if step[2]:
                self.is_running, self.local_url = True, LOCAL
            raise step[1]
        _, ret, share_attr = step
        self.is_running, self.local_url, self.share_url = True, LOCAL, share_attr
        return ret

    def close(self):
        self.closed += 1
        self.is_running = False

    def block_thread(self):
        self.blocked += 1


class FakeApp:
    def __init__(self, demo):
        self.demo = demo


def colab_modules(proxy):
    """Fake google.colab.output. proxy: a URL string, an Exception to raise, or None for 'not running in Colab'."""
    if proxy is None:
        return {"google.colab.output": None}  # makes `from google.colab.output import eval_js` raise ImportError
    calls = []

    def eval_js(expr, *a, **k):
        calls.append(expr)
        if isinstance(proxy, BaseException):
            raise proxy
        return proxy

    google, colab, output = types.ModuleType("google"), types.ModuleType("google.colab"), types.ModuleType("google.colab.output")
    output.eval_js = eval_js
    google.colab, colab.output = colab, output
    mods = {"google": google, "google.colab": colab, "google.colab.output": output}
    mods["_calls"] = calls
    return mods


def launch_source_from_generated_notebook():
    nb = generate_notebook(analyze_preset("sst2"))
    reloaded = json.loads(notebook_to_json(nb))  # what ends up in colab_notebook.ipynb on disk
    validate_notebook(reloaded)
    # the launch cell is identified by what it does, not by its position (the package cells before it are not part of this contract)
    launch_cells = ["".join(c["source"]) for c in reloaded["cells"] if c["cell_type"] == "code" and "app.demo.launch(" in "".join(c["source"])]
    assert len(launch_cells) == 1, len(launch_cells)
    return launch_cells[0]


def run_cell(steps, proxy=PREVIEW, source=None):
    demo = FakeDemo(steps)
    ns = {"app": FakeApp(demo)}
    mods = colab_modules(proxy)
    calls = mods.pop("_calls", [])
    buf = io.StringIO()
    with mock.patch.dict(sys.modules, mods), contextlib.redirect_stdout(buf):
        exec(compile(source or launch_source_from_generated_notebook(), "launch", "exec"), ns)
    out = buf.getvalue()
    result = None
    for line in out.splitlines():
        if line.startswith("M2G_LAUNCH_RESULT "):
            result = json.loads(line[len("M2G_LAUNCH_RESULT "):])
    return demo, ns, out, result, calls


class TestLaunchCellStructure(unittest.TestCase):
    def test_generated_notebook_with_new_launch_cell_validates(self):
        launch_source_from_generated_notebook()

    def test_share_attempt_is_kept_and_launch_is_called_once_in_the_source(self):
        src = launch_source_from_generated_notebook()
        self.assertIn("SHARE_PUBLIC_LINK = True", src)
        self.assertIn("app.demo.launch(share=SHARE_PUBLIC_LINK", src)
        self.assertNotIn(".close()", src, "the running server must never be closed")
        self.assertNotIn("debug=True", src)


class TestOneLaunchOnly(unittest.TestCase):
    def test_tunnel_works_one_launch_one_server(self):
        demo, ns, out, result, _ = run_cell([("ok", (None, LOCAL, PUBLIC), PUBLIC)])
        self.assertEqual(len(demo.launch_calls), 1)
        self.assertIs(demo.launch_calls[0]["share"], True)
        self.assertEqual(demo.closed, 0)
        self.assertEqual(demo.blocked, 1)
        self.assertEqual(out.count("=" * 70), 2, "one banner only, no repeated launch messages")

    def test_tunnel_raises_but_server_is_running_it_is_kept_not_relaunched(self):
        err = ValueError("dial tcp 44.237.78.176:7000: connect: connection refused")
        demo, ns, out, result, _ = run_cell([("raise", err, True)])
        self.assertEqual(len(demo.launch_calls), 1, "no second launch while the first server is up")
        self.assertEqual(demo.closed, 0)
        self.assertTrue(demo.is_running)
        self.assertEqual(demo.blocked, 1)
        self.assertIn("connection refused", out)  # real error is shown

    def test_tunnel_returns_no_url_without_raising_one_launch(self):
        demo, ns, out, result, _ = run_cell([("ok", (None, LOCAL, None), None)])
        self.assertEqual(len(demo.launch_calls), 1)
        self.assertEqual(demo.closed, 0)
        self.assertIn("no public gradio.live URL", ns["TUNNEL_ERROR"])

    def test_second_launch_only_when_first_never_started_a_server(self):
        err = RuntimeError("could not start")
        demo, ns, out, result, _ = run_cell([("raise", err, False), ("ok", (None, LOCAL, None), None)])
        self.assertEqual([c["share"] for c in demo.launch_calls], [True, False])
        self.assertEqual(demo.closed, 0)
        self.assertTrue(demo.is_running)
        self.assertEqual(result["mode"], "colab_preview")
        self.assertIn("could not start", result["tunnel_error"])

    def test_share_disabled_launches_once_privately(self):
        src = launch_source_from_generated_notebook().replace("SHARE_PUBLIC_LINK = True", "SHARE_PUBLIC_LINK = False")
        demo, ns, out, result, _ = run_cell([("ok", (None, LOCAL, None), None)], source=src)
        self.assertEqual([c["share"] for c in demo.launch_calls], [False])
        self.assertNotIn("PUBLIC ACCESS UNAVAILABLE", out)
        self.assertIsNone(result["tunnel_error"])


class TestUrlClassification(unittest.TestCase):
    def setUp(self):
        _, self.ns, _, _, _ = run_cell([("ok", (None, LOCAL, PUBLIC), PUBLIC)])
        self.classify = self.ns["classify_url"]

    def test_public_and_preview_are_distinguished(self):
        self.assertEqual(self.classify(PUBLIC), "public")
        self.assertEqual(self.classify("https://abc123.gradio.app/"), "public")
        self.assertEqual(self.classify(PREVIEW), "colab_preview")
        self.assertEqual(self.classify("https://7860-gpu-t4-abc.colab.dev/"), "colab_preview")

    def test_lookalikes_and_junk_are_not_classified(self):
        for bad in ("https://abc123.gradio.live.evil.com", "http://abc123.gradio.live", "https://evil.com/x.gradio.live",
                    "https://7860-x.colab.dev.evil.com/", "https://colab.dev/", "javascript:alert(1)", "", None, 7860,
                    "https://abc123.gradio.live/\nextra"):
            self.assertIsNone(self.classify(bad), repr(bad))

    def test_colab_proxy_url_is_never_taken_as_the_public_url(self):
        demo, ns, out, result, _ = run_cell([("ok", (None, LOCAL, PREVIEW), PREVIEW)])
        self.assertIsNone(ns["PUBLIC_URL"])
        self.assertEqual(result["mode"], "colab_preview")
        self.assertIsNone(result["public_url"])

    def test_platform_url_check_rejects_the_colab_proxy_url(self):
        ok, detail = verify_share_url(PREVIEW, opener=lambda *a, **k: self.fail("must not even be requested"))
        self.assertFalse(ok)
        self.assertIn("Unexpected share URL format", detail)


class TestAccurateStatus(unittest.TestCase):
    def test_public_success_shows_public_url_and_preview_separately(self):
        demo, ns, out, result, calls = run_cell([("ok", (None, LOCAL, PUBLIC), PUBLIC)])
        self.assertEqual(result, {"mode": "public", "public_url": PUBLIC, "colab_preview_url": PREVIEW,
                                  "tunnel_error": None})
        self.assertIn("PUBLIC URL (temporary", out)
        self.assertIn("COLAB PREVIEW AVAILABLE", out)
        self.assertNotIn("PUBLIC ACCESS UNAVAILABLE", out)
        self.assertEqual(calls, ["google.colab.kernel.proxyPort(7860)"], "port comes from the real local URL")

    def test_tunnel_failure_with_working_colab_preview_is_not_reported_as_failure(self):
        err = ValueError("dial tcp 44.237.78.176:7000: connect: connection refused")
        demo, ns, out, result, _ = run_cell([("raise", err, True)])
        self.assertEqual(result["mode"], "colab_preview")
        self.assertIsNone(result["public_url"])
        self.assertEqual(result["colab_preview_url"], PREVIEW)
        self.assertIn("connection refused", result["tunnel_error"])
        self.assertIn("PUBLIC ACCESS UNAVAILABLE", out)
        self.assertIn("COLAB PREVIEW AVAILABLE", out)
        self.assertIn("NOT a public link", out)
        self.assertIn(PREVIEW, out)
        self.assertNotIn("FAILED", out.upper().replace("DID NOT START", ""), "the whole app must not be labelled failed")
        self.assertNotIn("PUBLIC URL (temporary", out, "a preview is never presented as a public deployment")

    def test_no_preview_obtainable_says_so_and_invents_nothing(self):
        for proxy in (None, RuntimeError("no frontend"), "https://evil.example/", 12345):
            demo, ns, out, result, _ = run_cell([("ok", (None, LOCAL, None), None)], proxy=proxy)
            self.assertEqual(result["mode"], "local_only", repr(proxy))
            self.assertIsNone(result["colab_preview_url"], repr(proxy))
            self.assertIsNone(result["public_url"], repr(proxy))
            self.assertIn("running inside Colab only", out)
            self.assertNotIn("COLAB PREVIEW AVAILABLE", out)

    def test_app_that_never_started_is_reported_as_not_started(self):
        err = RuntimeError("boom")
        demo = FakeDemo([("raise", err, False), ("raise", err, False)])
        ns = {"app": FakeApp(demo)}
        with mock.patch.dict(sys.modules, colab_modules(None)), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(RuntimeError):  # a second failure is a real failure and is not swallowed
                exec(compile(launch_source_from_generated_notebook(), "launch", "exec"), ns)


if __name__ == "__main__":
    unittest.main()
