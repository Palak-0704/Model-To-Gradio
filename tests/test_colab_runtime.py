"""Offline tests for m2g.colab_runtime. No network, no browser, no real Google account is used.

These prove the logic (state machine, error mapping, request shapes, safety checks). They do NOT prove that Google's
live API accepts the requests - that needs an allowlisted project (see docs/COLAB_SETUP.md).
"""
import ast
import base64
import hashlib
import json
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

from m2g import colab_runtime as cr
from m2g.colab_runtime import (CONNECTED, CONNECTING, FAILED, NOT_CONNECTED, NOT_ENABLED, ColabApi, ColabConfig,
                               ColabError, ColabManager, GoogleAuth, JupyterClient, RuntimeSpec, TokenStore)

RT_URL = "https://abc.colab.googleusercontent.com/proxy"


class FakeTransport:
    """routes: list of [method, url_substring, response]; response may be a tuple, a list of tuples (consumed in order,
    last one repeats) or a callable(url, json_body, form, headers)."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def request(self, method, url, headers=None, json_body=None, form=None, timeout=30):
        self.calls.append((method, url, json_body, form, headers))
        for route in self.routes:
            m, sub, resp = route
            if m == method and sub in url:
                if callable(resp):
                    return resp(url, json_body, form, headers)
                if isinstance(resp, list):
                    return resp.pop(0) if len(resp) > 1 else resp[0]
                return resp
        raise AssertionError(f"unexpected request {method} {url}")

    def count(self, method, sub):
        return sum(1 for c in self.calls if c[0] == method and sub in c[1])


class FakeAuth:
    def __init__(self, signed_in=True):
        self._in = signed_in
        self.logins = 0

    def signed_in(self):
        return self._in

    def login(self):
        self.logins += 1
        self._in = True
        return "me@example.com"

    def email(self):
        return "me@example.com" if self._in else None

    def access_token(self):
        return "tok"


class FakeWS:
    def __init__(self, responder):
        self.responder, self.queue, self.closed = responder, [], False

    def send(self, raw):
        m = json.loads(raw)
        mid, code = m["header"]["msg_id"], m["content"]["code"]
        hdr = {"msg_id": mid}
        for kind, content in self.responder(code):
            self.queue.append(json.dumps({"msg_type": kind, "parent_header": hdr, "content": content}))
        self.queue.insert(0, json.dumps({"msg_type": "stream", "parent_header": {"msg_id": "other"}, "content": {"text": "noise"}}))
        self.queue.append(json.dumps({"msg_type": "status", "parent_header": hdr, "content": {"execution_state": "idle"}}))

    def recv(self, timeout=None):
        if not self.queue:
            raise TimeoutError()
        return self.queue.pop(0)

    def close(self):
        self.closed = True


def stream(text):
    return ("stream", {"name": "stdout", "text": text})


def hw_responder(gpu="Tesla T4", extra=None):
    def responder(code):
        if "M2G_HW" in code:
            return [stream("M2G_HW:" + json.dumps({"python": "3.12.1", "gpu": gpu, "gpu_mem_mib": 15360 if gpu else None,
                                                    "ram_gib": 12.7}) + "\n")]
        if extra:
            r = extra(code)
            if r is not None:
                return r
        return [stream("ok\n")]
    return responder


OK_CFG = ColabConfig(client_id="id.apps.googleusercontent.com", api_approved=True)
SPECS = {"runtimeSpecs": [
    {"key": {"variant": "VARIANT_CPU", "accelerator": "NONE", "shape": "SHAPE_STANDARD"}, "eligible": True},
    {"key": {"variant": "VARIANT_GPU", "accelerator": "T4", "shape": "SHAPE_STANDARD"}, "eligible": True},
    {"key": {"variant": "VARIANT_GPU", "accelerator": "A100", "shape": "SHAPE_STANDARD"}, "eligible": False}]}


def make_transport(create_resp=None, runtime_get=None):
    runtime = {"name": "runtimes/m2g-test", "connectionInfo": {"url": RT_URL, "token": "proxytok", "expireTime": "2030-01-01T00:00:00Z"}}
    return FakeTransport([
        ["GET", "/v1beta/runtimespecs", (200, SPECS)],
        ["POST", "/v1beta/runtimes", create_resp or (200, {"name": "operations/op1", "done": False})],
        ["GET", "/v1/operations/op1", (200, {"name": "operations/op1", "done": True,
                                             "response": {"name": "runtimes/m2g-test"}})],
        ["GET", "/v1beta/runtimes/m2g-test", runtime_get or (200, runtime)],
        ["DELETE", "/v1beta/runtimes/m2g-test", (200, {})],
        ["POST", "/api/kernels", (200, {"id": "k1"})],
        ["DELETE", "/api/kernels/k1", (204, {})],  # Phase 4: closing the kernel this client started
    ])


def make_manager(transport=None, responder=None, cfg=OK_CFG, auth=None):
    t = transport or make_transport()
    ws_holder = []

    def ws_connect(url, headers):
        assert url.startswith("wss://"), url
        assert headers == {cr.PROXY_HEADER: "proxytok"} or headers.get(cr.PROXY_HEADER)
        ws = FakeWS(responder or hw_responder())
        ws_holder.append(ws)
        return ws

    api = ColabApi(auth or FakeAuth(), t, sleep=lambda s: None, timeout=5)
    mgr = ColabManager(cfg=cfg, transport=t, ws_connect=ws_connect, auth=auth or FakeAuth(), api=api)
    return mgr, t, ws_holder


class TestNotEnabled(unittest.TestCase):
    def test_not_approved_means_not_enabled_and_no_network(self):
        t = FakeTransport([])
        mgr = ColabManager(cfg=ColabConfig(client_id="x", api_approved=False), transport=t, auth=FakeAuth())
        self.assertEqual(mgr.state, NOT_ENABLED)
        mgr.connect("T4 GPU")
        mgr.test()
        mgr.disconnect()
        self.assertEqual(mgr.refresh_hardware(), [])
        self.assertEqual(mgr.state, NOT_ENABLED)
        self.assertEqual(t.calls, [])
        md = mgr.status_markdown()
        self.assertIn("Google Colab API access not enabled", md)
        self.assertIn(cr.ACCESS_FORM_URL, md)

    def test_approved_but_no_client_id_is_still_not_enabled(self):
        mgr = ColabManager(cfg=ColabConfig(client_id=None, api_approved=True), transport=FakeTransport([]), auth=FakeAuth())
        self.assertEqual(mgr.state, NOT_ENABLED)
        self.assertIn("M2G_COLAB_CLIENT_ID", mgr.message)

    def test_from_env_defaults_to_not_approved(self):
        import os
        old, orig_loader = dict(os.environ), cr.load_dotenv_file
        try:
            cr.load_dotenv_file = lambda *a, **k: None  # ignore a real .env on this machine
            for k in ("M2G_COLAB_API_APPROVED", "M2G_COLAB_CLIENT_ID", "M2G_COLAB_CLIENT_SECRET"):
                os.environ.pop(k, None)
            cfg = ColabConfig.from_env()
            self.assertFalse(cfg.api_approved)
            self.assertIsNone(cfg.client_id)
        finally:
            cr.load_dotenv_file = orig_loader
            os.environ.clear()
            os.environ.update(old)

    def test_secret_not_in_repr(self):
        self.assertNotIn("SHH", repr(ColabConfig(client_secret="SHH-secret")))


class TestSpecs(unittest.TestCase):
    def test_labels_and_eligibility_come_from_api(self):
        api = ColabApi(FakeAuth(), make_transport())
        specs = api.list_specs()
        self.assertEqual([s.label for s in specs], ["CPU", "T4 GPU", "A100 GPU"])
        self.assertEqual([s.eligible for s in specs], [True, True, False])

    def test_label_variants(self):
        self.assertEqual(RuntimeSpec("VARIANT_GPU", "L4", "SHAPE_HIGHMEM", True).label, "L4 GPU (high-RAM)")
        self.assertEqual(RuntimeSpec("VARIANT_TPU", "v6e-1", "SHAPE_STANDARD", True).label, "v6e-1 TPU")

    def test_refresh_hardware_lists_only_eligible(self):
        mgr, _, _ = make_manager()
        self.assertEqual(mgr.refresh_hardware(), ["CPU", "T4 GPU"])
        self.assertIn("A100 GPU", mgr.status_markdown())  # reported as not available


class TestConnectFlow(unittest.TestCase):
    def test_success_creates_polls_detects_hardware(self):
        mgr, t, ws = make_manager()
        mgr.connect("T4 GPU")
        self.assertEqual(mgr.state, CONNECTED, mgr.message)
        self.assertEqual(mgr.hardware["gpu"], "Tesla T4")
        body = [c for c in t.calls if c[0] == "POST" and c[1].split("?")[0].endswith("/v1beta/runtimes")][0][2]
        self.assertEqual(body, {"runtimeSpec": {"variant": "VARIANT_GPU", "accelerator": "T4", "shape": "SHAPE_STANDARD"}})
        self.assertEqual(t.count("GET", "/v1/operations/op1"), 1)
        kernel_call = [c for c in t.calls if c[1].endswith("/api/kernels")][0]
        self.assertEqual(kernel_call[4][cr.PROXY_HEADER], "proxytok")
        self.assertTrue(ws[0].closed)
        self.assertIn("Tesla T4", mgr.status_markdown())
        self.assertIn("me@example.com", mgr.status_markdown())

    def test_not_signed_in_triggers_login(self):
        auth = FakeAuth(signed_in=False)
        mgr, _, _ = make_manager(auth=auth)
        mgr.connect("CPU")
        self.assertEqual(auth.logins, 1)
        self.assertEqual(mgr.state, CONNECTED, mgr.message)

    def test_ineligible_gpu_fails_without_creating_runtime(self):
        mgr, t, _ = make_manager()
        mgr.connect("A100 GPU")
        self.assertEqual(mgr.state, FAILED)
        self.assertEqual(mgr.error_kind, "unavailable")
        self.assertEqual(t.count("POST", "/v1beta/runtimes"), 0)

    def test_unknown_preference_lists_offered(self):
        mgr, _, _ = make_manager()
        mgr.connect("H100 GPU")
        self.assertEqual(mgr.state, FAILED)
        self.assertIn("CPU", mgr.message)

    def test_gpu_requested_but_none_detected_releases_runtime(self):
        mgr, t, _ = make_manager(responder=hw_responder(gpu=None))
        mgr.connect("T4 GPU")
        self.assertEqual(mgr.state, FAILED)
        self.assertEqual(t.count("DELETE", "/v1beta/runtimes/m2g-test"), 1)
        self.assertIsNone(mgr.runtime)

    def test_operation_failed_precondition_surfaces_reason(self):
        err = {"name": "operations/op1", "done": True, "error": {"code": 9, "message": "no capacity", "details": [
            {"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": "SOME_REASON"}]}}
        mgr, _, _ = make_manager(transport=make_transport(create_resp=(200, err)))
        mgr.connect("T4 GPU")
        self.assertEqual(mgr.state, FAILED)
        self.assertEqual(mgr.error_kind, "unavailable")
        self.assertIn("SOME_REASON", mgr.message)

    def test_expired_auth_maps_to_clear_message(self):
        t = make_transport()
        t.routes.insert(0, ["GET", "/v1beta/runtimespecs", (401, {"error": {"status": "UNAUTHENTICATED"}})])
        mgr, _, _ = make_manager(transport=t)
        mgr.connect("CPU")
        self.assertEqual(mgr.error_kind, "auth_expired")
        self.assertIn("sign", mgr.message.lower())

    def test_runtime_never_ready_times_out(self):
        t = make_transport()
        t.routes.insert(2, ["GET", "/v1/operations/op1", (200, {"name": "operations/op1", "done": False})])
        clock = iter(range(0, 10_000, 3))
        api = ColabApi(FakeAuth(), t, sleep=lambda s: None, clock=lambda: next(clock), timeout=10)
        with self.assertRaises(ColabError) as cm:
            api.create_runtime(RuntimeSpec("VARIANT_CPU", "NONE", "SHAPE_STANDARD", True))
        self.assertEqual(cm.exception.kind, "timeout")


class TestTestAndDisconnect(unittest.TestCase):
    def test_test_refreshes_token_and_hardware(self):
        mgr, t, _ = make_manager()
        mgr.connect("T4 GPU")
        gets_before = t.count("GET", "/v1beta/runtimes/m2g-test")
        mgr.test()
        self.assertEqual(mgr.state, CONNECTED)
        self.assertEqual(t.count("GET", "/v1beta/runtimes/m2g-test"), gets_before + 1)
        self.assertIn("Test passed", mgr.message)

    def test_lost_runtime_is_reported_not_hidden(self):
        mgr, t, _ = make_manager()
        mgr.connect("CPU")
        t.routes.insert(0, ["GET", "/v1beta/runtimes/m2g-test", (404, {"error": {"status": "NOT_FOUND"}})])
        mgr.test()
        self.assertEqual(mgr.state, FAILED)
        self.assertEqual(mgr.error_kind, "not_found")
        self.assertIsNone(mgr.runtime)

    def test_disconnect_deletes_runtime(self):
        mgr, t, _ = make_manager()
        mgr.connect("CPU")
        mgr.disconnect()
        self.assertEqual(mgr.state, NOT_CONNECTED)
        self.assertEqual(t.count("DELETE", "/v1beta/runtimes/m2g-test"), 1)
        self.assertEqual(mgr.run_predict("x", "", "[]"), "Error: not connected to a Colab runtime.")

    def test_disconnect_reports_release_failure(self):
        mgr, t, _ = make_manager()
        mgr.connect("CPU")
        t.routes.insert(0, ["DELETE", "/v1beta/runtimes/m2g-test", (500, {})])
        mgr.disconnect()
        self.assertEqual(mgr.state, NOT_CONNECTED)
        self.assertIn("colab.research.google.com", mgr.message)


class TestErrorMapping(unittest.TestCase):
    def test_mappings(self):
        self.assertEqual(cr.map_http_error(401, {}).kind, "auth_expired")
        self.assertEqual(cr.map_http_error(403, {"error": {"status": "PERMISSION_DENIED", "details": [{"reason": "SERVICE_DISABLED"}]}}).kind, "api_access")
        self.assertEqual(cr.map_http_error(403, {"error": {"status": "PERMISSION_DENIED", "message": "Request had insufficient authentication scopes"}}).kind, "permission")
        self.assertEqual(cr.map_http_error(403, {}).kind, "permission")
        self.assertEqual(cr.map_http_error(429, {}).kind, "quota")
        self.assertEqual(cr.map_http_error(404, {}).kind, "not_found")
        self.assertEqual(cr.map_http_error(500, "junk").kind, "other")
        self.assertEqual(cr.map_operation_error({"code": 8}).kind, "quota")


class TestSafety(unittest.TestCase):
    def test_non_google_hosts_refused(self):
        for bad in ("https://evil.example.com/x", "http://abc.googleusercontent.com/x", "https://user:pw@a.googleapis.com/x",
                    "https://googleapis.com.evil.io/x"):
            with self.assertRaises(ColabError, msg=bad):
                cr.check_url(bad)
        cr.check_url("https://colaboratory.googleapis.com/v1beta/runtimes")
        with self.assertRaises(ColabError):
            JupyterClient("https://evil.example.com/", "t", FakeTransport([]))

    def test_runtime_name_validated(self):
        api = ColabApi(FakeAuth(), FakeTransport([]))
        with self.assertRaises(ColabError):
            api.get_runtime("runtimes/../../v1beta/x")

    def test_no_tokens_in_repr(self):
        self.assertNotIn("proxytok", repr(cr.Runtime(name="runtimes/a", url=RT_URL, token="proxytok")))


class TestAuth(unittest.TestCase):
    def test_pkce_challenge_matches_verifier(self):
        v, c = cr.pkce_pair()
        self.assertEqual(c, base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).rstrip(b"=").decode())
        self.assertGreaterEqual(len(v), 43)

    def _auth(self, d, routes, now=1000.0):
        store = TokenStore(Path(d) / "tok.json")
        return GoogleAuth(ColabConfig(client_id="cid", client_secret="sec", api_approved=True), FakeTransport(routes),
                          store=store, clock=lambda: now, open_browser=lambda url: None), store  # never open a real browser

    def test_refresh_when_expired(self):
        with tempfile.TemporaryDirectory() as d:
            auth, store = self._auth(d, [["POST", "oauth2.googleapis.com/token", (200, {"access_token": "new", "expires_in": 3600})]])
            store.save({"access_token": "old", "refresh_token": "r", "expires_at": 1010.0, "email": "a@b.c"})
            self.assertEqual(auth.access_token(), "new")
            self.assertEqual(store.load()["refresh_token"], "r")
            self.assertEqual(auth.email(), "a@b.c")

    def test_valid_token_reused_without_network(self):
        with tempfile.TemporaryDirectory() as d:
            auth, store = self._auth(d, [])
            store.save({"access_token": "ok", "refresh_token": "r", "expires_at": 5000.0})
            self.assertEqual(auth.access_token(), "ok")

    def test_revoked_refresh_token_is_auth_expired(self):
        with tempfile.TemporaryDirectory() as d:
            auth, store = self._auth(d, [["POST", "oauth2.googleapis.com/token", (400, {"error": "invalid_grant"})]])
            store.save({"access_token": "old", "refresh_token": "SECRETREFRESH", "expires_at": 0})
            with self.assertRaises(ColabError) as cm:
                auth.access_token()
            self.assertEqual(cm.exception.kind, "auth_expired")
            self.assertNotIn("SECRETREFRESH", cm.exception.message)

    def test_not_signed_in(self):
        with tempfile.TemporaryDirectory() as d:
            auth, _ = self._auth(d, [])
            self.assertFalse(auth.signed_in())
            with self.assertRaises(ColabError):
                auth.access_token()

    def test_callback_server_receives_code(self):
        srv = cr._CallbackServer()
        try:
            def hit():
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # ignore system proxy for loopback
                opener.open(f"http://127.0.0.1:{srv.port}/?code=abc&state=xyz", timeout=5).read()
            th = threading.Thread(target=hit)
            th.start()
            result = srv.wait(5)
            th.join()
        finally:
            srv.close()
        self.assertEqual(result, {"code": "abc", "state": "xyz"})

    def test_login_rejects_state_mismatch_and_denial(self):
        with tempfile.TemporaryDirectory() as d:
            for result, kind in (({"code": "c", "state": "WRONG"}, "auth_failed"), ({"error": "access_denied"}, "permission"),
                                 (None, "auth_failed")):
                auth, _ = self._auth(d, [])
                orig = cr._CallbackServer.wait
                cr._CallbackServer.wait = lambda self, timeout, r=result: r
                try:
                    # login() logs the consent URL (with a random loopback port) at WARNING level; capture it instead of
                    # letting it reach the console, and prove it is only shown, never opened (open_browser is a stub).
                    with self.assertRaises(ColabError) as cm, self.assertLogs("m2g.colab", level="WARNING") as logs:
                        auth.login()
                finally:
                    cr._CallbackServer.wait = orig
                self.assertEqual(cm.exception.kind, kind)
                self.assertTrue(any("accounts.google.com/o/oauth2" in line and "redirect_uri=http%3A%2F%2F127.0.0.1%3A" in line
                                    for line in logs.output), logs.output)


class TestJupyter(unittest.TestCase):
    def _jc(self, responder):
        t = FakeTransport([["POST", "/api/kernels", (200, {"id": "k1"})]])
        return JupyterClient(RT_URL + "?x=1", "tok", t, lambda url, h: FakeWS(responder)), t

    def test_collects_output_ignores_foreign_messages(self):
        jc, t = self._jc(lambda code: [stream("hello\n"), ("execute_result", {"data": {"text/plain": "42"}})])
        r = jc.execute("1+1")
        self.assertTrue(r.ok)
        self.assertEqual((r.stdout, r.result), ("hello\n", "42"))
        self.assertIn("x=1", t.calls[0][1])  # query of the authenticated URL is preserved

    def test_kernel_error_reported(self):
        jc, _ = self._jc(lambda code: [("error", {"ename": "ValueError", "evalue": "bad"})])
        r = jc.execute("raise")
        self.assertFalse(r.ok)
        self.assertIn("ValueError: bad", r.error)

    def test_timeout(self):
        class Silent(FakeWS):
            def send(self, raw):
                pass
        t = FakeTransport([["POST", "/api/kernels", (200, {"id": "k"})]])
        jc = JupyterClient(RT_URL, "tok", t, lambda u, h: Silent(lambda c: []))
        with self.assertRaises(ColabError) as cm:
            jc.execute("x", timeout=0.2)
        self.assertEqual(cm.exception.kind, "timeout")

    def test_expired_proxy_token_and_missing_runtime(self):
        for status, kind in ((401, "auth_expired"), (404, "disconnected")):
            jc = JupyterClient(RT_URL, "t", FakeTransport([["POST", "/api/kernels", (status, {})]]), lambda u, h: None)
            with self.assertRaises(ColabError) as cm:
                jc.execute("x")
            self.assertEqual(cm.exception.kind, kind)

    def test_lost_connection_is_disconnected(self):
        class Broken(FakeWS):
            def recv(self, timeout=None):
                raise ConnectionResetError()
        t = FakeTransport([["POST", "/api/kernels", (200, {"id": "k"})]])
        jc = JupyterClient(RT_URL, "tok", t, lambda u, h: Broken(lambda c: []))
        with self.assertRaises(ColabError) as cm:
            jc.execute("x")
        self.assertEqual(cm.exception.kind, "disconnected")


APP = '''MODEL_ID = "x/y"
def get_pipe():
    return 1
def predict(text):
    return {"label": "POSITIVE", "text": text}
'''


class TestRunPredict(unittest.TestCase):
    def test_code_builders_are_valid_python_and_escape_input(self):
        nasty = "x = '''quote''' \\n \"q\" \u00e9"
        for code in (cr.build_write_app_code(nasty), cr.build_install_code(["gradio>=5.0", "torch"]),
                     cr.build_load_code("get_pipe", {"A": "b'c"}), cr.build_predict_code(["it's", {"k": 1}])):
            ast.parse(code)

    def test_requires_connection_and_valid_json(self):
        mgr, _, _ = make_manager()
        self.assertTrue(mgr.run_predict(APP, "gradio>=5.0", "[]").startswith("Error: not connected"))
        mgr.connect("CPU")
        self.assertIn("JSON", mgr.run_predict(APP, "gradio>=5.0", "not json"))
        self.assertIn("list", mgr.run_predict(APP, "gradio>=5.0", '{"a": 1}'))

    def test_refuses_unvalidated_code(self):
        mgr, _, _ = make_manager()
        mgr.connect("CPU")
        out = mgr.run_predict("import os\nos.system('x')\n", "gradio>=5.0", "[]")
        self.assertTrue(out.startswith("Error: refusing"), out)

    def test_deploys_once_then_predicts(self):
        seen = []

        def extra(code):
            seen.append(code)
            if "M2G_RESULT" in code:
                return [stream('M2G_RESULT:{"label": "POSITIVE"}\n')]
            if "M2G_LOADED" in code:
                return [stream("M2G_LOADED\n")]
            return None
        mgr, _, _ = make_manager(responder=hw_responder(extra=extra))
        mgr.connect("CPU")
        from m2g import colab_runtime
        orig = colab_runtime.validate_app_code, colab_runtime.validate_requirements_text
        colab_runtime.validate_app_code = lambda code, settings: None  # the sample app is not a full generated app
        colab_runtime.validate_requirements_text = lambda text: ["gradio>=5.0"]
        try:
            out1 = mgr.run_predict(APP, "gradio>=5.0", '["great"]')
            out2 = mgr.run_predict(APP, "gradio>=5.0", '["fine"]')
        finally:
            colab_runtime.validate_app_code, colab_runtime.validate_requirements_text = orig
        self.assertEqual(json.loads(out1), {"label": "POSITIVE"})
        self.assertEqual(out2, out1)
        self.assertEqual(sum("M2G_LOADED" in c for c in seen), 1)  # written/installed/loaded only once
        self.assertEqual(sum("M2G_RESULT" in c for c in seen), 2)


if __name__ == "__main__":
    unittest.main()
