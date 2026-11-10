"""Unit tests for the deep, static source analysis (m2g.source_analysis) and the normalized repository model (m2g.repo_model).

These are REAL unit tests over real code: the parser, the import resolver, the call graph, the loading-site detector and the
I/O contract are exercised directly on source text. Nothing is mocked, nothing is executed and nothing is downloaded.
"""
import unittest

from m2g import repo_model as rm
from m2g import source_analysis as sa
from m2g.inspector import RepoInfo
from m2g.security import parse_source_url


MODEL_PY = '''
import os
import torch
from transformers import AutoModelForSequenceClassification, AutoTokenizer
from .utils import helper
from pkg.missing import gone

class SentimentModel(torch.nn.Module):
    def __init__(self):
        super().__init__()
        self.model = None

    def load(self, path):
        self.model = AutoModelForSequenceClassification.from_pretrained(
            "distilbert/distilbert-base-uncased-finetuned-sst-2-english", device_map="auto")
        return self.model

    def predict(self, text):
        tok = AutoTokenizer.from_pretrained("distilbert-base-uncased")
        enc = tok(text, return_tensors="pt", padding=True, truncation=True)
        logits = self.model(**enc).logits
        return torch.softmax(logits, dim=-1).argmax(dim=-1)
'''

UTILS_PY = '''
def helper(x):
    return x
'''

MAIN_PY = '''
from pkg.model import SentimentModel

def main(text):
    m = SentimentModel()
    m.load("local")
    return m.predict(text)
'''


def analyse(files):
    listed = [(p, len(t)) for p, t in files] + [("pkg/missing.py", 5), ("README.md", 10)]
    return sa.analyze_repository(files, listed=listed)


class TestModuleNaming(unittest.TestCase):
    def test_module_name_for(self):
        self.assertEqual(sa.module_name_for("pkg/model.py"), ("pkg.model", False))
        self.assertEqual(sa.module_name_for("pkg/__init__.py"), ("pkg", True))
        self.assertEqual(sa.module_name_for("top.py"), ("top", False))
        self.assertEqual(sa.module_name_for("a/b/c.py"), ("a.b.c", False))


class TestImportResolution(unittest.TestCase):
    def test_resolution_states_are_explicit_and_honest(self):
        src = analyse([("pkg/model.py", MODEL_PY), ("pkg/utils.py", UTILS_PY), ("pkg/main.py", MAIN_PY)])
        states = {(e.target, e.state) for e in src.import_edges}
        self.assertIn(("pkg.utils", "relative_local"), states)  # a file that really is in the repository
        self.assertIn(("transformers", "third_party"), states)
        self.assertIn(("torch", "third_party"), states)
        self.assertIn(("os", "stdlib"), states)
        self.assertIn(("pkg.missing", "unresolved"), states)  # named but absent: never pretended to resolve

    def test_unresolved_imports_are_reported(self):
        src = analyse([("pkg/model.py", MODEL_PY)])
        self.assertTrue(any("missing" in u for u in src.unresolved_imports))

    def test_absolute_local_import_resolves_to_the_module(self):
        src = analyse([("pkg/model.py", MODEL_PY), ("pkg/utils.py", UTILS_PY), ("pkg/main.py", MAIN_PY)])
        main_edges = [e for e in src.import_edges if e.source == "pkg.main"]
        self.assertEqual(main_edges[0].target, "pkg.model")
        self.assertEqual(main_edges[0].state, "resolved_local")


