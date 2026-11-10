"""Local deployment progress: the tracker's rules, and how the launcher turns real events into progress.

Clocks are injected, so nothing here depends on wall time (59 s versus 60 s of silence is exact). No process is started.
"""
import io
import re
import unittest
from types import SimpleNamespace

from m2g.local_progress import STEPS, STUCK_AFTER_S, ProgressTracker
from m2g.local_script import windows_launcher
from m2g.local_workflow import LocalLauncher

STEP_IDS = [sid for sid, _ in STEPS]
EXPECTED_PERCENT = [12, 25, 37, 50, 62, 75, 87, 100]  # int(100 * n / 8) for n = 1..8
URL_LINE = "* Running on local URL:  http://127.0.0.1:7860"


class Clock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def started_tracker(clock=None):
    clock = clock or Clock()
    tracker = ProgressTracker(clock=clock)
    tracker.start()
    return tracker, clock


def states(tracker):
    return {s["id"]: s["state"] for s in tracker.snapshot()["steps"]}


class TestSteps(unittest.TestCase):
    def test_there_are_exactly_eight_ordered_steps(self):
        self.assertEqual(STEP_IDS, ["validating", "preparing", "creating_env", "upgrading_pip", "installing",
                                    "starting", "checking_url", "complete"])
        snap = ProgressTracker().snapshot()
        self.assertEqual((snap["status"], snap["percent"], snap["completed_steps"], snap["total_steps"]), ("idle", 0, 0, 8))

    def test_percentage_is_completed_steps_over_eight(self):
        tracker, _ = started_tracker()
        tracker.begin("validating")
        self.assertEqual(tracker.percent(), 0)  # a step that is only running does not count
        seen = []
        for step in STEP_IDS[1:7]:
            tracker.begin(step)  # starting a step is the evidence that the previous one finished
            seen.append(tracker.percent())
        tracker.finish("checking_url")
        seen.append(tracker.percent())
        tracker.complete()
        seen.append(tracker.percent())
        self.assertEqual(seen, EXPECTED_PERCENT)
        snap = tracker.snapshot()
        self.assertEqual((snap["completed_steps"], snap["total_steps"]), (8, 8))

    def test_skipped_and_reused_steps_count_as_completed(self):
        tracker, _ = started_tracker()
        tracker.begin("validating")
        tracker.begin("preparing")
        tracker.skip("creating_env", "Existing environment reused", keep_active=True)
        tracker.skip("upgrading_pip", "Requirements unchanged", keep_active=True)
        tracker.skip("installing", "Requirements unchanged", keep_active=True)
        self.assertEqual(states(tracker), {"validating": "done", "preparing": "active", "creating_env": "skipped",
                                           "upgrading_pip": "skipped", "installing": "skipped", "starting": "pending",
                                           "checking_url": "pending", "complete": "pending"})
        self.assertEqual(tracker.percent(), 50)  # 1 done + 3 skipped = 4 of 8
        tracker.begin("starting")  # the reused environment is verified; preparing ends
        self.assertEqual(tracker.percent(), 62)

    def test_skip_without_keep_active_ends_the_running_step(self):
        tracker, _ = started_tracker()
        tracker.begin("validating")
        tracker.begin("preparing")
        tracker.skip("creating_env")
        self.assertEqual(states(tracker)["preparing"], "done")

    def test_markers_are_idempotent(self):
        tracker, _ = started_tracker()
        tracker.begin("validating")
        tracker.begin("preparing")
        tracker.begin("validating")  # a repeated or late marker never moves progress back or forward
        tracker.begin("preparing")
        self.assertEqual((tracker.percent(), tracker.snapshot()["step_id"]), (12, "preparing"))

    def test_maximum_before_the_health_check_is_87_percent(self):
        tracker, _ = started_tracker()
        for step in STEP_IDS[:7]:
            tracker.begin(step)
        tracker.finish("checking_url")  # even with every step before "complete" finished
        snap = tracker.snapshot()
        self.assertEqual(snap["percent"], 87)
        self.assertNotEqual(snap["status"], "completed")
        self.assertEqual(snap["steps"][-1]["state"], "pending")

    def test_the_final_step_cannot_be_reached_except_by_complete(self):
        tracker, _ = started_tracker()
        for step in STEP_IDS[:7]:
            tracker.begin(step)
        tracker.finish("checking_url")
        tracker.begin("complete")
        tracker.skip("complete")
        tracker.finish("complete")
        self.assertEqual(tracker.percent(), 87)
        tracker.complete()
        self.assertEqual((tracker.percent(), tracker.snapshot()["status"]), (100, "completed"))

    def test_100_percent_only_after_complete(self):
        tracker, _ = started_tracker()
        for step in STEP_IDS[:7]:
            tracker.begin(step)
            self.assertLess(tracker.percent(), 100)
        self.assertLess(tracker.percent(), 100)
        tracker.complete()
        snap = tracker.snapshot()
        self.assertEqual((snap["percent"], snap["status"], snap["step_id"]), (100, "completed", "complete"))

    def test_there_is_no_finished_property(self):
        self.assertFalse(hasattr(ProgressTracker, "finished"))

    def test_a_new_start_forgets_the_previous_deployment(self):
        tracker, _ = started_tracker()
        tracker.begin("validating")
        tracker.fail("boom")
        tracker.start()
        snap = tracker.snapshot()
        self.assertEqual((snap["status"], snap["percent"], snap["error"]), ("waiting", 0, ""))
        self.assertIsNone(snap["failed_step"])


