import json
import tempfile
import unittest
from pathlib import Path

from m2g import analyzer as an
from m2g import inspector as ins
from m2g.config import Settings
from m2g.pipeline import run_pipeline, summarize
from fakehub import FakeWeb, hf, add_github_repo, add_hf_model, PRESETS


def analyze_preset(key, settings=None, **override):
    web = FakeWeb()
    hf(web, key, **override)
    info = ins.inspect_url("https://huggingface.co/" + PRESETS[key]["repo_id"], web.fetcher())
    return an.analyze(info, settings or Settings())


SUPPORTED_EXPECT = {
    "sst2": "text-classification", "chat": "text-generation", "gpt2": "text-generation", "ner": "token-classification",
    "qa": "question-answering", "mask": "fill-mask", "zs": "zero-shot-classification", "sum": "summarization",
    "t5": "translation", "minilm": "sentence-similarity", "whisper": "automatic-speech-recognition",
    "audioclf": "audio-classification", "tts": "text-to-speech", "vit": "image-classification", "detr": "object-detection",
    "seg": "image-segmentation", "blip": "image-to-text", "vqa": "visual-question-answering", "sd": "text-to-image",
}


class TestTaskDetection(unittest.TestCase):
    def test_supported_presets(self):
        for key, task in SUPPORTED_EXPECT.items():
            a = analyze_preset(key)
            self.assertEqual(a.task, task, key)
            self.assertTrue(a.generatable, (key, a.status, a.reasons))
            self.assertTrue(a.inputs and a.outputs, key)
            self.assertTrue(a.task_evidence, key)
            self.assertGreaterEqual(a.task_confidence, 0.6, key)

    def test_all_supported_tasks_have_io_and_preset_coverage(self):
        covered = set(SUPPORTED_EXPECT.values())
        # tasks without a preset are still defined in the IO table
        for t in an.SUPPORTED_TASKS:
            self.assertIn(t, an.TASK_IO)
        self.assertGreaterEqual(len(covered), 18)

    def test_task_inferred_from_architecture_without_pipeline_tag(self):
        a = analyze_preset("notag")
        self.assertEqual(a.task, "text-classification")
        # ForSequenceClassification exists for several modalities, so it is recorded as the generic architecture signal (structural tier)
        self.assertEqual({e.signal for e in a.task_evidence}, {"architecture_generic"})
        self.assertEqual({e.tier for e in a.task_evidence}, {"structural"})
        self.assertEqual(a.task_confidence, 0.7)

    def test_generic_architecture_vs_explicit_tag_is_weak_evidence_not_conflict(self):
        # BertForSequenceClassification (generic head) vs an explicit image-classification pipeline_tag: the explicit declaration wins
        # and the generic mapping is reported as weak evidence, not as an evidence conflict.
        a = analyze_preset("conflict")
        self.assertEqual(a.task, "image-classification")
        self.assertEqual(a.task_confidence, 1.0)
        self.assertFalse(any("evidence conflict" in w.lower() for w in a.warnings))
        self.assertTrue(any("generic architecture-head mapping" in w for w in a.warnings))
        weak = next(c for c in a.capabilities if c.name == "text-classification")
        self.assertEqual(weak.role, "hint")
        self.assertFalse(weak.builds_interface)
        self.assertEqual({e["signal"] for e in weak.evidence}, {"architecture_generic"})
        self.assertFalse(any(c.role == "conflicting" for c in a.capabilities))

    def test_conflicting_evidence_is_reported(self):
        # Genuine conflict: a modality-specific (non-generic) head, ViTForImageClassification, against an incompatible explicit tag.
        a = analyze_preset("vit", pipeline_tag="text-classification")
        self.assertEqual(a.task, "text-classification")
        self.assertEqual(a.task_confidence, 0.6)  # 1.0 (explicit tag) reduced by the 0.6 conflict factor
        self.assertTrue(any("Evidence conflict" in w and "'image-classification'" in w for w in a.warnings))
        self.assertEqual(a.capabilities[0].name, "text-classification")
        rival = next(c for c in a.capabilities if c.name == "image-classification")
        self.assertEqual(rival.role, "conflicting")
        self.assertFalse(rival.builds_interface)
        self.assertEqual({e["signal"] for e in rival.evidence}, {"architecture"})

    def test_unknown_and_unsupported_tasks(self):
        a = analyze_preset("unknown")
        self.assertIsNone(a.task)
        self.assertEqual(a.status, "unsupported")
        a = analyze_preset("unsupported_task")
        self.assertEqual(a.status, "unsupported")
        self.assertTrue(any("reinforcement" in r for r in a.reasons))

    def test_chat_detection_and_io(self):
        a = analyze_preset("chat")
        self.assertTrue(a.chat)
        self.assertEqual(a.inputs[0]["component"], "gr.ChatInterface")
        self.assertFalse(analyze_preset("gpt2").chat)

    def test_diffusers_detection(self):
        a = analyze_preset("sd")
        self.assertEqual(a.library, "diffusers")
        self.assertTrue(any(d.startswith("diffusers") for d in a.dependencies))
        self.assertTrue(a.hardware.needs_gpu)

    def test_sentence_transformers_library(self):
        a = analyze_preset("minilm")
        self.assertEqual(a.library, "sentence-transformers")
        self.assertTrue(any(d.startswith("sentence-transformers") for d in a.dependencies))


