"""Phase 3 integration tests: central error classification wired into the Colab launcher (m2g.colab_workflow), the Jupyter runtime
path (m2g.colab_runtime) and the panel text (m2g.colab_panel).

They prove, end to end and offline (scripted fakes, no Google account, no network, no Colab runtime):
  * a TRANSIENT failure is retried a bounded number of times and then reported;
  * a PERMANENT or UNRECOGNISED failure is never retried;
  * the original error text/traceback, the failing stage and every automatic retry stay in the diagnostics;
  * the person sees the cause and the next step while the technical detail stays available.
tests/test_error_classify.py covers the pure classifier; this file covers its integration.
"""
import json
import unittest
from unittest import mock

from m2g import colab_runtime as cr
from m2g import colab_workflow as cw
from m2g.colab_panel import ColabPanel
from m2g.config import Settings

import test_colab_runtime as tcr
import test_colab_workflow as twf

P = twf.P
DOWNLOAD_TIMEOUT = "requests.exceptions.ReadTimeout: HTTPSConnectionPool(host='huggingface.co', port=443): Read timed out."
PIP_TIMEOUT = ("WARNING: Retrying (Retry(total=4)) after connection broken by 'ReadTimeoutError': Read timed out.\n"
               "ERROR: Could not install packages due to an OSError\n")


def pip_fail(text):
    """What a failed pip step prints: the pip output followed by this run's failure marker."""
    return f"{text}\n{P} PIP_FAIL 1\n"


def fail_poll(text):
    """A runtime poll in which the app process reported its own failure."""
    return f'{P} ALIVE 1\n{P} IMPORTED\n{P} FAIL {json.dumps(text)}\n{P} TAIL ""\n'


class ScriptedCli(twf.FakeCli):
    """FakeCli whose pip replies and runtime polls follow a script (the last entry repeats). Everything else is unchanged."""

    def __init__(self, install_script=None, poll_script=None, **kw):
        super().__init__(**kw)
        self.install_script, self.poll_script = list(install_script or []), list(poll_script or [])

    @staticmethod
    def _next(script):
        return script.pop(0) if len(script) > 1 else script[0]

    def exec_code(self, name, code, timeout=600):
        plain = not any(tag in code for tag in ("WRITTEN", "STARTED", "M2G_GRADIO_API", " HW "))
        if plain and "PIP_OK" in code and self.install_script:
            self.exec_log.append("install")
            return twf.ok(self._next(self.install_script))
        if plain and "ALIVE" in code and self.poll_script:
            self.exec_log.append("poll")
            return twf.ok(self._next(self.poll_script))
        return super().exec_code(name, code, timeout)


class RecoveryCase(twf.LauncherCase):
    def launcher(self, cli, verify=None, **kw):
        self.sleeps = []  # every wait the launcher asked for; the real sleeping is replaced by this recorder
        return cw.ColabLauncher(cli, verify=verify or (lambda url, auth: (True, "HTTP 200 (test)")), sleep=self.sleeps.append,
                                poll_interval=0, url_retries=2, url_retry_delay=0, remote_dir=str(self.remote),
                                nonce=lambda: twf.NONCE, **kw)

    def go(self, cli, gpu="T4", **launcher_kw):
        launcher = self.launcher(cli, **launcher_kw)
        return launcher, launcher.start(self.code, self.reqs, gpu, settings=Settings())

    def waits(self):
        return [s for s in self.sleeps if s > 0]


