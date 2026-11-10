"""Offline tests for the live Logs behaviour of the Colab launch (m2g.colab_workflow._live_output and friends).

Reuses the scripted FakeCli / LauncherCase from test_colab_workflow. No Google account, network or runtime is used."""
import time
import unittest

from m2g import colab_workflow as cw
from m2g.colab_panel import ColabPanel
from test_colab_workflow import FakeCli, LauncherCase, P


class StreamingCli(FakeCli):
    """A FakeCli that behaves like the real ColabCli: while a long call runs it pushes output lines to ``on_output``."""

    on_output = None

    def _emit(self, stream, line):
        sink = self.on_output
        if sink is not None:
            sink(stream, line)

    def new_session(self, name, gpu=None):
        self._emit("stderr", "Allocating a T4 runtime...")
        self._emit("stdout", "")  # blank lines are ignored
        return super().new_session(name, gpu)

    def exec_code(self, name, code, timeout=600):
        if "PIP_OK" in code:
            self._emit("stdout", "Collecting transformers")
            self._emit("stdout", f"{P} PIP_OK")  # a marker line must never be echoed into the log
            self._emit("stderr", "debug token ya29.SECRETTOKENVALUE123 in output")
        return super().exec_code(name, code, timeout)


class TestLiveLogs(LauncherCase):
    def test_cli_output_reaches_the_log_while_the_call_runs(self):
        cli = StreamingCli()
        _, snap = self.run_launch(cli)
        self.assertEqual(snap.state, cw.RUNNING, snap.message)
        logs = "\n".join(snap.logs)
        self.assertIn("[stderr] Allocating a T4 runtime...", logs)
        self.assertIn("[stdout] Collecting transformers", logs)
        # live lines come BEFORE the call's own summary line
        live = next(i for i, ln in enumerate(snap.logs) if "Allocating a T4 runtime" in ln)
        done = next(i for i, ln in enumerate(snap.logs) if "[create runtime] exit=0" in ln)
        self.assertLess(live, done)

    def test_marker_lines_and_secrets_are_never_echoed(self):
        _, snap = self.run_launch(StreamingCli())
        logs = "\n".join(snap.logs)
        self.assertNotIn(f"{P} PIP_OK", logs)
        self.assertNotIn("SECRETTOKENVALUE123", logs)
        self.assertIn("[redacted]", logs)

    def test_the_sink_is_removed_again_after_every_call(self):
        cli = StreamingCli()
        self.run_launch(cli)
        self.assertIsNone(cli.on_output)

    def test_a_cli_without_streaming_support_still_launches(self):
        # The plain FakeCli has no on_output attribute at all (like a runner that does not accept on_line).
        cli = FakeCli()
        _, snap = self.run_launch(cli)
        self.assertEqual(snap.state, cw.RUNNING, snap.message)

    def test_the_log_is_not_empty_from_the_first_moment(self):
        _, snap = self.run_launch(StreamingCli())
        self.assertIn("Launch started", snap.logs[0])
        self.assertTrue(any("Requesting runtime" in ln for ln in snap.logs))
        self.assertTrue(any("Installing" in ln and "requirement" in ln for ln in snap.logs))

    def test_a_heartbeat_line_shows_that_a_long_call_is_still_running(self):
        launcher = self.launcher(FakeCli())
        with launcher._live_output("install dependencies", heartbeat=0.01):
            time.sleep(0.3)
        self.assertTrue(any("install dependencies: still running" in ln for ln in launcher.snapshot().logs))

    def test_the_panel_shows_the_live_lines_in_the_logs_text(self):
        cli = StreamingCli()
        launcher = self.launcher(cli)
        panel = ColabPanel(cli=cli, launcher=launcher)
        launcher.start(self.code, self.reqs, "T4", settings=cw.Settings())
        self.assertIn("Allocating a T4 runtime", panel.logs_text())


if __name__ == "__main__":
    unittest.main()
