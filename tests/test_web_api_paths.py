import tempfile
import unittest
from unittest import mock
from pathlib import Path
from types import SimpleNamespace

from m2g import web_api
from m2g.local_script import launcher_filename
from m2g.pipeline import BuildResult


class TestWebApiOutputDirectory(unittest.TestCase):
    def test_app_listing_and_detail_use_configured_output_directory(self):
        with tempfile.TemporaryDirectory() as td:
            output = Path(td) / "configured-output"
            app_dir = output / "owner__model"
            app_dir.mkdir(parents=True)
            (app_dir / "analysis.json").write_text(
                '{"source": {"repo_id": "owner/model"}, "task": "text-classification"}',
                encoding="utf-8",
            )
            (app_dir / "app.py").write_text("print('ok')", encoding="utf-8")
            settings = SimpleNamespace(output_dir=output)

            apps = web_api._list_apps(settings)
            detail = web_api._get_app_detail("owner__model", settings)

            self.assertEqual([app["slug"] for app in apps], ["owner__model"])
            self.assertEqual(detail["files"], ["analysis.json", "app.py"])

    def test_configured_output_directory_is_resolved_for_path_checks(self):
        with tempfile.TemporaryDirectory() as td:
            settings = SimpleNamespace(output_dir=Path(td) / "nested")
            self.assertEqual(web_api._output_dir(settings), settings.output_dir.resolve())

    def test_analysis_job_marks_artifact_and_generation_status(self):
        with tempfile.TemporaryDirectory() as td:
            output = Path(td) / "configured-output"
            settings = SimpleNamespace(output_dir=output)
            job = web_api._new_job("analysis")
            result = BuildResult(
                url="https://huggingface.co/owner/model",
                stage="done",
                app_code="app",
                notebook={},
                out_dir=output / "owner__model",
                # the full generated package: app.py + loader.py + requirements.txt, the platform records, and the model's own launcher
                files=sorted(["analysis.json", "app.py", "colab_notebook.ipynb", "loader.py", "requirements.txt",
                              launcher_filename("owner__model")]),
            )
            self.assertIn("loader.py", result.files)
            self.assertNotIn("run_local.bat", result.files)
            result.out_dir.mkdir(parents=True)
            for name in result.files:
                (result.out_dir / name).write_text("ok", encoding="utf-8")
            result.analysis = SimpleNamespace(
                generatable=True,
                to_dict=lambda: {"source": {"repo_id": "owner/model"}},
            )
            with mock.patch("m2g.pipeline.run_pipeline", return_value=result):
                web_api._run_analysis_job(job, result.url, settings)

            self.assertEqual(job.status, "done")
            self.assertEqual(job.result["analysis_status"], "completed")
            self.assertEqual(job.result["generation_status"], "completed")
            self.assertEqual(job.result["artifact_status"], "saved")


if __name__ == "__main__":
    unittest.main()