class TestInactivity(unittest.TestCase):
    def running_tracker(self):
        tracker, clock = started_tracker()
        tracker.begin("validating")
        tracker.begin("preparing")
        tracker.begin("creating_env")
        return tracker, clock

    def test_59_seconds_without_activity_is_not_stuck(self):
        tracker, clock = self.running_tracker()
        clock.advance(STUCK_AFTER_S - 1)
        snap = tracker.snapshot(process_alive=True)
        self.assertEqual(snap["status"], "running")
        self.assertEqual(snap["warning"], "")
        self.assertEqual(snap["seconds_since_activity"], 59.0)

    def test_60_seconds_without_activity_is_possibly_stuck_not_failed(self):
        tracker, clock = self.running_tracker()
        percent = tracker.percent()
        clock.advance(STUCK_AFTER_S)
        snap = tracker.snapshot(process_alive=True)
        self.assertEqual(snap["status"], "possibly_stuck")
        self.assertTrue(snap["warning"])
        self.assertEqual(snap["error"], "")
        self.assertIsNone(snap["failed_step"])
        self.assertEqual(snap["percent"], percent)

    def test_a_live_process_is_possibly_stuck_and_a_dead_one_is_not(self):
        tracker, clock = self.running_tracker()
        clock.advance(600)
        self.assertEqual(tracker.snapshot(process_alive=True)["status"], "possibly_stuck")
        dead = tracker.snapshot(process_alive=False)
        self.assertEqual(dead["status"], "waiting")  # the launcher decides what an ended process means
        self.assertNotIn(dead["status"], ("possibly_stuck", "failed"))
        self.assertEqual(tracker.snapshot(process_alive=None)["status"], "possibly_stuck")

    def test_time_alone_never_changes_the_percentage(self):
        tracker, clock = self.running_tracker()
        before = (tracker.percent(), tracker.snapshot()["completed_steps"], tracker.snapshot()["step_id"])
        for _ in range(5):
            clock.advance(3600)
            tracker.snapshot(process_alive=True)
        self.assertEqual((tracker.percent(), tracker.snapshot()["completed_steps"], tracker.snapshot()["step_id"]), before)

    def test_activity_resets_the_silence_but_not_the_progress(self):
        tracker, clock = self.running_tracker()
        clock.advance(59)
        tracker.touch()  # e.g. a pip output line
        clock.advance(59)
        snap = tracker.snapshot(process_alive=True)
        self.assertEqual(snap["status"], "running")
        self.assertEqual(snap["percent"], 25)
        clock.advance(1)  # exactly 60 s since the last activity
        self.assertEqual(tracker.snapshot(process_alive=True)["status"], "possibly_stuck")

    def test_an_ended_deployment_is_never_reported_as_stuck(self):
        tracker, clock = self.running_tracker()
        tracker.complete()
        clock.advance(10_000)
        self.assertEqual(tracker.snapshot(process_alive=True)["status"], "completed")


