"""Independent GitHub model repositories (``Analysis.origin == "github-native"``): Part 1 tests.

A public GitHub AI repository qualifies for analysis and for the SCAFFOLD artifacts on its own evidence, without a linked Hugging Face
model. The generated app is an honest scaffold: it never loads weights, never runs repository code and never produces a model output.

Everything runs offline against the fake transport in fakehub.py: no network, no weights, no repository code. The scaffold is executed
in-process ONLY inside ``run_scaffold`` (a stub ``gradio`` module, sockets and subprocesses blocked) to observe what its own code does.

NOTE: ``inspector._inspect_github`` does not currently fill ``RepoInfo.config`` / ``config_paths`` / ``release_assets`` from a GitHub
repository (the fields and ``GH_MODEL_CONFIG_FILES`` exist, nothing populates them). Tests that need a model config or a release asset
therefore set those fields on the ``RepoInfo`` with ``dataclasses.replace`` and exercise the analyzer on them; the tests that go through
the real inspector rely on weight files in the repository tree plus a declared / imported ML framework.
"""
import ast
import dataclasses
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import urlsplit

from m2g import analyzer as an
from m2g import gradio_gen as gg
from m2g import inspector as ins
from m2g import local_runner
from m2g.inference_contract import build_contract, select_loader
from m2g import native_scaffold as ns_mod
from m2g import web as web_ui
from m2g.colab_gen import generate_notebook
from m2g.config import Settings
from m2g.opencode_cli import OpenCodeResult
from m2g.pipeline import run_pipeline, summarize
from m2g.security import SourceRef
from m2g.validate import combine_package_source, validate_app_code, validate_notebook, validate_requirements_text

from fakehub import FakeWeb, PRESETS, add_github_repo, hf as add_hf_preset
from test_analyzer import analyze_preset
from test_github_generation import github_repo_for, readme_for

WEIGHT_SUFFIXES = (".safetensors", ".bin", ".gguf", ".pt", ".pth", ".ckpt", ".onnx")
REPO = "acme/netzero"
NATIVE_TREE = ["README.md", "requirements.txt", "main.py", "checkpoints/netzero.safetensors"]
PLAIN_README = "# Acme NetZero\nA small PyTorch model. Nothing here links to Hugging Face.\n"
HOSTILE_WEIGHT = ("weights/x\"\"\"\nimport os\nos.system(\"boom\")#<script>alert(1)</script>@@@@@MODEL_ID@@.safetensors")
TOKEN = "ghp_" + "A" * 36


# --------------------------------------------------------------------------------------------------------------------- helpers


def native_info(*, tree=None, requirements="torch>=2.0\n", readme=PLAIN_README, extra_files=None, repo=REPO, **kwargs):
    """(web, url, RepoInfo) of a GitHub repository read through the real inspector against the fake transport."""
    web = FakeWeb()
    add_github_repo(web, repo, readme=readme, requirements=requirements, tree=list(NATIVE_TREE if tree is None else tree),
                    extra_files=extra_files, **kwargs)
    url = f"https://github.com/{repo}"
    return web, url, ins.inspect_url(url, web.fetcher())


def native_analysis(*, settings=None, **kwargs):
    web, url, info = native_info(**kwargs)
    return web, url, an.analyze(info, settings or Settings())


def with_config(info, cfg, path="config.json"):
    """The same RepoInfo with a parsed model config attached (see the module note: the inspector does not fill it for GitHub)."""
    return dataclasses.replace(info, config=cfg, config_paths={"config": path})


def run_scaffold(code, env=None):
    """Execute the generated single-module scaffold with a stub gradio, no sockets and no subprocesses. Returns (namespace, gradio_stub).
    ``env`` is the process environment the module sees while it is executed (M2G_MODEL_PATH is read at import time)."""
    gr = mock.MagicMock(name="gradio")
    gr.Error = type("Error", (Exception,), {})
    namespace = {"__name__": "scaffold_under_test"}
    environment = {"M2G_MODEL_PATH": "", **(env or {})}
    with mock.patch.dict(sys.modules, {"gradio": gr}), mock.patch.dict(os.environ, environment), \
            mock.patch("socket.socket", side_effect=AssertionError("the scaffold must not open a socket")), \
            mock.patch("subprocess.Popen", side_effect=AssertionError("the scaffold must not start a process")):
        exec(compile(code, "scaffold.py", "exec"), namespace)
    return namespace, gr


def calls_of(tree, name):
    return [n for n in ast.walk(tree) if isinstance(n, ast.Call)
            and getattr(n.func, "attr", getattr(n.func, "id", "")) == name]


# ------------------------------------------------------------------------------------------------------------ eligibility


