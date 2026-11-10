"""End-to-end stabilisation tests for GitHub -> Gradio generation through a linked Hugging Face model.

All offline (fake transport, mocked Hub metadata). No weights, no GitHub code or requirements are run, imported or installed.
"""
import dataclasses
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from m2g import analyzer as an
from m2g import gradio_gen as gg
from m2g import inspector as ins
from m2g import pipeline
from m2g.colab_gen import generate_notebook
from m2g.config import Settings
from m2g.local_script import launcher_filename
from m2g.pipeline import run_pipeline
from m2g.validate import (combine_package_source, require_package_link, validate_app_code, validate_notebook,
                          validate_requirements_text)
from fakehub import FakeWeb, PRESETS, SHA, add_github_repo, add_hf_model
from test_github_generation import github_repo_for, readme_for

WEIGHTS = (".safetensors", ".bin", ".gguf", ".pt", ".pth", ".ckpt", ".onnx")
# The package is app.py + loader.py + requirements.txt + a launcher named after the model folder (run_<slug>.bat), plus the platform records.
PACKAGE_FILES = ["analysis.json", "app.py", "colab_notebook.ipynb", "loader.py", "requirements.txt"]


def package_files(slug):
    return sorted([*PACKAGE_FILES, launcher_filename(slug)])


STALE = "never executed or auto-wrapped"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.settings = Settings(output_dir=Path(self.tmp.name) / "out")

    def assert_no_weight_requests(self, web):
        self.assertFalse([c for c in web.calls if c.endswith(WEIGHTS)], web.calls)


class TestValidGitHubWithSafetensorsModel(Base):
    def test_full_pipeline_writes_four_files_with_consistent_content(self):
        web, url, _ = github_repo_for("sst2")
        res = run_pipeline(url, self.settings, web.fetcher())
        self.assertTrue(res.ok, (res.error, res.notes))
        self.assertEqual(res.out_dir.name, "github__distilbert__distilbert-base-uncased-finetuned-sst-2-english")
        expected = package_files(res.out_dir.name)
        self.assertIn("loader.py", expected)
        self.assertNotIn("run_local.bat", expected)
        self.assertEqual(res.files, expected)
        self.assertEqual(sorted(p.name for p in res.out_dir.iterdir() if p.name != ".m2g_manifest.json"), expected)
        self.assertEqual(res.out_dir.parent.resolve(), self.settings.output_dir.resolve())
        data = json.loads((res.out_dir / "analysis.json").read_text(encoding="utf-8"))
        model_id = PRESETS["sst2"]["repo_id"]
        self.assertEqual(data["source"]["kind"], "github")
        self.assertEqual((data["model_source"]["kind"], data["model_source"]["repo_id"]), ("huggingface", model_id))
        self.assertEqual(data["revision"], SHA)
        self.assertEqual(data["task"], "text-classification")
        self.assertEqual(data["status"], "supported_with_warnings")
        self.assertEqual(data["reasons"], [])
        self.assertEqual(data["inputs"], [{"name": "text", "component": "gr.Textbox"}])
        self.assertEqual(data["outputs"], [{"name": "labels", "component": "gr.Label"}])
        self.assertFalse(data["trust_remote_code"])
        # app.py and loader.py are one package: MODEL_ID and the other model constants live in loader.py, so the pair is validated together
        app_py = (res.out_dir / "app.py").read_text(encoding="utf-8")
        loader_py = (res.out_dir / "loader.py").read_text(encoding="utf-8")
        require_package_link(app_py, loader_py)
        report = validate_app_code(combine_package_source(app_py, loader_py))
        self.assertEqual((report["model_id"], report["revision"]), (model_id, SHA))
        validate_requirements_text((res.out_dir / "requirements.txt").read_text(encoding="utf-8"))
        nb = json.loads((res.out_dir / "colab_notebook.ipynb").read_text(encoding="utf-8"))
        validate_notebook(nb)
        self.assert_no_weight_requests(web)

    def test_successful_adoption_keeps_no_github_refusal_text_anywhere(self):
        web, url, gh = github_repo_for("sst2")
        self.assertEqual(gh.reasons, [])
        for text in gh.warnings + gh.uncertainties:
            self.assertNotIn(STALE, text)
        self.assertFalse([u for u in gh.uncertainties if u.startswith(("Competing task", "README keywords", "Metadata for the linked"))])
        self.assertTrue(all(e.source.startswith("linked model") for e in gh.task_evidence))
        res = run_pipeline(url, self.settings, web.fetcher())
        self.assertNotIn(STALE, (res.out_dir / "analysis.json").read_text(encoding="utf-8"))
        self.assertEqual(len(gh.warnings), len(set(gh.warnings)))

    def test_huggingface_url_generation_still_works(self):
        web, _, _ = github_repo_for("sst2")
        res = run_pipeline("https://huggingface.co/" + PRESETS["sst2"]["repo_id"], self.settings, web.fetcher())
        self.assertTrue(res.ok, res.error)
        self.assertEqual(res.files, package_files(res.out_dir.name))
        self.assertFalse(res.out_dir.name.startswith("github__"))
        self.assertIsNone(res.analysis.model_source)


