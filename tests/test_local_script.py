import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from m2g import local_script
from m2g.local_script import windows_launcher


def _label_pos(script: str, label: str) -> int:
    """Position of the line that DEFINES ``:label`` (not a ``goto :label``)."""
    return script.index(f"\n:{label}\n")


class TestWindowsLauncher(unittest.TestCase):
    def test_launcher_keeps_the_environment_inside_the_app_folder(self):
        script = windows_launcher("google__flan-t5-small")
        self.assertIn("google__flan-t5-small", script)
        self.assertIn(r'set "VENV_DIR=%APP_DIR%\.venv"', script)
        self.assertIn(r'set "VENV_PY=%VENV_DIR%\Scripts\python.exe"', script)
        self.assertIn('set "APP_DIR=%~dp0"', script)  # the app directory comes from the launcher's own location
        self.assertNotIn("%LOCALAPPDATA%", script)
        self.assertNotIn("C:\\Users\\", script)

    def test_launcher_requires_python_312_and_never_falls_back(self):
        script = windows_launcher("example__model")
        self.assertIn('py -3.12 -c "import sys"', script)
        self.assertIn("sys.version_info[:2] == (3, 12)", script)
        self.assertIn(f'set "RC={local_script.EXIT_PYTHON_MISSING}"', script)
        self.assertIn("Python 3.12 was not found", script)
        self.assertIn("No other Python version is used", script)
        for other in ("3.13", "3.11", "3.10", "py -0p"):
            self.assertNotIn(other, script)

    def test_launcher_is_windows_only_and_never_touches_the_platform_environment(self):
        script = windows_launcher("example__model").lower()
        for forbidden in ("wsl", "docker", "bash", "sudo", "apt-get", "powershell"):
            self.assertNotIn(forbidden, script)

    def test_launcher_creates_reuses_activates_installs_and_starts_in_order(self):
        script = windows_launcher("example__model")
        # reuse: an existing .venv python skips Python detection and venv creation entirely
        reuse = script.index('if not exist "%VENV_PY%" goto :find_python')
        self.assertLess(reuse, script.index('py -3.12 -c "import sys"'))
        self.assertLess(script.index("M2G_STAGE=env_reused"), _label_pos(script, "verify_venv"))
        # creation happens only on the not-yet-created path
        self.assertIn('%PY_CMD% -m venv "%VENV_DIR%"', script)
        order = [
            _label_pos(script, "create_venv"),
            _label_pos(script, "verify_venv"),
            script.index('call "%VENV_DIR%\\Scripts\\activate.bat"'),
            _label_pos(script, "install_deps"),
            _label_pos(script, "start_app"),
        ]
        self.assertEqual(order, sorted(order))

    def test_launcher_prints_a_stage_marker_for_every_real_step(self):
        script = windows_launcher("example__model")
        for marker in ("preparing_environment", "env_reused", "creating_environment", "deps_unchanged", "upgrading_pip",
                       "installing_dependencies", "starting_application"):
            self.assertIn(f"echo M2G_STAGE={marker}", script)
        # pip is upgraded before the requirements are installed, each announced right before it starts
        self.assertLess(script.index("echo M2G_STAGE=upgrading_pip"), script.index("-m pip install --upgrade pip"))
        self.assertLess(script.index("-m pip install --upgrade pip"), script.index("echo M2G_STAGE=installing_dependencies"))
        self.assertLess(script.index("echo M2G_STAGE=installing_dependencies"), script.index("-m pip install -r"))
        # the skip marker is printed only on the unchanged-requirements path (before it jumps to start_app)
        skip = script.index("echo M2G_STAGE=deps_unchanged")
        self.assertLess(skip, _label_pos(script, "install_deps"))

    def test_launcher_announces_validation_before_checking_files_and_preparation_after(self):
        script = windows_launcher("example__model")
        validating = script.index("echo M2G_STAGE=validating_app")
        files_checked = script.index('if not exist "%APP_FILE%" goto :missing_files')
        preparing = script.index("echo M2G_STAGE=preparing_environment")
        self.assertLess(validating, files_checked)
        self.assertLess(script.index('if not exist "%REQ_FILE%" goto :missing_files'), preparing)
        self.assertLess(preparing, script.index('if not exist "%VENV_PY%" goto :find_python'))

    def test_launcher_checks_every_package_file_before_preparing(self):
        script = windows_launcher("example__model")
        preparing = script.index("echo M2G_STAGE=preparing_environment")
        for var in ("APP_FILE", "LOADER_FILE", "REQ_FILE"):
            self.assertLess(script.index(f'if not exist "%{var}%" goto :missing_files'), preparing)
        self.assertIn('set "LOADER_FILE=%APP_DIR%\\loader.py"', script)
        self.assertIn("loader.py", script[script.index("\n:missing_files\n"):])  # the error names the file that can be missing

    def test_reuse_and_skip_markers_are_only_on_their_own_paths(self):
        script = windows_launcher("example__model")
        reused = script.index("echo M2G_STAGE=env_reused")
        self.assertGreater(reused, script.index('if not exist "%VENV_PY%" goto :find_python'))
        self.assertLess(reused, _label_pos(script, "find_python"))  # only the path that found an existing .venv python
        self.assertEqual(script.count("echo M2G_STAGE=env_reused"), 1)
        self.assertEqual(script.count("echo M2G_STAGE=creating_environment"), 1)
        self.assertGreater(script.index("echo M2G_STAGE=creating_environment"), _label_pos(script, "create_venv"))
        unchanged = script.index("echo M2G_STAGE=deps_unchanged")
        self.assertLess(unchanged, _label_pos(script, "install_deps"))
        self.assertLess(script.index("echo M2G_STAGE=starting_application"), script.index('"%VENV_PY%" -u "%APP_FILE%"'))

    def test_launcher_installs_requirements_into_the_app_environment_only(self):
        script = windows_launcher("example__model")
        self.assertIn('"%VENV_PY%" -m pip install --upgrade pip', script)
        self.assertIn('"%VENV_PY%" -m pip install -r "%REQ_FILE%"', script)
        self.assertNotIn("\npip install", script)  # never the pip of whatever Python happens to be on PATH
        self.assertNotIn("\npython -m pip", script)

    def test_launcher_skips_the_install_when_requirements_are_unchanged(self):
        script = windows_launcher("example__model")
        self.assertIn("certutil -hashfile", script)  # robust for folders with spaces, unlike a quoted PowerShell path
        self.assertIn("m2g_requirements.sha256", script)
        self.assertIn("Dependencies are unchanged; reusing the existing environment.", script)
        # the hash is stored only after pip succeeded
        self.assertLess(script.index('"%VENV_PY%" -m pip install -r'), script.index('>"%HASH_FILE%" echo %REQ_HASH%'))

    def test_launcher_starts_app_with_the_venv_python_and_keeps_the_exit_code(self):
        script = windows_launcher("example__model")
        self.assertIn('"%VENV_PY%" -u "%APP_FILE%"', script)
        self.assertIn('set "RC=%ERRORLEVEL%"', script)
        self.assertTrue(script.rstrip().endswith("exit /b %RC%"))
        self.assertIn("if not defined M2G_NO_PAUSE pause", script)  # a managed launch never waits for a key press

    def test_launcher_has_a_distinct_exit_code_and_message_for_each_failure(self):
        script = windows_launcher("example__model")
        codes = {
            local_script.EXIT_MISSING_FILES: ":missing_files",
            local_script.EXIT_PYTHON_MISSING: "Python 3.12 was not found",
            local_script.EXIT_VENV_FAILED: ":venv_failed",
            local_script.EXIT_INSTALL_FAILED: ":install_failed",
            local_script.EXIT_ACTIVATE_FAILED: ":activate_failed",
        }
        self.assertEqual(len(set(codes)), 5)
        for code, marker in codes.items():
            self.assertIn(f'set "RC={code}"', script)
            self.assertIn(marker, script)

    def test_launcher_reports_install_and_startup_failures(self):
        script = windows_launcher("example__model")
        self.assertIn(":install_failed", script)
        self.assertIn("ERROR: app.py exited with code", script)
        self.assertIn("pause", script)