class TestFailureAndStop(unittest.TestCase):
    def test_failure_records_the_running_step_and_the_error(self):
        tracker, clock = started_tracker()
        for step in ("validating", "preparing", "creating_env", "upgrading_pip", "installing"):
            tracker.begin(step)
        tracker.fail("Dependency installation failed.")
        snap = tracker.snapshot(process_alive=False)
        self.assertEqual(snap["status"], "failed")
        self.assertEqual(snap["failed_step"], {"id": "installing", "label": "Installing dependencies"})
        self.assertEqual(snap["step_id"], "installing")
        self.assertIn("Dependency installation failed.", snap["error"])
        self.assertEqual(snap["percent"], 50)  # only what really finished counts; a failure never adds progress
        clock.advance(10_000)
        self.assertEqual(tracker.snapshot(process_alive=True)["status"], "failed")  # silence cannot change a recorded failure

    def test_failure_after_the_deployment_completed_is_a_runtime_failure(self):
        tracker, _ = started_tracker()
        tracker.begin("validating")
        tracker.complete()
        tracker.fail("exit code 3")
        snap = tracker.snapshot()
        self.assertEqual((snap["status"], snap["failed_step"]["id"], snap["percent"]), ("failed", "running", 100))

    def test_stop_keeps_the_progress_that_was_reached(self):
        tracker, _ = started_tracker()
        tracker.begin("validating")
        tracker.begin("preparing")
        tracker.stop()
        snap = tracker.snapshot()
        self.assertEqual((snap["status"], snap["percent"], snap["error"]), ("stopped", 12, ""))

    def test_stop_after_completion_stays_at_100(self):
        tracker, _ = started_tracker()
        tracker.begin("validating")
        tracker.complete()
        tracker.stop()
        snap = tracker.snapshot()
        self.assertEqual((snap["status"], snap["percent"]), ("stopped", 100))

    def test_a_failure_is_not_overwritten_by_a_later_stop_and_the_reverse(self):
        tracker, _ = started_tracker()
        tracker.begin("validating")
        tracker.fail("boom")
        tracker.stop()
        self.assertEqual(tracker.snapshot()["status"], "failed")
        tracker.start()
        tracker.begin("validating")
        tracker.stop()
        tracker.fail("late")
        self.assertEqual(tracker.snapshot()["status"], "stopped")

    def test_events_after_the_end_change_nothing(self):
        tracker, _ = started_tracker()
        tracker.begin("validating")
        tracker.fail("boom")
        tracker.begin("preparing")
        tracker.skip("creating_env")
        tracker.complete()
        snap = tracker.snapshot()
        self.assertEqual((snap["status"], snap["percent"]), ("failed", 0))


class Lines:
    """Stand-in for the process the output reader consumes."""

    def __init__(self, *lines):
        self.stdout = io.StringIO("".join(line + "\n" for line in lines))
        self.returncode = None

    def poll(self):
        return None


def launcher_with_clock():
    clock = Clock()
    launcher = LocalLauncher(python="python", progress=ProgressTracker(clock=clock))
    launcher.progress.start()
    launcher.progress.begin("validating")
    return launcher, clock


