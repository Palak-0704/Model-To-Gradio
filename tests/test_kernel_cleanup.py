"""Phase 4 (R2) tests: the Jupyter kernel is closed when the Colab runtime is disconnected or fails.

Offline: the scripted FakeTransport / FakeWS of test_colab_runtime stand in for Google and the runtime. No network, no account.
What these prove: the ONE kernel this client started is deleted (and nothing else), before the runtime is released; a failed
close never blocks the release; repeated cleanup is harmless; the proxy token and runtime URL are never logged or shown.
What they cannot prove: that a real Colab runtime accepts DELETE /api/kernels/<id> (the standard Jupyter server API) - that
needs an allowlisted project (see docs/COLAB_SETUP.md).
"""
import unittest
from unittest import mock

from m2g import colab_runtime as cr

import test_colab_runtime as tcr

TOKEN = "tok-SECRET-123"


def client(routes=(), token=TOKEN):
    """A JupyterClient whose transport knows how to start a kernel (k1) plus the given extra routes."""
    t = tcr.FakeTransport([["POST", "/api/kernels", (200, {"id": "k1"})], *routes])
    jc = cr.JupyterClient(tcr.RT_URL, token, t, lambda url, headers: tcr.FakeWS(lambda code: [tcr.stream("ok\n")]))
    return jc, t


def deletes(transport):
    return [c[1] for c in transport.calls if c[0] == "DELETE"]


def explode(url, json_body, form, headers):
    raise ConnectionResetError("reset")


class TestJupyterClientClose(unittest.TestCase):
    def test_closing_before_any_kernel_was_started_sends_nothing(self):
        jc = cr.JupyterClient(tcr.RT_URL, TOKEN, tcr.FakeTransport([]), None)
        self.assertTrue(jc.close())
        self.assertTrue(jc.close())

    def test_close_deletes_only_the_kernel_this_client_started(self):
        jc, t = client([["DELETE", "/api/kernels/k1", (204, {})]])
        self.assertTrue(jc.execute("1").ok)
        self.assertTrue(jc.close())
        self.assertEqual(len(deletes(t)), 1)
        self.assertTrue(deletes(t)[0].endswith("/api/kernels/k1"))
        delete_call = [c for c in t.calls if c[0] == "DELETE"][0]
        self.assertEqual(delete_call[4], {cr.PROXY_HEADER: TOKEN}, "authenticated like every other call, nothing extra")

    def test_closing_twice_sends_one_request(self):
        jc, t = client([["DELETE", "/api/kernels/k1", (204, {})]])
        jc.execute("1")
        self.assertTrue(jc.close())
        self.assertTrue(jc.close())
        self.assertEqual(len(deletes(t)), 1)

    def test_a_kernel_the_runtime_no_longer_knows_counts_as_closed(self):
        jc, t = client([["DELETE", "/api/kernels/k1", (404, {})]])
        jc.execute("1")
        self.assertTrue(jc.close())
        self.assertTrue(jc.close())
        self.assertEqual(len(deletes(t)), 1)

    def test_a_refused_close_reports_false_and_can_be_retried(self):
        jc, t = client([["DELETE", "/api/kernels/k1", [(500, {}), (204, {})]]])
        jc.execute("1")
        with self.assertLogs("m2g.colab", level="WARNING"):
            self.assertFalse(jc.close())
        self.assertTrue(jc.close(), "the kernel id is kept after a failure, so a later call can finish the job")
        self.assertEqual(len(deletes(t)), 2)
        self.assertTrue(jc.close())
        self.assertEqual(len(deletes(t)), 2)

    def test_a_transport_error_never_escapes(self):
        jc, t = client([["DELETE", "/api/kernels/k1", explode]])
        jc.execute("1")
        with self.assertLogs("m2g.colab", level="WARNING") as captured:
            self.assertFalse(jc.close())
        self.assertIn("ConnectionResetError", "\n".join(captured.output))

    def test_the_token_and_the_runtime_url_are_never_logged(self):
        jc, t = client([["DELETE", "/api/kernels/k1", (500, {})]])
        jc.execute("1")
        with self.assertLogs("m2g.colab", level="WARNING") as captured:
            jc.close()
        text = "\n".join(captured.output)
        self.assertNotIn(TOKEN, text)
        self.assertNotIn("googleusercontent", text)

    def test_a_kernel_id_with_an_unexpected_shape_is_never_put_into_a_url(self):
        t = tcr.FakeTransport([])
        jc = cr.JupyterClient(tcr.RT_URL, TOKEN, t, None)
        jc._kernel = "../../v1beta/runtimes/m2g-test"
        with self.assertLogs("m2g.colab", level="WARNING"):
            self.assertFalse(jc.close())
        self.assertEqual(t.calls, [])


