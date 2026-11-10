"""Offline tests for the Colab launch workflow (m2g.colab_workflow) and the panel renderer (m2g.colab_panel).

A scripted FakeCli stands in for the `colab` command. The transfer step runs the REAL remote write script locally into a
temporary directory, so the checksum verification is exercised for real. No Google account, network or runtime is used.
"""
import contextlib
import io
import json
import os
import runpy
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from m2g import colab_workflow as cw
from m2g import gradio_gen as gg
from m2g.colab_cli import DOCUMENTED_GPUS, CliResult, GpuChoices
from m2g.colab_panel import ColabPanel
from m2g.config import Settings
from test_analyzer import analyze_preset
from test_preflight import run_runner_locally

NONCE = "t0"
P = "__M2G_t0__"
URL = "https://abc-123.gradio.live"


def ok(out=""):
    return CliResult([], 0, out, "", 0.0)


def transport_lost():
    """What the CLI actually printed when the T4 run died at the start-app step."""
    return CliResult([], 1, "", "Traceback (most recent call last):\n  File \"cli.py\", line 1, in <module>\n"
                               "RuntimeError: Connection was lost.\n", 1.0)


def server_503():
    """What `colab new` printed in the 15:10 run: the assignment request was rejected upstream."""
    return CliResult([], 1, "", "Traceback (most recent call last):\n  File \"cli.py\", line 1, in <module>\n"
                               "googleapiclient.errors.HttpError: <HttpError 503 when requesting the assignment "
                               "endpoint returned \"Service Unavailable\">\n", 2.0)


def status_unknown():
    """`colab sessions` itself failed, so whether a runtime exists cannot be decided."""
    return CliResult(["sessions"], 1, "", "RuntimeError: Connection was lost.\n", 1.0)


def good_poll(url=URL):
    return (f"{P} ALIVE 1\n{P} IMPORTED\n{P} LOADED\n{P} SELFTEST_NONE\n{P} URL {url}\n{P} TAIL \"\"\n")


class FakeCli:
    auth = "oauth2"
    available = True
    command = ["colab"]

    def __init__(self, *, gpu_name="Tesla T4", check_state="ok", poll=None, start_failures=0, hw_result=None,
                 new_failures=0, new_error=None, new_creates_despite_failure=False, install_failures=0,
                 install_pip_fail=False, upload_failures=0, upload_corrupt=False, sessions_error=None,
                 gradio_version=None, probe_result=None):
        self.gpu_name, self.check_state = gpu_name, check_state
        self.poll = poll if poll is not None else good_poll()
        self.start_failures, self.hw_result = start_failures, hw_result
        self.new_failures, self.new_error = new_failures, new_error
        self.new_creates_despite_failure = new_creates_despite_failure
        self.install_failures, self.install_pip_fail = install_failures, install_pip_fail
        self.upload_failures, self.upload_corrupt = upload_failures, upload_corrupt
        self.sessions_error = sessions_error
        self.gradio_version, self.probe_result = gradio_version, probe_result
        self.calls, self.live, self.on_new, self.exec_log = [], [], None, []

    def clear_interrupt(self):
        pass

    def interrupt(self):
        pass

    def identify(self):
        return True, "Official Colab CLI detected."

    def check(self):
        return self.check_state, "test"

    def supported_gpus(self, refresh=False):
        return GpuChoices(("T4", "L4"), True, "test")

    def new_session(self, name, gpu=None):
        self.calls.append(("new", name, gpu))
        if self.new_failures:
            self.new_failures -= 1
            if self.new_creates_despite_failure:  # the runtime WAS allocated even though the CLI reported an error
                self.live = [name]
            return self.new_error or server_503()
        self.live = [name]
        if self.on_new:
            self.on_new()
        return ok()

    def sessions(self):
        if self.sessions_error is not None:
            return self.sessions_error
        return ok("\n".join(self.live))

    def stop(self, name):
        self.calls.append(("stop", name))
        self.live = []
        return ok()

    def exec_code(self, name, code, timeout=600):
        if "M2G_GRADIO_API" in code:  # the runtime preflight probe
            self.exec_log.append("preflight")
            if self.probe_result is not None:
                return self.probe_result
            if self.gradio_version:  # run the real probe against the synthetic Gradio of that version
                return ok(run_runner_locally(code, self.gradio_version))
            snap = {"version": "5.49.0", "classes": {}, "missing": []}  # permissive: nothing is flagged
            # the real runner always prints a library line too; preflight fails closed when an app that imports a model library
            # gets none, so this permissive fake reports versions only (tasks unreadable = not enforced)
            libs = {"versions": {"transformers": "4.57.0", "torch": "2.9.0"}, "missing": {}, "tasks": None, "aliases": [],
                    "errors": [], "bad_kwargs": {}}
            reply = {"rc": 0, "out": "M2G_LIB_API " + json.dumps(libs) + "\nM2G_GRADIO_API " + json.dumps(snap), "err": ""}
            return ok(f"{P} PROBE {json.dumps(reply)}\n")
        if "WRITTEN" in code:  # run the real transfer script locally; it prints the real SHA-256 sums
            self.exec_log.append("write")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                exec(compile(code, "remote_write", "exec"), {})
            out = buf.getvalue()
            if self.upload_failures:
                self.upload_failures -= 1
                return transport_lost()
            if self.upload_corrupt:  # the bytes landed, but the checksum the runtime reports is wrong
                out = out.replace('"app.py": "', '"app.py": "0', 1)
            return ok(out)
        if "STARTED" in code:
            self.exec_log.append("start")
            if self.start_failures:
                self.start_failures -= 1
                return transport_lost()
            return ok(f"{P} STARTED 123\n")
        if "ALIVE" in code:
            self.exec_log.append("poll")
            return ok(self.poll)
        if "PIP_OK" in code:
            self.exec_log.append("install")
            if self.install_failures:
                self.install_failures -= 1
                return transport_lost()
            if self.install_pip_fail:
                return ok("ERROR: Could not find a version that satisfies the requirement m2g-nope\n"
                          f"{P} PIP_FAIL 1\n")
            return ok(f"{P} PIP_OK\n")
        if " HW " in code:
            self.exec_log.append("hardware")
            if self.hw_result is not None:
                return self.hw_result
            hw = {"python": "3.12", "gpu": self.gpu_name, "gpu_mem_mib": 15360, "ram_gib": 12.7, "disk_free_gib": 70.0}
            return ok(f"{P} HW {json.dumps(hw)}\n")
        raise AssertionError("unexpected remote script")

    def names(self, kind):
        return [c for c in self.calls if c[0] == kind]


class LauncherCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.remote = Path(self.tmp.name) / "remote"
        a = analyze_preset("sst2")
        self.code, self.reqs = gg.generate_app(a), gg.generate_requirements(a)

    def launcher(self, cli, verify=None, **kw):
        return cw.ColabLauncher(cli, verify=verify or (lambda url, auth: (True, "HTTP 200 (test)")),
                                sleep=lambda s: None, poll_interval=0, url_retries=2, url_retry_delay=0,
                                remote_dir=str(self.remote), nonce=lambda: NONCE, **kw)

    def run_launch(self, cli, gpu="T4", **kw):
        launcher = self.launcher(cli, kw.pop("verify", None))
        snap = launcher.start(self.code, self.reqs, gpu, settings=kw.pop("settings", Settings()), **kw)
        return launcher, snap


class TestSuccessfulLaunch(LauncherCase):
    def test_gpu_launch_reports_real_url_only_after_every_check(self):
        cli = FakeCli()
        _, snap = self.run_launch(cli)
        self.assertEqual(snap.state, cw.RUNNING, snap.message)
        self.assertEqual(snap.share_url, URL)
        self.assertTrue(snap.model_loaded)
        self.assertEqual(snap.hardware["gpu"], "Tesla T4")
        self.assertEqual(cli.names("new")[0][2], "T4")
        self.assertEqual(cli.names("stop"), [])  # a healthy run is left running for the user
        self.assertTrue((self.remote / "app.py").is_file())
        loader_py, app_py = gg.split_app_code(self.code)  # the runtime gets the same two files as the local package and the notebook
        self.assertEqual((self.remote / "app.py").read_text(encoding="utf-8"), app_py)  # exact transfer
        self.assertEqual((self.remote / "loader.py").read_text(encoding="utf-8"), loader_py)

    def test_cpu_launch_requests_no_gpu(self):
        cli = FakeCli(gpu_name=None)
        _, snap = self.run_launch(cli, gpu=cw.CPU_LABEL)
        self.assertEqual(snap.state, cw.RUNNING, snap.message)
        self.assertIsNone(cli.names("new")[0][2])

    def test_link_password_is_transferred_but_never_logged(self):
        _, snap = self.run_launch(FakeCli(), auth=("alice", "correct-horse-9"))
        self.assertEqual(snap.state, cw.RUNNING, snap.message)
        self.assertTrue(snap.auth_protected)
        self.assertTrue((self.remote / "m2g_auth.json").is_file())
        self.assertNotIn("correct-horse-9", "\n".join(snap.logs))
        self.assertNotIn("correct-horse-9", snap.message)


