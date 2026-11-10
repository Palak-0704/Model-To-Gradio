"""Regression tests for running a generated app locally: security gate, process supervision and the /api/local/* endpoints.

No real process is started: ``subprocess.Popen``/``subprocess.run`` are replaced by a scripted fake, and the code validators are
stubbed (they are covered by their own tests). Nothing is downloaded or installed.
"""
import json
import os
import queue
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from m2g import local_runner, web_api
from m2g.local_hardware import HardwareReport
from m2g.local_progress import STUCK_AFTER_S, ProgressTracker
from m2g.local_runner import LocalRunError, REQUIRED_ARTIFACTS
from m2g.local_script import launcher_filename, windows_launcher
from m2g.local_workflow import (
    CREATING_ENVIRONMENT, FAILED, INSTALLING_DEPENDENCIES, PREPARING, RUNNING, STARTING, STOPPED,
    AlreadyRunningError, LocalLauncher, LocalSnapshot,
)
from m2g.validate import ValidationError

SLUG = "owner__model"
BAT = launcher_filename(SLUG)  # run_<model folder>.bat: every generated package has its own launcher
URL = "http://127.0.0.1:7860"
STAGE_LINES = [
    "M2G_STAGE=preparing_environment",
    "M2G_STAGE=creating_environment",
    "M2G_STAGE=installing_dependencies",
    "M2G_STAGE=starting_application",
    f"* Running on local URL:  {URL}",
]
PRE_URL_LINES = [
    "M2G_STAGE=validating_app",
    "M2G_STAGE=preparing_environment",
    "M2G_STAGE=creating_environment",
    "M2G_STAGE=upgrading_pip",
    "M2G_STAGE=installing_dependencies",
    "M2G_STAGE=starting_application",
]
FULL_STAGE_LINES = PRE_URL_LINES + [f"* Running on local URL:  {URL}"]
PROGRESS_KEYS = ("status", "percentage", "stage", "completed_steps", "total_steps", "elapsed_seconds", "last_activity",
                 "message", "warning", "error")


def wait_for(predicate, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.01)
    return False


class FakeProc:
    """Scripted stand-in for the cmd.exe process: emits lines, stays alive until released, then exits with ``exit_code``."""

    def __init__(self, lines=(), exit_code=0, hold=True, pid=4242):
        self.pid = pid
        self.returncode = None
        self._exit = exit_code
        self._q = queue.Queue()
        self._done = threading.Event()
        self._released = False
        for line in lines:
            self._q.put(line + "\n")
        self.stdout = self._read()
        if not hold:
            self.release()

    def _read(self):
        while True:
            item = self._q.get(timeout=30)
            if item is None:
                break
            yield item
        self.returncode = self._exit
        self._done.set()

    def emit(self, line):
        self._q.put(line + "\n")

    def release(self):
        if not self._released:
            self._released = True
            self._q.put(None)

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        if not self._done.wait(timeout or 30):
            raise subprocess.TimeoutExpired("fake", timeout)
        return self._exit

    def kill(self):
        self.release()


def make_app(root: Path, slug: str = SLUG) -> Path:
    app_dir = root / slug
    app_dir.mkdir(parents=True)
    (app_dir / "app.py").write_text("from loader import get_model\n\nprint('app')\n", encoding="utf-8")  # app.py must import loader.py
    (app_dir / "loader.py").write_text("def get_model():\n    return None\n", encoding="utf-8")
    (app_dir / "requirements.txt").write_text("gradio\n", encoding="utf-8")
    (app_dir / "analysis.json").write_text("{}\n", encoding="utf-8")
    (app_dir / "colab_notebook.ipynb").write_text("{}\n", encoding="utf-8")
    return app_dir


class RunnerCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve() / "output"
        self.root.mkdir()
        self.app_dir = make_app(self.root)
        self.proc = None

        for name in ("validate_app_code", "validate_requirements_text"):
            patcher = mock.patch.object(local_runner, name)
            self.validators = getattr(self, "validators", {})
            self.validators[name] = patcher.start()
            self.addCleanup(patcher.stop)
        popen = mock.patch("m2g.local_workflow.subprocess.Popen")
        self.popen = popen.start()
        self.addCleanup(popen.stop)
        # The hardware preflight would probe the real host; these tests are about process supervision, so it reports nothing here.
        # Tests that exercise the preflight (test_local_hardware_integration.py) override ``self.hardware_check``.
        hardware = mock.patch.object(local_runner, "check_local_hardware", return_value=HardwareReport())
        self.hardware_check = hardware.start()
        self.addCleanup(hardware.stop)
        run = mock.patch("m2g.local_workflow.subprocess.run", side_effect=self._taskkill)
        self.run = run.start()
        self.addCleanup(run.stop)

    def _taskkill(self, cmd, **kwargs):
        if self.proc is not None:
            self.proc.release()  # killing the process tree ends the fake process
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def script_process(self, lines=STAGE_LINES, exit_code=0, hold=True):
        self.proc = FakeProc(lines, exit_code, hold)
        self.popen.return_value = self.proc
        return self.proc

    def new_launcher(self, verify=True, progress=None):
        launcher = LocalLauncher(python="python", sleep=lambda _s: None, progress=progress)
        launcher._is_windows = lambda: True
        launcher._verify_url = lambda url: verify
        self.addCleanup(launcher.stop)
        return launcher

    def start(self, launcher, slug=SLUG):
        return local_runner.start_local_app(launcher, self.root, slug, None, is_windows=True)


class TestBatchPreparation(RunnerCase):
    def test_backend_writes_its_own_launcher_with_windows_line_endings(self):
        bat = local_runner.prepare_launcher(self.app_dir)
        data = bat.read_bytes()
        self.assertEqual(bat, self.app_dir / BAT)
        self.assertEqual(data, windows_launcher(SLUG).replace("\n", "\r\n").encode("utf-8"))
        self.assertEqual(data.count(b"\n"), data.count(b"\r\n"))  # no bare LF: cmd.exe can miss labels with LF-only files

    def test_a_modified_launcher_is_replaced_by_the_template_before_running(self):
        (self.app_dir / BAT).write_text("@echo off\ncalc.exe\n", encoding="utf-8")
        self.script_process()
        self.start(self.new_launcher())
        self.assertTrue(wait_for(lambda: self.popen.called))  # the launch runs on its own thread
        self.assertNotIn(b"calc.exe", (self.app_dir / BAT).read_bytes())
        self.assertEqual(self.popen.call_args.args[0][-1], str(self.app_dir / BAT))

    def test_an_unchanged_launcher_is_not_rewritten(self):
        bat = local_runner.prepare_launcher(self.app_dir)
        before = bat.stat().st_mtime_ns
        local_runner.prepare_launcher(self.app_dir)
        self.assertEqual(bat.stat().st_mtime_ns, before)


