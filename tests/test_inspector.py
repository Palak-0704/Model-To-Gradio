import unittest

from m2g import inspector as ins
from m2g import security as sec
from m2g.config import Settings
from fakehub import FakeWeb, hf, add_github_repo, PRESETS, SHA


class TestHF(unittest.TestCase):
    def test_happy_path_sst2_no_weights_downloaded(self):
        web = FakeWeb()
        hf(web, "sst2", readme="# SST-2 model\nSee https://huggingface.co/datasets/glue and https://huggingface.co/bert-x/y")
        info = ins.inspect_url("https://huggingface.co/" + PRESETS["sst2"]["repo_id"], web.fetcher())
        self.assertEqual(info.pipeline_tag, "text-classification")
        self.assertEqual(info.config["model_type"], "distilbert")
        self.assertEqual(info.safetensors_total_params, 66_955_010)
        self.assertEqual(info.revision_sha, SHA)
        self.assertEqual(info.license, "apache-2.0")
        self.assertTrue(info.has_safetensors)
        self.assertIn("SST-2", info.readme)
        # only the metadata API + allow-listed small files were requested; never a weight file
        self.assertFalse([c for c in web.calls if c.endswith((".safetensors", ".bin", ".gguf"))])
        self.assertTrue(all(f"/resolve/{SHA}/" in c for c in web.calls if "/resolve/" in c))  # pinned to commit

    def test_allowlist_blocks_hostile_sibling_names(self):
        web = FakeWeb()
        hf(web, "sst2", files=[("config.json", 10), ("../../etc/passwd", 10), ("model.safetensors", 10), ("evil.py", 10)])
        ins.inspect_url("https://huggingface.co/" + PRESETS["sst2"]["repo_id"], web.fetcher())
        fetched = [c for c in web.calls if "/resolve/" in c]
        self.assertEqual(len(fetched), 1)
        self.assertTrue(fetched[0].endswith("/config.json"))

    def test_status_mapping(self):
        for status, needle in [(404, "not found"), (401, "denied"), (403, "denied")]:
            web = FakeWeb()
            web.add("https://huggingface.co/api/models/a/b?blobs=true", status=status)
            with self.assertRaises(ins.InspectionError) as cm:
                ins.inspect_url("https://huggingface.co/a/b", web.fetcher())
            self.assertIn(needle, str(cm.exception).lower())

    def test_network_error_message_and_no_retry_storm(self):
        web = FakeWeb()
        web.add("https://huggingface.co/api/models/a/b?blobs=true", error=sec.NetworkError("DNS resolution failed for huggingface.co"))
        f = web.fetcher()
        with self.assertRaises(ins.InspectionError) as cm:
            ins.inspect_url("https://huggingface.co/a/b", f)
        self.assertIn("DNS", str(cm.exception))
        with self.assertRaises(ins.InspectionError):  # second attempt short-circuits
            ins.inspect_url("https://huggingface.co/a/b", f)
        self.assertEqual(len(web.calls), 1)

    def test_bad_urls_rejected_before_any_request(self):
        web = FakeWeb()
        for u in ["http://huggingface.co/a/b", "https://evil.com/a/b", "https://127.0.0.1/a/b", "file:///x"]:
            with self.assertRaises(ins.InspectionError):
                ins.inspect_url(u, web.fetcher())
        self.assertEqual(web.calls, [])

    def test_config_fallback_to_api_summary_when_file_missing(self):
        web = FakeWeb()
        hf(web, "sst2")
        del web.routes[f"https://huggingface.co/{PRESETS['sst2']['repo_id']}/resolve/{SHA}/config.json"]
        info = ins.inspect_url("https://huggingface.co/" + PRESETS["sst2"]["repo_id"], web.fetcher())
        self.assertTrue(any("config.json" in w for w in info.warnings))

    def test_token_not_in_request_log_or_warnings(self):
        web = FakeWeb()
        hf(web, "big7b")
        tok = "hf_" + "Q" * 34
        f = web.fetcher(hf_token=tok)
        info = ins.inspect_url("https://huggingface.co/" + PRESETS["big7b"]["repo_id"], f)
        self.assertTrue(info.gated)
        blob = " ".join(f.request_log + info.warnings)
        self.assertNotIn(tok, blob)