class TestFailuresNeverShowAUrl(LauncherCase):
    def assert_failed(self, snap, kind):
        self.assertEqual(snap.state, cw.FAILED, snap.message)
        self.assertEqual(snap.error_kind, kind)
        self.assertIsNone(snap.share_url)

    def test_not_signed_in_creates_no_runtime(self):
        cli = FakeCli(check_state="needs_auth")
        _, snap = self.run_launch(cli)
        self.assert_failed(snap, "needs_auth")
        self.assertEqual(cli.names("new"), [])

    def test_unreachable_url_is_not_published_and_runtime_is_released(self):
        cli = FakeCli()
        _, snap = self.run_launch(cli, verify=lambda url, auth: (False, "HTTP 502"))
        self.assert_failed(snap, "url_unreachable")
        self.assertEqual(len(cli.names("stop")), 1)
        self.assertIsNone(snap.session)

    def test_requested_gpu_that_is_not_there_fails_and_releases(self):
        cli = FakeCli(gpu_name=None)
        _, snap = self.run_launch(cli, gpu="T4")
        self.assert_failed(snap, "no_gpu")
        self.assertEqual(len(cli.names("stop")), 1)

    def test_app_failure_on_the_runtime_is_diagnosed(self):
        poll = f"{P} ALIVE 1\n{P} FAIL {json.dumps('RuntimeError: CUDA out of memory')}\n{P} TAIL \"\"\n"
        cli = FakeCli(poll=poll)
        _, snap = self.run_launch(cli)
        self.assert_failed(snap, "app_failed")
        self.assertIn("memory", snap.message.lower())
        self.assertEqual(len(cli.names("stop")), 1)

    def test_tunnel_retry_progress_is_shown_but_never_a_url_before_one_exists(self):
        retry = {"attempt": 2, "of": 6, "reason": "login to server failed: connection refused", "wait": 10.0, "elapsed": 7}
        poll = (f"{P} ALIVE 1\n{P} IMPORTED\n{P} LOADED\n{P} SHARE_RETRY {json.dumps(retry)}\n{P} SELFTEST_NONE\n"
                f"{P} URL {URL}\n{P} TAIL \"\"\n")
        _, snap = self.run_launch(FakeCli(poll=poll))
        self.assertEqual(snap.state, cw.RUNNING, snap.message)
        self.assertTrue(any("public link attempt 2/6 failed" in line for line in snap.logs))
        self.assertEqual(snap.share_url, URL)

    def test_share_retry_marker_is_parsed_only_from_this_run(self):
        line = f"{P} SHARE_RETRY {json.dumps({'attempt': 1, 'of': 6, 'reason': 'x'})}"
        self.assertEqual(cw.parse_markers(line, P)["share_retry"]["attempt"], 1)
        self.assertIsNone(cw.parse_markers(line.replace(P, "__M2G_other__"), P)["share_retry"])
        self.assertIsNone(cw.parse_markers(f"{P} SHARE_RETRY not-json", P)["share_retry"])

    def test_a_tunnel_outage_is_reported_as_a_tunnel_problem_with_a_recovery_path_not_as_a_login_error(self):
        reason = ("RuntimeError: Gradio did not create a public share link after 6 attempt(s) in 95 s. login to server "
                  "failed: dial tcp 44.237.78.176:7000: connect: connection refused")
        poll = f"{P} ALIVE 1\n{P} LOADED\n{P} FAIL {json.dumps(reason)}\n{P} TAIL \"\"\n"
        cli = FakeCli(poll=poll)
        _, snap = self.run_launch(cli)
        self.assert_failed(snap, "app_failed")
        self.assertIn("connection refused", snap.message)
        self.assertIn("status.gradio.app", snap.message)
        self.assertIn("not a Google sign-in problem", snap.message)
        self.assertEqual(len(cli.names("new")), 1, "a tunnel failure must not create another runtime")
        self.assertEqual(len(cli.names("stop")), 1)
        self.assertIsNone(snap.session)

    def test_url_without_loaded_model_is_not_trusted(self):
        poll = f"{P} ALIVE 1\n{P} IMPORTED\n{P} URL {URL}\n{P} TAIL \"\"\n"
        _, snap = self.run_launch(FakeCli(poll=poll))
        self.assert_failed(snap, "app_failed")

    def test_cancel_releases_the_runtime(self):
        cli = FakeCli()
        launcher = self.launcher(cli)
        cli.on_new = launcher.cancel
        snap = launcher.start(self.code, self.reqs, "T4", settings=Settings())
        self.assertEqual(snap.state, cw.CANCELLED, snap.message)
        self.assertIsNone(snap.share_url)
        self.assertEqual(len(cli.names("stop")), 1)

    def test_weak_link_password_is_blocked_before_anything_happens(self):
        cli = FakeCli()
        _, snap = self.run_launch(cli, auth=("alice", "short"))
        self.assert_failed(snap, "blocked")
        self.assertEqual(cli.names("new"), [])

    def test_trust_remote_code_apps_are_never_sent(self):
        s = Settings(allow_remote_code=True)
        self.code = gg.generate_app(analyze_preset("remote", settings=s), s)
        cli = FakeCli()
        _, snap = self.run_launch(cli, settings=s)
        self.assert_failed(snap, "blocked")
        self.assertIn("trust_remote_code", snap.message)
        self.assertEqual(cli.names("new"), [])