class TestProcessSupervision(RunnerCase):
    def test_successful_startup_reports_ready_only_with_a_live_url(self):
        self.script_process()
        launcher = self.new_launcher()
        self.start(launcher)
        self.assertTrue(wait_for(lambda: launcher.snapshot.state == RUNNING))
        payload = local_runner.snapshot_payload(SLUG, launcher)
        self.assertEqual(payload["status"], "running")
        self.assertEqual(payload["stage_label"], "App ready")
        self.assertEqual(payload["url"], URL)
        self.assertEqual(payload["pid"], 4242)
        self.assertTrue(payload["running"])
        args, kwargs = self.popen.call_args
        self.assertEqual(args[0], ["cmd.exe", "/d", "/c", "call", str(self.app_dir / BAT)])
        self.assertEqual(kwargs["cwd"], str(self.app_dir))
        self.assertEqual(kwargs["env"]["M2G_NO_PAUSE"], "1")
        self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
        self.assertEqual(Path(launcher.snapshot.environment), self.app_dir / ".venv")

    def test_not_ready_when_the_announced_server_does_not_respond(self):
        self.script_process()
        launcher = self.new_launcher(verify=False)
        self.start(launcher)
        self.assertTrue(wait_for(lambda: launcher.snapshot.state == FAILED))
        payload = local_runner.snapshot_payload(SLUG, launcher)
        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["url"], "")  # an announced-but-unverified URL is never exposed as ready
        self.assertIn("did not respond", payload["error"])
        self.assertTrue(wait_for(lambda: any(c.args[0][:2] == ["taskkill", "/PID"] for c in self.run.call_args_list)),
                        "the half-started process tree must be killed, not left running")

    def test_url_is_hidden_until_the_state_is_running(self):
        snap = LocalSnapshot(state=STARTING, url=URL, pid=1)
        self.assertEqual(local_runner.describe_snapshot(snap, SLUG)["url"], "")
        snap.state = RUNNING
        self.assertEqual(local_runner.describe_snapshot(snap, SLUG)["url"], URL)

    def test_startup_stages_are_reported(self):
        expectations = {
            "M2G_STAGE=preparing_environment": (PREPARING, "Preparing environment"),
            "M2G_STAGE=creating_environment": (CREATING_ENVIRONMENT, "Creating virtual environment"),
            "M2G_STAGE=installing_dependencies": (INSTALLING_DEPENDENCIES, "Installing dependencies"),
            "M2G_STAGE=starting_application": (STARTING, "Starting Gradio app"),
        }
        for line, (state, label) in expectations.items():
            launcher = LocalLauncher(python="python")
            launcher._read_process_output(FakeProc([line], hold=False))
            self.assertEqual(launcher.snapshot.state, state, line)
            self.assertEqual(local_runner.describe_snapshot(launcher.snapshot)["stage_label"], label)
            self.assertEqual(local_runner.describe_snapshot(launcher.snapshot)["status"], "starting")
        self.assertEqual(local_runner.describe_snapshot(LocalSnapshot(state=STOPPED))["stage_label"], "Stopped")
        self.assertEqual(local_runner.describe_snapshot(LocalSnapshot(state=FAILED))["stage_label"], "Failed")

    def test_startup_failure_reports_the_launcher_exit_code(self):
        self.script_process(["M2G_STAGE=installing_dependencies", "ERROR: pip failed"], exit_code=13, hold=False)
        launcher = self.new_launcher()
        self.start(launcher)
        self.assertTrue(wait_for(lambda: launcher.snapshot.state == FAILED))
        payload = local_runner.snapshot_payload(SLUG, launcher)
        self.assertEqual(payload["exit_code"], 13)
        self.assertIn("Dependency installation failed", payload["error"])
        self.assertEqual(payload["url"], "")
        self.assertIn("ERROR: pip failed", payload["logs"])
        self.assertTrue(wait_for(lambda: not launcher.active))

    def test_missing_python_312_is_explained(self):
        self.script_process(["M2G_STAGE=preparing_environment", "ERROR: Python 3.12 was not found."], exit_code=11, hold=False)
        launcher = self.new_launcher()
        self.start(launcher)
        self.assertTrue(wait_for(lambda: launcher.snapshot.state == FAILED))
        self.assertEqual(launcher.snapshot.exit_code, 11)
        self.assertIn("Python 3.12 was not found", launcher.snapshot.error)

    def test_logs_are_available_while_running_and_incremental(self):
        proc = self.script_process()
        launcher = self.new_launcher()
        self.start(launcher)
        self.assertTrue(wait_for(lambda: launcher.snapshot.state == RUNNING))
        first = local_runner.logs_payload(SLUG, launcher, 0)
        self.assertTrue(any("Running on local URL" in line for line in first["lines"]))
        self.assertEqual(first["status"], "running")
        self.assertEqual(local_runner.logs_payload(SLUG, launcher, first["next"])["lines"], [])
        proc.emit("user clicked Submit")
        self.assertTrue(wait_for(lambda: local_runner.logs_payload(SLUG, launcher, first["next"])["lines"] == ["user clicked Submit"]))

    def test_secrets_never_appear_in_logs(self):
        self.script_process(STAGE_LINES + ["token hf_" + "a" * 30])
        launcher = self.new_launcher()
        self.start(launcher)
        self.assertTrue(wait_for(lambda: "[REDACTED]" in "\n".join(launcher.read_logs(0)[0])))
        self.assertNotIn("hf_" + "a" * 30, "\n".join(launcher.read_logs(0)[0]))

    def test_unexpected_termination_is_reported_as_a_failure(self):
        proc = self.script_process()
        launcher = self.new_launcher()
        self.start(launcher)
        self.assertTrue(wait_for(lambda: launcher.snapshot.state == RUNNING))
        proc._exit = 3
        proc.release()  # the app dies on its own; nobody asked it to stop
        self.assertTrue(wait_for(lambda: launcher.snapshot.state == FAILED))
        payload = local_runner.snapshot_payload(SLUG, launcher)
        self.assertEqual(payload["exit_code"], 3)
        self.assertIn("unexpectedly", payload["error"])
        self.assertEqual(payload["url"], "")
        self.assertTrue(wait_for(lambda: not launcher.active))

    def test_clean_exit_is_stopped_not_failed(self):
        proc = self.script_process()
        launcher = self.new_launcher()
        self.start(launcher)
        self.assertTrue(wait_for(lambda: launcher.snapshot.state == RUNNING))
        proc.release()
        self.assertTrue(wait_for(lambda: launcher.snapshot.state == STOPPED))
        self.assertEqual(launcher.snapshot.exit_code, 0)
        self.assertEqual(launcher.snapshot.error, "")

    def test_stop_kills_the_whole_process_tree_and_allows_a_relaunch(self):
        self.script_process()
        launcher = self.new_launcher()
        self.start(launcher)
        self.assertTrue(wait_for(lambda: launcher.snapshot.state == RUNNING))
        launcher.stop()
        self.run.assert_any_call(["taskkill", "/PID", "4242", "/T", "/F"], capture_output=True, text=True, timeout=20)
        self.assertEqual(launcher.snapshot.state, STOPPED)
        self.assertFalse(launcher.active)
        self.assertEqual(local_runner.snapshot_payload(SLUG, launcher)["status"], "stopped")
        self.script_process()  # a stopped app can be started again
        self.start(launcher)
        self.assertTrue(wait_for(lambda: launcher.snapshot.state == RUNNING))

    def test_duplicate_launch_is_prevented(self):
        self.script_process()
        launcher = self.new_launcher()
        self.start(launcher)
        self.assertTrue(wait_for(lambda: launcher.snapshot.state == RUNNING))
        with self.assertRaises(LocalRunError) as ctx:
            self.start(launcher)
        self.assertEqual(ctx.exception.status, 409)
        with self.assertRaises(AlreadyRunningError):
            launcher.launch_batch(self.app_dir / BAT, self.app_dir)
        self.assertEqual(self.popen.call_count, 1)

    def test_other_apps_run_independently(self):
        make_app(self.root, "other__model")
        self.script_process()
        first = self.new_launcher()
        self.start(first)
        self.assertTrue(wait_for(lambda: first.snapshot.state == RUNNING))
        second = self.new_launcher()
        self.script_process()
        self.start(second, "other__model")
        self.assertTrue(wait_for(lambda: second.snapshot.state == RUNNING))