class TestDependencies(unittest.TestCase):
    def names(self, a):
        return {d.split(">")[0].split("=")[0].split("[")[0] for d in a.dependencies}

    def test_conditional_dependencies(self):
        self.assertIn("timm", self.names(analyze_preset("detr")))
        self.assertNotIn("timm", self.names(analyze_preset("vit")))
        self.assertIn("soundfile", self.names(analyze_preset("whisper")))
        self.assertIn("pillow", self.names(analyze_preset("vit")))
        self.assertIn("sentencepiece", self.names(analyze_preset("t5")))
        self.assertNotIn("sentencepiece", self.names(analyze_preset("sst2")))

    def test_every_dependency_has_evidence_and_is_safe(self):
        a = analyze_preset("detr")
        for d in a.dependencies:
            name = d.split(">")[0].split("[")[0]
            self.assertIn(name, a.dependency_evidence)

    def test_transformers_floor_from_config(self):
        a = analyze_preset("sst2", config={"architectures": ["DistilBertForSequenceClassification"], "model_type": "distilbert",
                                           "transformers_version": "4.51.3"})
        self.assertEqual(a.min_transformers, "4.51.3")
        self.assertIn("transformers>=4.51.3", a.dependencies)
        a = analyze_preset("sst2", config={"architectures": ["DistilBertForSequenceClassification"], "model_type": "distilbert",
                                           "transformers_version": "4.20.0"})
        self.assertEqual(a.min_transformers, "4.44.0")


class TestHardware(unittest.TestCase):
    def test_small_model(self):
        hw = analyze_preset("sst2").hardware
        self.assertEqual(hw.params, 66_955_010)
        self.assertTrue(hw.runs_locally_cpu)
        self.assertTrue(hw.fits_free_colab_t4)
        self.assertFalse(hw.exceeds_colab)

    def test_7b_needs_4bit_on_t4(self):
        a = analyze_preset("big7b")
        self.assertEqual(a.status, "supported_with_warnings")
        self.assertTrue(a.hardware.suggest_4bit)
        self.assertFalse(a.hardware.fits_free_colab_t4)
        self.assertFalse(a.hardware.runs_locally_cpu)
        self.assertTrue(a.gated)
        self.assertTrue(any(d.startswith("bitsandbytes") for d in a.dependencies))
        self.assertTrue(a.load_in_4bit_recommended)

    def test_70b_exceeds_colab_is_a_warning_not_a_blocker(self):
        a = analyze_preset("huge70b")
        self.assertTrue(a.hardware.exceeds_colab)
        self.assertEqual(a.status, "supported_with_warnings")
        self.assertTrue(a.generatable, (a.status, a.reasons))
        self.assertEqual(a.reasons, [])
        self.assertTrue(any("exceeds a single Colab GPU" in w for w in a.warnings))

    def test_size_fallbacks(self):
        a = analyze_preset("sst2", safetensors_total=None)
        self.assertIn("file size", a.hardware.params_source)
        self.assertTrue(any("estimate" in w for w in a.warnings))