class TestInstallRecovery(RecoveryCase):
    def test_a_transient_pip_failure_is_retried_and_the_launch_succeeds(self):
        cli = ScriptedCli(install_script=[pip_fail(PIP_TIMEOUT), f"{P} PIP_OK\n"])
        _, snap = self.go(cli)
        self.assertEqual(snap.state, cw.RUNNING, snap.message)
        self.assertEqual(cli.exec_log.count("install"), 2)
        self.assertEqual(snap.retries, 1)
        self.assertEqual(len(snap.recovery_log), 1)
        self.assertIn("network/transient_network", snap.recovery_log[0])
        self.assertIn("Read timed out", snap.diagnostics, "the original pip output of the failed attempt must be kept")
        self.assertEqual(self.waits(), [8.0])
        self.assertEqual(snap.error_category, "")  # a launch that recovered carries no error

    def test_a_persistent_transient_failure_is_bounded_then_reported_with_everything_kept(self):
        cli = ScriptedCli(install_script=[pip_fail(PIP_TIMEOUT)])
        _, snap = self.go(cli)
        self.assertEqual(snap.state, cw.FAILED)
        self.assertEqual(snap.error_kind, "install_failed")
        self.assertEqual(cli.exec_log.count("install"), 3, "install_attempts bounds the total number of tries")
        self.assertEqual(snap.retries, 2)
        self.assertEqual(len(snap.recovery_log), 2)
        self.assertEqual(self.waits(), [8.0, 16.0])
        self.assertEqual((snap.error_category, snap.error_code, snap.retryable), ("network", "transient_network", True))
        self.assertEqual(snap.failed_stage, cw.INSTALLING)
        self.assertIn("Read timed out", snap.diagnostics)
        self.assertEqual(len(cli.names("stop")), 1, "the runtime is released after the retries are used up")
        self.assertIsNone(snap.session)

    def test_a_permanent_pip_failure_is_never_retried_and_is_explained(self):
        cli = twf.FakeCli(install_pip_fail=True)
        _, snap = self.go(cli)
        self.assertEqual(snap.state, cw.FAILED)
        self.assertEqual(cli.exec_log.count("install"), 1)
        self.assertEqual((snap.error_category, snap.error_code, snap.retryable), ("dependency", "pip_unsatisfiable", False))
        self.assertEqual((snap.retries, snap.recovery_log), (0, []))
        self.assertEqual(self.waits(), [])
        self.assertTrue(snap.recovery)
        self.assertIn("[dependency/pip_unsatisfiable] What to do:", snap.message)
        self.assertIn("m2g-nope", snap.diagnostics)
        self.assertEqual(snap.failed_stage, cw.INSTALLING)

    def test_an_unrecognised_pip_failure_is_permanent(self):
        cli = ScriptedCli(install_script=[pip_fail("something nobody has seen before")])
        _, snap = self.go(cli)
        self.assertEqual(snap.state, cw.FAILED)
        self.assertEqual(cli.exec_log.count("install"), 1)
        self.assertEqual((snap.error_category, snap.retryable), ("dependency", False))
        self.assertEqual(snap.recovery_log, [])

    def test_install_attempts_of_one_disables_the_retry(self):
        cli = ScriptedCli(install_script=[pip_fail(PIP_TIMEOUT)])
        _, snap = self.go(cli, install_attempts=1)
        self.assertEqual(snap.state, cw.FAILED)
        self.assertEqual(cli.exec_log.count("install"), 1)
        self.assertEqual(snap.recovery_log, [])

    def test_secrets_in_a_failed_pip_output_are_redacted_in_the_diagnostics(self):
        leaky = PIP_TIMEOUT + "Authorization failed for token ya29.SECRETTOKENVALUE123456789\n"
        cli = ScriptedCli(install_script=[pip_fail(leaky), f"{P} PIP_OK\n"])
        _, snap = self.go(cli)
        self.assertEqual(snap.state, cw.RUNNING, snap.message)
        self.assertNotIn("ya29.", snap.diagnostics)
        self.assertNotIn("SECRETTOKENVALUE", snap.diagnostics)