class TestDeploymentProgress(RunnerCase):
    """The tracker is driven by the real launch supervision (markers, process state, health check), never by elapsed time."""

    def progress(self, launcher):
        return local_runner.snapshot_payload(SLUG, launcher)["progress"]

    def test_100_percent_only_after_the_health_check_passes(self):
        gate = threading.Event()
        self.script_process(FULL_STAGE_LINES)
        launcher = self.new_launcher()
        launcher._verify_url = lambda url: gate.wait(10)  # the health check stays pending until the test releases it
        self.addCleanup(gate.set)
        self.start(launcher)
        self.assertTrue(wait_for(lambda: self.progress(launcher)["stage"] == "checking_url"))
        held = self.progress(launcher)
        self.assertEqual((held["status"], held["percentage"], held["completed_steps"], held["total_steps"]),
                         ("running", 75, 6, 8))
        self.assertEqual(launcher.snapshot.state, STARTING)  # URL announced but not verified: not ready, not 100%
        gate.set()
        self.assertTrue(wait_for(lambda: launcher.snapshot.state == RUNNING))
        done = self.progress(launcher)
        self.assertEqual((done["status"], done["percentage"], done["completed_steps"], done["stage"]),
                         ("completed", 100, 8, "complete"))

    def test_an_unanswered_health_check_is_a_failure_and_never_100_percent(self):
        self.script_process(FULL_STAGE_LINES)
        launcher = self.new_launcher(verify=False)
        self.start(launcher)
        self.assertTrue(wait_for(lambda: launcher.snapshot.state == FAILED))
        progress = self.progress(launcher)
        self.assertEqual(progress["status"], "failed")
        self.assertEqual(progress["failed_step"]["id"], "checking_url")
        self.assertEqual(progress["percentage"], 75)
        self.assertIn("did not respond", progress["error"])

    def test_launcher_failure_is_recorded_on_the_step_that_was_running(self):
        cases = {
            13: (PRE_URL_LINES[:5], "installing", 50),
            11: (PRE_URL_LINES[:2], "preparing", 12),
            10: (PRE_URL_LINES[:1], "validating", 0),
        }
        for code, (lines, step, percent) in cases.items():
            with self.subTest(exit_code=code):
                self.script_process(lines + ["ERROR: it failed"], exit_code=code, hold=False)
                launcher = self.new_launcher()
                self.start(launcher)
                self.assertTrue(wait_for(lambda: launcher.snapshot.state == FAILED))
                progress = self.progress(launcher)
                self.assertEqual((progress["status"], progress["failed_step"]["id"], progress["percentage"]),
                                 ("failed", step, percent))
                self.assertTrue(progress["error"])
                self.assertTrue(wait_for(lambda: not launcher.active))

    def test_reused_environment_is_reported_as_skipped_steps(self):
        lines = ["M2G_STAGE=validating_app", "M2G_STAGE=preparing_environment", "M2G_STAGE=env_reused",
                 "M2G_STAGE=deps_unchanged", "M2G_STAGE=starting_application", f"* Running on local URL:  {URL}"]
        self.script_process(lines)
        launcher = self.new_launcher()
        self.start(launcher)
        self.assertTrue(wait_for(lambda: launcher.snapshot.state == RUNNING))
        steps = {s["id"]: s["state"] for s in self.progress(launcher)["steps"]}
        self.assertEqual((steps["creating_env"], steps["upgrading_pip"], steps["installing"]), ("skipped",) * 3)
        self.assertEqual(self.progress(launcher)["percentage"], 100)

    def test_a_requested_stop_is_reported_as_stopped_with_the_progress_reached(self):
        self.script_process(PRE_URL_LINES[:5])
        launcher = self.new_launcher()
        self.start(launcher)
        self.assertTrue(wait_for(lambda: self.progress(launcher)["stage"] == "installing"))
        launcher.stop()
        progress = self.progress(launcher)
        self.assertEqual((progress["status"], progress["percentage"], progress["error"]), ("stopped", 50, ""))
        self.assertIsNone(progress["failed_step"])

    def test_stopping_a_running_app_keeps_100_percent_and_is_not_a_failure(self):
        self.script_process(FULL_STAGE_LINES)
        launcher = self.new_launcher()
        self.start(launcher)
        self.assertTrue(wait_for(lambda: launcher.snapshot.state == RUNNING))
        launcher.stop()
        progress = self.progress(launcher)
        self.assertEqual((progress["status"], progress["percentage"]), ("stopped", 100))

    def test_a_crash_after_deployment_is_a_runtime_failure(self):
        proc = self.script_process(FULL_STAGE_LINES)
        launcher = self.new_launcher()
        self.start(launcher)
        self.assertTrue(wait_for(lambda: launcher.snapshot.state == RUNNING))
        proc._exit = 3
        proc.release()
        self.assertTrue(wait_for(lambda: launcher.snapshot.state == FAILED))
        progress = self.progress(launcher)
        self.assertEqual((progress["status"], progress["failed_step"]["id"], progress["percentage"]), ("failed", "running", 100))

    def test_a_clean_exit_is_stopped_not_failed(self):
        proc = self.script_process(FULL_STAGE_LINES)
        launcher = self.new_launcher()
        self.start(launcher)
        self.assertTrue(wait_for(lambda: launcher.snapshot.state == RUNNING))
        proc.release()
        self.assertTrue(wait_for(lambda: self.progress(launcher)["status"] == "stopped"))
        self.assertEqual(self.progress(launcher)["error"], "")

    def test_silence_of_a_live_launcher_is_possibly_stuck_never_failed(self):
        class Clock:
            now = 5000.0

            def __call__(self):
                return self.now

        clock = Clock()
        self.script_process(PRE_URL_LINES[:5])  # installing dependencies, process alive, no further output
        launcher = self.new_launcher(progress=ProgressTracker(clock=clock))
        self.start(launcher)
        self.assertTrue(wait_for(lambda: self.progress(launcher)["stage"] == "installing"))
        percent = self.progress(launcher)["percentage"]
        clock.now += STUCK_AFTER_S - 1
        self.assertEqual(self.progress(launcher)["status"], "running")
        clock.now += 1
        stuck = self.progress(launcher)
        self.assertEqual((stuck["status"], stuck["percentage"], stuck["error"]), ("possibly_stuck", percent, ""))
        self.assertTrue(stuck["warning"])
        clock.now += 3600
        self.assertEqual(self.progress(launcher)["status"], "possibly_stuck")  # still alive: time never turns it into a failure
        self.assertEqual(self.progress(launcher)["percentage"], percent)
        self.assertEqual(launcher.snapshot.state, INSTALLING_DEPENDENCIES)

    def test_a_new_launch_starts_from_zero(self):
        self.script_process(PRE_URL_LINES[:3], exit_code=13, hold=False)
        launcher = self.new_launcher()
        self.start(launcher)
        self.assertTrue(wait_for(lambda: launcher.snapshot.state == FAILED))
        self.assertTrue(wait_for(lambda: not launcher.active))
        self.script_process(FULL_STAGE_LINES)
        self.start(launcher)
        self.assertTrue(wait_for(lambda: launcher.snapshot.state == RUNNING))
        progress = self.progress(launcher)
        self.assertEqual((progress["status"], progress["error"], progress["failed_step"]), ("completed", "", None))