class TestSafetyGates(unittest.TestCase):
    def test_remote_code_blocked_by_default(self):
        a = analyze_preset("remote")
        self.assertEqual(a.status, "unsupported")
        self.assertFalse(a.trust_remote_code)
        self.assertTrue(any("trust_remote_code" in r for r in a.reasons))

    def test_remote_code_opt_in(self):
        a = analyze_preset("remote", settings=Settings(allow_remote_code=True))
        self.assertEqual(a.status, "supported_with_warnings")
        self.assertTrue(a.trust_remote_code)
        self.assertTrue(any("DANGER" in w for w in a.warnings))

    def test_pickle_only_generates_candidate_but_keeps_runtime_consent(self):
        blocked = analyze_preset("pickle")
        self.assertEqual(blocked.status, "supported_with_warnings")
        self.assertTrue(blocked.generatable)
        self.assertFalse(blocked.generation_blocked_reasons)
        self.assertTrue(blocked.ui_hints["safetensors_unavailable"])
        a = analyze_preset("pickle", settings=Settings(allow_pickle_weights=True))
        self.assertEqual(a.status, "supported_with_warnings")
        self.assertTrue(a.generatable)

    def test_indexed_safetensors_shards_are_safe_and_same_revision(self):
        files = [
            ("config.json", 800),
            ("model.safetensors.index.json", 900),
            ("model-00001-of-00002.safetensors", 100),
            ("model-00002-of-00002.safetensors", 100),
            ("pytorch_model.bin", 100),
            ("README.md", 100),
        ]
        web = FakeWeb()
        add_hf_model(web, "test/indexed-safe", files=files,
                     config={"architectures": ["BertForSequenceClassification"], "model_type": "bert"},
                     pipeline_tag="text-classification")
        info = ins.inspect_url("https://huggingface.co/test/indexed-safe", web.fetcher())
        a = an.analyze(info, Settings())
        self.assertTrue(info.has_verified_safetensors)
        self.assertTrue(a.generatable)
        self.assertTrue(a.use_safetensors)
        self.assertEqual(a.ui_hints["weight_format"]["safetensors_indexed"], True)

    def test_unsupported_formats(self):
        for key in ("gguf", "gptq", "lora"):
            a = analyze_preset(key)
            self.assertEqual(a.status, "unsupported", key)
            self.assertTrue(a.reasons, key)

    def test_bnb_prequantized_ok_with_gpu_note(self):
        a = analyze_preset("bnb4")
        self.assertEqual(a.status, "supported_with_warnings")
        self.assertEqual(a.quantization, "bitsandbytes")
        self.assertTrue(any(d.startswith("bitsandbytes") for d in a.dependencies))


class TestGitHub(unittest.TestCase):
    def test_partial_status_and_evidence(self):
        web = FakeWeb()
        add_github_repo(web, "acme/yolo-demo", readme="# Object detection with YOLO\nWeights at https://huggingface.co/acme/yolo-weights",
                        requirements="torch\nultralytics\ngradio\n", tree=["README.md", "requirements.txt", "app.py", "src/infer.py", "x/y/z.py"])
        info = ins.inspect_url("https://github.com/acme/yolo-demo", web.fetcher())
        a = an.analyze(info)
        self.assertEqual(a.status, "partial")
        self.assertFalse(a.generatable)
        self.assertEqual(a.task, "object-detection")
        self.assertLess(a.task_confidence, 0.6)
        self.assertEqual(a.linked_hf_models, ["acme/yolo-weights"])
        self.assertIn("app.py", a.entrypoint_candidates)
        self.assertTrue(any("Gradio" in w for w in a.warnings))
        # hardware is never invented: with no readable model metadata it is explicitly unknown
        self.assertFalse(a.hardware.needs_gpu)
        self.assertNotIn("T4", a.hardware.recommended_runtime)
        self.assertEqual(a.host, "github")

    def test_to_dict_is_json_serialisable(self):
        import json
        json.dumps(analyze_preset("sst2").to_dict())

    def test_repository_evidence_ledger_preserves_provenance_and_coverage(self):
        web = FakeWeb()
        add_github_repo(web, "acme/ledger", tree=["README.md", "requirements.txt", "pkg/model.py"],
                        extra_files={"pkg/model.py": "import torch\nclass Model: pass\n"})
        info = ins.inspect_url("https://github.com/acme/ledger", web.fetcher())
        result = an.analyze(info)
        self.assertTrue(result.evidence_ledger)
        self.assertTrue(all("source" in row and "finding" in row and "provenance" in row for row in result.evidence_ledger))
        self.assertIn("repository", result.investigation_coverage)

    def test_evidence_ledger_rows_carry_a_normalized_evidence_class(self):
        # Every ledger row is labelled with one of the five honest classes, and the coverage tally adds up to the row count: an
        # inference can never be read as an observation, and a recorded gap is never counted as a finding.
        from m2g.analyzer import _EVIDENCE_CLASSES
        web = FakeWeb()
        add_github_repo(web, "acme/ledger", tree=["README.md", "requirements.txt", "pkg/model.py"],
                        extra_files={"pkg/model.py": "import torch\nclass Model: pass\n"})
        result = an.analyze(ins.inspect_url("https://github.com/acme/ledger", web.fetcher()))
        self.assertTrue(result.evidence_ledger)
        self.assertTrue(all(row.get("evidence_class") in _EVIDENCE_CLASSES for row in result.evidence_ledger))
        tally = result.investigation_coverage["evidence_classes"]
        self.assertEqual(sum(tally.values()), len(result.evidence_ledger))
        self.assertEqual(set(tally), set(_EVIDENCE_CLASSES))

    def test_a_rea_finding_is_classified_as_rea_not_metadata(self):
        from m2g.analyzer import _evidence_class
        self.assertEqual(_evidence_class("REA", "REA"), "rea")
        self.assertEqual(_evidence_class("authoritative"), "metadata")
        self.assertEqual(_evidence_class("structural"), "source")
        self.assertEqual(_evidence_class("heuristic"), "inferred")
        self.assertEqual(_evidence_class("unknown"), "unknown")