class TestStartupRecovery(RecoveryCase):
    def test_a_transient_model_download_failure_restarts_the_app_and_succeeds(self):
        cli = ScriptedCli(poll_script=[fail_poll(DOWNLOAD_TIMEOUT), twf.good_poll()])
        _, snap = self.go(cli)
        self.assertEqual(snap.state, cw.RUNNING, snap.message)
        self.assertEqual(snap.share_url, twf.URL)
        self.assertEqual(cli.exec_log.count("start"), 2)
        self.assertEqual((cli.exec_log.count("install"), cli.exec_log.count("write")), (1, 1),
                         "only the app process restarts; nothing is reinstalled or re-uploaded")
        self.assertEqual(snap.retries, 1)
        self.assertIn("model_loading/download_network_error", snap.recovery_log[0])
        self.assertIn("Read timed out", snap.diagnostics, "the traceback of the failed attempt must be kept")

    def test_a_persistent_transient_startup_failure_is_bounded_and_fully_reported(self):
        cli = ScriptedCli(poll_script=[fail_poll(DOWNLOAD_TIMEOUT)])
        launcher, snap = self.go(cli)
        self.assertEqual((snap.state, snap.error_kind), (cw.FAILED, "app_failed"))
        self.assertEqual(cli.exec_log.count("start"), 3, "startup_attempts bounds the total number of starts")
        self.assertEqual((snap.retries, len(snap.recovery_log)), (2, 2))
        self.assertEqual(self.waits(), [8.0, 16.0])
        self.assertEqual((snap.error_category, snap.error_code, snap.retryable),
                         ("model_loading", "download_network_error", True))
        self.assertIn(snap.failed_stage, (cw.STARTING, cw.WAITING))
        self.assertEqual(len(cli.names("stop")), 1)
        self.assertIsNone(snap.session)
        md = ColabPanel._runtime_markdown(snap)
        self.assertIn("Automatic retries made", md)
        self.assertIn("transient", md)

    def test_permanent_startup_errors_are_never_restarted(self):
        cases = (("RuntimeError: CUDA out of memory. Tried to allocate 2.00 GiB", "resource", "gpu_oom"),
                 ("OSError: You are trying to access a gated repo.", "model_loading", "model_access_denied"),
                 ("ModuleNotFoundError: No module named 'timm'", "dependency", "missing_module"),
                 ("RuntimeError: Gradio did not create a public share link after 6 attempt(s). Could not create share link",
                  "network", "share_tunnel"))
        for text, category, code in cases:
            with self.subTest(code=code):
                cli = ScriptedCli(poll_script=[fail_poll(text)])
                _, snap = self.go(cli)
                self.assertEqual((snap.state, snap.error_kind), (cw.FAILED, "app_failed"))
                self.assertEqual(cli.exec_log.count("start"), 1, "a permanent error must not restart the app")
                self.assertEqual((snap.error_category, snap.error_code, snap.retryable), (category, code, False))
                self.assertEqual((snap.retries, snap.recovery_log), (0, []))
                self.assertTrue(snap.recovery)
                self.assertEqual(len(cli.names("stop")), 1)

    def test_an_unrecognised_startup_error_is_permanent(self):
        cli = ScriptedCli(poll_script=[fail_poll("Exception: something nobody has seen before")])
        _, snap = self.go(cli)
        self.assertEqual(snap.state, cw.FAILED)
        self.assertEqual(cli.exec_log.count("start"), 1)
        self.assertEqual((snap.error_category, snap.retryable), ("startup", False))

    def test_startup_attempts_of_one_disables_the_restart(self):
        cli = ScriptedCli(poll_script=[fail_poll(DOWNLOAD_TIMEOUT)])
        _, snap = self.go(cli, startup_attempts=1)
        self.assertEqual(snap.state, cw.FAILED)
        self.assertEqual(cli.exec_log.count("start"), 1)
        self.assertEqual(snap.recovery_log, [])


