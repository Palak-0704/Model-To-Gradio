import unittest

from m2g import security as sec
from m2g.config import Settings

PUBLIC = lambda host, port: ["93.184.216.34"]


class TestParseUrl(unittest.TestCase):
    def test_valid_hf(self):
        r = sec.parse_source_url("https://huggingface.co/distilbert/distilbert-base-uncased-finetuned-sst-2-english")
        self.assertEqual((r.kind, r.repo_id), ("huggingface", "distilbert/distilbert-base-uncased-finetuned-sst-2-english"))

    def test_hf_tree_revision_and_legacy(self):
        r = sec.parse_source_url("https://huggingface.co/openai/whisper-tiny/tree/main")
        self.assertEqual((r.repo_id, r.revision), ("openai/whisper-tiny", "main"))
        self.assertEqual(sec.parse_source_url("https://huggingface.co/gpt2").repo_id, "gpt2")

    def test_valid_github(self):
        r = sec.parse_source_url("https://github.com/huggingface/transformers.git")
        self.assertEqual((r.kind, r.repo_id), ("github", "huggingface/transformers"))
        r = sec.parse_source_url("https://github.com/o/r/tree/dev")
        self.assertEqual(r.revision, "dev")

    def test_rejects(self):
        bad = [
            "", "   ", "http://huggingface.co/gpt2", "ftp://huggingface.co/gpt2",
            "https://huggingface.co@evil.com/gpt2", "https://user:pw@huggingface.co/gpt2",
            "https://huggingface.co.evil.com/a/b", "https://evilhuggingface.co/a/b",
            "https://localhost/a/b", "https://127.0.0.1/a/b", "https://169.254.169.254/latest/meta-data",
            "https://[::1]/a/b", "https://huggingface.co:8443/a/b", "https://huggingface.co/datasets/a/b",
            "https://huggingface.co/spaces/a/b", "https://huggingface.co/a/b/../../c",
            "https://huggingface.co/a/%2e%2e/b", "https://github.com/a", "https://github.com/settings/profile",
            "https://github.com/a/b/../c", "https://huggingface.co/a/b c", "https://huggingface.co/a\\b",
            "javascript:alert(1)", "file:///etc/passwd", "https://gіthub.com/a/b",  # cyrillic i
            "https://github.com/" + "a" * 400, "https://huggingface.co/a/b/unknownpage",
        ]
        for u in bad:
            with self.assertRaises(sec.SecurityError, msg=u):
                sec.parse_source_url(u)

    def test_non_string(self):
        with self.assertRaises(sec.SecurityError):
            sec.parse_source_url(None)  # type: ignore


class TestIpAndOutbound(unittest.TestCase):
    def test_is_public_ip(self):
        for ip in ["127.0.0.1", "10.0.0.5", "192.168.1.1", "172.16.0.1", "169.254.169.254", "::1", "fe80::1",
                   "0.0.0.0", "100.64.0.1", "::ffff:127.0.0.1", "fc00::1", "224.0.0.1", "2002:7f00:0001::1", "not-an-ip"]:
            self.assertFalse(sec.is_public_ip(ip), ip)
        self.assertTrue(sec.is_public_ip("93.184.216.34"))

    def test_validate_outbound(self):
        parts, ips = sec.validate_outbound_url("https://huggingface.co/api/models/x", PUBLIC)
        self.assertEqual(parts.hostname, "huggingface.co")
        for u in ["http://huggingface.co/x", "https://evil.com/x", "https://huggingface.co:444/x",
                  "https://1.2.3.4/x", "https://user@huggingface.co/x", "https://huggingface.co.evil.com/x"]:
            with self.assertRaises(sec.SecurityError, msg=u):
                sec.validate_outbound_url(u, PUBLIC)

    def test_dns_rebinding_to_private_blocked(self):
        with self.assertRaises(sec.SecurityError):
            sec.validate_outbound_url("https://huggingface.co/x", lambda h, p: ["10.0.0.1"])
        with self.assertRaises(sec.SecurityError):  # mixed answer set
            sec.validate_outbound_url("https://huggingface.co/x", lambda h, p: ["93.184.216.34", "127.0.0.1"])

    def test_cdn_suffix_allowed_but_lookalike_not(self):
        sec.validate_outbound_url("https://cdn-lfs.hf.co/x", PUBLIC)
        with self.assertRaises(sec.SecurityError):
            sec.validate_outbound_url("https://evilhf.co/x", PUBLIC)


class FakeTransport:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def __call__(self, parts, ips, headers, timeout, max_bytes):
        url = f"https://{parts.hostname}{parts.path}" + (f"?{parts.query}" if parts.query else "")
        self.calls.append((url, dict(headers)))
        r = self.routes[url]
        if isinstance(r, Exception):
            raise r
        return r


