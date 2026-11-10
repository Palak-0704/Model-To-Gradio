"""Regression tests for the UI path (ColabPanel -> start_async -> ColabLauncher -> status text), run with the REAL panel,
launcher and ColabCli against a scripted subprocess runner. No Gradio, Google account, network or Colab runtime."""
import importlib.util
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from m2g import colab_workflow as cw
from m2g import gradio_gen as gg
from m2g.colab_auth import SignInError
from m2g.colab_cli import CliResult, ColabCli
from m2g.colab_panel import CANCELLING_NOTICE, URL_PLACEHOLDER, ColabPanel, url_box_html, verified_url
from m2g.config import Settings
from test_analyzer import analyze_preset
from test_colab_workflow import FakeCli, NONCE, P, URL

HELP = "usage: colab {new,exec,sessions,stop,status,version}"


class PanelCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        a = analyze_preset("sst2")
        self.code, self.reqs = gg.generate_app(a), gg.generate_requirements(a)

    def launcher(self, cli):
        return cw.ColabLauncher(cli, verify=lambda url, auth: (True, "HTTP 200 (test)"), sleep=lambda s: None,
                                poll_interval=0, url_retries=2, url_retry_delay=0,
                                remote_dir=str(Path(self.tmp.name) / "remote"), nonce=lambda: NONCE)


class TestUiLaunchReachesTheStatusText(PanelCase):
    def test_async_launch_from_the_panel_ends_with_a_clickable_url_in_the_status(self):
        cli = FakeCli()
        launcher = self.launcher(cli)
        panel = ColabPanel(cli=cli, launcher=launcher, settings_factory=Settings)
        self.assertIn("Runtime state", panel.launch(self.code, self.reqs, "T4"))  # returns at once, launch runs in a thread
        launcher.wait(30)
        md = panel.status_markdown()  # what the 3-second timer renders
        self.assertIn(f"[{URL}]({URL})", md)
        self.assertIn("HTTP 200 (test)", md)
        self.assertEqual(launcher.snapshot().state, cw.RUNNING)

    def test_a_second_launch_while_running_is_refused_and_keeps_the_url(self):
        cli = FakeCli()
        launcher = self.launcher(cli)
        panel = ColabPanel(cli=cli, launcher=launcher, settings_factory=Settings)
        panel.launch(self.code, self.reqs, "T4")
        launcher.wait(30)
        md = panel.launch(self.code, self.reqs, "T4")
        self.assertIn("already in progress or a runtime is still running", md)
        self.assertIn(URL, md)
        self.assertEqual(len(cli.names("new")), 1)


class TestCancelDoesNotPoisonLaterCliCalls(PanelCase):
    """Regression: Cancel before a runtime exists left ColabCli's interrupt latch set. Check connection / Sign in / the
    GPU list then returned 'cancelled' until another launch happened (the smoke script never cancels, so never saw it).
    A cancel during the CLI check was also reported as FAILED ('cli_failed') instead of CANCELLED."""

    def make_cli(self, launcher_ref):
        calls = []

        def runner(cmd, *, input="", timeout=120, env=None, on_start=None):
            calls.append(cmd[3:])
            if len(calls) == 1:  # the cancel arrives while the first CLI call (`--help`) is running
                launcher_ref[0].cancel()
            return subprocess.CompletedProcess(cmd, 0, HELP if "--help" in cmd else "ok\n", "")

        return ColabCli(command=["colab"], runner=runner, auth="oauth2", environ={}), calls

    def test_cli_works_again_after_a_launch_cancelled_before_any_runtime_existed(self):
        ref = [None]
        cli, calls = self.make_cli(ref)
        launcher = ref[0] = self.launcher(cli)
        snap = launcher.start(self.code, self.reqs, "T4", settings=Settings())
        self.assertEqual(snap.state, cw.CANCELLED, snap.message)
        self.assertNotIn(["new"], [c[:1] for c in calls], "no runtime may be requested after a cancel")
        self.assertFalse(cli.interrupted, "the interrupt latch must be released once the launch has ended")
        panel = ColabPanel(cli=cli, launcher=launcher, settings_factory=Settings)
        md = panel.check_connection()
        self.assertIn("**Google:** connected - verified by a real `colab sessions` call", md)
        self.assertEqual(panel.google[0], "ok", panel.google)

    def test_cancel_with_a_runtime_still_releases_it_and_clears_the_latch(self):
        cli = FakeCli()
        launcher = self.launcher(cli)
        cli.on_new = launcher.cancel
        snap = launcher.start(self.code, self.reqs, "T4", settings=Settings())
        self.assertEqual(snap.state, cw.CANCELLED)
        self.assertEqual(len(cli.names("stop")), 1)