class TestFailuresAreClassifiedHonestly(unittest.TestCase):
    def kind(self, stderr):
        return cw.classify_cli_failure(CliResult([], 1, "", stderr, 1.0))

    def test_a_dropped_kernel_is_not_reported_as_an_expired_google_sign_in(self):
        # Frame paths mention oauth/credentials; only the exception message is evidence.
        kind, msg = self.kind("Traceback (most recent call last):\n  File \"/opt/oauth/credential.py\", line 1\n"
                              "RuntimeError: Connection was lost.\n")
        self.assertEqual(kind, "kernel_connection")
        self.assertNotIn("sign in again", msg.lower())

    def test_a_bare_eoferror_is_a_transport_symptom(self):
        self.assertEqual(self.kind("\nEOFError\n")[0], "kernel_connection")

    def test_a_real_credential_failure_is_still_needs_auth(self):
        kind = self.kind("\ngoogle.auth.exceptions.RefreshError: invalid_grant: Token has been expired or revoked\n")
        self.assertEqual(kind[0], "needs_auth")


class TestTransportFailuresDoNotDestroyTheLaunch(LauncherCase):
    def test_one_dropped_connection_is_retried_instead_of_releasing_the_runtime(self):
        cli = FakeCli(start_failures=1)
        launcher, snap = self.run_launch(cli)
        self.assertEqual(snap.state, cw.RUNNING, snap.message)
        self.assertEqual(snap.retries, 1)
        self.assertEqual(cli.names("stop"), [])

    def test_a_persistent_dropped_connection_reports_the_connection_and_releases(self):
        cli = FakeCli(start_failures=99)
        _, snap = self.run_launch(cli, gpu=cw.CPU_LABEL)
        self.assertEqual(snap.state, cw.FAILED, snap.message)
        self.assertEqual(snap.error_kind, "kernel_connection")
        self.assertNotIn("sign in again", snap.message.lower())
        self.assertIn("start app", snap.message)
        self.assertIsNone(snap.session)
        self.assertEqual(len(cli.names("stop")), 1)

    def test_a_hardware_probe_failure_keeps_its_real_cause(self):
        cli = FakeCli(hw_result=transport_lost())
        _, snap = self.run_launch(cli)
        self.assertEqual(snap.state, cw.FAILED, snap.message)
        self.assertEqual(snap.error_kind, "kernel_connection")
        self.assertIn("hardware probe", snap.message)
        self.assertIsNone(snap.share_url)


class Resp:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TestUrlAndMarkers(unittest.TestCase):
    def test_only_gradio_share_hosts_are_trusted(self):
        for bad in ("https://evil.example.com/", "https://abc.gradio.live.evil.com", "http://abc.gradio.live",
                    "https://user@abc.gradio.live", ""):
            self.assertFalse(cw.verify_share_url(bad, opener=lambda r, timeout: Resp())[0], bad)
        self.assertTrue(cw.verify_share_url(URL, opener=lambda r, timeout: Resp())[0])

    def test_password_wall_counts_only_when_configured(self):
        bodies = []

        def opener(req, timeout):
            body = io.BytesIO(b"")  # a real response body, as urllib supplies; fp=None leaves an unmanaged HTTPError
            bodies.append(body)
            raise urllib.error.HTTPError(URL, 401, "unauthorized", {}, body)
        self.assertTrue(cw.verify_share_url(URL, opener=opener, allow_auth_wall=True)[0])
        self.assertFalse(cw.verify_share_url(URL, opener=opener)[0])
        # Regression for `ResourceWarning: Implicitly cleaning up <HTTPError 401: 'unauthorized'>`: the error response
        # must be closed by verify_share_url itself, not left for the garbage collector.
        self.assertEqual(len(bodies), 2)
        self.assertTrue(all(b.closed for b in bodies))

    def test_markers_from_another_run_cannot_fake_a_url(self):
        spoof = "__M2G_other__ URL https://evil.gradio.live\nprint hello https://evil.gradio.live"
        self.assertIsNone(cw.parse_markers(spoof, P)["url"])
        self.assertEqual(cw.parse_markers(f"{P} URL {URL}", P)["url"], URL)


