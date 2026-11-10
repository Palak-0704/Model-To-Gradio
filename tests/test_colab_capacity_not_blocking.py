"""Regression: a model that exceeds Google Colab's estimated VRAM is still analysed AND generatable.

A Colab capacity limit is a hardware/deployment warning (the Gradio app can run on any machine with enough GPU memory). It must not land
in ``Analysis.reasons`` (which makes the analysis ``unsupported``). Genuine security / repository-inspection / generation blockers must
keep blocking, even for an oversized model. Everything runs offline against fakehub.
"""
import tempfile
import unittest
from pathlib import Path

from m2g import gradio_gen as gg
from m2g import analyzer as an
from m2g import inspector as ins
from m2g.colab_gen import generate_notebook
from m2g.config import Settings
from m2g.local_script import launcher_filename
from m2g.pipeline import run_pipeline
from m2g.validate import validate_notebook
from fakehub import FakeWeb, PRESETS, add_hf_model, hf
from test_analyzer import analyze_preset
from test_github_generation import github_repo_for

COLAB_WARNING = "exceeds a single Colab GPU"
# The package is app.py + loader.py + requirements.txt + a launcher named after the model folder (run_<slug>.bat), plus the platform records.
PACKAGE_FILES = ["analysis.json", "app.py", "colab_notebook.ipynb", "loader.py", "requirements.txt"]


def all_artifacts(slug):
    return sorted([*PACKAGE_FILES, launcher_filename(slug)])


PICKLE_ONLY_FILES = [("config.json", 800), ("pytorch_model.bin", 140_000_000_000), ("README.md", 100)]


class TestColabCapacityIsOnlyAWarning(unittest.TestCase):
    def test_oversized_model_has_warning_but_no_refusal_reason(self):
        a = analyze_preset("huge70b")
        self.assertTrue(a.hardware.exceeds_colab)
        self.assertFalse(a.hardware.fits_free_colab_t4)
        self.assertEqual(a.reasons, [])
        self.assertEqual(a.generation_blocked_reasons, [])
        self.assertTrue(any(COLAB_WARNING in w for w in a.warnings), a.warnings)
        self.assertEqual(a.status, "supported_with_warnings")
        self.assertTrue(a.generatable)

    def test_model_that_fits_colab_gets_no_capacity_warning(self):
        a = analyze_preset("sst2")
        self.assertFalse(any(COLAB_WARNING in w for w in a.warnings))


class TestGenuineBlockersStillBlockOversizedModels(unittest.TestCase):
    """The oversized fixture combined with a real blocker: still refused, and the Colab warning never hides or replaces the blocker."""

    def test_pickle_only_weights_do_not_hide_capacity_warning(self):
        a = analyze_preset("huge70b", files=PICKLE_ONLY_FILES)
        self.assertTrue(a.hardware.exceeds_colab)
        self.assertTrue(a.generatable)
        self.assertFalse(a.generation_blocked_reasons)
        self.assertTrue(any(COLAB_WARNING in w for w in a.warnings))

    def test_remote_code_still_blocks_unsupported(self):
        cfg = {"architectures": ["LlamaForCausalLM"], "model_type": "llama",
               "auto_map": {"AutoModelForCausalLM": "modeling_custom.CustomForCausalLM"}}
        a = analyze_preset("huge70b", config=cfg)
        self.assertTrue(a.hardware.exceeds_colab)
        self.assertEqual(a.status, "unsupported")
        self.assertFalse(a.generatable)
        self.assertTrue(any("trust_remote_code" in r for r in a.reasons))
        self.assertFalse(any(COLAB_WARNING in r for r in a.reasons))  # the capacity text is never a refusal reason

    def test_adapter_only_repo_still_blocks_unsupported(self):
        a = analyze_preset("huge70b", library_name="peft",
                           adapter_config={"base_model_name_or_path": "meta-llama/Llama-2-70b-hf", "peft_type": "LORA"})
        self.assertEqual(a.status, "unsupported")
        self.assertFalse(a.generatable)
        self.assertTrue(any("Adapter-only" in r for r in a.reasons))

    def test_generators_can_write_a_pickle_guarded_candidate_for_oversized_model(self):
        a = analyze_preset("huge70b", files=PICKLE_ONLY_FILES)
        code = gg.generate_app(a)
        self.assertIn("LOAD_SAFETENSORS", code)
        self.assertIn("PICKLE_WEIGHTS_NOTICE", code)
        validate_notebook(generate_notebook(a))