class TestCompactCard(PanelCase):
    """The default Colab card: Connected / Not connected, progress, the verified link, and nothing technical."""

    def panel(self, cli=None):
        cli = cli or FakeCli()
        return ColabPanel(cli=cli, launcher=self.launcher(cli), settings_factory=Settings)

    def test_unchecked_card_is_not_connected_but_launch_is_not_blocked(self):
        v = self.panel().card_view()
        self.assertFalse(v.connected)
        self.assertTrue(v.can_launch, "not checked yet is not the same as known to be unusable")
        self.assertFalse(v.busy or v.running or v.has_runtime)
        self.assertIn("Not connected", v.html)

    def test_verified_connection_shows_connected(self):
        p = self.panel()
        p.cli_identity, p.google = (True, "ok"), ("ok", "ok", 0.0)
        v = p.card_view()
        self.assertTrue(v.connected)
        self.assertIn("Connected", v.html)
        self.assertNotIn("Not connected", v.html)

    def test_known_unusable_connection_disables_launch_and_says_how_to_fix_it(self):
        p = self.panel()
        p.cli_identity, p.google = (True, "ok"), ("needs_auth", "Sign in", 0.0)
        v = p.card_view()
        self.assertFalse(v.connected)
        self.assertFalse(v.can_launch)
        self.assertIn("Sign in with Google to continue", v.html)
        p.cli_identity, p.google = (False, "colab not found"), ("unavailable", "colab not found", 0.0)
        self.assertFalse(p.card_view().can_launch)

    def test_verified_run_shows_the_link_and_stop_but_no_internals(self):
        cli = FakeCli()
        p = self.panel(cli)
        p.launch(self.code, self.reqs, "T4")
        p.launcher.wait(30)
        v = p.card_view()
        self.assertTrue(v.running and v.has_runtime)
        self.assertFalse(v.busy)
        self.assertEqual(v.url, URL)
        self.assertIn(f'href="{URL}"', v.url_html)
        self.assertNotIn(URL, v.html, "the link lives in its own box, not in the status card")
        self.assertIn("Runtime running", v.html)
        session = p.launcher.snapshot().session
        if session:
            self.assertNotIn(session, v.html, "raw session ids belong in Run logs only")
        self.assertNotIn("nvidia-smi", v.html)
        self.assertNotIn("Colab CLI", v.html)

    def test_progress_groups_the_launcher_states_into_four_steps(self):
        html = ColabPanel._runtime_html(cw.LaunchSnapshot(state=cw.INSTALLING), True)
        self.assertIn("Step 3 of 4", html)
        self.assertIn("Installing", html)
        self.assertIn('class="current">Installing', html)
        self.assertIn('class="done">Starting runtime', html)

    def test_no_link_is_rendered_unless_the_run_is_verified_and_https(self):
        self.assertNotIn("href", ColabPanel._runtime_html(cw.LaunchSnapshot(state=cw.FAILED, share_url=URL), False))
        self.assertNotIn("href", ColabPanel._runtime_html(
            cw.LaunchSnapshot(state=cw.RUNNING, share_url="javascript:alert(1)"), False))

    def test_failure_text_is_escaped_and_shortened(self):
        html = ColabPanel._runtime_html(
            cw.LaunchSnapshot(state=cw.FAILED, message="<script>alert(1)</script>" + "x" * 500), False)
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertNotIn("x" * 300, html)
        self.assertIn("Launch failed", html)

    def test_cancelling_notice_does_not_outlive_the_cancel(self):
        p = self.panel()
        p._notice = CANCELLING_NOTICE
        self.assertNotIn("Cancelling", p.card_view().html)

    def test_the_detailed_status_text_is_unchanged_and_still_available(self):
        md = self.panel().status_markdown()
        self.assertIn("### Google Colab (official Colab CLI)", md)

    @unittest.skipUnless(importlib.util.find_spec("gradio"), "gradio is not installed")
    def test_the_page_builds_with_the_compact_card(self):
        from m2g.web import build_ui
        build_ui(Settings(), panel=self.panel())


