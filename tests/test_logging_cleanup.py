"""Phase 4 tests: stage-tagged redacted logging, diagnostics and resource cleanup of the Colab launch.

Covers m2g.colab_workflow (the launcher) and the redaction added to m2g.colab_runtime (the Jupyter path).
Offline: scripted fakes only, no Google account, no network, no Colab runtime. The REAL password-removal script is executed
locally against a temporary directory (like the transfer script in test_colab_workflow), so the removal itself is exercised.
"""
import contextlib
import io
import os
import re
import threading
import unittest
from pathlib import Path
from unittest import mock

from m2g import colab_runtime as cr
from m2g import colab_workflow as cw
from m2g.colab_cli import CliResult

import test_colab_runtime as tcr
import test_colab_workflow as twf

P = twf.P
STAMP = re.compile(r"^\d{2}:\d{2}:\d{2} \[([a-z ]+)\] ")
PASSWORD = "correct-horse-9"
UNREACHABLE = lambda url, auth: (False, "HTTP 502")  # noqa: E731  (a launch that fails AFTER the files were transferred)


def stages(lines):
    """The [stage] label of every log line (None for a line that does not follow the format)."""
    return [(STAMP.match(ln).group(1) if STAMP.match(ln) else None) for ln in lines]


class RemoteCli(twf.FakeCli):
    """FakeCli that also RUNS the real password-removal script locally, so the removal is real, not assumed."""

    def exec_code(self, name, code, timeout=600):
        if "SECRETS_REMOVED" in code:
            self.exec_log.append("remove")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                exec(compile(code, "remote_remove", "exec"), {})
            return twf.ok(buf.getvalue())
        return super().exec_code(name, code, timeout)


class SilentRemoveCli(twf.FakeCli):
    """The removal call succeeds but prints no marker, so removal cannot be confirmed."""

    def exec_code(self, name, code, timeout=600):
        if "SECRETS_REMOVED" in code:
            self.exec_log.append("remove")
            return twf.ok("")
        return super().exec_code(name, code, timeout)


class BrokenRemoveCli(twf.FakeCli):
    def exec_code(self, name, code, timeout=600):
        if "SECRETS_REMOVED" in code:
            raise RuntimeError("remove exploded")
        return super().exec_code(name, code, timeout)


class StuckStopCli(RemoteCli):
    """`colab stop` fails and the runtime stays listed, so the launcher cannot release it."""

    def stop(self, name):
        self.calls.append(("stop", name))
        return CliResult(["stop"], 1, "", "stop refused", 0.0)