FLAMINGO_README = (
    "# Flamingo Mini\n"
    "A small Flamingo-style language model. A language model reads interleaved images and text; the language model "
    "generates text.\n\n"
    "The pretrained model is available at https://huggingface.co/dhansmair/flamingo-mini\n\n"
    "Acknowledgements: built on the language model facebook/opt-125m (https://huggingface.co/facebook/opt-125m) as lm: and the "
    "vision encoder https://huggingface.co/openai/clip-vit-large-patch14 (backbone).\n"
)
FLAMINGO_SETUP = (
    "from setuptools import setup\n"
    "setup(name='flamingo-mini', install_requires=['torch>=2.0', 'transformers>=4.30'], keywords=['vision-language'])\n"
)
FLAMINGO_MODELING = (
    "import torch\n"
    "from transformers import CLIPVisionModel, OPTForCausalLM, PreTrainedModel\n\n"
    "class FlamingoModel(PreTrainedModel):\n"
    "    def generate(self, pixel_values, input_ids):\n"
    "        return input_ids\n"
)


def flamingo_info(web, primary_model=False, **primary_kw):
    add_github_repo(web, "dhansmair/flamingo-mini", readme=FLAMINGO_README, requirements=None, description="Flamingo mini",
                    tree=["README.md", "setup.py", "flamingo_mini/modeling.py"],
                    extra_files={"setup.py": FLAMINGO_SETUP, "flamingo_mini/modeling.py": FLAMINGO_MODELING})
    if primary_model:
        add_hf_model(web, "dhansmair/flamingo-mini", **primary_kw)
    return ins.inspect_url("https://github.com/dhansmair/flamingo-mini", web.fetcher())


