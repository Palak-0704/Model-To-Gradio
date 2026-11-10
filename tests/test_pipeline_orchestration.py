"""Phase 4.1 backend orchestration: one definition of a complete package, no duplicate pipeline runs for a repeated request,
artifact checks that include the model's launcher, redacted job errors, and a handoff gate that refuses a folder whose code does not
belong to its analysis.json. Everything is offline; no pipeline, OpenCode, REA or process is started.
"""
import json
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from m2g import local_runner, pipeline, web_api
from m2g.local_runner import LocalRunError
from m2g.local_script import launcher_filename
from m2g.pipeline import BuildResult

SLUG = "owner__model"
PACKAGE = ["analysis.json", "app.py", "colab_notebook.ipynb", "loader.py", "requirements.txt"]
REV_A, REV_B = "a" * 40, "b" * 40


class TestRequiredArtifacts(unittest.TestCase):
    def test_a_complete_package_is_the_four_package_files_records_and_the_model_launcher(self):
        names = pipeline.required_artifacts(SLUG)
        self.assertEqual(sorted(names), sorted([*PACKAGE, launcher_filename(SLUG)]))
        self.assertIn("loader.py", names)
        self.assertNotIn("run_local.bat", names)

    def test_local_runner_requires_exactly_what_the_pipeline_generates(self):
        self.assertEqual(set(local_runner.REQUIRED_ARTIFACTS), set(pipeline.GENERATED_ARTIFACTS))


class TestRepeatedRequestsShareOneJob(unittest.TestCase):
    def key(self):
        return "https://huggingface.co/owner/dedupe-" + uuid.uuid4().hex

    def test_a_request_for_the_same_target_joins_the_active_job(self):
        key = self.key()
        first, created_first = web_api._new_or_active_job("analysis", key)
        second, created_second = web_api._new_or_active_job("analysis", key)
        self.assertTrue(created_first)
        self.assertFalse(created_second)
        self.assertIs(first, second)

    def test_other_targets_and_other_job_types_are_independent(self):
        key = self.key()
        job, _ = web_api._new_or_active_job("analysis", key)
        other_type, created_type = web_api._new_or_active_job("generation", key)
        other_key, created_key = web_api._new_or_active_job("analysis", self.key())
        self.assertTrue(created_type and created_key)
        self.assertEqual(len({job.id, other_type.id, other_key.id}), 3)

    def test_a_finished_job_does_not_block_a_new_request(self):
        key = self.key()
        job, _ = web_api._new_or_active_job("analysis", key)
        for status in ("done", "failed"):
            web_api._update_job(job, status=status)
            again, created = web_api._new_or_active_job("analysis", key)
            self.assertTrue(created, status)
            self.assertIsNot(again, job)
            job = again

    def test_concurrent_requests_create_exactly_one_job(self):
        key = self.key()
        results, barrier = [], threading.Barrier(8)

        def request():
            barrier.wait()
            results.append(web_api._new_or_active_job("analysis", key))

        threads = [threading.Thread(target=request) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)
        self.assertEqual(len(results), 8)
        self.assertEqual(sum(1 for _, created in results if created), 1)
        self.assertEqual(len({job.id for job, _ in results}), 1)


class TestAnalysisJobArtifactChecks(unittest.TestCase):
    def run_job(self, files=None, side_effect=None):
        files = list(files if files is not None else pipeline.required_artifacts(SLUG))
        with tempfile.TemporaryDirectory() as td:
            output = Path(td) / "out"
            settings = SimpleNamespace(output_dir=output)
            job = web_api._new_job("analysis")
            result = BuildResult(url="https://huggingface.co/owner/model", stage="done", app_code="app", notebook={},
                                 out_dir=output / SLUG, files=sorted(files))
            result.out_dir.mkdir(parents=True)
            for name in files:
                (result.out_dir / name).write_text("ok", encoding="utf-8")
            result.analysis = SimpleNamespace(generatable=True, to_dict=lambda: {"source": {"repo_id": "owner/model"}})
            patch = mock.patch("m2g.pipeline.run_pipeline", return_value=result, side_effect=side_effect)
            with patch:
                web_api._run_analysis_job(job, result.url, settings)
        return job

    def test_the_complete_package_is_done(self):
        self.assertEqual(self.run_job().status, "done")

    def test_a_missing_model_launcher_fails_the_job(self):
        job = self.run_job([n for n in pipeline.required_artifacts(SLUG) if n != launcher_filename(SLUG)])
        self.assertEqual((job.status, job.stage), ("failed", "write"))
        self.assertIn(launcher_filename(SLUG), job.error)

    def test_a_missing_loader_fails_the_job(self):
        job = self.run_job([n for n in pipeline.required_artifacts(SLUG) if n != "loader.py"])
        self.assertEqual(job.status, "failed")
        self.assertIn("loader.py", job.error)

    def test_an_unexpected_exception_is_stored_without_secrets(self):
        token = "hf_" + "a" * 30
        job = self.run_job(side_effect=RuntimeError("request failed for token " + token))
        self.assertEqual(job.status, "failed")
        self.assertNotIn(token, job.error)