class TestRefusalsAreAccurateAndNotStale(Base):
    def refused_run(self, web, url):
        res = run_pipeline(url, self.settings, web.fetcher())
        self.assertIsNone(res.error)
        self.assertFalse(res.ok)
        self.assertIsNone(res.app_code)
        self.assertEqual(res.files, ["analysis.json"])
        self.assertEqual(sorted(p.name for p in res.out_dir.iterdir()), ["analysis.json"])
        self.assertEqual(res.analysis.status, "partial")
        self.assertIsNone(res.analysis.model_source)
        reasons = res.analysis.reasons
        self.assertEqual(len(reasons), len(set(reasons)), "duplicate refusal reasons")
        self.assertEqual(sum(STALE in r for r in reasons), 1, "the GitHub refusal sentence must appear exactly once")
        self.assert_no_weight_requests(web)
        return res

    def test_pickle_only_linked_model_is_rejected_with_a_clear_reason(self):
        web, url, _ = github_repo_for("pickle")
        res = self.refused_run(web, url)
        text = " ".join(res.analysis.reasons)
        self.assertIn("cannot be generated", text)
        self.assertIn("pickle", text)
        self.assertIn("safetensors", text)
        self.assertNotIn("analyse that URL", text)  # analysing the HF URL would be refused for the same reason
        self.assertNotIn(".;", text)

    def test_missing_link_is_rejected_and_hub_untouched(self):
        web = FakeWeb()
        add_github_repo(web, "acme/plain", readme="# Plain\nNo model link.\n")
        res = self.refused_run(web, "https://github.com/acme/plain")
        self.assertFalse([c for c in web.calls if "huggingface.co" in c])
        self.assertIsNone(res.analysis.primary_model)

    def test_ambiguous_link_is_rejected_without_guessing(self):
        web = FakeWeb()
        for m in ("foo/bar-a", "foo/bar-b"):
            add_hf_model(web, m, **{k: v for k, v in PRESETS["sst2"].items() if k != "repo_id"})
        add_github_repo(web, "acme/kit", readme="See https://huggingface.co/foo/bar-a and https://huggingface.co/foo/bar-b\n")
        res = self.refused_run(web, "https://github.com/acme/kit")
        self.assertIn("none could be confirmed", " ".join(res.analysis.reasons))
        self.assertFalse([c for c in web.calls if "huggingface.co/api/models" in c])

    def test_linked_model_without_a_pinned_revision_is_rejected(self):
        web, url, _ = github_repo_for("sst2")
        info = ins.inspect_url(url, web.fetcher())
        info.primary_model_info.revision_sha = None
        info.primary_model_info.source = dataclasses.replace(info.primary_model_info.source, revision=None)
        gh = an.analyze(info)
        self.assertFalse(gh.generatable)
        self.assertIn("no pinned commit", " ".join(gh.reasons))


class TestTaskContradiction(Base):
    def test_code_taking_image_or_audio_input_contradicts_a_text_model(self):
        cases = {
            "image": "from transformers import CLIPVisionModel\n\nclass Enc(CLIPVisionModel):\n    pass\n",
            "audio": "import torchaudio\nfrom transformers import Wav2Vec2Model\n\nclass A(Wav2Vec2Model):\n    pass\n",
        }
        for label, code in cases.items():
            with self.subTest(label):
                web, url, gh = github_repo_for("sst2", tree=["README.md", "requirements.txt", "pkg/model.py"],
                                               extra_files={"pkg/model.py": code})
                self.assertFalse(gh.generatable, gh.status)
                self.assertIn(f"{label} input", " ".join(gh.reasons))
                res = run_pipeline(url, self.settings, web.fetcher())
                self.assertEqual(res.files, ["analysis.json"])

    def test_readme_keywords_alone_cannot_override_declared_model_metadata(self):
        readme = readme_for(PRESETS["sst2"]["repo_id"], "x") + "\nspeech recognition speech recognition whisper asr\n"
        _, _, gh = github_repo_for("sst2", readme=readme)
        self.assertTrue(gh.generatable, gh.reasons)
        self.assertEqual(gh.task, "text-classification")
        self.assertEqual(gh.reasons, [])
        self.assertFalse([u for u in gh.uncertainties if u.startswith("Competing task")])
        self.assertTrue(all(e.source.startswith("linked model") for e in gh.task_evidence))