class TestPanel(unittest.TestCase):
    def test_url_is_rendered_only_for_a_verified_running_state(self):
        running = cw.LaunchSnapshot(state=cw.RUNNING, share_url=URL, url_check="HTTP 200", session="s")
        self.assertIn(URL, ColabPanel._runtime_markdown(running))
        for state in (cw.FAILED, cw.CANCELLED, cw.STOPPED, cw.DISCONNECTED):
            stale = cw.LaunchSnapshot(state=state, share_url=URL, session="s")
            self.assertNotIn(URL, ColabPanel._runtime_markdown(stale), state)

    def test_launch_without_an_app_does_nothing(self):
        panel = ColabPanel(cli=FakeCli(), launcher=cw.ColabLauncher(FakeCli()))
        self.assertIn("Generate an app first", panel.launch("", "", "T4"))

    def test_gpu_dropdown_falls_back_to_documented_names_until_checked(self):
        names, _ = ColabPanel(cli=FakeCli(), launcher=cw.ColabLauncher(FakeCli())).gpu_choices()
        self.assertEqual(names, [cw.CPU_LABEL, *DOCUMENTED_GPUS])

    def test_gpu_suggestion_follows_the_memory_estimate(self):
        def hw(need, needs_gpu=True, cpu_ok=False):
            return SimpleNamespace(est_vram_gb_4bit=None, est_vram_gb_fp16=need, suggest_4bit=False,
                                   needs_gpu=needs_gpu, runs_locally_cpu=cpu_ok)
        avail = ["T4", "L4", "A100", "H100"]
        self.assertEqual(cw.suggest_gpu(hw(30.0), avail)[0], "A100")
        self.assertEqual(cw.suggest_gpu(hw(5.0), avail)[0], "T4")
        self.assertEqual(cw.suggest_gpu(hw(5.0, needs_gpu=False, cpu_ok=True), avail)[0], cw.CPU_LABEL)


class TestCreationInstallAndUploadSurviveBlips(LauncherCase):
    """The three stages that used to be single-shot: runtime creation, pip install, file transfer."""

    def test_a_transient_create_failure_is_retried_and_then_succeeds(self):
        cli = FakeCli(new_failures=1)
        _, snap = self.run_launch(cli)
        self.assertEqual(snap.state, cw.RUNNING)
        self.assertEqual(len(cli.names("new")), 2)
        self.assertEqual(snap.retries, 1)
        self.assertEqual(snap.share_url, URL)

    def test_a_503_that_still_allocated_the_runtime_is_adopted_not_duplicated(self):
        cli = FakeCli(new_failures=1, new_creates_despite_failure=True)
        _, snap = self.run_launch(cli)
        self.assertEqual(snap.state, cw.RUNNING)
        self.assertEqual(len(cli.names("new")), 1, "a second runtime would be a leaked GPU")
        self.assertEqual(cli.names("new")[0][1], snap.session)
        self.assertTrue(any("adopting" in line for line in snap.logs))

    def test_a_persistent_create_failure_reports_the_stage_and_releases_everything(self):
        cli = FakeCli(new_failures=99)
        _, snap = self.run_launch(cli)
        self.assertEqual(snap.state, cw.FAILED)
        self.assertEqual(snap.error_kind, "kernel_connection")
        self.assertIn("create runtime", snap.message)
        self.assertIn("connection to the runtime's kernel was lost", snap.message)
        self.assertNotIn("sign in", snap.message.lower())
        self.assertEqual(len(cli.names("new")), 3)  # bounded by create_attempts
        self.assertEqual(len(cli.names("stop")), 1, "the launch must not leave a runtime behind")

    def test_an_unknown_session_listing_never_creates_a_second_runtime(self):
        cli = FakeCli(new_failures=1, sessions_error=status_unknown())
        _, snap = self.run_launch(cli)
        self.assertEqual(snap.state, cw.FAILED)
        self.assertEqual(len(cli.names("new")), 1, "creation must not repeat when the listing is unknown")
        self.assertIn("not requesting a second one", snap.message)

    def test_install_is_retried_after_a_dropped_connection(self):
        cli = FakeCli(install_failures=1)
        _, snap = self.run_launch(cli)
        self.assertEqual(snap.state, cw.RUNNING)
        self.assertEqual(cli.exec_log.count("install"), 2)
        self.assertGreaterEqual(snap.retries, 1)

    def test_an_install_that_pip_really_failed_is_not_replayed(self):
        cli = FakeCli(install_pip_fail=True)
        _, snap = self.run_launch(cli)
        self.assertEqual(snap.state, cw.FAILED)
        self.assertEqual(snap.error_kind, "install_failed")
        self.assertEqual(cli.exec_log.count("install"), 1)
        self.assertIn("pip reported a failure", snap.message)
        self.assertIn("m2g-nope", snap.message)

    def test_upload_is_retried_and_the_landed_files_are_checksum_verified(self):
        cli = FakeCli(upload_failures=1)
        _, snap = self.run_launch(cli)
        self.assertEqual(snap.state, cw.RUNNING)
        self.assertEqual(cli.exec_log.count("write"), 2)
        for name in ("app.py", "loader.py", "requirements.txt", "m2g_launch.py"):
            self.assertTrue((self.remote / name).is_file(), name)
        self.assertEqual((self.remote / "app.py").read_text(encoding="utf-8"), gg.split_app_code(self.code)[1])
        self.assertTrue(any("checksums verified" in line for line in snap.logs))

    def test_a_corrupted_transfer_is_rewritten_once_then_reported(self):
        cli = FakeCli(upload_corrupt=True)
        launcher = self.launcher(cli, exec_attempts=2)
        snap = launcher.start(self.code, self.reqs, "T4", settings=Settings())
        self.assertEqual(snap.state, cw.FAILED)
        self.assertEqual(snap.error_kind, "transfer_failed")
        self.assertEqual(cli.exec_log.count("write"), 2, "the idempotent rewrite is the only retry allowed")
        self.assertIn("wrong checksum: app.py", snap.message)
        self.assertEqual(len(cli.names("stop")), 1)