class TestStageAndOriginalErrorsArePreserved(RecoveryCase):
    def test_a_cancelled_launch_records_the_stage_it_was_in(self):
        cli = twf.FakeCli()
        launcher = self.launcher(cli)
        cli.on_new = launcher.cancel
        snap = launcher.start(self.code, self.reqs, "T4", settings=Settings())
        self.assertEqual(snap.state, cw.CANCELLED)
        self.assertEqual(snap.failed_stage, cw.CREATING)

    def test_an_unexpected_internal_error_keeps_its_full_traceback_in_the_diagnostics(self):
        launcher = self.launcher(twf.FakeCli())
        with mock.patch.object(cw.ColabLauncher, "_check_cli", side_effect=ValueError("kaboom")), \
                self.assertLogs("m2g.colab", level="ERROR"):
            snap = launcher.start(self.code, self.reqs, "T4", settings=Settings())
        self.assertEqual((snap.state, snap.error_kind, snap.error_category), (cw.FAILED, "internal", "internal"))
        self.assertIn("Traceback", snap.diagnostics)
        self.assertIn("ValueError: kaboom", snap.diagnostics)
        self.assertNotIn("kaboom", snap.message, "the short message names only the exception type")
        self.assertEqual(snap.failed_stage, cw.PREPARING)

    def test_an_app_that_dies_later_is_classified_and_keeps_its_stage(self):
        cli = twf.FakeCli()
        launcher, snap = self.go(cli)
        self.assertEqual(snap.state, cw.RUNNING, snap.message)
        cli.poll = f'{P} ALIVE 0\n{P} TAIL ""\n'
        after = launcher.refresh()
        self.assertEqual(after.state, cw.FAILED)
        self.assertEqual((after.error_category, after.error_code), ("startup", "app_exited"))
        self.assertEqual(after.failed_stage, cw.RUNNING)
        self.assertTrue(after.recovery)


class TestPanelShowsCauseAndKeepsDiagnostics(RecoveryCase):
    def test_the_failed_card_names_the_cause_and_the_next_step_and_the_logs_keep_the_original_errors(self):
        cli = twf.FakeCli(install_pip_fail=True)
        launcher, snap = self.go(cli)
        panel = ColabPanel(cli=cli, launcher=launcher, settings_factory=Settings)
        html = panel.card_view().html
        self.assertIn("Launch failed", html)
        self.assertIn("dependency/pip_unsatisfiable", html)
        self.assertIn("installing dependencies", html)
        self.assertIn("What to do:", html, "the next step must not be cut off by the message length limit")
        md = panel.status_markdown()
        self.assertIn("permanent", md)
        self.assertIn("Technical diagnostics", md)
        self.assertIn("m2g-nope", md)

    def test_panel_text_from_the_runtime_cannot_inject_markup(self):
        snap = cw.LaunchSnapshot(state=cw.FAILED, message="m", error_category="x", error_code="<b>y</b>",
                                 recovery="<script>alert(1)</script>", diagnostics="a```b <script>x</script>")
        html = ColabPanel._runtime_html(snap, False)
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)
        md = ColabPanel._runtime_markdown(snap)
        self.assertEqual(md.count("```"), 2, "diagnostics may never close their own code block")

    def test_a_snapshot_without_a_classification_renders_as_before(self):
        html = ColabPanel._runtime_html(cw.LaunchSnapshot(state=cw.FAILED, message="boom"), False)
        self.assertIn("Launch failed", html)
        self.assertNotIn("What to do", html)
        self.assertNotIn("Technical diagnostics", "\n".join(ColabPanel._failure_markdown(cw.LaunchSnapshot(state=cw.FAILED))))


# ------------------------------------------------------------------------------------------- Jupyter runtime path
def kernel_fail(stdout, ename, evalue):
    """A runtime step that printed ``stdout`` and then raised ``ename: evalue``."""
    return [tcr.stream(stdout), ("error", {"ename": ename, "evalue": evalue})]


PIP_FAIL = "pip install failed"