class TestGitHub(unittest.TestCase):
    def test_happy_path(self):
        web = FakeWeb()
        add_github_repo(web, "acme/cool-ai", readme="Weights: https://huggingface.co/acme/cool-weights\n"
                        "`AutoModel.from_pretrained(\"acme/other\")`", requirements="torch>=2.0\nnumpy==1.26 # c\n-e .\n"
                        "git+https://github.com/x/y.git\n")
        info = ins.inspect_url("https://github.com/acme/cool-ai", web.fetcher())
        self.assertEqual(info.default_branch, "main")
        self.assertEqual([r["name"] for r in info.requirements], ["torch", "numpy"])
        self.assertEqual(info.linked_hf_models, ["acme/cool-weights", "acme/other"])
        self.assertTrue(any("VCS" in f for f in info.requirement_flags))
        self.assertTrue(any("direct URL" in f for f in info.requirement_flags))

    def test_api_403_falls_back_to_raw_without_repeating(self):
        web = FakeWeb()
        add_github_repo(web, "acme/cool-ai", api_status=403)
        info = ins.inspect_url("https://github.com/acme/cool-ai", web.fetcher())
        self.assertIn("Cool AI", info.readme)
        self.assertTrue(any("403" in w for w in info.warnings))
        self.assertEqual(sum(1 for c in web.calls if c.startswith("https://api.github.com")), 1)  # no tree/other API retries

    def test_not_found(self):
        web = FakeWeb()
        web.add("https://api.github.com/repos/a/b", status=404)
        with self.assertRaises(ins.InspectionError):
            ins.inspect_url("https://github.com/a/b", web.fetcher())

    def test_pyproject_deps_and_setup_not_executed(self):
        web = FakeWeb()
        add_github_repo(web, "acme/pkg", requirements=None, tree=["README.md", "pyproject.toml", "setup.py"],
                        extra_files={"pyproject.toml": '[project]\nname="x"\ndependencies=["torch>=2", "Pillow"]\n',
                                     "setup.py": "import os; os.system('echo pwned')"})
        info = ins.inspect_url("https://github.com/acme/pkg", web.fetcher())
        self.assertEqual([r["name"] for r in info.requirements], ["torch", "pillow"])
        self.assertTrue(any("not executed" in w for w in info.warnings))

    def test_large_repository_uses_configurable_source_budget_and_reports_coverage(self):
        web = FakeWeb()
        tree = [f"pkg/model_{n}.py" for n in range(12)] + ["pkg/huge.py"]
        extra = {p: "import torch\ndef predict(x): return x\n" for p in tree if p != "pkg/huge.py"}
        add_github_repo(web, "acme/large", tree=tree, extra_files=extra)
        # The fake tree reports size=100, so a tiny byte budget stops after one file.
        info = ins.inspect_url("https://github.com/acme/large", web.fetcher(),
                               Settings(github_max_source_files=20, github_max_source_bytes=60, github_max_source_file_bytes=1000))
        coverage = info.inspection_coverage
        self.assertTrue(coverage["inspected"])
        self.assertTrue(coverage["remaining"])
        self.assertIn("budget", coverage)
        self.assertTrue(info.evidence_ledger)


class TestRequirementsParser(unittest.TestCase):
    def test_flags(self):
        reqs, flags = ins.parse_requirements("--index-url https://x/simple\n-r other.txt\nfoo @ https://x/f.whl\nBar_Baz>=1; python_version<'3.9'\n")
        self.assertEqual([r["name"] for r in reqs], ["bar-baz"])
        self.assertEqual(len(flags), 3)