FAKE_APP = '''import json, os, sys
_HERE = os.path.dirname(os.path.abspath(__file__))


def _load(name, default):
    path = os.path.join(_HERE, name)
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _note(what):
    calls = _load("calls.json", [])
    calls.append(what)
    with open(os.path.join(_HERE, "calls.json"), "w", encoding="utf-8") as f:
        json.dump(calls, f)


def load():
    pass


class Demo:
    def __init__(self):
        self.examples = []
        self.share_url = None

    def launch(self, **kw):
        plan = _load("plan.json", ["ok"])
        what = plan[min(len(_load("calls.json", [])), len(plan) - 1)]
        _note({"do": what, "share": kw.get("share"), "thread_lock": kw.get("prevent_thread_lock")})
        if what == "raise":
            raise ValueError("local server could not start")
        if what == "ok":
            self.share_url = "https://abc-123.gradio.live"
            return None, "http://127.0.0.1:7860", self.share_url
        if what == "checksum":
            print("Could not create share link. Checksum mismatch for file: /tmp/frpc.")
        else:
            print("\\nCould not create share link. Please check your internet connection or our status page: "
                  "https://status.gradio.app.")
            print("2026/10/29 10:25:09 [W] [service.go:132] login to server failed: dial tcp 44.237.78.176:7000: "
                  "connect: connection refused", file=sys.stderr)
        return None, "http://127.0.0.1:7860", None

    def block_thread(self):
        pass


demo = Demo()
'''