class TestNativeEligibility(unittest.TestCase):
    def test_public_repository_without_a_huggingface_link_qualifies_on_its_own_evidence(self):
        _, _, a = native_analysis()
        self.assertEqual(a.origin, "github-native")
        self.assertTrue(a.generatable, (a.status, a.reasons))
        self.assertEqual(a.status, "supported_with_warnings")
        self.assertEqual(a.reasons, [])
        self.assertEqual(a.source.kind, "github")
        self.assertIsNone(a.model_source)  # no Hugging Face model is invented
        self.assertIs(a.model_ref, a.source)
        self.assertIsNone(a.primary_model)
        n = a.native
        self.assertTrue(n["eligible"])
        self.assertTrue(n["scaffold_only"])
        self.assertIn("weight-files", n["signals"])
        self.assertIn("ml-framework", n["signals"])
        self.assertEqual(n["weight_files"], ["checkpoints/netzero.safetensors"])
        self.assertTrue(any("no Hugging Face model is used" in w for w in a.warnings), a.warnings)
        self.assertFalse(a.inputs or a.outputs, "inputs/outputs of the repository's own model must not be fabricated")

    def test_no_huggingface_request_is_made_for_an_independent_repository(self):
        web, _, a = native_analysis()
        self.assertTrue(a.generatable)
        self.assertFalse([c for c in web.calls if "huggingface.co" in c], web.calls)

    def test_evidence_is_split_into_confirmed_inferred_and_unknown(self):
        _, _, a = native_analysis(readme="# Acme NetZero\nImage classification with PyTorch.\n")
        n = a.native
        self.assertEqual(a.task, "image-classification")
        self.assertLessEqual(a.task_confidence, 0.35)  # README keywords alone never establish a task firmly
        confirmed = " | ".join(n["confirmed"])
        self.assertIn("ML framework evidence", confirmed)
        self.assertIn("weight file(s)", confirmed)
        self.assertNotIn("task", confirmed.lower())  # a guessed task is never listed as confirmed
        self.assertTrue(any("task 'image-classification'" in s and "not a verified inference contract" in s for s in n["inferred"]),
                        n["inferred"])
        unknown = " | ".join(n["unknown"])
        self.assertIn("inference API and input/output contract", unknown)
        self.assertIn("GPU/VRAM requirements", unknown)
        self.assertTrue(all(u in " ".join(a.uncertainties) for u in n["unknown"]), "unknowns are also reported as uncertainties")

    def test_readme_task_evidence_is_retained_when_source_has_auxiliary_modality(self):
        # An audio import/function can be playback or an auxiliary endpoint; it
        # must not erase TTS evidence about the repository's primary model.
        _, _, a = native_analysis(
            readme="# Example\nThis is a text-to-speech (TTS) model. Text to speech is supported.\n",
            extra_files={"main.py": "import torchaudio\ndef play_audio(x): return x\n"},
        )
        tts = [e for e in a.task_evidence if e.task == "text-to-speech"]
        self.assertTrue(tts, a.task_evidence)
        self.assertEqual(a.task, "text-to-speech")
        self.assertLessEqual(a.task_confidence, 0.35)  # README alone is still heuristic
        self.assertTrue(any("Both were retained" in u for u in a.uncertainties), a.uncertainties)
        self.assertFalse(a.inputs or a.outputs, "conflicting hints do not invent a native inference contract")

    def test_native_opencode_is_called_with_complete_static_generation_evidence(self):
        _, _, a = native_analysis(tree=[*NATIVE_TREE, "weights/model.onnx"])
        a.native["approved_loader"] = "onnxruntime-data-only"
        a.native["inference_contract"] = {"inputs": ["text"], "outputs": ["audio"]}
        a.native["generation_evidence"] = {
            "artifact_path": "weights/model.onnx", "revision": a.revision or "a" * 40,
            "adapter_verified": True, "contract_verified": True,
            "preprocessing_evidence": True, "postprocessing_evidence": True,
        }
        class RecordingAdapter:
            def __init__(self): self.requests = []
            def run(self, request):
                self.requests.append(request)
                return OpenCodeResult(returncode=1, error="exit_nonzero", message="controlled response")
        adapter = RecordingAdapter()
        with self.assertRaises(gg.GenerationError):
            gg.generate_app(a, adapter=adapter)
        self.assertEqual(len(adapter.requests), 1)
        self.assertIn("native_generation_evidence", adapter.requests[0])
        self.assertIn("onnxruntime-data-only", adapter.requests[0])

    def test_native_opencode_is_not_called_when_static_evidence_is_incomplete(self):
        _, _, a = native_analysis(tree=[*NATIVE_TREE, "weights/model.onnx"])
        class RecordingAdapter:
            def __init__(self): self.requests = []
            def run(self, request): self.requests.append(request); return OpenCodeResult()
        adapter = RecordingAdapter()
        code = gg.generate_app(a, adapter=adapter)
        self.assertEqual(adapter.requests, [])
        self.assertIn("M2G_NATIVE_SCAFFOLD", code)

    def test_explicit_readme_onnx_contract_is_recorded_without_executing_source(self):
        readme = ("# Model\nInput: text\nOutput: audio\n"
                  "## Preprocessing\nTokenize text with the documented tokenizer.\n"
                  "## Postprocessing\nReturn PCM audio with the documented sample rate.\n")
        _, _, a = native_analysis(tree=[*NATIVE_TREE, "weights/model.onnx"], readme=readme)
        c = a.native["inference_contract"]
        self.assertEqual((c["inputs"], c["outputs"]), (["text"], ["audio"]))
        self.assertTrue(c["preprocessing"])
        self.assertTrue(c["postprocessing"])
        self.assertTrue(a.native["generation_evidence"]["preprocessing_evidence"])
        self.assertTrue(a.native["generation_evidence"]["postprocessing_evidence"])

    def test_unknown_architecture_stays_unknown_without_a_declaring_config(self):
        _, _, a = native_analysis()
        self.assertIn("model architecture (no config declares one)", a.native["unknown"])
        self.assertNotIn("declared architecture", " ".join(a.native["confirmed"]))
        _, _, info = native_info()
        a2 = an.analyze(with_config(info, {"model_type": "mystery"}))  # a model_type alone does not declare an architecture
        self.assertTrue(a2.generatable)
        self.assertIn("model architecture (no config declares one)", a2.native["unknown"])
        self.assertNotIn("declared architecture", " ".join(a2.native["confirmed"]))
        code = gg.generate_app(a2)
        self.assertIn("no config declares one", code)
        for invented in ("from_pretrained", "AutoModel", "pipeline(", "import torch", "import transformers", "diffusers"):
            self.assertNotIn(invented, code)

    def test_a_declared_architecture_is_confirmed_but_the_inference_contract_stays_unknown(self):
        _, _, info = native_info()
        a = an.analyze(with_config(info, {"architectures": ["NetZeroForClassification"], "model_type": "netzero"}))
        n = a.native
        self.assertTrue(any("declared architecture(s): NetZeroForClassification" in c for c in n["confirmed"]), n["confirmed"])
        self.assertNotIn("model architecture (no config declares one)", n["unknown"])
        self.assertTrue(any("inference API" in u for u in n["unknown"]), n["unknown"])
        self.assertFalse(a.inputs or a.outputs)

    def test_model_config_plus_a_framework_qualifies_without_weights(self):
        _, _, info = native_info(tree=["README.md", "requirements.txt", "main.py"])
        a = an.analyze(with_config(info, {"architectures": ["NetZero"]}))
        self.assertTrue(a.generatable, (a.status, a.reasons, a.native["missing"]))
        self.assertEqual(a.native["signals"], ["ml-framework", "model-config"])
        self.assertEqual(a.native["weight_files"], [])

    def test_release_asset_weights_count_as_listed_metadata_only(self):
        _, _, info = native_info(tree=["README.md", "requirements.txt", "main.py"])
        a = an.analyze(dataclasses.replace(info, release_assets=[("v1/netzero.safetensors", 1234)]))
        self.assertTrue(a.generatable, (a.status, a.reasons))
        self.assertEqual(a.native["release_weight_assets"], ["v1/netzero.safetensors"])
        self.assertIn("weight-files", a.native["signals"])

    def test_an_import_found_in_source_is_a_framework_signal(self):
        web, _, a = native_analysis(requirements=None, extra_files={"main.py": "import torch\n\nclass Net(torch.nn.Module):\n    pass\n"})
        self.assertTrue(a.generatable, (a.status, a.reasons, a.native["missing"]))
        self.assertIn("PyTorch", a.native["frameworks"])
        self.assertIn("ml-framework", a.native["signals"])

    def test_an_unreadable_linked_huggingface_model_does_not_block_an_independent_repository(self):
        # the README names acme/netzero on Hugging Face as the repository's own model, but nothing is served for it
        _, _, a = native_analysis(readme=readme_for(REPO, "netzero"))
        self.assertEqual(a.origin, "github-native")
        self.assertTrue(a.generatable, (a.status, a.reasons))
        self.assertIsNone(a.model_source)
        self.assertTrue(any("could not be read" in w for w in a.warnings), a.warnings)

    def test_a_linked_model_that_cannot_be_generated_does_not_block_an_independent_repository(self):
        _, _, a = github_repo_for("gguf", tree=[*NATIVE_TREE])
        self.assertEqual(a.origin, "github-native", (a.status, a.reasons))
        self.assertTrue(a.generatable)
        self.assertIsNone(a.model_source)
        self.assertTrue(any("cannot be generated" in w for w in a.warnings), a.warnings)
        code = gg.generate_app(a)
        self.assertIn(f"MODEL_ID = {a.source.repo_id!r}", code)  # the GitHub repository, not the linked Hugging Face model

    def test_pickle_only_weights_are_listed_and_flagged_not_trusted(self):
        _, _, a = native_analysis(tree=["README.md", "requirements.txt", "main.py", "weights/netzero.bin"])
        self.assertTrue(a.generatable)
        self.assertEqual(a.native["pickle_weights"], ["weights/netzero.bin"])
        self.assertEqual(a.native["safe_weights"], [])
        self.assertTrue(any("pickle-format weights" in w for w in a.warnings), a.warnings)