class TestSafeFetcher(unittest.TestCase):
    def mk(self, routes, **s):
        t = FakeTransport(routes)
        return sec.SafeFetcher(Settings(**s), resolver=PUBLIC, transport=t), t

    def test_ok_and_json(self):
        f, t = self.mk({"https://huggingface.co/api/models/a/b": (200, {}, b'{"id": "a/b"}')})
        self.assertEqual(f.get_json("https://huggingface.co/api/models/a/b")["id"], "a/b")

    def test_redirect_to_evil_host_blocked(self):
        f, t = self.mk({"https://huggingface.co/a": (302, {"location": "https://evil.com/steal"}, b"")})
        with self.assertRaises(sec.SecurityError):
            f.fetch("https://huggingface.co/a")
        self.assertEqual(len(t.calls), 1)  # never contacted evil.com

    def test_redirect_to_internal_ip_and_http_blocked(self):
        for loc in ["https://127.0.0.1/x", "http://huggingface.co/x", "https://169.254.169.254/x", "https://huggingface.co@evil.com/x"]:
            f, t = self.mk({"https://huggingface.co/a": (301, {"location": loc}, b"")})
            with self.assertRaises(sec.SecurityError, msg=loc):
                f.fetch("https://huggingface.co/a")

    def test_redirect_to_cdn_ok_without_token(self):
        f, t = self.mk({"https://huggingface.co/a": (302, {"location": "https://cdn-lfs.hf.co/blob"}, b""),
                        "https://cdn-lfs.hf.co/blob": (200, {}, b"data")}, hf_token="hf_" + "A" * 30)
        self.assertEqual(f.fetch("https://huggingface.co/a").body, b"data")
        self.assertIn("Authorization", t.calls[0][1])
        self.assertNotIn("Authorization", t.calls[1][1])  # credential not forwarded across hosts

    def test_relative_redirect_and_loop_limit(self):
        f, t = self.mk({"https://huggingface.co/a": (302, {"location": "/a"}, b"")}, max_redirects=2)
        with self.assertRaises(sec.SecurityError):
            f.fetch("https://huggingface.co/a")

    def test_tokens_only_to_own_hosts(self):
        f, t = self.mk({"https://api.github.com/r": (200, {}, b"{}"), "https://huggingface.co/r": (200, {}, b"{}"),
                        "https://raw.githubusercontent.com/r": (200, {}, b"x")},
                       hf_token="hf_" + "B" * 30, github_token="ghp_" + "C" * 30)
        f.fetch("https://api.github.com/r"); f.fetch("https://huggingface.co/r"); f.fetch("https://raw.githubusercontent.com/r")
        auth = {u: h.get("Authorization") for u, h in t.calls}
        self.assertEqual(auth["https://api.github.com/r"], "Bearer ghp_" + "C" * 30)
        self.assertEqual(auth["https://huggingface.co/r"], "Bearer hf_" + "B" * 30)
        self.assertIsNone(auth["https://raw.githubusercontent.com/r"])

    def test_caller_cannot_inject_auth_header(self):
        f, t = self.mk({"https://huggingface.co/r": (200, {}, b"{}")})
        f.fetch("https://huggingface.co/r", extra_headers={"Authorization": "Bearer evil", "Cookie": "a=b", "Accept": "text/plain"})
        h = t.calls[0][1]
        self.assertNotIn("Authorization", h); self.assertNotIn("Cookie", h); self.assertEqual(h["Accept"], "text/plain")

    def test_http_error_and_secret_redaction(self):
        f, t = self.mk({"https://huggingface.co/r": (403, {}, b"")})
        with self.assertRaises(sec.HTTPStatusError) as cm:
            f.fetch("https://huggingface.co/r")
        self.assertEqual(cm.exception.status, 403)
        self.assertNotIn("hf_", sec.redact_secrets("token hf_" + "Z" * 30))
        self.assertNotIn("ghp_", sec.redact_secrets("ghp_" + "Z" * 30))
        self.assertNotIn("abcdefghijklmnop", sec.redact_secrets("Authorization: Bearer abcdefghijklmnop"))

    def test_circuit_breaker_stops_repeated_failures(self):
        f, t = self.mk({"https://huggingface.co/a": sec.NetworkError("dns fail"), "https://huggingface.co/b": (200, {}, b"x")})
        with self.assertRaises(sec.NetworkError):
            f.fetch("https://huggingface.co/a")
        with self.assertRaises(sec.NetworkError):
            f.fetch("https://huggingface.co/b")
        self.assertEqual(len(t.calls), 1)  # second request never hit the transport

    def test_invalid_json(self):
        f, t = self.mk({"https://huggingface.co/j": (200, {}, b"<html>")})
        with self.assertRaises(sec.FetchError):
            f.get_json("https://huggingface.co/j")


class TestIdentifiers(unittest.TestCase):
    def test_pip_requirement(self):
        for ok in ["torch", "transformers>=4.40", "gradio[oauth]>=5.0,<6", "pillow"]:
            sec.validate_pip_requirement(ok)
        for bad in ["torch; rm -rf /", "git+https://x/y", "-r evil.txt", "a b", "x $(id)", "http://x/y.whl", "--index-url=http://x"]:
            with self.assertRaises(sec.SecurityError, msg=bad):
                sec.validate_pip_requirement(bad)

    def test_repo_id_slug_within(self):
        sec.validate_repo_id("a/b-c.d")
        for bad in ["a/b/c", "a b", "a;b", "../x", "a/'", ""]:
            with self.assertRaises(sec.SecurityError):
                sec.validate_repo_id(bad)
        self.assertEqual(sec.safe_slug("../../etc/passwd"), "etc_passwd")
        import tempfile, pathlib
        with tempfile.TemporaryDirectory() as d:
            sec.ensure_within(pathlib.Path(d), pathlib.Path(d) / "x")
            with self.assertRaises(sec.SecurityError):
                sec.ensure_within(pathlib.Path(d), pathlib.Path(d) / ".." / "x")

    def test_assert_no_secrets(self):
        with self.assertRaises(sec.SecurityError):
            sec.assert_no_secrets("x = 'hf_" + "A" * 30 + "'")
        with self.assertRaises(sec.SecurityError):
            sec.assert_no_secrets("custom-secret-value-123", Settings(hf_token="custom-secret-value-123"))
        sec.assert_no_secrets("harmless text")


if __name__ == "__main__":
    unittest.main()
