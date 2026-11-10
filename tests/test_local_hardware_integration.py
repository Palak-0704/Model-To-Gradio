"""Phase A: the local hardware preflight inside ``local_runner._start_verified`` and its findings in the launch snapshot.

The evaluator itself (``m2g.local_hardware.evaluate``) is not re-tested here; these tests cover the integration:

* order: verify the app -> read analysis.json -> hardware preflight -> ``prepare_launcher`` -> launch
* BLOCK refuses the launch (422) before the launcher is written or any process is started; WARN never does
* unknown / estimated hardware data and a failing probe are warnings, a CPU-capable model without CUDA is not blocked
* the findings survive ``launch_batch`` and the fresh snapshot ``_run_batch`` builds, through running, failed and stopped launches
* the findings are visible in the existing status API (``hardware`` field) without changing the response structure

Nothing real is probed or started: ``Popen``/``subprocess.run`` are the scripted fakes of ``test_local_runner`` and the host probes are
injected through ``check_local_hardware``'s own probe arguments.
"""
import json
import unittest
import urllib.error
import urllib.request
from types import SimpleNamespace
from unittest import mock

import test_local_runner as base
from m2g import local_hardware as lh
from m2g import local_runner, web_api
from m2g.local_hardware import BLOCK, WARN, Finding, HardwareReport, MemoryInfo
from m2g.local_runner import LocalRunError
from m2g.local_workflow import FAILED, IDLE, RUNNING, STOPPED, LocalSnapshot

SLUG, BAT, URL, wait_for = base.SLUG, base.BAT, base.URL, base.wait_for

# ``hardware`` blocks as the analyzer writes them into analysis.json (see HardwareAssessment).
SMALL_CPU = {"params": 66_955_010, "params_source": "Hub safetensors metadata", "weights_gb_fp16": 0.13, "est_vram_gb_fp16": 1.1,
             "est_vram_gb_4bit": 1.0, "runs_locally_cpu": True, "suggest_4bit": False, "needs_gpu": False}
SMALL_GPU_FLAGGED = dict(SMALL_CPU, needs_gpu=True)
HUGE = {"params": 70_000_000_000, "params_source": "Hub safetensors metadata", "weights_gb_fp16": 140.0, "est_vram_gb_fp16": 169.0,
        "est_vram_gb_4bit": 43.0, "runs_locally_cpu": False, "suggest_4bit": True, "needs_gpu": True}
HUGE_ESTIMATED = dict(HUGE, params_source="estimated from weight-file size (assuming 16-bit)")

RAM_16 = MemoryInfo(total_gib=16.0, available_gib=12.0, commit_limit_gib=40.0)


def real_check(gpus=(), memory=RAM_16, disk=500.0):
    """A stand-in for ``local_runner.check_local_hardware`` that runs the REAL ``local_hardware.check_local_hardware`` (probe selection,
    evaluator, never-raise guard) with injected probe results. A probe argument may be a value or a callable."""
    gpu_fn = gpus if callable(gpus) else (lambda: gpus)
    memory_fn = memory if callable(memory) else (lambda: memory)
    disk_fn = disk if callable(disk) else (lambda _path: disk)

    def run(app_dir, analysis=None, **_kwargs):
        return lh.check_local_hardware(app_dir, analysis, gpu_probe=gpu_fn, memory_probe=memory_fn, disk_probe=disk_fn)

    return run


def write_analysis(app_dir, hardware=None, **extra):
    data = dict(extra)
    if hardware is not None:
        data["hardware"] = hardware
    (app_dir / "analysis.json").write_text(json.dumps(data), encoding="utf-8")


class HardwareCase(base.RunnerCase):
    """``self.hardware_check`` (installed by RunnerCase) replaces ``local_runner.check_local_hardware``."""

    def use_real_evaluator(self, hardware, *, quantization=None, **probes):
        write_analysis(self.app_dir, hardware, **({"quantization": quantization} if quantization else {}))
        self.hardware_check.side_effect = real_check(**probes)

    def start_and_wait_running(self):
        self.script_process()
        launcher = self.new_launcher()
        self.start(launcher)
        self.assertTrue(wait_for(lambda: launcher.snapshot.state == RUNNING))
        return launcher

    def assert_not_started(self, launcher):
        self.popen.assert_not_called()
        self.assertFalse((self.app_dir / BAT).exists(), "no launcher may be written for a refused launch")
        self.assertFalse(launcher.active)