class TestOversizedModelGeneratesArtifacts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = Path(self.tmp.name) / "out"

    def test_generators_accept_an_oversized_model(self):
        a = analyze_preset("huge70b")
        self.assertTrue(gg.generate_app(a))
        validate_notebook(generate_notebook(a))

    def test_pipeline_passes_eligibility_and_writes_every_artifact(self):
        web = FakeWeb()
        hf(web, "huge70b")
        res = run_pipeline("https://huggingface.co/" + PRESETS["huge70b"]["repo_id"], Settings(output_dir=self.out), web.fetcher())
        self.assertIsNone(res.error)
        self.assertFalse(res.analysis_only)
        self.assertEqual(res.steps["eligibility"]["status"], "ok")
        self.assertTrue(res.ok, (res.error, res.notes))
        self.assertEqual(res.out_dir.name, "acme__giant-70b")
        self.assertEqual(res.files, all_artifacts(res.out_dir.name))
        self.assertIn("loader.py", res.files)
        self.assertNotIn("run_local.bat", res.files)
        self.assertTrue(any(COLAB_WARNING in w for w in res.analysis.warnings))  # the hardware warning is still reported to the user

    def test_github_repo_with_oversized_linked_model_is_generatable(self):
        _, _, gh = github_repo_for("huge70b")
        self.assertTrue(gh.generatable, (gh.status, gh.reasons))
        self.assertEqual(gh.model_ref.repo_id, PRESETS["huge70b"]["repo_id"])
        self.assertTrue(any(COLAB_WARNING in w for w in gh.warnings))


class TestMissingEvidenceAndUnknownHardwareNeverBlockAnalysis(unittest.TestCase):
    """Requirement #5: missing evidence and unknown/incompatible hardware are warnings or unknowns, never refusal reasons. Analysis
    and generation must still complete when the model itself is established by firm evidence. These run the real Analyzer over the
    fake Hub; nothing is downloaded or executed."""

    def _analyze(self, repo_id="acme/x", **override):
        web = FakeWeb()
        add_hf_model(web, repo_id, **override)
        info = ins.inspect_url(f"https://huggingface.co/{repo_id}", web.fetcher())
        return an.analyze(info), info

    def test_missing_readme_is_not_a_refusal_reason(self):
        a, _ = self._analyze(pipeline_tag="text-classification",  # no README.md
                             files=[("config.json", 800), ("model.safetensors", 300_000_000)],
                             config={"architectures": ["BertForSequenceClassification"], "model_type": "bert"})
        self.assertEqual(a.status, "supported_with_warnings")
        self.assertTrue(a.generatable)
        self.assertEqual(a.reasons, [])

    def test_incomplete_config_is_reported_but_does_not_block(self):
        a, _ = self._analyze(pipeline_tag="text-classification",  # no config.json
                             files=[("model.safetensors", 300_000_000), ("README.md", 100)])
        self.assertTrue(a.generatable, (a.status, a.reasons))
        self.assertEqual(a.reasons, [])
        self.assertTrue(any("config.json could not be read" in w for w in a.warnings))

    def test_unknown_hardware_is_reported_and_still_generatable(self):
        # A well-defined model whose weight file size (and safetensors metadata) is unknown: hardware is 'unknown', not a blocker.
        a, _ = self._analyze(pipeline_tag="text-classification", files=[("config.json", 800), ("model.safetensors", 0), ("README.md", 100)],
                             config={"architectures": ["BertForSequenceClassification"], "model_type": "bert"})
        self.assertEqual(a.hardware.params, None)
        self.assertEqual(a.hardware.params_source, "unknown")
        self.assertTrue(a.generatable)
        self.assertEqual(a.reasons, [])

    def test_hardware_incompatibility_never_reaches_the_refusal_reasons(self):
        a = analyze_preset("huge70b")
        self.assertTrue(a.hardware.exceeds_colab)
        self.assertEqual(a.reasons, [])
        self.assertEqual(a.generation_blocked_reasons, [])
        self.assertTrue(a.generatable)
        self.assertNotIn("hardware", " ".join(a.generation_blocked_reasons).lower())


if __name__ == "__main__":
    unittest.main()
