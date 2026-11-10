import tempfile
import unittest
import io
from pathlib import Path
from unittest import mock

from m2g.local_workflow import FAILED, LocalLauncher


class TestLocalLauncher(unittest.TestCase):
    def test_local_output_publishes_url_only_after_gradio_reports_one(self):
        launcher = LocalLauncher(python="python")
        class Proc:
            stdout = io.StringIO("Loading\nRunning on local URL: http://127.0.0.1:7860\n")
            def poll(self):
                return None
        launcher._read_process_output(Proc())
        self.assertEqual(launcher.snapshot.url, "http://127.0.0.1:7860")
        self.assertFalse(launcher._startup_event.is_set() is False)

    def test_local_output_without_url_fails_startup(self):
        launcher = LocalLauncher(python="python")
        class Proc:
            stdout = io.StringIO("ImportError: incompatible dependency\n")
            def poll(self):
                return 1
            returncode = 1
        launcher._read_process_output(Proc())
        self.assertIn("exited with code 1", launcher._startup_error)
    def test_local_launcher_uses_an_isolated_environment_and_never_colab(self):
        with tempfile.TemporaryDirectory() as td:
            launcher = LocalLauncher(python="python", sleep=lambda _: None)
            with mock.patch.object(launcher, "_run") as run:
                snap = launcher.launch("print('x')", "gradio>=5.0\n", Path(td))
                run.assert_called_once()
            self.assertIn("ModelToGradio", snap.environment)
            self.assertNotIn(str(Path(td)), snap.environment)

    def test_local_failure_is_explicit(self):
        with tempfile.TemporaryDirectory() as td:
            launcher = LocalLauncher(python="python")
            launcher._hardware = mock.Mock(side_effect=RuntimeError("no hardware probe"))
            with mock.patch("m2g.local_workflow.validate_app_code"), mock.patch(
                    "m2g.local_workflow.validate_requirements_text"):
                launcher._run("x", "y", Path(td))
            self.assertEqual(launcher.snapshot.state, FAILED)
            self.assertIn("no hardware probe", launcher.snapshot.error)

    def test_batch_launcher_uses_the_generated_app_launcher(self):
        with tempfile.TemporaryDirectory() as td:
            output = Path(td)
            batch = output / "run_local.bat"
            batch.write_text("@echo off\n", encoding="utf-8")
            launcher = LocalLauncher(python="python")
            with mock.patch.object(launcher, "_run_batch") as run:
                snap = launcher.launch_batch(batch, output)
                run.assert_called_once_with(batch.resolve(), output.resolve())
            # the BAT launch keeps its environment inside the generated app directory (not under %LOCALAPPDATA%)
            self.assertEqual(Path(snap.environment), output.resolve() / ".venv")

    def test_batch_launcher_rejects_a_launcher_outside_the_app_directory(self):
        with tempfile.TemporaryDirectory() as td:
            output = Path(td) / "app"
            outside = Path(td) / "run_local.bat"
            output.mkdir()
            outside.write_text("@echo off\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                LocalLauncher().launch_batch(outside, output)


if __name__ == "__main__":
    unittest.main()