@unittest.skipUnless(os.name == "nt", "executes the real run_local.bat with cmd.exe")
class TestWindowsLauncherExecution(unittest.TestCase):
    """Runs the real BAT in a folder whose name contains spaces, with PATH reduced to System32 so no Python can be found."""

    def _run(self, app_dir: Path):
        bat = app_dir / "run_local.bat"
        bat.write_bytes(windows_launcher("example__model").replace("\n", "\r\n").encode("utf-8"))
        root = os.environ.get("SystemRoot", r"C:\Windows")
        env = {"SystemRoot": root, "PATH": os.path.join(root, "System32"), "M2G_NO_PAUSE": "1"}
        return subprocess.run(["cmd.exe", "/d", "/c", "call", str(bat)], cwd=str(app_dir), env=env,
                              capture_output=True, text=True, timeout=120)

    def test_missing_app_files_exit_10(self):
        with tempfile.TemporaryDirectory(prefix="m2g test ") as td:
            result = self._run(Path(td))
            self.assertEqual(result.returncode, local_script.EXIT_MISSING_FILES, result.stdout + result.stderr)
            self.assertIn("M2G_STAGE=validating_app", result.stdout)
            self.assertNotIn("M2G_STAGE=preparing_environment", result.stdout)  # the files were not valid: no preparation

    def test_missing_loader_py_exits_10_before_any_preparation(self):
        with tempfile.TemporaryDirectory(prefix="m2g test ") as td:
            app_dir = Path(td)
            (app_dir / "app.py").write_text("print('x')\n", encoding="utf-8")
            (app_dir / "requirements.txt").write_text("gradio\n", encoding="utf-8")
            result = self._run(app_dir)
            self.assertEqual(result.returncode, local_script.EXIT_MISSING_FILES, result.stdout + result.stderr)
            self.assertNotIn("M2G_STAGE=preparing_environment", result.stdout)
            self.assertFalse((app_dir / ".venv").exists())

    def test_stage_markers_are_printed_in_order_before_python_is_missing(self):
        with tempfile.TemporaryDirectory(prefix="m2g test ") as td:
            app_dir = Path(td)
            (app_dir / "app.py").write_text("print('x')\n", encoding="utf-8")
            (app_dir / "loader.py").write_text("print('loader')\n", encoding="utf-8")
            (app_dir / "requirements.txt").write_text("gradio\n", encoding="utf-8")
            result = self._run(app_dir)
            markers = [line.strip() for line in result.stdout.splitlines() if line.startswith("M2G_STAGE=")]
            self.assertEqual(markers, ["M2G_STAGE=validating_app", "M2G_STAGE=preparing_environment"], result.stdout)

    def test_missing_python_312_exits_11_without_creating_an_environment(self):
        with tempfile.TemporaryDirectory(prefix="m2g test ") as td:
            app_dir = Path(td)
            (app_dir / "app.py").write_text("print('x')\n", encoding="utf-8")
            (app_dir / "loader.py").write_text("print('loader')\n", encoding="utf-8")
            (app_dir / "requirements.txt").write_text("gradio\n", encoding="utf-8")
            result = self._run(app_dir)
            self.assertEqual(result.returncode, local_script.EXIT_PYTHON_MISSING, result.stdout + result.stderr)
            self.assertIn("Python 3.12 was not found", result.stdout)
            self.assertFalse((app_dir / ".venv").exists())


if __name__ == "__main__":
    unittest.main()