class TestCallGraphAndLoadSites(unittest.TestCase):
    def setUp(self):
        self.src = analyse([("pkg/model.py", MODEL_PY), ("pkg/utils.py", UTILS_PY), ("pkg/main.py", MAIN_PY)])

    def test_loading_sites_carry_framework_family_and_model_id(self):
        calls = {s.call: s for s in self.src.load_sites}
        self.assertIn("AutoModelForSequenceClassification.from_pretrained", calls)
        site = calls["AutoModelForSequenceClassification.from_pretrained"]
        self.assertEqual(site.framework, "transformers")
        self.assertEqual(site.family, "from_pretrained")
        self.assertEqual(site.model_id, "distilbert/distilbert-base-uncased-finetuned-sst-2-english")
        self.assertEqual(site.kwargs.get("device_map"), "auto")

    def test_frameworks_are_detected_from_loading_calls(self):
        self.assertIn("transformers", self.src.frameworks)

    def test_inference_sites_are_found(self):
        kinds = {s["kind"] for s in self.src.infer_sites}
        self.assertTrue({"generate", "predict"} & kinds, kinds)

    def test_entrypoints_include_prediction_functions(self):
        names = {(e["module"], e["function"]) for e in self.src.entrypoints}
        self.assertIn(("pkg.model", "predict"), names)
        self.assertIn(("pkg.main", "main"), names)

    def test_call_edges_are_tagged_with_their_kind(self):
        kinds = {e.kind for e in self.src.call_edges}
        self.assertTrue(kinds <= {"resolved_local", "possible", "unresolved"})


class TestIOContract(unittest.TestCase):
    def test_contract_records_evidence_backed_inputs_and_outputs(self):
        src = analyse([("pkg/model.py", MODEL_PY), ("pkg/utils.py", UTILS_PY), ("pkg/main.py", MAIN_PY)])
        io = src.io_contract
        self.assertTrue(any(i["modality"] == "text" for i in io["inputs"]), io["inputs"])
        self.assertTrue(io["postprocessing"], io["postprocessing"])
        self.assertIn("distilbert/distilbert-base-uncased-finetuned-sst-2-english", io["model_ids"])
        self.assertTrue(io["contract_verified"])

    def test_contract_lists_unknowns_instead_of_inventing_them(self):
        src = analyse([("pkg/empty.py", "x = 1\n")])
        io = src.io_contract
        self.assertEqual(io["inputs"], [])
        self.assertFalse(io["contract_verified"])
        self.assertTrue(any("input" in u for u in io["unknown"]))

    def test_signature_modality_is_read_from_the_parameter_name(self):
        code = "def predict(self, image):\n    return image\n"
        src = analyse([("m.py", code)])
        self.assertTrue(any(i["modality"] == "image" for i in src.io_contract["inputs"]), src.io_contract["inputs"])

    def test_cross_file_inference_flow_is_reconstructed(self):
        # A `predict` method that loads the model, tokenizes, calls the model and post-processes: the flow is rebuilt from the
        # enclosing function of each call, cross-file, and reported as complete.
        code = (
            "import torch\n"
            "from transformers import AutoModelForSequenceClassification, AutoTokenizer\n"
            "class M:\n"
            "    def predict(self, text):\n"
            "        tok = AutoTokenizer.from_pretrained('x')\n"
            "        enc = tok(text, return_tensors='pt')\n"
            "        model = AutoModelForSequenceClassification.from_pretrained('x')\n"
            "        out = model(**enc)\n"
            "        return torch.softmax(out.logits, dim=-1)\n"
        )
        src = analyse([("m.py", code)])
        flows = src.io_contract["inference_flow"]
        self.assertTrue(flows, "a loading site in a predict function must produce an inference flow")
        flow = flows[0]
        self.assertEqual(flow["function"], "M.predict")
        self.assertTrue(flow["inference"], flow)
        self.assertNotIn("inference", flow["missing"])

    def test_inference_flow_reports_missing_steps_not_invents_them(self):
        # The loading site's function has no preprocessing or inference call: those are recorded as missing, never fabricated.
        code = (
            "from transformers import AutoModel\n"
            "def load():\n"
            "    return AutoModel.from_pretrained('x')\n"
        )
        src = analyse([("m.py", code)])
        flows = src.io_contract["inference_flow"]
        self.assertTrue(flows)
        self.assertFalse(flows[0]["complete"])
        self.assertIn("inference", flows[0]["missing"])
        self.assertIn("preprocessing", flows[0]["missing"])

    def test_processor_and_tokenizer_discovery_from_real_code(self):
        code = (
            "from transformers import AutoTokenizer, AutoProcessor\n"
            "def build():\n"
            "    return AutoProcessor.from_pretrained('x')\n"
        )
        src = analyse([("m.py", code)])
        names = {p["name"] for p in src.io_contract["processors"]}
        self.assertIn("AutoTokenizer", names)
        self.assertIn("AutoProcessor", names)

    def test_processor_discovery_invents_nothing_when_absent(self):
        src = analyse([("m.py", "import torch\n")])
        self.assertEqual(src.io_contract["processors"], [])