class TestLauncherShareLink(LauncherCase):
    """The shipped m2g_launch.py template, run in-process against a scripted app.py, so the share-link handling is
    exercised exactly as it runs on the runtime (no Gradio, no tunnel, no network)."""

    def _fake_connect(self, addr, timeout=None):
        """Stands in for the runtime's network: api.gradio.app answers, the refused tunnel server does not."""
        self.probed.append(addr)
        if addr[0] == "api.gradio.app":
            return mock.MagicMock()
        raise ConnectionRefusedError("refused")

    def run_launcher(self, plan):
        self.probed = []
        here = Path(self.tmp.name) / "runtime"
        here.mkdir()
        (here / "app.py").write_text(FAKE_APP, encoding="utf-8")
        (here / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
        (here / "m2g_launch.py").write_text(cw._render(cw.LAUNCHER_SCRIPT, PREFIX=P, LOADER="load"), encoding="utf-8")
        out, err = io.StringIO(), io.StringIO()
        old_cwd, old_path = os.getcwd(), list(sys.path)

        def restore():
            os.chdir(old_cwd)
            sys.path[:] = old_path

        self.addCleanup(restore)
        code = 0
        with mock.patch("time.sleep") as slept, mock.patch("socket.create_connection", side_effect=self._fake_connect), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                runpy.run_path(str(here / "m2g_launch.py"), run_name="__main__")
            except SystemExit as exc:
                code = exc.code
        calls_file = here / "calls.json"
        calls = json.loads(calls_file.read_text(encoding="utf-8")) if calls_file.exists() else []
        return code, out.getvalue(), err.getvalue(), calls, slept

    def test_a_healthy_share_link_starts_the_app_without_retrying(self):
        code, out, _, calls, slept = self.run_launcher(["ok"])
        self.assertEqual(code, 0)
        self.assertIn(f"{P} URL {URL}", out)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["share"], True)
        self.assertEqual(calls[0]["thread_lock"], True)
        self.assertFalse(slept.called)

    def test_a_refused_tunnel_login_is_retried_until_the_link_appears(self):
        code, out, err, calls, slept = self.run_launcher(["tunnel", "ok"])
        self.assertEqual(code, 0)
        self.assertIn(f"{P} URL {URL}", out)
        self.assertEqual([c["do"] for c in calls], ["tunnel", "ok"])
        self.assertIn(f"{P} SHARE_RETRY", out)
        self.assertIn("login to server failed", out + err)  # the real reason, not a generic message
        self.assertEqual(slept.call_count, 1)

    def test_a_persistent_tunnel_outage_fails_with_the_real_reason_and_is_bounded(self):
        code, out, err, calls, slept = self.run_launcher(["tunnel"])
        self.assertEqual(code, 1)
        self.assertNotIn(f"{P} URL", out, "a failed tunnel must never publish a URL")
        self.assertEqual(len(calls), 6, "the retry budget must stay bounded")
        self.assertEqual(slept.call_count, 5)
        self.assertIn(f"{P} FAIL", out)
        self.assertIn("connection refused", out + err)
        self.assertIn("Could not create share link", out + err)
        # the workflow reads this payload, so the real tunnel reason must be in the message the user sees
        fail = cw.parse_markers(out, P)["fail"]
        self.assertIn("connection refused", fail)
        self.assertIn("Could not create share link", fail)

    def test_tunnel_retries_back_off_over_a_bounded_window(self):
        # Regression: the old budget (3 tries, 5 s apart) gave up after ~10 s of a refused tunnel login.
        _, out, _, calls, slept = self.run_launcher(["tunnel"])
        self.assertEqual([c.args[0] for c in slept.call_args_list], [5.0, 10.0, 20.0, 30.0, 30.0])
        self.assertEqual(len(calls), 6)
        self.assertTrue(all(c["share"] is True for c in calls))  # the same local server is re-used; no new process
        retries = [cw.parse_markers(ln, P)["share_retry"] for ln in out.splitlines() if "SHARE_RETRY" in ln]
        self.assertEqual([r["attempt"] for r in retries], [1, 2, 3, 4, 5])
        self.assertTrue(all(r["of"] == 6 for r in retries))
        self.assertIn("connection refused", retries[0]["reason"])

    def test_the_final_failure_names_the_attempts_the_server_and_what_the_runtime_could_reach(self):
        code, out, _, _, _ = self.run_launcher(["tunnel"])
        self.assertEqual(code, 1)
        fail = cw.parse_markers(out, P)["fail"]
        self.assertIn("after 6 attempt(s)", fail)
        self.assertIn("44.237.78.176:7000", fail)
        self.assertIn("api.gradio.app:443 accepts connections", fail)
        self.assertIn("44.237.78.176:7000 ConnectionRefusedError", fail)
        self.assertEqual(self.probed, [("api.gradio.app", 443), ("44.237.78.176", 7000)])

    def test_a_non_transient_share_failure_is_reported_once_without_retrying(self):
        code, out, _, calls, slept = self.run_launcher(["checksum"])
        self.assertEqual(code, 1)
        self.assertEqual(len(calls), 1)
        self.assertFalse(slept.called)
        self.assertIn("Checksum mismatch", out)
        self.assertEqual(self.probed, [], "only a transient tunnel outage is probed")

    def test_a_local_server_failure_is_not_treated_as_a_share_problem(self):
        code, out, _, calls, slept = self.run_launcher(["raise"])
        self.assertEqual(code, 1)
        self.assertEqual(len(calls), 1)
        self.assertFalse(slept.called)
        self.assertIn("local server could not start", out)
        self.assertIn(f"{P} FAIL", out)


TOY_APP = 'import gradio as gr\n\ndemo = gr.Blocks()\nwith demo:\n    gr.Markdown("x"%s)\n'