class TestPreflightOrder(HardwareCase):
    def test_runs_after_verification_and_before_prepare_launcher(self):
        order = []
        real_verify, real_prepare = local_runner.verify_generated_app, local_runner.prepare_launcher

        def verify(*args, **kwargs):
            order.append("verify")
            return real_verify(*args, **kwargs)

        def check(*_args, **_kwargs):
            order.append("hardware")
            return HardwareReport()

        def prepare(*args, **kwargs):
            order.append("prepare")
            return real_prepare(*args, **kwargs)

        self.hardware_check.side_effect = check
        self.script_process()
        with mock.patch.object(local_runner, "verify_generated_app", side_effect=verify), \
                mock.patch.object(local_runner, "prepare_launcher", side_effect=prepare):
            self.start(self.new_launcher())
        self.assertEqual(order, ["verify", "hardware", "prepare"])
        self.assertTrue(wait_for(lambda: self.popen.called))

    def test_an_app_that_fails_verification_is_never_probed(self):
        (self.app_dir / "requirements.txt").unlink()
        with self.assertRaises(LocalRunError) as ctx:
            self.start(self.new_launcher())
        self.assertEqual(ctx.exception.status, 400)
        self.hardware_check.assert_not_called()

    def test_the_preflight_receives_this_apps_analysis_json(self):
        write_analysis(self.app_dir, SMALL_CPU, quantization="bitsandbytes")
        self.script_process()
        self.start(self.new_launcher())
        args, _kwargs = self.hardware_check.call_args
        self.assertEqual(args[0], self.app_dir)
        self.assertEqual(args[1]["hardware"], SMALL_CPU)
        self.assertEqual(args[1]["quantization"], "bitsandbytes")

    def test_a_launch_that_is_already_running_is_refused_before_any_probe(self):
        launcher = self.start_and_wait_running()
        self.hardware_check.reset_mock()
        with self.assertRaises(LocalRunError) as ctx:
            self.start(launcher)  # the API reuses one launcher per slug
        self.assertEqual(ctx.exception.status, 409)
        self.hardware_check.assert_not_called()

    def test_the_non_windows_in_process_launch_is_not_preflighted(self):
        launcher = mock.Mock()
        launcher.active = False
        launcher.launch.return_value = LocalSnapshot()
        local_runner.start_local_app(launcher, self.root, SLUG, None, is_windows=False)
        self.hardware_check.assert_not_called()
        launcher.launch.assert_called_once()
        launcher.launch_batch.assert_not_called()


class TestBlockAndWarn(HardwareCase):
    def test_a_block_stops_before_launcher_preparation_and_launch(self):
        message = "Only 0.2 GiB is free on the drive that holds the app folder."
        self.hardware_check.return_value = HardwareReport((Finding(BLOCK, "disk_full", message),), "free disk: 0.2 GiB (app drive)")
        launcher = self.new_launcher()
        with mock.patch.object(local_runner, "prepare_launcher") as prepare:
            with self.assertRaises(LocalRunError) as ctx:
                self.start(launcher)
        self.assertEqual(ctx.exception.status, 422)
        self.assertIn(message, ctx.exception.message)
        self.assertIn("refused", ctx.exception.message)
        prepare.assert_not_called()
        self.assert_not_started(launcher)
        self.assertEqual(launcher.snapshot.state, IDLE)  # a refused launch leaves the launcher untouched

    def test_a_block_is_not_overridden_by_warnings_in_the_same_report(self):
        report = HardwareReport((Finding(BLOCK, "disk_full", "No room."), Finding(WARN, "ram_low", "Low RAM.")), "")
        self.hardware_check.return_value = report
        launcher = self.new_launcher()
        with self.assertRaises(LocalRunError) as ctx:
            self.start(launcher)
        self.assertEqual(ctx.exception.status, 422)
        self.assert_not_started(launcher)

    def test_a_warning_allows_the_launch_and_is_visible_in_the_snapshot_and_log(self):
        warn = Finding(WARN, "ram_low", "Only 3.0 GiB of RAM is available for this model.")
        summary = "GPU: no NVIDIA GPU detected; RAM: 16.0 GiB total, 3.0 GiB available"
        self.hardware_check.return_value = HardwareReport((warn,), summary)
        self.script_process()
        launcher = self.new_launcher()
        returned = self.start(launcher)
        self.assertIn(summary, returned.hardware)  # visible in the snapshot start_local_app returns ...
        self.assertIn(warn.message, returned.hardware)
        self.assertTrue(wait_for(lambda: launcher.snapshot.state == RUNNING))
        self.assertTrue(self.popen.called)
        self.assertIn(summary, launcher.snapshot.hardware)  # ... and in the one _run_batch replaced it with
        self.assertIn(warn.message, launcher.snapshot.hardware)
        logs = "\n".join(launcher.read_logs(0)[0])
        self.assertIn("Hardware check: " + summary, logs)
        self.assertIn("Hardware check WARNING: " + warn.message, logs)

    def test_a_clean_report_leaves_the_hardware_field_empty(self):
        launcher = self.start_and_wait_running()
        self.assertEqual(launcher.snapshot.hardware, "")