class TestRobustnessAndSafety(unittest.TestCase):
    def test_syntax_error_is_skipped_not_fatal(self):
        src = analyse([("bad.py", "def (:"), ("ok.py", "x = 1\n")])
        self.assertTrue(any(s["path"] == "bad.py" for s in src.skipped))
        self.assertEqual([m.path for m in src.modules], ["ok.py"])

    def test_large_module_is_recorded_skipped(self):
        paths = [("big.py", sa.MAX_MODULE_BYTES + 1), ("small.py", 10)]
        chosen, skipped = sa.select_modules(paths)
        self.assertIn("small.py", chosen)
        self.assertTrue(any(s["path"] == "big.py" for s in skipped))

    def test_tests_and_vendor_directories_are_not_analysed(self):
        chosen, _ = sa.select_modules([("tests/test_x.py", 10), ("vendor/lib.py", 10), ("model.py", 10)])
        self.assertIn("model.py", chosen)
        self.assertNotIn("tests/test_x.py", chosen)
        self.assertNotIn("vendor/lib.py", chosen)

    def test_coverage_reports_what_was_not_analysed(self):
        src = analyse([("pkg/model.py", MODEL_PY)])
        self.assertFalse(src.coverage["complete"])
        self.assertIn("pkg/missing.py", src.coverage["not_analysed"])

    def test_repository_text_is_never_executed(self):
        import os
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            sentinel = str(Path(tmp) / "ran.txt")
            evil = f"open({sentinel!r}, 'w').write('ran')\nimport os\nos.system('touch {sentinel}')\n"
            sa.analyze_repository([("evil.py", evil)])
            self.assertFalse(os.path.exists(sentinel), "repository source must never be executed")


class TestRepoModelNormalization(unittest.TestCase):
    def _gh(self):
        return RepoInfo(source=parse_source_url("https://github.com/acme/thing"), commit_sha="a" * 40, revision_kind="commit",
                        files=[("README.md", 100), ("requirements.txt", 20), ("src/model.py", 300), ("config.json", 40),
                               ("weights/model.safetensors", 1000), ("notes.txt", 5)],
                        requirements=[{"name": "torch", "spec": "torch>=2.0", "source": "requirements.txt"}],
                        release_assets=[("v1/model.gguf", 2000)])

    def test_inventories_and_stable_ids(self):
        repo = rm.from_info(self._gh())
        self.assertEqual(repo.provider, "github")
        self.assertEqual(repo.resolved_revision, "a" * 40)
        self.assertIn("src/model.py", repo.source_files)
        self.assertIn("config.json", repo.config_files)
        self.assertIn("requirements.txt", repo.dependency_files)
        self.assertIn("weights/model.safetensors", repo.artifact_files)
        self.assertTrue(any(a["path"] == "weights/model.safetensors" and a["format"] == "safetensors" for a in repo.artifact_inventory))
        self.assertTrue(any(a["path"].startswith("release:") for a in repo.artifact_inventory))
        self.assertEqual(repo.files[0].id, rm.stable_id("file", repo.files[0].path))

    def test_missing_evidence_is_reported(self):
        repo = rm.from_info(RepoInfo(source=parse_source_url("https://github.com/acme/empty"), files=[]))
        self.assertTrue(any("README" in m for m in repo.missing))

    def test_huggingface_repo_does_not_leak_github_fields(self):
        info = RepoInfo(source=parse_source_url("https://huggingface.co/acme/m"), revision_sha="b" * 40,
                        files=[("config.json", 10), ("README.md", 10)])
        repo = rm.from_info(info)
        self.assertEqual(repo.provider, "huggingface")
        self.assertEqual(repo.resolved_revision, "b" * 40)
        self.assertEqual(repo.gaps, [])
        self.assertIsNone(repo.revision_kind)


if __name__ == "__main__":
    unittest.main()