class TestExecutionSecurity(RunnerCase):
    def test_valid_slug_resolves_to_the_app_directory(self):
        self.assertEqual(local_runner.resolve_app_dir(self.root, SLUG), self.app_dir)

    def test_path_traversal_and_unsafe_identifiers_are_rejected(self):
        bad = ["..", ".", "../owner__model", "..\\owner__model", "a/b", "a\\b", ".hidden", "_x", "a..b", "", " ",
               "C:\\Windows", "/etc", "%2e%2e", "owner__model/..", "x" * 121, None, 5]
        for slug in bad:
            with self.subTest(slug=slug), self.assertRaises(LocalRunError) as ctx:
                local_runner.resolve_app_dir(self.root, slug)
            self.assertEqual(ctx.exception.status, 400)
        self.popen.assert_not_called()

    def test_unknown_or_non_directory_app_is_rejected(self):
        with self.assertRaises(LocalRunError) as ctx:
            local_runner.resolve_app_dir(self.root, "does__not-exist")
        self.assertEqual(ctx.exception.status, 404)
        (self.root / "just-a-file").write_text("x", encoding="utf-8")
        with self.assertRaises(LocalRunError) as ctx:
            local_runner.resolve_app_dir(self.root, "just-a-file")
        self.assertEqual(ctx.exception.status, 404)

    def test_a_link_pointing_outside_the_output_directory_is_rejected(self):
        outside = self.root.parent / "outside"
        make_app(outside.parent, "outside")
        link = self.root / "linked"
        try:
            os.symlink(outside, link, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks are not available here")
        with self.assertRaises(LocalRunError) as ctx:
            local_runner.resolve_app_dir(self.root, "linked")
        self.assertEqual(ctx.exception.status, 400)

    def test_only_complete_generated_apps_run(self):
        for missing in REQUIRED_ARTIFACTS:
            app = make_app(self.root, "incomplete")
            (app / missing).unlink()
            with self.subTest(missing=missing), self.assertRaises(LocalRunError) as ctx:
                self.start(self.new_launcher(), "incomplete")
            self.assertEqual(ctx.exception.status, 400)
            self.assertIn(missing, ctx.exception.message)
            for child in app.iterdir():
                child.unlink()
            app.rmdir()
        self.popen.assert_not_called()

    def test_an_app_that_fails_validation_is_not_started(self):
        self.validators["validate_app_code"].side_effect = ValidationError("forbidden call: os.system")
        with self.assertRaises(LocalRunError) as ctx:
            self.start(self.new_launcher())
        self.assertEqual(ctx.exception.status, 422)
        self.assertIn("failed validation", ctx.exception.message)
        self.popen.assert_not_called()
        self.assertFalse((self.app_dir / BAT).exists())  # nothing is prepared for an invalid app

    def test_an_app_that_does_not_import_its_loader_is_not_started(self):
        (self.app_dir / "app.py").write_text("print('standalone')\n", encoding="utf-8")
        with self.assertRaises(LocalRunError) as ctx:
            self.start(self.new_launcher())
        self.assertEqual(ctx.exception.status, 422)
        self.assertIn("does not import it", ctx.exception.message)
        self.popen.assert_not_called()

    def test_invalid_requirements_are_not_started(self):
        self.validators["validate_requirements_text"].side_effect = ValidationError("unsafe requirement")
        with self.assertRaises(LocalRunError) as ctx:
            self.start(self.new_launcher())
        self.assertEqual(ctx.exception.status, 422)
        self.popen.assert_not_called()

    def test_corrupt_analysis_json_is_rejected(self):
        (self.app_dir / "analysis.json").write_text("{not json", encoding="utf-8")
        with self.assertRaises(LocalRunError) as ctx:
            self.start(self.new_launcher())
        self.assertEqual(ctx.exception.status, 400)
        self.popen.assert_not_called()


class TestLocalApi(RunnerCase):
    def setUp(self):
        super().setUp()
        self.launcher = self.new_launcher()
        real_start = local_runner.start_local_app
        patches = [
            mock.patch.object(web_api, "_get_settings", return_value=SimpleNamespace(output_dir=self.root)),
            mock.patch.object(web_api, "_local_launcher", return_value=self.launcher),
            mock.patch.object(local_runner, "start_local_app",
                              lambda launcher, root, slug, settings=None: real_start(launcher, root, slug, settings, is_windows=True)),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.server = web_api.WebApiServer("127.0.0.1", 0)
        self.server.start()
        self.addCleanup(self.server.stop)
        self.port = self.server._server.server_address[1]

    def call(self, method, path, body=None):
        data = json.dumps(body or {}).encode("utf-8") if method == "POST" else None
        request = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data, method=method,
                                         headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_idle_status_before_any_launch(self):
        status, body = self.call("GET", f"/api/local/{SLUG}/status")
        self.assertEqual(status, 200)
        self.assertEqual((body["app"], body["status"], body["state"], body["url"], body["pid"]), (SLUG, "idle", "idle", "", None))

    def test_start_status_logs_and_stop(self):
        proc = self.script_process()
        status, started = self.call("POST", f"/api/local/{SLUG}/start")
        self.assertEqual(status, 202)
        for key in ("app", "state", "status", "stage", "stage_label", "pid", "url", "error", "exit_code", "logs", "log_next", "logs_url"):
            self.assertIn(key, started)
        self.assertEqual(started["app"], SLUG)

        self.assertTrue(wait_for(lambda: self.call("GET", f"/api/local/{SLUG}/status")[1]["status"] == "running"))
        _, running = self.call("GET", f"/api/local/{SLUG}/status")
        self.assertEqual((running["url"], running["pid"], running["stage_label"], running["state"]), (URL, 4242, "App ready", "running"))
        self.assertTrue(any("Running on local URL" in line for line in running["logs"]))

        status, logs = self.call("GET", f"/api/local/{SLUG}/logs?since=0")
        self.assertEqual(status, 200)
        self.assertTrue(any("Running on local URL" in line for line in logs["lines"]))
        proc.emit("new log line")
        self.assertTrue(wait_for(lambda: self.call("GET", f"/api/local/{SLUG}/logs?since={logs['next']}")[1]["lines"] == ["new log line"]))

        status, duplicate = self.call("POST", f"/api/local/{SLUG}/start")
        self.assertEqual(status, 409)
        self.assertIn("already", duplicate["error"])

        status, stopped = self.call("POST", f"/api/local/{SLUG}/stop")
        self.assertEqual(status, 200)
        self.assertEqual((stopped["status"], stopped["state"], stopped["url"], stopped["running"]), ("stopped", "stopped", "", False))
        self.run.assert_any_call(["taskkill", "/PID", "4242", "/T", "/F"], capture_output=True, text=True, timeout=20)

    def test_failed_start_surfaces_error_and_exit_code(self):
        self.script_process(["M2G_STAGE=installing_dependencies", "ERROR: pip failed"], exit_code=13, hold=False)
        self.assertEqual(self.call("POST", f"/api/local/{SLUG}/start")[0], 202)
        self.assertTrue(wait_for(lambda: self.call("GET", f"/api/local/{SLUG}/status")[1]["status"] == "failed"))
        _, body = self.call("GET", f"/api/local/{SLUG}/status")
        self.assertEqual(body["exit_code"], 13)
        self.assertIn("Dependency installation failed", body["error"])
        self.assertEqual(body["url"], "")
        self.assertEqual(body["stage_label"], "Failed")

    def test_unsafe_identifiers_are_rejected_on_every_endpoint(self):
        for slug in ("..", "%2e%2e", ".hidden", "a..b"):
            for method, suffix in (("POST", "start"), ("GET", "status"), ("GET", "logs"), ("POST", "stop")):
                with self.subTest(slug=slug, endpoint=suffix):
                    status, body = self.call(method, f"/api/local/{slug}/{suffix}")
                    self.assertEqual(status, 400)
                    self.assertIn("error", body)
        self.popen.assert_not_called()

    def test_unknown_and_incomplete_apps_are_refused(self):
        self.assertEqual(self.call("POST", "/api/local/nope__nothing/start")[0], 404)
        (self.app_dir / "requirements.txt").unlink()
        status, body = self.call("POST", f"/api/local/{SLUG}/start")
        self.assertEqual(status, 400)
        self.assertIn("requirements.txt", body["error"])
        self.popen.assert_not_called()

    def test_logs_since_must_be_an_integer(self):
        self.assertEqual(self.call("GET", f"/api/local/{SLUG}/logs?since=abc")[0], 400)
        self.assertEqual(self.call("GET", f"/api/local/{SLUG}/logs?since=-5")[0], 200)

    def test_idle_status_has_an_idle_progress_object(self):
        _, body = self.call("GET", f"/api/local/{SLUG}/status")
        progress = body["progress"]
        for key in PROGRESS_KEYS:
            self.assertIn(key, progress)
        self.assertEqual((progress["status"], progress["percentage"], progress["completed_steps"], progress["total_steps"],
                          progress["stage"]), ("idle", 0, 0, 8, ""))

    def test_status_exposes_live_progress_next_to_the_existing_fields(self):
        gate = threading.Event()
        self.script_process(FULL_STAGE_LINES)
        self.launcher._verify_url = lambda url: gate.wait(10)
        self.addCleanup(gate.set)
        self.assertEqual(self.call("POST", f"/api/local/{SLUG}/start")[0], 202)
        self.assertTrue(wait_for(lambda: self.call("GET", f"/api/local/{SLUG}/status")[1]["progress"]["stage"] == "checking_url"))
        _, body = self.call("GET", f"/api/local/{SLUG}/status")
        for key in PROGRESS_KEYS:
            self.assertIn(key, body["progress"])
        self.assertEqual((body["progress"]["status"], body["progress"]["percentage"], body["progress"]["completed_steps"],
                          body["progress"]["total_steps"]), ("running", 75, 6, 8))
        self.assertEqual(body["progress"]["stage_label"], "Checking app URL")
        self.assertEqual((body["status"], body["url"]), ("starting", ""))  # existing fields keep their meaning
        gate.set()
        self.assertTrue(wait_for(lambda: self.call("GET", f"/api/local/{SLUG}/status")[1]["status"] == "running"))
        _, body = self.call("GET", f"/api/local/{SLUG}/status")
        self.assertEqual((body["progress"]["status"], body["progress"]["percentage"], body["progress"]["completed_steps"]),
                         ("completed", 100, 8))
        self.assertIsNotNone(body["progress"]["last_activity"])
        self.assertEqual(body["url"], URL)

    def test_failed_start_reports_failed_progress(self):
        self.script_process(PRE_URL_LINES[:5] + ["ERROR: pip failed"], exit_code=13, hold=False)
        self.assertEqual(self.call("POST", f"/api/local/{SLUG}/start")[0], 202)
        self.assertTrue(wait_for(lambda: self.call("GET", f"/api/local/{SLUG}/status")[1]["progress"]["status"] == "failed"))
        _, body = self.call("GET", f"/api/local/{SLUG}/status")
        progress = body["progress"]
        self.assertEqual(progress["failed_step"]["id"], "installing")
        self.assertIn("Dependency installation failed", progress["error"])
        self.assertLess(progress["percentage"], 100)
        self.assertEqual(body["status"], "failed")

    def test_stop_endpoint_reports_stopped_progress(self):
        self.script_process(FULL_STAGE_LINES)
        self.call("POST", f"/api/local/{SLUG}/start")
        self.assertTrue(wait_for(lambda: self.call("GET", f"/api/local/{SLUG}/status")[1]["status"] == "running"))
        _, stopped = self.call("POST", f"/api/local/{SLUG}/stop")
        self.assertEqual((stopped["progress"]["status"], stopped["status"]), ("stopped", "stopped"))

    def test_existing_status_fields_are_preserved(self):
        _, body = self.call("GET", f"/api/local/{SLUG}/status")
        for key in ("state", "python", "hardware", "environment", "error", "pid", "url", "logs"):
            self.assertIn(key, body)


if __name__ == "__main__":
    unittest.main()