class TestEvaluatorPolicyThroughTheRunner(HardwareCase):
    """The real evaluator with injected probes: what blocks, what only warns."""

    def test_a_cpu_capable_model_without_cuda_is_not_blocked(self):
        self.use_real_evaluator(SMALL_CPU, gpus=())  # no NVIDIA GPU at all
        launcher = self.start_and_wait_running()
        self.assertTrue(self.popen.called)
        self.assertNotIn("BLOCKED", "\n".join(launcher.read_logs(0)[0]))

    def test_a_gpu_flagged_small_model_without_cuda_only_warns(self):
        self.use_real_evaluator(SMALL_GPU_FLAGGED, gpus=())
        launcher = self.start_and_wait_running()
        self.assertIn("no NVIDIA GPU was detected", launcher.snapshot.hardware)
        self.assertIn("Warning:", launcher.snapshot.hardware)

    def test_a_full_app_drive_is_a_block(self):
        self.use_real_evaluator(SMALL_CPU, disk=0.2)
        launcher = self.new_launcher()
        with self.assertRaises(LocalRunError) as ctx:
            self.start(launcher)
        self.assertEqual(ctx.exception.status, 422)
        self.assertIn("0.2 GiB", ctx.exception.message)
        self.assert_not_started(launcher)

    def test_weights_that_fit_nowhere_are_a_block_only_with_a_firm_parameter_count(self):
        self.use_real_evaluator(HUGE, gpus=(), memory=RAM_16)
        launcher = self.new_launcher()
        with self.assertRaises(LocalRunError) as ctx:
            self.start(launcher)
        self.assertEqual(ctx.exception.status, 422)
        self.assertIn("out-of-memory", ctx.exception.message)
        self.assert_not_started(launcher)

    def test_estimated_sizes_only_warn(self):
        self.use_real_evaluator(HUGE_ESTIMATED, gpus=(), memory=RAM_16)
        launcher = self.start_and_wait_running()
        self.assertIn("Only 12.0 GiB of RAM is available", launcher.snapshot.hardware)
        self.assertTrue(self.popen.called)

    def test_unknown_hardware_data_only_warns(self):
        self.use_real_evaluator(HUGE, gpus=None, memory=None, disk=None)  # every probe gave no answer
        launcher = self.start_and_wait_running()
        self.assertIn("Could not tell whether an NVIDIA GPU", launcher.snapshot.hardware)
        self.assertTrue(self.popen.called)

    def test_an_inconclusive_gpu_probe_never_turns_a_tight_ram_into_a_block(self):
        self.use_real_evaluator(HUGE, gpus=None, memory=RAM_16)
        launcher = self.start_and_wait_running()
        self.assertTrue(self.popen.called)
        self.assertIn("Could not tell whether an NVIDIA GPU", launcher.snapshot.hardware)

    def test_an_analysis_without_a_hardware_block_does_not_block(self):
        self.use_real_evaluator(None, gpus=(), memory=RAM_16)  # analysis.json is just {}
        launcher = self.start_and_wait_running()
        self.assertTrue(self.popen.called)
        self.assertIn("does not record this model's size", launcher.snapshot.hardware)


class TestProbeFailures(HardwareCase):
    def test_a_probe_that_raises_becomes_a_warning_not_a_crash_or_a_block(self):
        def broken_gpu_probe():
            raise RuntimeError("driver exploded")

        self.use_real_evaluator(SMALL_GPU_FLAGGED, gpus=broken_gpu_probe)
        launcher = self.start_and_wait_running()
        self.assertIn("could not be completed", launcher.snapshot.hardware)
        self.assertIn("RuntimeError", launcher.snapshot.hardware)
        self.assertTrue(self.popen.called)

    def test_a_memory_probe_that_raises_does_not_block(self):
        def broken_memory_probe():
            raise OSError("no counters")

        self.use_real_evaluator(HUGE, gpus=(), memory=broken_memory_probe)
        launcher = self.start_and_wait_running()
        self.assertIn("could not be completed", launcher.snapshot.hardware)

    def test_the_check_function_itself_failing_does_not_block_either(self):
        self.hardware_check.side_effect = RuntimeError("boom")
        launcher = self.start_and_wait_running()
        self.assertIn("could not be completed", launcher.snapshot.hardware)
        self.assertIn("RuntimeError", launcher.snapshot.hardware)
        self.assertTrue(self.popen.called)

    def test_a_missing_analysis_file_after_verification_is_not_a_crash(self):
        with mock.patch.object(local_runner, "_read_analysis", return_value=None):
            self.hardware_check.return_value = HardwareReport()
            self.start_and_wait_running()
        self.assertIsNone(self.hardware_check.call_args.args[1])