class TestManagerClosesTheKernel(unittest.TestCase):
    def connected(self, **kw):
        mgr, t, ws = tcr.make_manager(**kw)
        mgr.connect("CPU")
        self.assertEqual(mgr.state, cr.CONNECTED, mgr.message)
        return mgr, t

    def assert_kernel_then_runtime(self, t):
        d = deletes(t)
        self.assertEqual(len(d), 2, d)
        self.assertTrue(d[0].endswith("/api/kernels/k1"), "the kernel is closed first")
        self.assertIn("/v1beta/runtimes/m2g-test", d[1], "then the runtime is released")

    def test_a_successful_disconnect_closes_the_kernel_and_then_releases_the_runtime(self):
        mgr, t = self.connected()
        mgr.disconnect()
        self.assertEqual(mgr.state, cr.NOT_CONNECTED)
        self.assertEqual(mgr.message, "Disconnected.")
        self.assertEqual(mgr.kernel_cleanup, "closed")
        self.assert_kernel_then_runtime(t)
        self.assertIsNone(mgr.runtime)
        self.assertIsNone(mgr.jupyter)
        self.assertNotIn("proxytok", mgr.status_markdown())

    def test_a_refused_kernel_close_never_blocks_the_runtime_release(self):
        mgr, t = self.connected()
        t.routes.insert(0, ["DELETE", "/api/kernels/k1", (500, {})])
        with self.assertLogs("m2g.colab", level="WARNING"):
            mgr.disconnect()
        self.assertEqual(mgr.kernel_cleanup, "failed")
        self.assertEqual(mgr.state, cr.NOT_CONNECTED)
        self.assertEqual(mgr.message, "Disconnected.", "the runtime was released, so there is nothing to warn about")
        self.assertEqual(t.count("DELETE", "/v1beta/runtimes/m2g-test"), 1)

    def test_a_kernel_close_that_raises_never_blocks_the_runtime_release(self):
        mgr, t = self.connected()
        t.routes.insert(0, ["DELETE", "/api/kernels/k1", explode])
        with self.assertLogs("m2g.colab", level="WARNING"):
            mgr.disconnect()
        self.assertEqual((mgr.kernel_cleanup, mgr.state), ("failed", cr.NOT_CONNECTED))
        self.assertEqual(t.count("DELETE", "/v1beta/runtimes/m2g-test"), 1)

    def test_a_failed_close_and_a_failed_release_both_leave_the_manual_instructions(self):
        mgr, t = self.connected()
        t.routes.insert(0, ["DELETE", "/api/kernels/k1", (500, {})])
        t.routes.insert(0, ["DELETE", "/v1beta/runtimes/m2g-test", (500, {})])
        with self.assertLogs("m2g.colab", level="WARNING"):
            mgr.disconnect()
        self.assertEqual(mgr.state, cr.NOT_CONNECTED)
        self.assertIn("colab.research.google.com", mgr.message)
        self.assertNotIn("proxytok", mgr.message)

    def test_repeated_disconnects_send_nothing_more(self):
        mgr, t = self.connected()
        mgr.disconnect()
        sent = len(t.calls)
        mgr.disconnect()
        mgr.disconnect()
        self.assertEqual(len(t.calls), sent)
        self.assertIsNone(mgr.kernel_cleanup)
        self.assertEqual(len(deletes(t)), 2)

    def test_disconnect_never_deletes_anything_but_that_kernel_and_that_runtime(self):
        mgr, t = self.connected()
        mgr.disconnect()
        self.assertEqual(sorted(c[0] for c in t.calls if c[0] == "DELETE"), ["DELETE", "DELETE"])
        for url in deletes(t):
            self.assertTrue(url.endswith("/api/kernels/k1") or url.endswith("/v1beta/runtimes/m2g-test"), url)

    def test_disconnect_without_a_connection_makes_no_request(self):
        mgr, t, _ = tcr.make_manager()
        mgr.disconnect()
        self.assertEqual(t.calls, [])
        self.assertIsNone(mgr.kernel_cleanup)

    def test_a_failed_connect_closes_the_kernel_and_releases_the_runtime(self):
        mgr, t, _ = tcr.make_manager(responder=tcr.hw_responder(gpu=None))
        mgr.connect("T4 GPU")  # a GPU was requested but none was assigned
        self.assertEqual(mgr.state, cr.FAILED)
        self.assertEqual(mgr.kernel_cleanup, "closed")
        self.assert_kernel_then_runtime(t)
        self.assertIsNone(mgr.jupyter)

    def test_an_expected_runtime_error_closes_the_kernel_and_releases_the_runtime(self):
        mgr, t = self.connected()
        t.routes.insert(0, ["GET", "/v1beta/runtimes/m2g-test", (404, {"error": {"status": "NOT_FOUND"}})])
        mgr.test()
        self.assertEqual((mgr.state, mgr.error_kind), (cr.FAILED, "not_found"))
        self.assertEqual(mgr.kernel_cleanup, "closed")
        self.assert_kernel_then_runtime(t)

    def test_an_unexpected_exception_closes_the_kernel_and_releases_the_runtime(self):
        mgr, t = self.connected()
        with mock.patch.object(mgr.jupyter, "execute", side_effect=RuntimeError("boom")), \
                mock.patch.object(cr, "validate_app_code", lambda code, settings: None), \
                mock.patch.object(cr, "validate_requirements_text", lambda text: ["gradio>=5.0"]), \
                self.assertLogs("m2g.colab", level="ERROR"):
            out = mgr.run_predict(tcr.APP, "gradio>=5.0", '["x"]')
        self.assertTrue(out.startswith("Error:"), out)
        self.assertEqual((mgr.state, mgr.error_kind), (cr.FAILED, "other"))
        self.assertNotIn("boom", mgr.message, "only the exception type is shown")
        self.assertEqual(mgr.kernel_cleanup, "closed")
        self.assert_kernel_then_runtime(t)

    def test_a_runtime_that_is_already_gone_is_not_contacted_again(self):
        mgr, t = self.connected()
        before = len(t.calls)
        with mock.patch.object(cr, "detect_hardware", side_effect=cr.ColabError("disconnected", "gone")):
            mgr.test()
        self.assertEqual(mgr.state, cr.FAILED)
        self.assertEqual(deletes(t), [], "no kernel or runtime request for a runtime that already disconnected")
        self.assertIsNone(mgr.kernel_cleanup)
        self.assertGreater(len(t.calls), before)  # only the token refresh of test() itself

    def test_a_new_connection_after_a_cleanup_starts_a_fresh_kernel(self):
        mgr, t = self.connected()
        mgr.disconnect()
        mgr.connect("CPU")
        self.assertEqual(mgr.state, cr.CONNECTED, mgr.message)
        self.assertEqual(t.count("POST", "/api/kernels"), 2)
        mgr.disconnect()
        self.assertEqual(len(deletes(t)), 4)


if __name__ == "__main__":
    unittest.main()