class TestGitHubEvidenceRanking(unittest.TestCase):
    """Regression: a vision-language GitHub repo must not be called text-generation from README keywords."""

    def test_static_structure_beats_readme_keywords_and_host_is_not_the_library(self):
        a = an.analyze(flamingo_info(FakeWeb()))
        self.assertEqual(a.host, "github")
        self.assertNotEqual(a.library, "github")
        self.assertEqual(a.library, "transformers")
        self.assertIn("PyTorch", a.frameworks)
        self.assertEqual(a.task, "image-text-to-text")
        self.assertGreater(a.task_confidence, 0.35)
        self.assertIn("image", a.input_modalities)
        self.assertIn("text", a.input_modalities)
        self.assertEqual(a.output_modalities, ["text"])
        self.assertTrue(any(e.signal == "source_modalities" and e.tier == "structural" for e in a.task_evidence))
        # Conflicting README evidence remains visible rather than being erased
        # solely because static source structure ranks a different task higher.
        self.assertTrue(any(e.signal == "readme_keyword" for e in a.task_evidence))
        self.assertTrue(any("Both were retained" in u for u in a.uncertainties))
        self.assertEqual(a.status, "partial")

    def test_dependencies_come_from_static_setup_py_parse_with_evidence(self):
        a = an.analyze(flamingo_info(FakeWeb()))
        self.assertEqual(a.dependencies, ["torch", "transformers"])
        self.assertIn("setup.py", a.dependency_evidence["torch"])

    def test_primary_model_is_ranked_above_backbones(self):
        a = an.analyze(flamingo_info(FakeWeb()))
        self.assertEqual(a.primary_model, "dhansmair/flamingo-mini")
        self.assertIn("facebook/opt-125m", a.linked_hf_models)
        roles = a.ui_hints["hf_link_roles"]
        self.assertEqual(roles["dhansmair/flamingo-mini"]["role"], "primary")
        self.assertNotEqual(roles["facebook/opt-125m"]["role"], "primary")
        self.assertTrue(any("huggingface.co/dhansmair/flamingo-mini" in r for r in a.reasons))

    def test_hardware_is_unknown_not_invented_when_model_metadata_is_unreadable(self):
        a = an.analyze(flamingo_info(FakeWeb()))  # primary model metadata returns 404 here
        self.assertIsNone(a.hardware.params)
        self.assertFalse(a.hardware.needs_gpu)
        self.assertIn("Unknown", a.hardware.recommended_runtime)
        self.assertNotIn("T4", a.hardware.recommended_runtime)
        self.assertTrue(any("could not be read" in u for u in a.uncertainties))
        self.assertTrue(any("not established" in u for u in a.uncertainties))

    def test_readable_primary_model_metadata_is_authoritative_and_sizes_hardware(self):
        web = FakeWeb()
        info = flamingo_info(web, primary_model=True, pipeline_tag="image-to-text", safetensors_total=500_000_000,
                             config={"architectures": ["FlamingoModel"], "model_type": "flamingo"},
                             files=[("config.json", 800), ("model.safetensors", 1_000_000_000), ("README.md", 100)])
        self.assertIsNotNone(info.primary_model_info)
        a = an.analyze(info)
        self.assertEqual(a.task, "image-to-text")
        self.assertGreaterEqual(a.task_confidence, 0.9)
        self.assertTrue(any(e.tier == "authoritative" and "linked model" in e.source for e in a.task_evidence))
        self.assertEqual(a.hardware.params, 500_000_000)
        self.assertEqual(a.hardware.params_source, "Hub safetensors metadata")
        self.assertIn("primary_model_status", a.ui_hints)
        self.assertEqual(a.input_modalities, ["image"])
        # The repo's own linked model was identified, readable and passed the full Hugging Face analysis, so the app is generated
        # THROUGH that Hugging Face model (never from the GitHub code), and no weight file was ever requested.
        self.assertTrue(a.generatable, a.reasons)
        self.assertEqual(a.source.kind, "github")
        self.assertEqual(a.model_ref.repo_id, "dhansmair/flamingo-mini")
        self.assertEqual(a.model_ref.kind, "huggingface")
        self.assertFalse([c for c in web.calls if c.endswith((".safetensors", ".bin", ".gguf"))])

    def test_keywords_alone_never_give_high_confidence(self):
        web = FakeWeb()
        add_github_repo(web, "acme/chatty", readme="# Chat\nA language model chatbot. LLM text generation. language model.\n" * 4,
                        requirements=None, tree=["README.md"])
        a = an.analyze(ins.inspect_url("https://github.com/acme/chatty", web.fetcher()))
        self.assertEqual(a.task, "text-generation")
        self.assertLessEqual(a.task_confidence, 0.35)
        self.assertTrue(all(e.tier == "heuristic" for e in a.task_evidence))
        self.assertEqual(a.library, "unknown")
        self.assertNotIn("T4", a.hardware.recommended_runtime)
        self.assertFalse(a.hardware.needs_gpu)

    def test_analysis_json_and_summary_use_the_new_fields(self):
        web = FakeWeb()
        flamingo_info(web)  # registers the routes; the pipeline re-inspects through the same fake web
        with tempfile.TemporaryDirectory() as tmp:
            res = run_pipeline("https://github.com/dhansmair/flamingo-mini", Settings(output_dir=Path(tmp) / "out"), web.fetcher())
            self.assertIsNone(res.error, res.error)
            self.assertFalse(res.ok)
            data = json.loads((res.out_dir / "analysis.json").read_text(encoding="utf-8"))
        for key in ("host", "frameworks", "uncertainties", "input_modalities", "output_modalities", "primary_model"):
            self.assertIn(key, data)
        self.assertEqual(data["host"], "github")
        self.assertTrue(all("tier" in e for e in data["task_evidence"]))
        text = summarize(res)
        self.assertIn("**Host:** `github`", text)
        self.assertIn("Uncertainties", text)
        self.assertIn("[structural]", text)

    def test_hf_analysis_has_host_tiers_and_modalities(self):
        a = analyze_preset("vit")
        self.assertEqual(a.host, "huggingface")
        self.assertEqual(a.input_modalities, ["image"])
        self.assertEqual({e.tier for e in a.task_evidence if e.signal == "pipeline_tag"}, {"authoritative"})
        self.assertIn("transformers", a.frameworks)


if __name__ == "__main__":
    unittest.main()