class TestSnapshotLifecycle(HardwareCase):
    """The findings must outlive ``launch_batch``'s snapshot and the fresh snapshot ``_run_batch`` builds."""

    HW = "GPU: no NVIDIA GPU detected | Warning: Only 3.0 GiB of RAM is available."
    LOG = ["Hardware check: GPU: no NVIDIA GPU detected", "Hardware check WARNING: Only 3.0 GiB of RAM is available."]

    def launch(self, launcher, **kwargs):
        return launcher.launch_batch(local_runner.prepare_launcher(self.app_dir), self.app_dir, **kwargs)

    def test_launch_batch_snapshot_carries_the_findings(self):
        launcher = self.new_launcher()
        with mock.patch.object(launcher, "_run_batch") as run:
            snap = self.launch(launcher, hardware=self.HW, hardware_log=self.LOG)
            self.assertTrue(wait_for(lambda: run.called))  # the launch runs on its own thread
            run.assert_called_once_with((self.app_dir / BAT).resolve(), self.app_dir.resolve())  # thread arguments are unchanged
        self.assertEqual(snap.hardware, self.HW)

    def test_findings_survive_the_run_batch_snapshot_reset(self):
        self.script_process()
        launcher = self.new_launcher()
        first = self.launch(launcher, hardware=self.HW, hardware_log=self.LOG)
        self.assertTrue(wait_for(lambda: launcher.snapshot.state == RUNNING))
        self.assertIsNot(launcher.snapshot, first, "the reset this test is about did not happen")
        self.assertEqual(launcher.snapshot.hardware, self.HW)
        logs = launcher.read_logs(0)[0]
        for line in self.LOG:
            self.assertIn(line, logs)
        self.assertEqual(local_runner.snapshot_payload(SLUG, launcher)["hardware"], self.HW)

    def test_findings_survive_a_launcher_failure(self):
        self.script_process(base.PRE_URL_LINES[:5] + ["ERROR: pip failed"], exit_code=13, hold=False)
        launcher = self.new_launcher()
        self.launch(launcher, hardware=self.HW, hardware_log=self.LOG)
        self.assertTrue(wait_for(lambda: launcher.snapshot.state == FAILED))
        payload = local_runner.snapshot_payload(SLUG, launcher)
        self.assertEqual((payload["status"], payload["hardware"]), ("failed", self.HW))

    def test_findings_survive_a_failed_health_check(self):
        self.script_process()
        launcher = self.new_launcher(verify=False)
        self.launch(launcher, hardware=self.HW, hardware_log=self.LOG)
        self.assertTrue(wait_for(lambda: launcher.snapshot.state == FAILED))
        self.assertEqual(launcher.snapshot.hardware, self.HW)

    def test_findings_survive_stopping(self):
        self.script_process()
        launcher = self.new_launcher()
        self.launch(launcher, hardware=self.HW, hardware_log=self.LOG)
        self.assertTrue(wait_for(lambda: launcher.snapshot.state == RUNNING))
        launcher.stop()
        self.assertEqual(launcher.snapshot.state, STOPPED)
        self.assertEqual(launcher.snapshot.hardware, self.HW)

    def test_findings_survive_every_state_change_of_one_launch(self):
        self.script_process(base.FULL_STAGE_LINES)
        launcher = self.new_launcher()
        seen = set()
        original = launcher._log

        def watching_log(text):
            seen.add((launcher.snapshot.state, launcher.snapshot.hardware))
            original(text)

        launcher._log = watching_log
        self.launch(launcher, hardware=self.HW, hardware_log=self.LOG)
        self.assertTrue(wait_for(lambda: launcher.snapshot.state == RUNNING))
        self.assertTrue(seen)
        self.assertEqual({hardware for _state, hardware in seen}, {self.HW}, "a log call saw a snapshot without the findings")

    def test_a_relaunch_without_findings_does_not_inherit_the_previous_ones(self):
        self.script_process()
        launcher = self.new_launcher()
        self.launch(launcher, hardware=self.HW, hardware_log=self.LOG)
        self.assertTrue(wait_for(lambda: launcher.snapshot.state == RUNNING))
        launcher.stop()
        self.script_process()
        self.launch(launcher)
        self.assertTrue(wait_for(lambda: launcher.snapshot.state == RUNNING))
        self.assertEqual(launcher.snapshot.hardware, "")
        self.assertNotIn(self.LOG[0], launcher.read_logs(0)[0])

    def test_end_to_end_through_start_local_app_with_a_real_evaluator_warning(self):
        self.use_real_evaluator(SMALL_GPU_FLAGGED, gpus=())
        launcher = self.start_and_wait_running()
        self.assertIn("GPU: no NVIDIA GPU detected", launcher.snapshot.hardware)
        self.assertIn("Warning:", launcher.snapshot.hardware)
        launcher.stop()
        self.assertIn("GPU: no NVIDIA GPU detected", launcher.snapshot.hardware)