class LatchCli(twf.FakeCli):
    """Models the real CLI's interrupt latch: after interrupt() every call answers 'cancelled' until it is cleared."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.latched = False

    def interrupt(self):
        self.latched = True

    def clear_interrupt(self):
        self.latched = False


class TestPasswordFileRemoval(twf.LauncherCase):
    def run_script(self, directory, patches=()):
        code = cw._render(cw.REMOVE_SECRETS_SCRIPT, DIR=str(directory), PREFIX=P)
        buf = io.StringIO()
        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            stack.enter_context(contextlib.redirect_stdout(buf))
            exec(compile(code, "remote_remove", "exec"), {})
        return buf.getvalue()

    def make_auth_file(self):
        d = Path(self.tmp.name) / "r"
        d.mkdir()
        f = d / "m2g_auth.json"
        f.write_text('["alice", "%s"]' % PASSWORD, encoding="utf-8")
        return d, f

    def test_the_script_overwrites_the_file_with_zero_bytes_and_then_deletes_it(self):
        d, f = self.make_auth_file()
        size = f.stat().st_size
        seen = []
        real_remove = os.remove

        def spy(path):
            seen.append(Path(path).read_bytes())  # what is on disk at the moment of deletion
            real_remove(path)

        out = self.run_script(d, [mock.patch("os.remove", spy)])
        self.assertEqual(seen, [bytes(size)])
        self.assertFalse(f.exists())
        self.assertEqual(cw.parse_markers(out, P)["secrets"], "removed")

    def test_the_script_reports_kept_when_the_file_cannot_be_deleted(self):
        d, f = self.make_auth_file()
        out = self.run_script(d, [mock.patch("os.remove", side_effect=OSError("denied"))])
        self.assertTrue(f.exists())
        self.assertEqual(cw.parse_markers(out, P)["secrets"], "kept")

    def test_the_script_is_harmless_when_there_is_no_password_file(self):
        d = Path(self.tmp.name) / "empty"
        d.mkdir()
        self.assertEqual(cw.parse_markers(self.run_script(d), P)["secrets"], "removed")

    def test_the_secrets_marker_is_parsed_only_with_this_runs_prefix(self):
        self.assertEqual(cw.parse_markers(f"{P} SECRETS_KEPT", P)["secrets"], "kept")
        self.assertIsNone(cw.parse_markers("__M2G_other__ SECRETS_REMOVED", P)["secrets"])

    def test_the_password_file_is_removed_from_the_runtime_once_the_app_is_up(self):
        cli = RemoteCli()
        _, snap = self.run_launch(cli, auth=("alice", PASSWORD))
        self.assertEqual(snap.state, cw.RUNNING, snap.message)
        self.assertTrue(snap.auth_protected)
        self.assertTrue((self.remote / "app.py").is_file())
        self.assertFalse((self.remote / "m2g_auth.json").exists())
        self.assertEqual(cli.exec_log.count("remove"), 1)
        logs = "\n".join(snap.logs)
        self.assertIn("Removed the link password file from the runtime", logs)
        self.assertNotIn(PASSWORD, logs)

    def test_a_launch_without_a_link_password_makes_no_removal_call(self):
        cli = RemoteCli()
        _, snap = self.run_launch(cli)
        self.assertEqual(snap.state, cw.RUNNING, snap.message)
        self.assertNotIn("remove", cli.exec_log)

    def test_an_unconfirmed_removal_is_logged_and_never_fails_the_launch(self):
        _, snap = self.run_launch(SilentRemoveCli(), auth=("alice", PASSWORD))
        self.assertEqual(snap.state, cw.RUNNING, snap.message)
        self.assertTrue(any("could not be confirmed as removed" in ln for ln in snap.logs))

    def test_a_removal_that_raises_is_logged_and_never_fails_the_launch(self):
        _, snap = self.run_launch(BrokenRemoveCli(), auth=("alice", PASSWORD))
        self.assertEqual(snap.state, cw.RUNNING, snap.message)
        self.assertTrue(any("Could not remove the link password file" in ln and "RuntimeError" in ln for ln in snap.logs))
        self.assertNotIn("remove exploded", "\n".join(snap.logs), "only the exception type is shown")

    def test_when_the_runtime_cannot_be_stopped_the_password_is_removed_and_the_person_is_told_how_to_stop_it(self):
        cli = StuckStopCli()
        launcher, snap = self.run_launch(cli, auth=("alice", PASSWORD), verify=UNREACHABLE)
        self.assertEqual((snap.state, snap.error_kind), (cw.FAILED, "url_unreachable"))
        self.assertTrue((self.remote / "app.py").is_file(), "the failure happened after the transfer")
        self.assertFalse((self.remote / "m2g_auth.json").exists(), "the password must not stay on a runtime that keeps running")
        self.assertEqual(cli.exec_log.count("remove"), 1)
        self.assertIn("may still be running", snap.message)
        self.assertIn("colab --auth oauth2 stop -s", snap.message)
        self.assertIsNotNone(snap.session, "a runtime that was not released stays tracked")
        self.assertFalse(launcher.busy)
        self.assertNotIn(PASSWORD, snap.message + "\n".join(snap.logs) + snap.diagnostics)

    def test_a_runtime_that_was_released_needs_no_removal_call(self):
        cli = RemoteCli()
        _, snap = self.run_launch(cli, auth=("alice", PASSWORD), verify=UNREACHABLE)
        self.assertEqual(snap.state, cw.FAILED)
        self.assertEqual(len(cli.names("stop")), 1)
        self.assertIsNone(snap.session)
        self.assertNotIn("remove", cli.exec_log)


class TestCleanupAlwaysFinishes(twf.LauncherCase):
    def test_an_exception_while_stopping_the_runtime_is_contained_and_keeps_its_traceback(self):
        launcher = self.launcher(twf.FakeCli(), verify=UNREACHABLE)  # a failure after the runtime exists, so cleanup runs
        with mock.patch.object(cw.ColabLauncher, "_stop_session", side_effect=RuntimeError("stop exploded")), \
                self.assertLogs("m2g.colab", level="ERROR"):
            snap = launcher.start(self.code, self.reqs, "T4", settings=cw.Settings())
        self.assertEqual((snap.state, snap.error_kind), (cw.FAILED, "url_unreachable"))
        self.assertIn("may still be running", snap.message)
        self.assertFalse(launcher.busy, "cleanup must never leave the launcher stuck busy")
        self.assertIn("Traceback", snap.diagnostics)
        self.assertIn("RuntimeError: stop exploded", snap.diagnostics)
        self.assertIn("cleanup: full traceback while stopping the runtime", snap.diagnostics)
        self.assertNotIn("stop exploded", snap.message, "the short message names only the exception type")

    def test_a_stopped_runtime_leaves_the_launcher_free_for_a_new_launch(self):
        cli = twf.FakeCli()
        launcher = self.launcher(cli)
        first = launcher.start(self.code, self.reqs, "T4", settings=cw.Settings())
        self.assertEqual(first.state, cw.RUNNING, first.message)
        launcher.stop()
        self.assertFalse(launcher.busy)
        second = launcher.start(self.code, self.reqs, "T4", settings=cw.Settings())
        self.assertEqual(second.state, cw.RUNNING, second.message)

    def test_the_cli_interrupt_latch_is_cleared_after_a_cancelled_launch(self):
        cli = LatchCli()
        launcher = self.launcher(cli)
        cli.on_new = launcher.cancel
        snap = launcher.start(self.code, self.reqs, "T4", settings=cw.Settings())
        self.assertEqual(snap.state, cw.CANCELLED, snap.message)
        self.assertFalse(cli.latched, "a stale latch would make every later CLI call answer 'cancelled'")
        self.assertFalse(launcher.busy)
        self.assertEqual(len(cli.names("stop")), 1)

    def test_the_busy_flag_is_reset_after_every_kind_of_failure(self):
        for label, cli, kw in (("not signed in", twf.FakeCli(check_state="needs_auth"), {}),
                               ("url unreachable", twf.FakeCli(), {"verify": UNREACHABLE}),
                               ("pip failure", twf.FakeCli(install_pip_fail=True), {})):
            with self.subTest(label):
                launcher, snap = self.run_launch(cli, **kw)
                self.assertEqual(snap.state, cw.FAILED, snap.message)
                self.assertFalse(launcher.busy)
                self.assertIsNone(snap.session)


class TestStageTaggedLogs(twf.LauncherCase):
    def test_every_line_is_timestamped_and_stage_tagged(self):
        _, snap = self.run_launch(twf.FakeCli())
        self.assertEqual(snap.state, cw.RUNNING, snap.message)
        self.assertTrue(snap.logs)
        bad = [ln for ln, st in zip(snap.logs, stages(snap.logs)) if st is None]
        self.assertEqual(bad, [], "every log line must start with 'HH:MM:SS [stage] '")

    def test_a_normal_launch_labels_each_deployment_stage(self):
        _, snap = self.run_launch(twf.FakeCli())
        seen = set(stages(snap.logs))
        for expected in ("inspection", "runtime", "dependencies", "preflight", "transfer", "startup", "model loading",
                         "inference", "verification"):
            self.assertIn(expected, seen, f"no log line was labelled [{expected}]")

    def test_preflight_lines_carry_the_preflight_label(self):
        _, snap = self.run_launch(twf.FakeCli())
        lines = [ln for ln in snap.logs if "Preflight" in ln]
        self.assertTrue(lines)
        self.assertTrue(all(STAMP.match(ln).group(1) == "preflight" for ln in lines))

    def test_cleanup_after_a_failed_launch_is_labelled_cleanup(self):
        _, snap = self.run_launch(twf.FakeCli(), verify=UNREACHABLE)
        self.assertEqual(snap.state, cw.FAILED)
        released = [ln for ln in snap.logs if "released after the failure" in ln]
        self.assertEqual(len(released), 1, snap.logs)
        self.assertEqual(STAMP.match(released[0]).group(1), "cleanup")
        stop_lines = [ln for ln in snap.logs if "[stop runtime] exit=" in ln]
        self.assertTrue(stop_lines)
        self.assertTrue(all(STAMP.match(ln).group(1) == "cleanup" for ln in stop_lines))

    def test_the_stage_override_nests_and_is_restored(self):
        launcher = self.launcher(twf.FakeCli())
        launcher._log("a")
        with launcher._in_stage("cleanup"):
            launcher._log("b")
            with launcher._in_stage("verification"):
                launcher._log("c")
            launcher._log("d")
        launcher._log("e")
        self.assertEqual(stages(launcher.snapshot().logs), ["idle", "cleanup", "verification", "cleanup", "idle"])

    def test_the_stage_override_belongs_to_one_thread_only(self):
        launcher = self.launcher(twf.FakeCli())
        with launcher._in_stage("cleanup"):
            worker = threading.Thread(target=launcher._log, args=("from another thread",))
            worker.start()
            worker.join()
            launcher._log("from this thread")
        tags = {ln.split("] ", 1)[1]: STAMP.match(ln).group(1) for ln in launcher.snapshot().logs}
        self.assertEqual(tags["from another thread"], "idle")
        self.assertEqual(tags["from this thread"], "cleanup")

    def test_the_log_keeps_only_the_most_recent_600_lines(self):
        launcher = self.launcher(twf.FakeCli())
        for i in range(700):
            launcher._log(f"line {i}")
        logs = launcher.snapshot().logs
        self.assertEqual(len(logs), 600)
        self.assertTrue(logs[0].endswith("line 100"))
        self.assertTrue(logs[-1].endswith("line 699"))


class TestLogRedaction(twf.LauncherCase):
    def test_credentials_in_urls_and_key_value_pairs_never_reach_the_log(self):
        launcher = self.launcher(twf.FakeCli())
        cases = (("fetching https://alice:s3cret-pass@example.com/model", "s3cret-pass"),
                 ("retry with password=hunter2222 now", "hunter2222"),
                 ("config api_key=abcd1234efgh set", "abcd1234efgh"),
                 ("ACCESS_TOKEN=abcd1234efgh in the environment", "abcd1234efgh"),
                 ("got ya29.TOKENVALUE123456 back", "TOKENVALUE123456"))
        for text, secret in cases:
            launcher._log(text)
        logs = "\n".join(launcher.snapshot().logs)
        for _, secret in cases:
            self.assertNotIn(secret, logs)
        self.assertIn("https://[redacted]@example.com/model", logs)
        self.assertIn("password=[redacted]", logs)
        self.assertIn("api_key=[redacted]", logs)

    def test_a_diagnostic_code_is_not_mistaken_for_a_secret(self):
        launcher = self.launcher(twf.FakeCli())
        text = "[dependency/pip_unsatisfiable] code=pip_unsatisfiable"
        self.assertEqual(launcher._scrub(text), text)

    def test_the_diagnostics_are_redacted_too(self):
        launcher = self.launcher(twf.FakeCli())
        launcher._add_text_diag("pip output", "failed with password=hunter2222 and ya29.TOKENVALUE123456")
        diag = launcher.snapshot().diagnostics
        self.assertNotIn("hunter2222", diag)
        self.assertNotIn("TOKENVALUE123456", diag)
        self.assertIn("password=[redacted]", diag)


class TestRuntimePathIsRedacted(unittest.TestCase):
    """ColabManager.run_predict (the Jupyter path): runtime text and log lines are redacted like the launcher's."""

    APP = tcr.APP

    def manager(self, install_reply):
        def extra(code):
            if "'pip', 'install'" in code:
                return install_reply
            return None

        mgr, _, _ = tcr.make_manager(responder=tcr.hw_responder(extra=extra))
        mgr.connect("CPU")
        self.assertEqual(mgr.state, cr.CONNECTED, mgr.message)
        self.sleeps = []
        mgr.retry_sleep = self.sleeps.append
        return mgr

    def run_predict(self, mgr):
        with mock.patch.object(cr, "validate_app_code", lambda code, settings: None), \
                mock.patch.object(cr, "validate_requirements_text", lambda text: ["gradio>=5.0"]):
            return mgr.run_predict(self.APP, "gradio>=5.0", '["great"]')

    def test_secrets_in_a_failed_step_are_redacted_in_last_error_and_in_the_returned_text(self):
        reply = [tcr.stream("debug ya29.SECRETTOKENVALUE123456789 seen\nNo matching distribution found for nope==9\n"),
                 ("error", {"ename": "AssertionError", "evalue": "pip install failed password=hunter2222"})]
        mgr = self.manager(reply)
        out = self.run_predict(mgr)
        self.assertTrue(out.startswith("Error on the runtime:"), out)
        self.assertIn("pip install failed", out, "the original error text is still shown first")
        self.assertIn("password=[redacted]", out)
        self.assertNotIn("hunter2222", out)
        self.assertNotIn("hunter2222", mgr.last_error["original"])
        self.assertNotIn("SECRETTOKENVALUE", mgr.last_error["stdout_tail"])
        self.assertIn("No matching distribution", mgr.last_error["stdout_tail"], "only the secret is removed, not the evidence")

    def test_the_retry_warning_in_the_console_log_is_redacted(self):
        reply = [tcr.stream("Read timed out api_key=abc12345xyz\n"),
                 ("error", {"ename": "AssertionError", "evalue": "pip install failed"})]
        mgr = self.manager(reply)
        with self.assertLogs("m2g.colab", level="WARNING") as captured:
            self.run_predict(mgr)
        self.assertNotIn("abc12345xyz", "\n".join(captured.output))


if __name__ == "__main__":
    unittest.main()