class TestNativeRejection(unittest.TestCase):
    def assert_rejected(self, a, *fragments):
        self.assertFalse(a.generatable, (a.status, a.reasons))
        self.assertEqual(a.status, "partial")
        self.assertFalse(a.native["eligible"])
        text = " ".join([*a.reasons, *a.native["missing"]])
        for fragment in fragments:
            self.assertIn(fragment, text)
        with self.assertRaises(gg.GenerationError):
            gg.generate_app(a)

    def test_a_non_ai_repository_is_rejected(self):
        _, _, a = native_analysis(tree=["README.md", "requirements.txt", "app.py"], requirements="flask\nrequests\n",
                                  readme="# Shop\nA web shop.\n")
        self.assert_rejected(a, "does not show enough evidence of being an AI model", "no model artifact")

    def test_readme_claims_and_entrypoint_names_never_qualify(self):
        readme = "# NetZero\nAn AI model: deep learning, neural network, LLM, image classification. Model weights available on request.\n"
        _, _, a = native_analysis(tree=["README.md", "requirements.txt", "inference.py", "predict.py", "demo.py"],
                                  requirements="requests\n", readme=readme, topics=("ai", "llm", "deep-learning"),
                                  description="A state of the art AI model")
        self.assert_rejected(a, "no model artifact")
        self.assertTrue(a.entrypoint_candidates)  # entry points were seen, and still do not qualify

    def test_a_data_file_in_a_non_ai_repository_is_not_enough(self):
        _, _, a = native_analysis(tree=["README.md", "requirements.txt", "app.py", "data/blob.bin"], requirements="flask\n")
        self.assert_rejected(a, "no ML framework detected")

    def test_weights_alone_without_a_second_signal_do_not_qualify(self):
        _, _, a = native_analysis(tree=["README.md", "model.safetensors"], requirements=None)
        self.assert_rejected(a, "no ML framework detected")

    def test_a_framework_alone_without_a_model_artifact_does_not_qualify(self):
        _, _, a = native_analysis(tree=["README.md", "requirements.txt", "main.py"])
        self.assert_rejected(a, "no model artifact")

    def test_a_model_config_alone_without_a_second_signal_does_not_qualify(self):
        _, _, info = native_info(tree=["README.md", "main.py"], requirements=None)
        a = an.analyze(with_config(info, {"architectures": ["NetZero"]}))
        self.assert_rejected(a, "no ML framework detected")

    def test_an_arbitrary_config_json_is_not_a_model_config(self):
        _, _, info = native_info(tree=["README.md", "requirements.txt", "main.py"])
        a = an.analyze(with_config(info, {"debug": True, "port": 8080}))
        self.assert_rejected(a, "no model artifact")

    def test_incomplete_metadata_when_the_api_is_unavailable_is_unknown_not_assumed(self):
        web = FakeWeb()
        add_github_repo(web, REPO, api_status=403, readme=PLAIN_README, requirements="torch\n")
        a = an.analyze(ins.inspect_url(f"https://github.com/{REPO}", web.fetcher()))
        self.assert_rejected(a, "no model artifact")
        self.assertTrue(any("api_unavailable" in u for u in a.uncertainties), a.uncertainties)

    def test_a_rejected_repository_gets_analysis_only_through_the_pipeline(self):
        web, url, _ = native_analysis(tree=["README.md", "requirements.txt", "app.py"], requirements="flask\n")
        with tempfile.TemporaryDirectory() as tmp:
            res = run_pipeline(url, Settings(output_dir=Path(tmp) / "out"), web.fetcher())
            self.assertIsNone(res.error)
            self.assertFalse(res.ok)
            self.assertIsNone(res.app_code)
            self.assertEqual(res.files, ["analysis.json"])
            self.assertFalse((res.out_dir / "app.py").exists())
            self.assertIn("Why it cannot be generated", summarize(res))

    def test_a_github_repository_whose_linked_model_is_refused_and_has_no_artifact_stays_refused(self):
        _, _, gh = github_repo_for("gguf")  # default tree: no weight file, no model config
        self.assertFalse(gh.generatable)
        self.assertEqual(gh.status, "partial")
        self.assertTrue(any("cannot be generated" in r for r in gh.reasons), gh.reasons)