class TestRuntimeRecovery(unittest.TestCase):
    """ColabManager.run_predict: the install and model-load steps retry only transient failures; inference never retries."""

    APP = tcr.APP

    def manager(self, install=(), load=(), predict=None):
        self.counts = {"install": 0, "load": 0, "predict": 0}

        def pick(script, n, ok_reply):
            if not script:
                return ok_reply
            return script[min(n, len(script)) - 1]

        def extra(code):
            if "'pip', 'install'" in code:
                self.counts["install"] += 1
                return pick(install, self.counts["install"], [tcr.stream("ok\n")])
            if "M2G_LOADED" in code:
                self.counts["load"] += 1
                return pick(load, self.counts["load"], [tcr.stream("M2G_LOADED\n")])
            if "M2G_RESULT" in code:
                self.counts["predict"] += 1
                return predict or [tcr.stream('M2G_RESULT:{"label": "POSITIVE"}\n')]
            return None

        mgr, _, _ = tcr.make_manager(responder=tcr.hw_responder(extra=extra))
        mgr.connect("CPU")
        self.assertEqual(mgr.state, cr.CONNECTED, mgr.message)
        self.sleeps = []
        mgr.retry_sleep = self.sleeps.append
        return mgr

    def run_predict(self, mgr, args='["great"]'):
        with mock.patch.object(cr, "validate_app_code", lambda code, settings: None), \
                mock.patch.object(cr, "validate_requirements_text", lambda text: ["gradio>=5.0"]):
            return mgr.run_predict(self.APP, "gradio>=5.0", args)

    def test_a_transient_install_failure_is_retried_then_succeeds(self):
        mgr = self.manager(install=[kernel_fail("Read timed out\n", "AssertionError", PIP_FAIL), [tcr.stream("ok\n")]])
        out = self.run_predict(mgr)
        self.assertEqual(json.loads(out), {"label": "POSITIVE"})
        self.assertEqual(self.counts["install"], 2)
        self.assertEqual(self.sleeps, [5.0])
        self.assertIsNone(mgr.last_error)

    def test_a_permanent_install_failure_is_never_retried_and_keeps_the_original_error(self):
        mgr = self.manager(install=[kernel_fail("ERROR: No matching distribution found for nope==9\n", "AssertionError", PIP_FAIL)])
        out = self.run_predict(mgr)
        self.assertTrue(out.startswith("Error on the runtime:"), out)
        self.assertIn(PIP_FAIL, out, "the original error comes first")
        self.assertIn("dependency/pip_unsatisfiable", out)
        self.assertIn("What to do", out)
        self.assertEqual((self.counts["install"], self.sleeps), (1, []))
        self.assertEqual((mgr.last_error["stage"], mgr.last_error["transient"]), ("install", False))
        self.assertIn("No matching distribution", mgr.last_error["stdout_tail"])

    def test_a_persistent_transient_install_failure_is_bounded(self):
        mgr = self.manager(install=[kernel_fail("Read timed out\n", "AssertionError", PIP_FAIL)])
        out = self.run_predict(mgr)
        self.assertEqual(self.counts["install"], mgr.install_attempts)
        self.assertEqual(self.sleeps, [5.0, 10.0])
        self.assertIn("network/transient_network", out)
        self.assertTrue(mgr.last_error["transient"])

    def test_a_partial_model_download_is_retried_at_most_once(self):
        mgr = self.manager(load=[kernel_fail("", "SafetensorError", "Error while deserializing header: MetadataIncompleteBuffer")])
        out = self.run_predict(mgr)
        self.assertEqual(self.counts["load"], 2, "the classifier allows one retry for a partial download")
        self.assertEqual(self.sleeps, [5.0])
        self.assertIn("model_loading/partial_download", out)
        self.assertIsNone(mgr._loaded_hash, "a failed load must not be remembered as deployed")

    def test_an_unrecognised_load_failure_is_permanent(self):
        mgr = self.manager(load=[kernel_fail("", "Exception", "something nobody has seen before")])
        out = self.run_predict(mgr)
        self.assertEqual((self.counts["load"], self.sleeps), (1, []))
        self.assertIn("model_loading/unrecognized", out)

    def test_an_inference_error_is_reported_with_its_stage_and_never_retried(self):
        mgr = self.manager(predict=kernel_fail("", "RuntimeError", "Expected all tensors to be on the same device"))
        out = self.run_predict(mgr)
        self.assertEqual((self.counts["predict"], self.sleeps), (1, []))
        self.assertIn("inference/bad_input_or_shape", out)
        self.assertEqual(mgr.last_error["stage"], "inference")
        self.run_predict(mgr, '["again"]')
        self.assertEqual((self.counts["install"], self.counts["load"]), (1, 1), "the deployed app is reused, not reinstalled")


if __name__ == "__main__":
    unittest.main()