class TestTamperedAnalysis(Base):
    def gh(self):
        return github_repo_for("sst2")[2]

    def tampered(self, gh):
        ms = gh.model_source
        return {
            "refusal reasons present": dataclasses.replace(gh, reasons=["still refused"]),
            "task outside the supported set": dataclasses.replace(gh, task="evil-task"),
            "inputs do not match task": dataclasses.replace(gh, inputs=[{"name": "x", "component": "gr.Textbox'); import os #"}]),
            "outputs do not match task": dataclasses.replace(gh, outputs=[]),
            "model swapped to another repo": dataclasses.replace(gh, model_source=dataclasses.replace(
                ms, repo_id="evil/other", name="other", owner="evil", canonical_url="https://huggingface.co/evil/other")),
            "primary model mismatch": dataclasses.replace(gh, primary_model="evil/other"),
            "link role not primary": dataclasses.replace(gh, ui_hints={**gh.ui_hints, "hf_link_roles": {ms.repo_id: {"role": "backbone"}}}),
            "model url inconsistent": dataclasses.replace(gh, model_source=dataclasses.replace(ms, canonical_url="https://evil.example/x")),
            "remote code switched on": dataclasses.replace(gh, trust_remote_code=True),
            "pickle weights switched on": dataclasses.replace(gh, use_safetensors=False),
            "no revision": dataclasses.replace(gh, revision=None),
            "refused status": dataclasses.replace(gh, status="partial"),
        }

    def test_generator_rejects_every_tampering(self):
        gh = self.gh()
        for name, bad in self.tampered(gh).items():
            with self.subTest(name), self.assertRaises(gg.GenerationError):
                gg.generate_app(bad)

    def test_a_hugging_face_analysis_must_not_carry_a_model_source(self):
        a = dataclasses.replace(self.gh(), source=self.gh().model_source, host="huggingface")
        with self.assertRaises(gg.GenerationError):
            gg.generate_app(a)

    def test_tampered_dependencies_are_rejected(self):
        gh = self.gh()
        bad = dataclasses.replace(gh, dependencies=[*gh.dependencies, "evil @ git+https://evil.example/x.git"])
        with self.assertRaises(Exception):
            gg.generate_requirements(bad)

    def test_notebook_refuses_an_app_that_does_not_match_the_analysis(self):
        gh = self.gh()
        other = gg.generate_app(github_repo_for("gpt2")[2])
        with self.assertRaises(gg.GenerationError):
            generate_notebook(gh, other)

    def test_pipeline_refuses_to_write_a_tampered_analysis(self):
        web, url, gh = github_repo_for("sst2")
        bad = self.tampered(gh)["model swapped to another repo"]
        with mock.patch.object(pipeline, "analyze", return_value=bad):
            res = run_pipeline(url, self.settings, web.fetcher())
        self.assertIsNotNone(res.error)
        self.assertEqual(res.stage, "generate")
        self.assertIsNone(res.app_code)
        self.assertFalse(self.settings.output_dir.exists() and any(self.settings.output_dir.rglob("*.py")))

    def test_pipeline_detects_app_not_matching_analysis(self):
        web, url, gh = github_repo_for("sst2")
        other_app = gg.generate_app(github_repo_for("gpt2")[2])
        with mock.patch.object(pipeline, "generate_app", return_value=other_app):
            res = run_pipeline(url, self.settings, web.fetcher())
        self.assertIsNotNone(res.error)
        self.assertIn("does not match the analysis", res.error)
        self.assertFalse(self.settings.output_dir.exists() and any(self.settings.output_dir.rglob("*.py")))


if __name__ == "__main__":
    unittest.main()