# ---------------------------------------------------------------------------------------------------------------- integrity


class TestNativeIntegrity(unittest.TestCase):
    def setUp(self):
        _, _, self.a = native_analysis()
        self.assertTrue(self.a.generatable, (self.a.status, self.a.reasons))
        self.hf_source = SourceRef("huggingface", "a/b", "a", "b", None, "https://huggingface.co/a/b")

    def tampered(self, **changes):
        return dataclasses.replace(self.a, **changes)

    def test_an_untouched_native_analysis_passes_and_generates(self):
        gg.check_analysis_integrity(self.a)
        self.assertIn("M2G_NATIVE_SCAFFOLD = True", gg.generate_app(self.a))

    def test_every_tampering_of_the_recorded_evidence_is_refused(self):
        n = dict(self.a.native)
        cases = {
            "no evidence record": dict(native=None),
            "not eligible": dict(native={**n, "eligible": False}),
            "not scaffold only": dict(native={**n, "scaffold_only": False}),
            "no signals": dict(native={**n, "signals": []}),
            "framework only": dict(native={**n, "signals": ["ml-framework"]}),
            "weights only": dict(native={**n, "signals": ["weight-files"]}),
            "config only": dict(native={**n, "signals": ["model-config"]}),
            "a separate model source": dict(model_source=self.hf_source),
            "a Hugging Face source": dict(source=self.hf_source),
            "status supported": dict(status="supported"),
            "status partial": dict(status="partial"),
            "refusal reasons": dict(reasons=["refused"]),
            "blocked by safety checks": dict(generation_blocked_reasons=["blocked"]),
            "trust_remote_code": dict(trust_remote_code=True),
            "hostile repo id": dict(source=SourceRef("github", "a/b'; import os #", "a", "b'; import os #", None, "https://github.com/a/b")),
            "origin switched to huggingface": dict(origin="huggingface"),
        }
        for name, changes in cases.items():
            with self.subTest(name), self.assertRaises(gg.GenerationError):
                gg.generate_app(self.tampered(**changes))

    def test_the_integrity_check_names_what_is_wrong(self):
        for changes, fragment in (
                (dict(native={**self.a.native, "eligible": False}), "eligible independent GitHub model evidence"),
                (dict(native={**self.a.native, "signals": ["ml-framework"]}), "sufficient AI model evidence"),
                (dict(model_source=self.hf_source), "carry no separate model source"),
                (dict(trust_remote_code=True), "trust_remote_code"),
                (dict(reasons=["refused"]), "refusal reasons")):
            with self.subTest(fragment), self.assertRaises(gg.GenerationError) as cm:
                gg.check_analysis_integrity(self.tampered(**changes))
            self.assertIn(fragment, str(cm.exception))

    def test_an_huggingface_analysis_cannot_be_relabelled_as_native(self):
        hf = analyze_preset("sst2")
        with self.assertRaises(gg.GenerationError):
            gg.generate_app(dataclasses.replace(hf, origin="github-native"))

    def test_the_remote_code_opt_in_never_reaches_the_scaffold(self):
        settings = Settings(allow_remote_code=True)
        _, _, a = native_analysis(settings=settings)
        self.assertIn("TRUST_REMOTE_CODE = False", gg.generate_app(a, settings))

    def test_a_scaffold_that_was_altered_after_generation_is_refused(self):
        code = gg.build_native_scaffold(self.a)
        gg.finalize_native_scaffold(self.a, code)  # the untouched text passes
        altered = {
            "mark removed": code.replace(ns_mod.NATIVE_MARK, "M2G_OTHER = True"),
            "subprocess import": code.replace("import gradio as gr\n", "import gradio as gr\nimport subprocess\n", 1),
            "pickle import": code.replace("import gradio as gr\n", "import gradio as gr\nimport pickle\n", 1),
            "eval call": code + "\n\ndef helper():\n    return eval('1')\n",
            "os.system call": code + "\n\ndef helper():\n    os.system('x')\n",
            "remote code on": code.replace("TRUST_REMOTE_CODE = False", "TRUST_REMOTE_CODE = True"),
            "template placeholder": code.replace("TASK = ", "TASK = '@@X@@'  #", 1),
            "get_model removed": code.replace("def get_model():", "def get_model_x():"),
        }
        for name, text in altered.items():
            with self.subTest(name), self.assertRaises(gg.GenerationError):
                gg.finalize_native_scaffold(self.a, text)