class TestLauncherMarkers(unittest.TestCase):
    def test_fresh_environment_markers_drive_every_step(self):
        launcher, _ = launcher_with_clock()
        launcher._read_process_output(Lines(
            "M2G_STAGE=validating_app", "M2G_STAGE=preparing_environment", "M2G_STAGE=creating_environment",
            "M2G_STAGE=upgrading_pip", "M2G_STAGE=installing_dependencies", "M2G_STAGE=starting_application"))
        snap = launcher.progress.snapshot()
        self.assertEqual((snap["step_id"], snap["percent"]), ("starting", 62))
        launcher._read_process_output(Lines(URL_LINE))
        snap = launcher.progress.snapshot()
        self.assertEqual((snap["step_id"], snap["percent"], snap["status"]), ("checking_url", 75, "running"))

    def test_reused_environment_and_unchanged_requirements_are_skipped(self):
        launcher, _ = launcher_with_clock()
        launcher._read_process_output(Lines(
            "M2G_STAGE=validating_app", "M2G_STAGE=preparing_environment", "M2G_STAGE=env_reused",
            "M2G_STAGE=deps_unchanged", "M2G_STAGE=starting_application", URL_LINE))
        snap = launcher.progress.snapshot()
        self.assertEqual({s["id"]: s["state"] for s in snap["steps"]}, {
            "validating": "done", "preparing": "done", "creating_env": "skipped", "upgrading_pip": "skipped",
            "installing": "skipped", "starting": "done", "checking_url": "active", "complete": "pending"})
        self.assertEqual(snap["percent"], 75)

    def test_environment_reused_but_requirements_changed_runs_pip_and_install(self):
        launcher, _ = launcher_with_clock()
        launcher._read_process_output(Lines(
            "M2G_STAGE=validating_app", "M2G_STAGE=preparing_environment", "M2G_STAGE=env_reused",
            "M2G_STAGE=upgrading_pip", "M2G_STAGE=installing_dependencies"))
        snap = launcher.progress.snapshot()
        self.assertEqual({s["id"]: s["state"] for s in snap["steps"]}["creating_env"], "skipped")
        self.assertEqual((snap["step_id"], snap["percent"]), ("installing", 50))

    def test_pip_output_is_activity_but_never_progress(self):
        launcher, clock = launcher_with_clock()
        launcher._read_process_output(Lines("M2G_STAGE=validating_app", "M2G_STAGE=preparing_environment",
                                            "M2G_STAGE=installing_dependencies"))
        percent = launcher.progress.percent()
        clock.advance(59)
        launcher._read_process_output(Lines("Collecting torch", "Downloading torch-2.0.whl (800 MB)"))
        clock.advance(59)
        snap = launcher.progress.snapshot(process_alive=True)
        self.assertEqual((snap["status"], snap["percent"]), ("running", percent))

    def test_output_from_the_app_cannot_move_progress(self):
        launcher, _ = launcher_with_clock()
        launcher._read_process_output(Lines("M2G_STAGE=starting_application", URL_LINE))
        before = launcher.progress.snapshot()
        launcher._startup_event.set()  # the URL was announced: everything after is the app's own output
        launcher._read_process_output(Lines("M2G_STAGE=installing_dependencies", "M2G_STAGE=env_reused"))
        after = launcher.progress.snapshot()
        self.assertEqual((after["step_id"], after["percent"]), (before["step_id"], before["percent"]))

    def test_text_that_merely_contains_a_marker_is_not_a_marker(self):
        launcher, _ = launcher_with_clock()
        launcher._read_process_output(Lines("echo M2G_STAGE=upgrading_pip", "log: M2G_STAGE=installing_dependencies"))
        self.assertEqual(launcher.progress.snapshot()["step_id"], "validating")

    def test_unknown_marker_is_only_activity(self):
        launcher, clock = launcher_with_clock()
        clock.advance(50)
        launcher._apply_stage_marker("something_new")
        clock.advance(50)
        self.assertEqual(launcher.progress.snapshot(process_alive=True)["status"], "running")
        self.assertEqual(launcher.progress.percent(), 0)

    def test_every_marker_the_bat_prints_is_understood(self):
        printed = set(re.findall(r"echo M2G_STAGE=(\w+)", windows_launcher("example__model")))
        self.assertEqual(printed, {"validating_app", "preparing_environment", "env_reused", "creating_environment",
                                   "deps_unchanged", "upgrading_pip", "installing_dependencies", "starting_application"})
        for marker in sorted(printed - {"validating_app", "preparing_environment"}):
            launcher, _ = launcher_with_clock()
            launcher._apply_stage_marker("preparing_environment")
            before = [s["state"] for s in launcher.progress.snapshot()["steps"]]
            launcher._apply_stage_marker(marker)
            after = [s["state"] for s in launcher.progress.snapshot()["steps"]]
            self.assertNotEqual(before, after, marker)


class TestLauncherLiveness(unittest.TestCase):
    def stuck_launcher(self):
        launcher, clock = launcher_with_clock()
        launcher.progress.begin("preparing")
        clock.advance(STUCK_AFTER_S)
        return launcher

    def test_a_live_process_is_possibly_stuck(self):
        launcher = self.stuck_launcher()
        launcher._process = SimpleNamespace(poll=lambda: None)
        self.assertEqual(launcher.progress_snapshot()["status"], "possibly_stuck")

    def test_a_dead_process_is_not_reported_as_stuck_or_failed_by_silence(self):
        launcher = self.stuck_launcher()
        launcher._process = SimpleNamespace(poll=lambda: 1)
        snap = launcher.progress_snapshot()
        self.assertEqual(snap["status"], "waiting")
        self.assertIsNone(snap["failed_step"])

    def test_before_a_process_exists_an_idle_launcher_is_unknown_not_failed(self):
        launcher = self.stuck_launcher()
        self.assertEqual(launcher.progress_snapshot()["status"], "possibly_stuck")  # nothing alive is known: never "failed"

    def test_59_seconds_with_a_live_process_is_running(self):
        launcher, clock = launcher_with_clock()
        launcher.progress.begin("preparing")
        clock.advance(STUCK_AFTER_S - 1)
        launcher._process = SimpleNamespace(poll=lambda: None)
        self.assertEqual(launcher.progress_snapshot()["status"], "running")


if __name__ == "__main__":
    unittest.main()