class TestStaticParsers(unittest.TestCase):
    """Everything here works on text only: nothing is imported, executed or evaluated."""

    def test_setup_py_literal_install_requires_and_keywords(self):
        reqs, kws, problem = ins.parse_setup_py(
            "from setuptools import setup\nsetup(name='x', install_requires=['torch>=2', 'Transformers'], keywords='a, b')\n")
        self.assertEqual([r["name"] for r in reqs], ["torch", "transformers"])
        self.assertEqual(kws, ["a", "b"])
        self.assertIsNone(problem)

    def test_setup_py_computed_requirements_are_reported_not_evaluated(self):
        reqs, _kws, problem = ins.parse_setup_py("import os\nos.system('echo pwned')\nsetup(install_requires=get_reqs())\n")
        self.assertEqual(reqs, [])
        self.assertIn("computed", problem)
        reqs, _kws, problem = ins.parse_setup_py("def (:")
        self.assertEqual(reqs, [])
        self.assertIn("could not be parsed", problem)

    def test_setup_cfg(self):
        reqs, kws = ins.parse_setup_cfg("[options]\ninstall_requires =\n    numpy\n    torch>=2\n[metadata]\nkeywords = a, b\n")
        self.assertEqual([r["name"] for r in reqs], ["numpy", "torch"])
        self.assertEqual(kws, ["a", "b"])

    def test_scan_python_source_is_ast_only(self):
        src = ("import torch\nfrom transformers import CLIPVisionModel\n\nclass M(PreTrainedModel):\n    def generate(self):\n        pass\n\n"
               "x = CLIPVisionModel.from_pretrained('openai/clip-vit-large-patch14')\nos.system('never run')\n")
        sc = ins.scan_python_source("pkg/m.py", src)
        self.assertEqual(sc["classes"], {"M": ["PreTrainedModel"]})
        self.assertIn("generate", sc["defs"])
        self.assertEqual(sc["imports"], ["torch", "transformers"])
        self.assertEqual(sc["pretrained_ids"], ["openai/clip-vit-large-patch14"])
        self.assertEqual(sc["pretrained_classes"], ["CLIPVisionModel"])
        self.assertIsNone(ins.scan_python_source("bad.py", "def (:"))

    def test_pick_source_files_skips_tests_docs_and_setup(self):
        picked = ins.pick_source_files([("setup.py", 10), ("tests/test_a.py", 10), ("docs/conf.py", 10), ("pkg/modeling.py", 10),
                                        ("pkg/huge.py", 10_000_000), ("README.md", 10)])
        self.assertEqual(picked, ["pkg/modeling.py"])

    def test_rank_hf_links_separates_primary_model_from_backbones(self):
        ref = sec.parse_source_url("https://github.com/dhansmair/flamingo-mini")
        readme = ("The pretrained model is available at https://huggingface.co/dhansmair/flamingo-mini\n\n"
                  "Acknowledgements: built on the language model https://huggingface.co/facebook/opt-125m as lm:\n")
        primary, roles = ins.rank_hf_links(readme, ref, ["facebook/opt-125m", "dhansmair/flamingo-mini"])
        self.assertEqual(primary, "dhansmair/flamingo-mini")
        self.assertEqual(roles["dhansmair/flamingo-mini"]["role"], "primary")
        self.assertNotEqual(roles["facebook/opt-125m"]["role"], "primary")

    def test_rank_hf_links_does_not_guess_when_nothing_is_distinctive(self):
        ref = sec.parse_source_url("https://github.com/acme/tool")
        primary, roles = ins.rank_hf_links("See https://huggingface.co/x/y and https://huggingface.co/p/q", ref, ["x/y", "p/q"])
        self.assertIsNone(primary)
        self.assertEqual({r["role"] for r in roles.values()}, {"referenced"})


if __name__ == "__main__":
    unittest.main()