# --------------------------------------------------------------------------------------------------------- scaffold honesty


class TestNativeScaffold(unittest.TestCase):
    def setUp(self):
        _, _, self.a = native_analysis(readme="# Acme NetZero\nImage classification with PyTorch.\n")
        self.code = gg.generate_app(self.a)

    def test_the_scaffold_states_what_it_is_and_invents_nothing(self):
        for text in ("M2G_NATIVE_SCAFFOLD = True", "TRUST_REMOTE_CODE = False", "USE_SAFETENSORS = True", 'SOURCE_HOST = "github"',
                     f"MODEL_ID = {REPO!r}", "SCAFFOLD ONLY"):
            self.assertIn(text, self.code)
        for invented in ("from_pretrained", "AutoModel", "pipeline(", "import torch", "import transformers", "diffusers"):
            self.assertNotIn(invented, self.code)
        report = validate_app_code(self.code)
        self.assertEqual(report["model_id"], REPO)

    def test_the_task_is_marked_inferred_in_the_scaffold(self):
        self.assertIn("TASK = 'image-classification'  # inferred from repository evidence, not a verified inference contract", self.code)

    def test_the_report_separates_confirmed_inferred_and_unknown(self):
        namespace, _ = run_scaffold(self.code)
        report = namespace["SCAFFOLD_REPORT"]
        for heading in ("SCAFFOLD ONLY", "Confirmed from repository metadata", "Inferred (not verified)", "Unknown (not assumed)",
                        "Weight files listed (never downloaded by this app)"):
            self.assertIn(heading, report)
        self.assertIn("checkpoints/netzero.safetensors", namespace["WEIGHT_FILES"])

    def test_predict_never_fabricates_a_model_output(self):
        namespace, gr = run_scaffold(self.code)
        for value in ("hello", "", None, b"\x00"):
            with self.assertRaises(gr.Error):
                namespace["predict"](value)
        kwargs = gr.Interface.call_args.kwargs
        self.assertIs(kwargs["fn"], namespace["predict"])
        self.assertIn("SCAFFOLD ONLY", kwargs["description"])
        self.assertIn("no inference is implemented", str(gr.Textbox.call_args_list))  # the output box says so in its label

    def test_get_model_never_loads_anything_whatever_the_environment_says(self):
        for path in ("", "weights/net.safetensors", "weights/net.onnx"):
            for consent in ("", "1", "true", "yes"):
                with self.subTest(path=path, consent=consent):
                    namespace, _ = run_scaffold(self.code, {"M2G_MODEL_PATH": path, "M2G_ALLOW_PICKLE_WEIGHTS": consent})
                    with self.assertRaises(NotImplementedError):
                        namespace["get_model"]()

    def test_pickle_weights_are_refused_and_no_consent_setting_exists(self):
        self.assertNotIn("ALLOW_PICKLE_WEIGHTS", self.code)  # neither the constant nor the environment variable
        for path in ("w/net.pt", "w/net.pth", "w/net.ckpt", "w/net.bin", "w/net.pkl", "w/net.pickle", "W/NET.CKPT"):
            for consent in ("", "1", "true", "on"):
                with self.subTest(path=path, consent=consent):
                    namespace, _ = run_scaffold(self.code, {"M2G_MODEL_PATH": path, "M2G_ALLOW_PICKLE_WEIGHTS": consent})
                    self.assertNotIn("ALLOW_PICKLE_WEIGHTS", namespace)
                    with self.assertRaises(RuntimeError) as cm:
                        namespace["get_model"]()
                    self.assertNotIsInstance(cm.exception, NotImplementedError)
                    self.assertIn("Pickle", str(cm.exception))
                    self.assertIn("no setting enables it", str(cm.exception))

    def test_the_settings_pickle_opt_in_does_not_turn_the_scaffold_into_a_loader(self):
        settings = Settings(allow_pickle_weights=True)
        _, _, a = native_analysis(settings=settings, tree=["README.md", "requirements.txt", "main.py", "weights/netzero.bin"])
        code = gg.generate_app(a, settings)
        namespace, _ = run_scaffold(code, {"M2G_MODEL_PATH": "weights/netzero.bin"})
        with self.assertRaises(RuntimeError) as cm:
            namespace["get_model"]()
        self.assertNotIsInstance(cm.exception, NotImplementedError)

    def test_scaffold_text_derived_from_the_repository_is_neutralised(self):
        _, _, info = native_info(tree=["README.md", "requirements.txt", "main.py", HOSTILE_WEIGHT],
                                 description="\"\"\"\nimport os\nos.system('x')\n", topics=("a\"b",))
        hostile_cfg = {"architectures": ["Net\"\"\"\nimport subprocess\nsubprocess.run('x')", "@@MODEL_ID@@", "<img src=x>"]}
        a = an.analyze(with_config(info, hostile_cfg))
        self.assertTrue(a.generatable, (a.status, a.reasons))
        code = gg.generate_app(a)
        tree = ast.parse(code)
        self.assertNotIn("@@", code)
        self.assertNotIn("<script>", code)
        self.assertNotIn("<img", code)
        self.assertEqual(calls_of(tree, "system"), [])
        self.assertEqual(calls_of(tree, "run"), [])
        imported = {alias.name for node in tree.body if isinstance(node, ast.Import) for alias in node.names}
        self.assertEqual(imported, {"os", "gradio"})
        validate_app_code(code)
        namespace, gr = run_scaffold(code)  # and it still behaves like every other scaffold
        with self.assertRaises(gr.Error):
            namespace["predict"]("x")

    def test_a_secret_looking_name_never_reaches_the_generated_files(self):
        _, _, a = native_analysis(tree=["README.md", "requirements.txt", "main.py", f"weights/{TOKEN}.safetensors"])
        try:
            code = gg.generate_app(a)
        except gg.GenerationError:
            return  # refusing to write it is also acceptable
        self.assertNotIn(TOKEN, code)

    def test_repository_requirements_are_never_installed_or_listed_for_install(self):
        evil = "torch\nevil-pkg==1.0\ngit+https://evil.example/x.git\n--index-url http://evil.example/simple\n"
        _, _, a = native_analysis(requirements=evil)
        self.assertTrue(a.generatable, (a.status, a.reasons, a.native["missing"]))
        code = gg.generate_app(a)
        text = gg.generate_requirements(a)
        validate_requirements_text(text)
        self.assertIn("gradio", text)
        for banned in ("evil", "torch", "git+"):
            self.assertNotIn(banned, text)
        self.assertIn("evil-pkg==1.0", a.repo_requirements)  # recorded for the person, never installed
        nb = generate_notebook(a, code)
        validate_notebook(json.loads(json.dumps(nb)))
        pip = "".join(next(c for c in nb["cells"] if c.get("id") == "m2g-03-pip")["source"])
        self.assertIn("gradio", pip)
        for banned in ("evil", "torch", "git+"):
            self.assertNotIn(banned, pip)
        self.assertNotIn("evil-pkg", json.dumps(nb))