class TestHardwareInTheStatusApi(HardwareCase):
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

    def call(self, method, path):
        data = b"{}" if method == "POST" else None
        request = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data, method=method,
                                         headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_warnings_are_in_the_start_response_and_every_later_status(self):
        warn = Finding(WARN, "ram_low", "Only 3.0 GiB of RAM is available for this model.")
        self.hardware_check.return_value = HardwareReport((warn,), "RAM: 16.0 GiB total, 3.0 GiB available")
        self.script_process()
        status, started = self.call("POST", f"/api/local/{SLUG}/start")
        self.assertEqual(status, 202)
        self.assertIn("hardware", started)
        self.assertTrue(wait_for(lambda: self.call("GET", f"/api/local/{SLUG}/status")[1]["status"] == "running"))
        _, running = self.call("GET", f"/api/local/{SLUG}/status")
        self.assertIn("RAM: 16.0 GiB total", running["hardware"])
        self.assertIn(warn.message, running["hardware"])
        self.assertEqual((running["url"], running["state"]), (URL, "running"))  # the rest of the payload is unchanged

    def test_a_block_is_a_422_with_the_existing_error_shape_and_nothing_starts(self):
        self.hardware_check.return_value = HardwareReport((Finding(BLOCK, "disk_full", "Only 0.2 GiB is free."),), "")
        status, body = self.call("POST", f"/api/local/{SLUG}/start")
        self.assertEqual(status, 422)
        self.assertEqual(list(body), ["error"])
        self.assertIn("Only 0.2 GiB is free.", body["error"])
        self.popen.assert_not_called()
        _, idle = self.call("GET", f"/api/local/{SLUG}/status")
        self.assertEqual((idle["status"], idle["hardware"]), ("idle", ""))

    def test_status_keeps_every_field_it_had_before(self):
        self.hardware_check.return_value = HardwareReport((Finding(WARN, "x", "y"),), "GPU: none")
        self.script_process()
        self.call("POST", f"/api/local/{SLUG}/start")
        self.assertTrue(wait_for(lambda: self.call("GET", f"/api/local/{SLUG}/status")[1]["status"] == "running"))
        _, body = self.call("GET", f"/api/local/{SLUG}/status")
        for key in ("app", "slug", "state", "status", "stage", "stage_label", "pid", "url", "error", "exit_code", "python", "hardware",
                    "environment", "running", "logs", "log_next", "logs_url", "progress"):
            self.assertIn(key, body)

    def test_describe_snapshot_exposes_the_hardware_field(self):
        snap = LocalSnapshot(state=RUNNING, hardware="GPU: none | Warning: low RAM", url=URL)
        self.assertEqual(local_runner.describe_snapshot(snap, SLUG)["hardware"], "GPU: none | Warning: low RAM")
        self.assertEqual(local_runner.describe_snapshot(None, SLUG)["hardware"], "")


class TestSnapshotText(unittest.TestCase):
    def test_summary_then_each_warning_blocks_are_not_listed(self):
        report = HardwareReport((Finding(BLOCK, "b", "Blocked."), Finding(WARN, "w1", "First."), Finding(WARN, "w2", "Second.")), "GPU: none")
        self.assertEqual(local_runner.hardware_snapshot_text(report), "GPU: none | Warning: First. | Warning: Second.")

    def test_an_empty_report_is_an_empty_string(self):
        self.assertEqual(local_runner.hardware_snapshot_text(HardwareReport()), "")


if __name__ == "__main__":
    unittest.main()