def make_package(root: Path, slug: str = SLUG, analysis=None) -> Path:
    app_dir = root / slug
    app_dir.mkdir(parents=True)
    (app_dir / "app.py").write_text("from loader import get_model\n", encoding="utf-8")
    (app_dir / "loader.py").write_text("def get_model():\n    return None\n", encoding="utf-8")
    (app_dir / "requirements.txt").write_text("gradio\n", encoding="utf-8")
    (app_dir / "colab_notebook.ipynb").write_text("{}\n", encoding="utf-8")
    (app_dir / "analysis.json").write_text(json.dumps(analysis if analysis is not None else {}), encoding="utf-8")
    return app_dir


class TestPackageMatchesAnalysis(unittest.TestCase):
    report = {"model_id": "owner/model", "revision": REV_A}

    def check(self, analysis, report=None):
        local_runner.ensure_package_matches_analysis(Path("."), self.report if report is None else report, analysis)

    def test_matching_model_and_revision_pass(self):
        self.check({"source": {"repo_id": "owner/model"}, "model_source": None, "revision": REV_A})

    def test_a_different_revision_is_refused(self):
        with self.assertRaises(LocalRunError) as ctx:
            self.check({"source": {"repo_id": "owner/model"}, "revision": REV_B})
        self.assertEqual(ctx.exception.status, 422)
        self.assertIn("do not belong together", ctx.exception.message)
        self.assertIn("revision", ctx.exception.message)

    def test_a_different_model_is_refused(self):
        with self.assertRaises(LocalRunError) as ctx:
            self.check({"source": {"repo_id": "other/model"}, "revision": REV_A})
        self.assertEqual(ctx.exception.status, 422)

    def test_a_github_analysis_is_compared_with_its_linked_hugging_face_model(self):
        self.check({"source": {"repo_id": "acme/kit"}, "model_source": {"repo_id": "owner/model"}, "revision": REV_A})
        with self.assertRaises(LocalRunError):
            self.check({"source": {"repo_id": "owner/model"}, "model_source": {"repo_id": "owner/other"}, "revision": REV_A})

    def test_values_that_are_not_recorded_on_both_sides_are_not_compared(self):
        self.check({})
        self.check({"source": {"repo_id": "owner/model"}})  # no revision recorded
        self.check({"revision": REV_B}, {"model_id": "owner/model", "revision": None})  # app records no revision
        self.check({"revision": REV_B}, object())  # not a validation report

    def test_analysis_json_in_the_folder_is_used_when_none_is_passed(self):
        with tempfile.TemporaryDirectory() as td:
            app_dir = make_package(Path(td).resolve(), analysis={"source": {"repo_id": "owner/model"}, "revision": REV_B})
            with self.assertRaises(LocalRunError):
                local_runner.ensure_package_matches_analysis(app_dir, self.report)
            (app_dir / "analysis.json").unlink()
            local_runner.ensure_package_matches_analysis(app_dir, self.report)  # nothing to compare with

    def test_a_folder_mixing_a_new_analysis_with_old_code_is_not_started(self):
        with tempfile.TemporaryDirectory() as td:
            app_dir = make_package(Path(td).resolve(), analysis={"source": {"repo_id": "owner/model"}, "revision": REV_B})
            with mock.patch.object(local_runner, "validate_app_code", return_value=self.report), \
                    mock.patch.object(local_runner, "validate_requirements_text"):
                with self.assertRaises(LocalRunError) as ctx:
                    local_runner.verify_generated_app(app_dir)
            self.assertEqual(ctx.exception.status, 422)
            self.assertIn("generate the app again", ctx.exception.message)

    def test_a_consistent_folder_still_verifies(self):
        with tempfile.TemporaryDirectory() as td:
            app_dir = make_package(Path(td).resolve(), analysis={"source": {"repo_id": "owner/model"}, "revision": REV_A})
            with mock.patch.object(local_runner, "validate_app_code", return_value=self.report), \
                    mock.patch.object(local_runner, "validate_requirements_text"):
                code, requirements = local_runner.verify_generated_app(app_dir)
            self.assertIn("get_model", code)
            self.assertEqual(requirements, "gradio\n")


if __name__ == "__main__":
    unittest.main()