# ------------------------------------------------------------------------------------- no weights, no repository code run


class TestNativeNeverTouchesWeightsOrRepositoryCode(unittest.TestCase):
    def test_no_weight_file_is_requested_during_inspection_analysis_and_generation(self):
        web, _, a = native_analysis(tree=[*NATIVE_TREE, "weights/extra.bin", "weights/extra.onnx", "model.gguf"])
        gg.generate_app(a)
        self.assertTrue(a.generatable)
        self.assertFalse([c for c in web.calls if c.lower().endswith(WEIGHT_SUFFIXES)], web.calls)
        self.assertFalse([c for c in web.calls if "/releases/download/" in c], web.calls)
        for call in web.calls:
            self.assertIn(urlsplit(call).hostname, {"api.github.com", "raw.githubusercontent.com"}, call)

    def test_repository_source_is_read_as_text_and_never_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            sentinel = str(Path(tmp) / "executed.txt")
            source = (f"open({sentinel!r}, 'w').write('the repository code ran')\n"
                      "import torch\n\nclass Net(torch.nn.Module):\n    pass\n")
            web, _, info = native_info(requirements=None, extra_files={"main.py": source})
            a = an.analyze(info)
            gg.generate_app(a)
            self.assertTrue(any(sc["path"] == "main.py" for sc in info.source_scan), "the source was scanned statically")
            self.assertFalse(os.path.exists(sentinel), "repository code must never be executed")
            self.assertIn("PyTorch", a.frameworks)
            self.assertTrue(a.generatable)

    def test_the_pipeline_starts_no_process_and_runs_no_command(self):
        web, url, _ = native_analysis()
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch("subprocess.Popen", side_effect=AssertionError("no process may be started")), \
                mock.patch("os.system", side_effect=AssertionError("no command may be run")):
            res = run_pipeline(url, Settings(output_dir=Path(tmp) / "out"), web.fetcher())
        self.assertTrue(res.ok, (res.error, res.notes))
        self.assertFalse([c for c in web.calls if c.lower().endswith(WEIGHT_SUFFIXES)])
        self.assertFalse([c for c in web.calls if "huggingface.co" in c])