class CheckCli(FakeCli):
    """FakeCli plus the one extra call ColabPanel.check_connection makes."""

    def version(self):
        return "test-1.0"


class StuckCli(CheckCli):
    """`colab stop` fails and the session stays listed, as when Google is unreachable."""

    def stop(self, name):
        self.calls.append(("stop", name))
        return CliResult([], 1, "", "boom", 0.0)


class FakeSignIn:
    """Stands in for ColabSignIn: no browser, no CLI."""

    def __init__(self, submit_result=(True, "Signed in."), begin_error=None):
        self.waiting, self.cancelled = False, False
        self.submit_result, self.begin_error = submit_result, begin_error

    def begin(self):
        if self.begin_error:
            raise self.begin_error
        self.waiting = True
        return SimpleNamespace(url="https://accounts.google.com/o/oauth2/auth?x=1",
                               scopes=("https://www.googleapis.com/auth/colaboratory",))

    def submit(self, code):
        self.waiting = False
        return self.submit_result

    def cancel(self):
        self.waiting, self.cancelled = False, True


class TestCardButtons(PanelCase):
    """Sign in / Check status / Stop Colab runtime / Launch: every enabled state comes from the real backend state."""

    def panel(self, cli=None, signin=None):
        cli = cli or CheckCli()
        return ColabPanel(cli=cli, launcher=self.launcher(cli), settings_factory=Settings,
                          signin_factory=(lambda c: signin) if signin else None)

    @staticmethod
    def states(v):
        return (v.signin_enabled, v.check_enabled, v.stop_enabled, v.launch_enabled)  # sign in, check, stop, launch

    def running_panel(self, cli=None):
        p = self.panel(cli)
        p.launch(self.code, self.reqs, "T4")
        p.launcher.wait(30)
        return p

    # ---- the five states in the spec
    def test_not_connected(self):
        p = self.panel()
        p.cli_identity, p.google = (True, "ok"), ("needs_auth", "Sign in", 0.0)
        v = p.card_view()
        self.assertEqual(self.states(v), (True, True, False, False))
        self.assertEqual(v.signin_label, "Sign in with Google")

    def test_connected_without_a_runtime(self):
        p = self.panel()
        p.cli_identity, p.google = (True, "ok"), ("ok", "ok", 0.0)
        v = p.card_view()
        self.assertEqual(self.states(v), (False, True, False, True))
        self.assertEqual(v.signin_label, "Signed in")
        self.assertIn("Google sign-in verified", v.html)

    def test_starting_blocks_duplicate_launches_shows_progress_and_can_be_cancelled(self):
        cli = CheckCli()
        p = self.panel(cli)
        seen, notices = [], []

        def during_launch():  # runs inside the real launch, right after the runtime was requested
            seen.append(p.card_view())
            p.launch(self.code, self.reqs, "T4")  # a second launch must be refused
            notices.append(p._notice)

        cli.on_new = during_launch
        p.launcher.start(self.code, self.reqs, "T4", settings=Settings())
        v = seen[0]
        self.assertTrue(v.busy)
        self.assertIn("m2g-steps", v.html)
        self.assertEqual(v.launch_label, "Launching…")
        # sign in off, check off (it must not interfere), stop on (it cancels this launch), launch off
        self.assertEqual(self.states(v), (False, False, True, False))
        self.assertIn("already in progress", notices[0])
        self.assertEqual(len(cli.names("new")), 1)

    def test_running(self):
        v = self.running_panel().card_view()
        self.assertTrue(v.connected and v.running and v.has_runtime)
        self.assertEqual(self.states(v), (False, True, True, False))
        self.assertEqual(v.signin_label, "Signed in")
        self.assertIn(f'href="{URL}"', v.url_html)

    def test_stopped(self):
        p = self.running_panel()
        p.stop_runtime()
        v = p.card_view()
        self.assertEqual(p.launcher.snapshot().state, cw.STOPPED)
        self.assertFalse(v.stop_enabled)
        self.assertTrue(v.launch_enabled)
        self.assertNotIn(URL, v.html, "a stopped runtime must not leave a stale link")
        self.assertNotIn(URL, v.url_html)
        self.assertEqual(v.url, "")
        self.assertIn(URL_PLACEHOLDER, v.url_html)
        self.assertIn("Runtime stopped", v.html)

    # ---- safe stopping
    def test_stop_only_touches_the_runtime_this_app_created(self):
        cli = CheckCli()
        p = self.running_panel(cli)
        session = p.launcher.snapshot().session
        self.assertTrue(session)
        cli.live.append("someone-elses-session")
        p.stop_runtime()
        self.assertEqual(cli.names("stop"), [("stop", session)])

    def test_stop_with_nothing_tracked_does_nothing(self):
        cli = CheckCli()
        p = self.panel(cli)
        cli.live.append("someone-elses-session")
        p.stop_runtime()
        self.assertEqual(cli.names("stop"), [])
        self.assertFalse(p.card_view().stop_enabled)

    def test_stop_while_starting_cancels_this_launch_and_releases_its_runtime(self):
        cli = CheckCli()
        p = self.panel(cli)
        cli.on_new = p.stop_runtime
        snap = p.launcher.start(self.code, self.reqs, "T4", settings=Settings())
        self.assertEqual(snap.state, cw.CANCELLED)
        self.assertEqual(len(cli.names("stop")), 1)
        v = p.card_view()
        self.assertFalse(v.stop_enabled)
        self.assertTrue(v.launch_enabled)
        self.assertIn("Launch cancelled", v.html)

    def test_a_failed_stop_keeps_the_runtime_tracked_shows_the_error_and_allows_a_retry(self):
        p = self.running_panel(StuckCli())
        p.stop_runtime()
        v = p.card_view()
        self.assertTrue(v.has_runtime and v.stop_enabled)
        self.assertFalse(v.launch_enabled)
        self.assertIn("Stopping failed", v.html)
        self.assertIn("m2g-note-bad", v.html)

    def test_the_stop_button_is_disabled_while_stopping(self):
        p = self.running_panel()
        p.begin_stop()
        v = p.card_view()
        self.assertFalse(v.stop_enabled)
        self.assertEqual(v.stop_label, "Stopping…")
        self.assertFalse(v.check_enabled)
        p.stop_runtime()
        self.assertFalse(p._stopping, "the flag must be released once the stop has finished")

    def test_ui_stop_request_is_non_blocking_and_clears_busy_state(self):
        p = self.running_panel()
        entered, release = threading.Event(), threading.Event()
        original = p.launcher.stop

        def delayed_stop():
            entered.set()
            release.wait(2)
            return original()

        p.launcher.stop = delayed_stop
        started = time.monotonic()
        p.request_stop()
        self.assertLess(time.monotonic() - started, 0.5)
        self.assertTrue(entered.wait(1))
        self.assertTrue(p.card_view().stopping)
        release.set()
        p._stop_thread.join(3)
        self.assertFalse(p._stopping)
        self.assertFalse(p.card_view().stop_enabled)
        self.assertTrue(p.card_view().launch_enabled)

    def test_ui_stop_request_clears_busy_state_when_launcher_raises(self):
        p = self.running_panel()

        def failed_stop():
            raise RuntimeError("stop transport failed")

        p.launcher.stop = failed_stop
        p.request_stop()
        p._stop_thread.join(3)
        self.assertFalse(p._stopping)
        self.assertFalse(p.card_view().launch_enabled)
        self.assertTrue(p.card_view().stop_enabled)
        self.assertIn("Stopping failed", p.card_view().html)

    # ---- check status
    def test_check_status_reads_the_real_authentication_state(self):
        for state, connected in (("ok", True), ("needs_auth", False)):
            p = self.panel(CheckCli(check_state=state))
            p.check_status()
            v = p.card_view()
            self.assertEqual(v.connected, connected, state)
            self.assertEqual(v.signin_enabled, not connected, state)
        self.assertIn("Sign in with Google to continue", v.html)

    def test_the_check_button_is_disabled_while_checking(self):
        p = self.panel()
        p.begin_check()
        v = p.card_view()
        self.assertFalse(v.check_enabled)
        self.assertFalse(v.signin_enabled)
        self.assertEqual(v.check_label, "Checking…")
        p.check_status()
        self.assertTrue(p.card_view().check_enabled)

    def test_check_status_never_runs_during_a_launch(self):
        cli = CheckCli()
        p = self.panel(cli)
        seen = []
        cli.on_new = lambda: (p.check_status(), seen.append(p.google[0]))
        p.launcher.start(self.code, self.reqs, "T4", settings=Settings())
        self.assertEqual(seen, ["unknown"], "the check must be skipped, not run, while a launch is in progress")

    def test_check_status_notices_that_the_app_died_and_drops_the_stale_link(self):
        cli = CheckCli()
        p = self.running_panel(cli)
        cli.poll = f'{P} ALIVE 0\n{P} TAIL ""\n'
        p.check_status()
        v = p.card_view()
        self.assertEqual(p.launcher.snapshot().state, cw.FAILED)
        self.assertFalse(v.running)
        self.assertNotIn(URL, v.html)
        self.assertNotIn(URL, v.url_html)
        self.assertEqual(v.url, "")
        self.assertFalse(v.stop_enabled, "the runtime was released, so there is nothing left to stop")

    # ---- authentication errors
    def test_sign_in_shows_the_google_link_and_blocks_a_second_sign_in(self):
        p = self.panel(signin=FakeSignIn())
        _md, show = p.begin_sign_in()
        v = p.card_view()
        self.assertTrue(show and v.signing_in)
        self.assertIn("Open Google sign-in", v.html)
        self.assertIn('href="https://accounts.google.com/', v.html)
        self.assertFalse(v.signin_enabled)
        self.assertFalse(v.check_enabled)

    def test_a_rejected_code_leaves_the_card_not_connected_with_a_readable_error(self):
        p = self.panel(signin=FakeSignIn(submit_result=(False, "That code was not accepted. Try signing in again.")))
        p.begin_sign_in()
        _md, show = p.submit_code("wrong")
        v = p.card_view()
        self.assertFalse(show)
        self.assertFalse(v.connected)
        self.assertTrue(v.signin_enabled, "the user must be able to try again")
        self.assertIn("not accepted", v.html)
        self.assertIn("m2g-note-bad", v.html)

    def test_an_accepted_code_shows_connected(self):
        p = self.panel(signin=FakeSignIn(submit_result=(True, "Signed in.")))
        p.begin_sign_in()
        p.submit_code("good")
        v = p.card_view()
        self.assertTrue(v.connected)
        self.assertFalse(v.signin_enabled)
        self.assertEqual(v.signin_label, "Signed in")

    def test_a_sign_in_that_cannot_start_says_why(self):
        p = self.panel(signin=FakeSignIn(begin_error=SignInError("no browser available")))
        _md, show = p.begin_sign_in()
        v = p.card_view()
        self.assertFalse(show)
        self.assertIn("Sign-in could not start: no browser available", v.html)
        self.assertTrue(v.signin_enabled)

    def test_cli_and_error_text_is_escaped(self):
        p = self.panel()
        p.cli_identity, p.google = (False, "<img src=x onerror=alert(1)>"), ("unavailable", "x", 0.0)
        html = p.card_view().html
        self.assertNotIn("<img", html)
        self.assertIn("&lt;img", html)

    # ---- the page
    @unittest.skipUnless(importlib.util.find_spec("gradio"), "gradio is not installed")
    def test_the_page_has_exactly_the_three_colab_controls_and_no_gemini(self):
        import gradio as gr
        from m2g.web import build_ui
        demo = build_ui(Settings(), panel=self.panel())
        labels = [b.value for b in demo.blocks.values() if isinstance(b, gr.Button)]
        for want in ("Sign in with Google", "Check status", "Stop Colab runtime", "Launch on Colab"):
            self.assertEqual(labels.count(want), 1, want)
        for gone in ("Disconnect", "Cancel launch", "Check app", "Stop runtime"):
            self.assertNotIn(gone, labels)
        texts = [str(getattr(b, "value", "")) for b in demo.blocks.values()]
        self.assertTrue(any("3. Run on Google Colab" in t for t in texts))
        self.assertTrue(any("manage your remote runtime" in t for t in texts))
        self.assertFalse(any("gemini" in t.lower() for t in texts))

    @unittest.skipUnless(importlib.util.find_spec("gradio"), "gradio is not installed")
    def test_every_card_handler_returns_one_value_per_output(self):
        from m2g.web import build_ui
        demo = build_ui(Settings(), panel=self.panel())
        fns = getattr(demo, "fns", None)
        if not isinstance(fns, dict):
            self.skipTest("this Gradio version does not expose Blocks.fns as a dict")
        checked = 0
        for f in fns.values():
            if getattr(f, "name", "") in ("_tick", "_check_begin", "_stop"):
                result = f.fn()
                self.assertEqual(len(result), len(f.outputs), f.name)
                checked += 1
        self.assertGreaterEqual(checked, 3)