class TestRuntimePreflight(LauncherCase):
    """Preflight runs on the runtime after the install and before anything is transferred or started."""

    def direct(self, cli, app):
        launcher = self.launcher(cli)
        launcher._prefix = P
        files = {"app.py": app}
        return launcher, files

    def test_a_normal_launch_runs_preflight_between_install_and_transfer(self):
        cli = FakeCli()
        _, snap = self.run_launch(cli)
        self.assertEqual(snap.state, cw.RUNNING, snap.message)
        order = [s for s in cli.exec_log if s in ("install", "preflight", "write", "start")]
        self.assertEqual(order, ["install", "preflight", "write", "start"])
        self.assertEqual(snap.preflight["status"], "passed")
        self.assertEqual(snap.preflight["gradio_version"], "5.49.0")
        self.assertTrue(any("Preflight passed" in ln for ln in snap.logs))
        self.assertTrue(any("runtime Gradio 5.49.0" in ln for ln in snap.logs))

    def test_unreadable_runtime_gradio_stops_the_launch_before_upload_and_releases_the_runtime(self):
        bad = {"rc": 1, "out": "", "err": "ModuleNotFoundError: No module named 'gradio'"}
        cli = FakeCli(probe_result=ok(f"{P} PROBE {json.dumps(bad)}\n"))
        _, snap = self.run_launch(cli)
        self.assertEqual(snap.state, cw.FAILED)
        self.assertEqual(snap.error_kind, "preflight_failed")
        self.assertIn("No module named 'gradio'", snap.message)
        self.assertNotIn("write", cli.exec_log)
        self.assertNotIn("start", cli.exec_log)
        self.assertTrue(cli.names("stop"), "the runtime must be released")
        self.assertEqual(snap.preflight["status"], "probe_failed")

    def test_a_probe_that_prints_nothing_stops_the_launch(self):
        cli = FakeCli(probe_result=ok(""))
        _, snap = self.run_launch(cli)
        self.assertEqual(snap.state, cw.FAILED)
        self.assertEqual(snap.error_kind, "preflight_failed")
        self.assertNotIn("write", cli.exec_log)

    def test_safe_repair_changes_app_py_and_is_logged(self):
        cli = FakeCli(gradio_version="6.30.0")
        launcher, files = self.direct(cli, TOY_APP % ', theme="soft"')
        with mock.patch.object(cw, "validate_app_code", lambda code, settings: {}):
            launcher._preflight("s", files, Settings())
        self.assertNotIn("theme", files["app.py"])
        snap = launcher.snapshot()
        self.assertEqual(snap.preflight["status"], "repaired")
        self.assertEqual(snap.preflight["gradio_version"], "6.30.0")
        self.assertTrue(any("removed unsupported argument" in ln for ln in snap.logs))

    def test_repair_is_revalidated_with_the_projects_validator(self):
        cli = FakeCli(gradio_version="6.30.0")
        launcher, files = self.direct(cli, TOY_APP % ', theme="soft"')
        seen = []
        with mock.patch.object(cw, "validate_app_code", lambda code, settings: seen.append(code) or {}):
            launcher._preflight("s", files, Settings())
        self.assertEqual(seen, [files["app.py"]])

    def test_unresolved_incompatibility_blocks_and_leaves_app_py_untouched(self):
        app = TOY_APP % ", bogus=1"
        cli = FakeCli(gradio_version="6.30.0")
        launcher, files = self.direct(cli, app)
        with self.assertRaises(cw._Abort) as cm:
            launcher._preflight("s", files, Settings())
        self.assertEqual(cm.exception.kind, "preflight_failed")
        self.assertIn("bogus", cm.exception.message)
        self.assertEqual(files["app.py"], app)
        snap = launcher.snapshot()
        self.assertEqual(snap.preflight["status"], "blocked")
        self.assertIn("bogus", snap.diagnostics)

    def test_probe_marker_is_parsed_only_with_this_runs_prefix(self):
        reply = json.dumps({"rc": 0, "out": "x", "err": ""})
        self.assertEqual(cw.parse_markers(f"{P} PROBE {reply}", P)["probe"]["rc"], 0)
        self.assertIsNone(cw.parse_markers(f"__M2G_other__ PROBE {reply}", P)["probe"])

    def test_the_probe_runner_sent_to_the_runtime_is_ascii(self):
        sent = []

        class Spy(FakeCli):
            def exec_code(self, name, code, timeout=600):
                if "M2G_GRADIO_API" in code:
                    sent.append(code)
                return super().exec_code(name, code, timeout)
        self.run_launch(Spy())
        self.assertEqual(len(sent), 1)
        sent[0].encode("ascii")


if __name__ == "__main__":
    unittest.main()