# ------------------------------------------------------------------------------------------------------------------ pipeline


class TestNativePipeline(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.settings = Settings(output_dir=Path(self.tmp.name) / "out")

    def test_url_to_scaffold_artifacts_without_a_huggingface_model(self):
        web, url, _ = native_analysis(readme="# Acme NetZero\nImage classification with PyTorch.\n")
        res = run_pipeline(url, self.settings, web.fetcher())
        self.assertTrue(res.ok, (res.error, res.notes))
        self.assertEqual(res.analysis.origin, "github-native")
        self.assertTrue(res.out_dir.name.startswith("github__"), res.out_dir.name)
        self.assertEqual(res.files, sorted(["analysis.json", "app.py", "colab_notebook.ipynb", "loader.py", "requirements.txt",
                                            "run_" + res.out_dir.name + ".bat"]))
        self.assertIn("scaffold", res.steps["generation"]["detail"])
        app = (res.out_dir / "app.py").read_text(encoding="utf-8")
        loader = (res.out_dir / "loader.py").read_text(encoding="utf-8")
        report = validate_app_code(combine_package_source(app, loader))
        self.assertEqual(report["model_id"], REPO)
        self.assertIn("M2G_NATIVE_SCAFFOLD = True", loader)
        self.assertNotIn("ALLOW_PICKLE_WEIGHTS", loader)
        requirements = (res.out_dir / "requirements.txt").read_text(encoding="utf-8")
        validate_requirements_text(requirements)
        self.assertNotIn("torch", requirements)
        data = json.loads((res.out_dir / "analysis.json").read_text(encoding="utf-8"))
        self.assertEqual(data["origin"], "github-native")
        self.assertIsNone(data["model_source"])
        self.assertTrue(data["native"]["eligible"])
        self.assertTrue(data["native"]["scaffold_only"])
        self.assertIn("inference_contract", data["native"])
        self.assertIsNone(data["native"]["approved_loader"])
        self.assertEqual(data["native"]["weight_files"], ["checkpoints/netzero.safetensors"])
        manifest = json.loads((res.out_dir / ".m2g_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["readiness"]["state"], "scaffold")
        self.assertIn("safe loading contract", manifest["readiness"]["reason"])
        with self.assertRaises(local_runner.LocalRunError) as blocked:
            local_runner.verify_generated_app(res.out_dir, self.settings)
        self.assertEqual(blocked.exception.status, 422)
        self.assertIn("incomplete scaffold", blocked.exception.message)


class TestGenericInferenceContracts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.settings = Settings(output_dir=Path(self.tmp.name) / "out")

    def test_data_only_onnx_contract_selects_the_generic_loader(self):
        contract = build_contract(frameworks=["ONNX Runtime"], task="image-classification", inputs=["image"],
                                  outputs=["labels"], native={"weight_files": ["artifacts/model.onnx"],
                                  "architecture_files": ["configs/model.json"], "inference_entrypoints": ["predict.py"],
                                  "frameworks": ["ONNX Runtime"]}, revision="a" * 40)
        adapter = select_loader(contract)
        self.assertIsNotNone(adapter)
        self.assertEqual(adapter.name, "onnxruntime-data-only")
        self.assertEqual(contract["evidence"][0]["revision"], "a" * 40)

    def test_unsafe_or_unknown_contract_has_no_loader(self):
        unsafe = build_contract(frameworks=["PyTorch"], task="image-classification", inputs=["image"], outputs=["labels"],
                                native={"weight_files": ["weights/model.pt"]}, revision=None)
        self.assertIsNone(select_loader(unsafe))

    def test_the_notebook_is_a_scaffold_notebook_that_loads_and_downloads_nothing(self):
        web, url, _ = native_analysis()
        res = run_pipeline(url, self.settings, web.fetcher())
        self.assertTrue(res.ok, (res.error, res.notes))
        nb = json.loads((res.out_dir / "colab_notebook.ipynb").read_text(encoding="utf-8"))
        validate_notebook(nb)
        title = "".join(nb["cells"][0]["source"])
        self.assertIn("scaffold", title.lower())
        self.assertIn("Independent GitHub model repository", title)
        self.assertNotIn("Weights download on first run", title)
        load = "".join(next(c for c in nb["cells"] if c.get("id") == "m2g-07-load")["source"])
        self.assertIn("SCAFFOLD_REPORT", load)
        self.assertNotIn("getattr(importlib.import_module(MODEL_MODULE)", load)  # the model loader is never called as a self-test
        self.assertIn("implements no loading and no inference", load)
        self.assertEqual([c for c in web.calls if c.lower().endswith(WEIGHT_SUFFIXES)], [])

    def test_the_summary_and_the_web_banner_do_not_claim_working_inference(self):
        web, url, _ = native_analysis(readme="# Acme NetZero\nImage classification with PyTorch.\n")
        res = run_pipeline(url, self.settings, web.fetcher())
        self.assertTrue(res.ok, (res.error, res.notes))
        text = summarize(res)
        self.assertIn("scaffold only, no inference is implemented", text)
        self.assertIn("**Confirmed:**", text)
        self.assertIn("**Inferred:**", text)
        self.assertIn("**Unknown:**", text)
        self.assertNotIn("App generated from the linked Hugging Face model", text)
        banner = web_ui.generation_banner(res)
        self.assertIn("SCAFFOLD", banner)
        self.assertNotIn("A working Gradio app", banner)

    def test_generation_is_deterministic_across_runs(self):
        web, url, _ = native_analysis()
        first = run_pipeline(url, self.settings, web.fetcher())
        self.assertTrue(first.ok, (first.error, first.notes))
        before = {n: (first.out_dir / n).read_text(encoding="utf-8") for n in ("app.py", "loader.py", "requirements.txt")}
        second = run_pipeline(url, self.settings, web.fetcher())
        self.assertTrue(second.ok, (second.error, second.notes))
        self.assertEqual(second.out_dir, first.out_dir)
        for name, text in before.items():
            self.assertEqual((second.out_dir / name).read_text(encoding="utf-8"), text, name)

    def test_a_github_native_run_never_overwrites_a_huggingface_run_of_the_same_name(self):
        repo_id = PRESETS["sst2"]["repo_id"]
        web = FakeWeb()
        add_hf_preset(web, "sst2")
        add_github_repo(web, repo_id, readme=PLAIN_README, requirements="torch\n", tree=list(NATIVE_TREE))
        hf_res = run_pipeline("https://huggingface.co/" + repo_id, self.settings, web.fetcher())
        self.assertTrue(hf_res.ok, hf_res.error)
        before = (hf_res.out_dir / "app.py").read_text(encoding="utf-8")
        gh_res = run_pipeline("https://github.com/" + repo_id, self.settings, web.fetcher())
        self.assertTrue(gh_res.ok, (gh_res.error, gh_res.notes))
        self.assertEqual(gh_res.analysis.origin, "github-native")
        self.assertNotEqual(hf_res.out_dir, gh_res.out_dir)
        self.assertEqual((hf_res.out_dir / "app.py").read_text(encoding="utf-8"), before)


# ----------------------------------------------------------------------------------------------------- existing flows unchanged


class TestExistingFlowsAreUnchanged(unittest.TestCase):
    def test_a_huggingface_url_still_generates_a_real_app_with_its_pickle_gate(self):
        a = analyze_preset("sst2")
        self.assertEqual(a.origin, "huggingface")
        self.assertFalse(a.native)
        code = gg.generate_app(a)
        self.assertNotIn("M2G_NATIVE_SCAFFOLD", code)
        self.assertIn(f"MODEL_ID = {PRESETS['sst2']['repo_id']!r}", code)
        self.assertIn("ALLOW_PICKLE_WEIGHTS", code)  # the Hugging Face pickle consent is untouched
        self.assertIn("LOAD_SAFETENSORS", code)

    def test_a_github_repository_with_a_usable_linked_model_still_uses_that_model(self):
        _, _, a = github_repo_for("sst2")
        self.assertEqual(a.origin, "github-linked-hf")
        self.assertFalse(a.native)
        self.assertEqual(a.model_source.repo_id, PRESETS["sst2"]["repo_id"])
        code = gg.generate_app(a)
        self.assertNotIn("M2G_NATIVE_SCAFFOLD", code)
        self.assertIn("linked from GitHub repository", code)

    def test_a_linked_model_wins_over_native_evidence_when_it_is_usable(self):
        # weights and a framework are present too, yet the usable linked model remains the source of the app
        _, _, a = github_repo_for("sst2", tree=[*NATIVE_TREE])
        self.assertEqual(a.origin, "github-linked-hf")
        self.assertIsNotNone(a.model_source)

    def test_a_huggingface_pipeline_run_is_not_a_scaffold(self):
        with tempfile.TemporaryDirectory() as tmp:
            web = FakeWeb()
            add_hf_preset(web, "sst2")
            res = run_pipeline("https://huggingface.co/" + PRESETS["sst2"]["repo_id"], Settings(output_dir=Path(tmp) / "out"),
                               web.fetcher())
            self.assertTrue(res.ok, (res.error, res.notes))
            self.assertEqual(res.analysis.origin, "huggingface")
            self.assertNotIn("M2G_NATIVE_SCAFFOLD", res.app_code)
            self.assertNotIn("SCAFFOLD", web_ui.generation_banner(res))


if __name__ == "__main__":
    unittest.main()