def poll_with(selftest_line: str) -> str:
    """A runtime poll reply in which the model loaded, the given self-test line was printed, and a share URL was published."""
    return f'{P} ALIVE 1\n{P} IMPORTED\n{P} LOADED\n{selftest_line}\n{P} URL {URL}\n{P} TAIL ""\n'


class TestLiveUrlBox(PanelCase):
    """The dedicated Live Gradio URL box: a link only after loading, the self-test and the URL check all passed."""

    def panel(self, cli=None, launcher=None):
        cli = cli or CheckCli()
        return ColabPanel(cli=cli, launcher=launcher or self.launcher(cli), settings_factory=Settings)

    def launch_and_wait(self, p):
        p.launch(self.code, self.reqs, "T4")
        p.launcher.wait(30)
        return p.card_view()

    def test_before_any_launch_the_box_shows_only_the_placeholder(self):
        v = self.panel().card_view()
        self.assertEqual(v.url, "")
        self.assertIn("Live Gradio URL", v.url_html)
        self.assertIn(URL_PLACEHOLDER, v.url_html)
        self.assertNotIn("href", v.url_html)

    def test_while_launching_the_box_stays_on_the_placeholder(self):
        cli = CheckCli()
        p = self.panel(cli)
        seen = []
        cli.on_new = lambda: seen.append(p.card_view())
        p.launcher.start(self.code, self.reqs, "T4", settings=Settings())
        self.assertTrue(seen[0].busy)
        self.assertEqual(seen[0].url, "")
        self.assertIn(URL_PLACEHOLDER, seen[0].url_html)
        self.assertNotIn("href", seen[0].url_html)

    def test_a_verified_run_fills_the_box_with_a_clickable_link(self):
        v = self.launch_and_wait(self.panel())
        self.assertEqual(v.url, URL)
        self.assertIn(f'href="{URL}"', v.url_html)
        self.assertIn('target="_blank"', v.url_html)
        self.assertIn("noopener", v.url_html)
        self.assertIn("Live Gradio URL", v.url_html)
        self.assertNotIn(URL_PLACEHOLDER, v.url_html)
        self.assertIn("no built-in example", v.url_html, "with no example, the box must not claim inference was tested")

    def test_a_passed_self_test_is_reported_in_the_box(self):
        v = self.launch_and_wait(self.panel(CheckCli(poll=poll_with(f'{P} SELFTEST_OK "POSITIVE"'))))
        self.assertEqual(v.url, URL)
        self.assertIn("example ran successfully", v.url_html)

    def test_a_failed_self_test_keeps_the_link_out_of_the_box(self):
        p = self.panel(CheckCli(poll=poll_with(f'{P} SELFTEST_FAIL "ValueError: boom"')))
        v = self.launch_and_wait(p)
        self.assertEqual(p.launcher.snapshot().state, cw.RUNNING)
        self.assertEqual(v.url, "")
        self.assertIn(URL_PLACEHOLDER, v.url_html)
        self.assertIn("built-in example failed", v.url_html)
        self.assertNotIn(URL, v.url_html)
        self.assertTrue(v.stop_enabled, "the runtime is still the app's and must stay stoppable")

    def test_a_url_that_fails_verification_never_reaches_the_box(self):
        cli = CheckCli()
        launcher = cw.ColabLauncher(cli, verify=lambda url, auth: (False, "HTTP 502 (test)"), sleep=lambda s: None,
                                    poll_interval=0, url_retries=2, url_retry_delay=0,
                                    remote_dir=str(Path(self.tmp.name) / "remote"), nonce=lambda: NONCE)
        p = self.panel(cli, launcher)
        v = self.launch_and_wait(p)
        self.assertEqual(launcher.snapshot().state, cw.FAILED)
        self.assertEqual(v.url, "")
        self.assertIn(URL_PLACEHOLDER, v.url_html)
        self.assertNotIn(URL, v.url_html + v.html)

    def test_only_a_fully_verified_snapshot_yields_a_link(self):
        good = dict(state=cw.RUNNING, share_url=URL, model_loaded=True, selftest="ok")
        self.assertEqual(verified_url(cw.LaunchSnapshot(**good)), URL)
        self.assertEqual(verified_url(cw.LaunchSnapshot(**{**good, "selftest": "none"})), URL)
        for bad in ({"state": cw.WAITING}, {"state": cw.FAILED}, {"state": cw.STOPPED}, {"model_loaded": False},
                    {"selftest": "fail"}, {"selftest": None}, {"share_url": "http://abc.gradio.live"},
                    {"share_url": "javascript:alert(1)"}, {"share_url": None}):
            self.assertEqual(verified_url(cw.LaunchSnapshot(**{**good, **bad})), "", bad)

    def test_the_link_text_is_escaped(self):
        html = url_box_html(cw.LaunchSnapshot(state=cw.RUNNING, selftest="ok"), 'https://a.gradio.live/"><script>x</script>')
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)

    @unittest.skipUnless(importlib.util.find_spec("gradio"), "gradio is not installed")
    def test_the_page_has_the_url_box_and_a_disabled_copy_button(self):
        import gradio as gr
        from m2g.web import build_ui
        demo = build_ui(Settings(), panel=self.panel())
        copy = [b for b in demo.blocks.values() if isinstance(b, gr.Button) and b.value == "Copy URL"]
        self.assertEqual(len(copy), 1)
        self.assertFalse(copy[0].interactive, "nothing to copy before the URL is verified")
        texts = [str(getattr(b, "value", "")) for b in demo.blocks.values()]
        self.assertTrue(any("Live Gradio URL" in t and URL_PLACEHOLDER in t for t in texts))


if __name__ == "__main__":
    unittest.main()
