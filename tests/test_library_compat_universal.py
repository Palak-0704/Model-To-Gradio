"""Universal library/runtime compatibility: different models, tasks, library versions and runtimes (Colab-like and local).

Nothing is mocked on the probe path: each scenario writes tiny fake ``gradio`` / ``transformers`` / ``sentence_transformers``
packages for one "runtime" into a temp folder and runs the REAL probe runner in a fresh interpreter whose PYTHONPATH is that
folder, then feeds the reply to the REAL ``run_preflight``. The runtimes differ only in what their libraries provide, so the
same app is judged differently on each, which is the point: the verdict comes from the environment that will run the app,
never from a table of guesses about versions. The fake packages are a hand-written subset, not the real libraries.
"""
import contextlib
import io
import json
import os
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

from m2g import local_check as lc
from m2g import runtime_compat as rc
from m2g.error_classify import classify_text
from m2g.preflight import render_probe_runner, run_preflight

GRADIO = '''
__version__ = "6.30.0"
class Interface:
    def __init__(self, fn=None, inputs=None, outputs=None, *, title=None, description=None): pass
    def launch(self, **k): pass
class Textbox:
    def __init__(self, value=None, *, label=None, lines=None): pass
class Label:
    def __init__(self, value=None, *, label=None): pass
'''

TRANSFORMERS = '''
__version__ = "{version}"
{names}
def pipeline(task=None, model=None, **kwargs): pass
class BitsAndBytesConfig:
    def __init__(self, load_in_8bit=False, load_in_4bit=False{extra_bnb}): pass
class AutoTokenizer:
    @classmethod
    def from_pretrained(cls, name, **kwargs): pass
'''

PIPELINES = '''
SUPPORTED_TASKS = {tasks}
'''

SENTENCE_TRANSFORMERS = '''
class SentenceTransformer:
    def __init__(self, model_name_or_path=None, device=None, {auth}=None): pass
'''


def make_runtime(root, *, tasks, names=(), version="4.60.0", bnb_extra="", st_auth="token", with_st=True):
    """Write the packages of one runtime into ``root`` and return it."""
    root = Path(root)
    (root / "gradio").mkdir(parents=True)
    (root / "gradio" / "__init__.py").write_text(GRADIO)
    (root / "transformers").mkdir()
    (root / "transformers" / "__init__.py").write_text(
        TRANSFORMERS.format(version=version, names="\n".join(f"class {n}: pass" for n in names), extra_bnb=bnb_extra))
    (root / "transformers" / "pipelines.py").write_text(PIPELINES.format(tasks=json.dumps({t: {} for t in tasks})))
    if with_st:
        (root / "sentence_transformers").mkdir()
        (root / "sentence_transformers" / "__init__.py").write_text(SENTENCE_TRANSFORMERS.format(auth=st_auth))
    return root


GRADIO_TAIL = ("\n\ndemo = gr.Interface(fn=predict, inputs=gr.Textbox(label='in'), outputs=gr.Textbox(label='out'), "
               "title='m')\n")

CLASSIFIER_APP = ("import gradio as gr\nfrom transformers import pipeline\n\nTASK = 'text-classification'\n_p = None\n\n\n"
                  "def get_model():\n    global _p\n    if _p is None:\n        _p = pipeline(TASK, model='acme/clf')\n    return _p\n\n\n"
                  "def predict(t):\n    return str(get_model()(t))\n" + GRADIO_TAIL)

VISION_APP = ("import gradio as gr\nfrom transformers import AutoTokenizer, AutoModelForVision2Seq\n\n\n"
              "def predict(t):\n    m = AutoModelForVision2Seq.from_pretrained('acme/vlm')\n    return str(m)\n" + GRADIO_TAIL)

SEQ2SEQ_APP = ("import gradio as gr\nfrom transformers import pipeline\n\n\n"
               "def predict(t):\n    return str(pipeline('text2text-generation', model='acme/t5')(t))\n" + GRADIO_TAIL)

QUANT_APP = ("import gradio as gr\nfrom transformers import BitsAndBytesConfig\n\n\n"
             "def predict(t):\n    cfg = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_use_double_quant=True)\n    return str(cfg)\n"
             + GRADIO_TAIL)

EMBED_APP = ("import gradio as gr\nfrom sentence_transformers import SentenceTransformer\n\n\n"
             "def predict(t):\n    m = SentenceTransformer('acme/emb', device='cpu', use_auth_token=None)\n    return str(m)\n" + GRADIO_TAIL)

PLAIN_ENV = {"PYTHONDONTWRITEBYTECODE": "1"}


def probe_in(root, code):
    """Run the real runner for ``code`` in a fresh interpreter that sees only ``root``'s packages."""
    env = {**os.environ, **PLAIN_ENV, "PYTHONPATH": str(root)}
    out = io.StringIO()
    with mock.patch.dict(os.environ, env, clear=True), contextlib.redirect_stdout(out):
        exec(compile(render_probe_runner(code, "M2G_T:"), "runner", "exec"), {})
    line = next(ln for ln in out.getvalue().splitlines() if ln.startswith("M2G_T:"))
    return json.loads(line[len("M2G_T:"):])


class Runtimes(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.n = 0

    def runtime(self, **kw):
        self.n += 1
        return make_runtime(self.base / f"rt{self.n}", **kw)

    def verdict(self, root, code):
        return run_preflight(code, probe_in(root, code))


class TestSameAppDifferentRuntimes(Runtimes):
    """One app, several library generations: the verdict follows the runtime that will run it."""

    def test_removed_task_blocks_on_the_new_runtime_and_passes_on_the_old_one(self):
        new = self.runtime(tasks=["text-classification"], version="5.0.0")
        old = self.runtime(tasks=["text-classification", "text2text-generation"], version="4.40.0")
        blocked = self.verdict(new, SEQ2SEQ_APP)
        self.assertFalse(blocked.ok)
        self.assertEqual(blocked.status, "blocked")
        self.assertIn("text2text-generation", blocked.message)
        self.assertIn("5.0.0", blocked.message)  # the installed version is named
        self.assertTrue(self.verdict(old, SEQ2SEQ_APP).ok)

    def test_removed_class_blocks_and_present_class_passes(self):
        without = self.runtime(tasks=["text-classification"], names=["AutoModelForCausalLM"])
        with_it = self.runtime(tasks=["text-classification"], names=["AutoModelForVision2Seq"])
        res = self.verdict(without, VISION_APP)
        self.assertEqual(res.status, "blocked")
        self.assertIn("AutoModelForVision2Seq", res.message)
        self.assertTrue(self.verdict(with_it, VISION_APP).ok)

    def test_renamed_keyword_of_a_library_class_is_caught_before_deployment(self):
        # sentence_transformers 'old' takes use_auth_token, 'new' only token: same app, different verdict, nothing hard-coded
        old = self.runtime(tasks=["text-classification"], st_auth="use_auth_token")
        new = self.runtime(tasks=["text-classification"], st_auth="token")
        self.assertTrue(self.verdict(old, EMBED_APP).ok)
        res = self.verdict(new, EMBED_APP)
        self.assertEqual(res.status, "blocked")
        self.assertIn("use_auth_token", res.message)
        self.assertIn("sentence_transformers.SentenceTransformer", res.message)
        self.assertEqual(res.code, EMBED_APP)  # a different API means different code: never rewritten automatically

    def test_explicit_signature_config_class(self):
        old = self.runtime(tasks=["text-classification"], bnb_extra=", bnb_4bit_use_double_quant=False")
        new = self.runtime(tasks=["text-classification"])
        self.assertTrue(self.verdict(old, QUANT_APP).ok)
        res = self.verdict(new, QUANT_APP)
        self.assertEqual(res.status, "blocked")
        self.assertIn("bnb_4bit_use_double_quant", res.message)

    def test_callables_with_var_keyword_are_never_guessed_about(self):
        # pipeline(**kwargs) and from_pretrained(**kwargs) cannot be verified statically: no finding, never a false block
        code = CLASSIFIER_APP.replace("pipeline(TASK, model='acme/clf')", "pipeline(TASK, model='acme/clf', totally_new_arg=1)")
        self.assertTrue(self.verdict(self.runtime(tasks=["text-classification"]), code).ok)

    def test_a_supported_app_passes_and_reports_versions(self):
        res = self.verdict(self.runtime(tasks=["text-classification"], version="4.60.0"), CLASSIFIER_APP)
        self.assertTrue(res.ok, res.message)
        self.assertEqual(res.status, "passed")
        self.assertTrue(any("transformers 4.60.0" in ln or "runtime libraries" in ln for ln in res.log_lines))

    def test_reported_version_comes_from_imported_runtime_module(self):
        root = self.runtime(tasks=["text-classification"], version="4.12.3")
        res = self.verdict(root, CLASSIFIER_APP)
        self.assertTrue(res.ok, res.message)
        self.assertTrue(any("transformers 4.12.3" in ln for ln in res.log_lines))
        self.assertFalse(any("transformers 5.22.0" in ln for ln in res.log_lines))

    def test_library_that_is_not_installed_blocks_instead_of_starting(self):
        res = self.verdict(self.runtime(tasks=["text-classification"], with_st=False), EMBED_APP)
        self.assertEqual(res.status, "blocked")
        self.assertIn("could not inspect", res.message)
        self.assertIn("sentence_transformers", res.message)


class TestFailClosedWhenUnverifiable(Runtimes):
    def test_missing_library_line_blocks_an_app_that_imports_libraries(self):
        root = self.runtime(tasks=["text-classification"])
        reply = probe_in(root, CLASSIFIER_APP)
        reply["out"] = "\n".join(ln for ln in reply["out"].splitlines() if not ln.startswith(rc.LIB_PROBE_MARKER))
        res = run_preflight(CLASSIFIER_APP, reply)
        self.assertFalse(res.ok)
        self.assertIn("could not be verified", res.message)

    def test_unreadable_pipeline_tasks_are_reported_not_ignored(self):
        root = self.runtime(tasks=["text-classification"])
        (root / "transformers" / "pipelines.py").write_text("raise RuntimeError('broken')\n")
        res = self.verdict(root, CLASSIFIER_APP)
        self.assertEqual(res.status, "blocked")
        self.assertIn("could not inspect", res.message)

    def test_app_without_model_libraries_does_not_depend_on_them(self):
        root = self.runtime(tasks=["text-classification"])
        plain = "import gradio as gr\n\n\ndef predict(t):\n    return t\n" + GRADIO_TAIL
        self.assertTrue(self.verdict(root, plain).ok)


class TestGradioAndLibrariesTogether(Runtimes):
    def test_gradio_incompatibility_is_still_found_next_to_library_checks(self):
        root = self.runtime(tasks=["text-classification"])
        code = CLASSIFIER_APP.replace("title='m'", "title='m', theme='x', not_a_kwarg=1")
        res = self.verdict(root, code)
        self.assertEqual(res.status, "blocked")  # not_a_kwarg is not a cosmetic keyword: reported, never removed
        self.assertIn("not_a_kwarg", res.message)

    def test_cosmetic_gradio_keyword_is_repaired_and_the_library_check_still_runs(self):
        root = self.runtime(tasks=["text-classification"])
        code = CLASSIFIER_APP.replace("title='m'", "title='m', theme='x'")
        res = self.verdict(root, code)
        self.assertEqual(res.status, "repaired")
        self.assertNotIn("theme=", res.code)


class TestLocalSystemCheck(Runtimes):
    def write_app(self, code):
        d = self.base / "out"
        d.mkdir(exist_ok=True)
        (d / "app.py").write_text(code, encoding="utf-8")
        return d

    def local(self, root, code, **kw):
        folder = self.write_app(code)
        with mock.patch.dict(os.environ, {**os.environ, **PLAIN_ENV, "PYTHONPATH": str(root)}):
            return folder, lc.check_local_app(str(folder), revalidate=lambda c: None, **kw)

    def test_local_environment_is_judged_like_the_runtime(self):
        old = self.runtime(tasks=["text-classification", "text2text-generation"])
        new = self.runtime(tasks=["text-classification"])
        self.assertTrue(self.local(old, SEQ2SEQ_APP)[1].ok)
        res = self.local(new, SEQ2SEQ_APP)[1]
        self.assertEqual(res.status, "blocked")
        self.assertIn("text2text-generation", res.message)

    def test_fix_writes_only_a_proven_repair_and_keeps_a_backup(self):
        root = self.runtime(tasks=["text-classification"])
        code = CLASSIFIER_APP.replace("title='m'", "title='m', theme='x'")
        folder, res = self.local(root, code, fix=True)
        self.assertEqual(res.status, "repaired")
        self.assertNotIn("theme=", (folder / "app.py").read_text())
        self.assertEqual((folder / "app.py.m2g-backup").read_text(), code)

    def test_blocked_app_is_never_modified_by_fix(self):
        root = self.runtime(tasks=["text-classification"])
        folder, res = self.local(root, SEQ2SEQ_APP, fix=True)
        self.assertFalse(res.ok)
        self.assertEqual((folder / "app.py").read_text(), SEQ2SEQ_APP)
        self.assertFalse((folder / "app.py.m2g-backup").exists())

    def test_unusable_interpreter_is_reported_not_hidden(self):
        folder = self.write_app(CLASSIFIER_APP)
        res = lc.check_local_app(str(folder), python=str(self.base / "no-such-python"), revalidate=lambda c: None)
        self.assertFalse(res.ok)
        self.assertEqual(res.status, "probe_failed")

    def test_cli_exit_codes(self):
        old = self.runtime(tasks=["text-classification", "text2text-generation"])
        new = self.runtime(tasks=["text-classification"])
        folder = self.write_app(SEQ2SEQ_APP)
        for root, want in ((old, 0), (new, 1)):
            with mock.patch.dict(os.environ, {**os.environ, **PLAIN_ENV, "PYTHONPATH": str(root)}), \
                    mock.patch("m2g.validate.validate_app_code", lambda *a, **k: None), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(lc.main([str(folder)]), want)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(lc.main([str(self.base / "missing")]), 2)


class TestCompatibilityIsNotMisreported(unittest.TestCase):
    """A compatibility failure must never be labelled sign-in or network, whatever words its text contains."""

    CASES = {
        "removed task through hub frames": "KeyError: \"Unknown task text2text-generation, available tasks are ['text-generation']\"\n  File huggingface_hub/utils/_validators.py token=HF_TOKEN",
        "renamed class": "ImportError: cannot import name 'AutoModelForVision2Seq' from 'transformers' (/x/transformers/__init__.py)\nlogin(token=HF_TOKEN) https://huggingface.co",
        "removed hub helper": "ImportError: cannot import name 'cached_download' from 'huggingface_hub' (/x/huggingface_hub/__init__.py)",
        "module attribute": "AttributeError: module 'diffusers' has no attribute 'OldPipeline' connection reset",
        "removed keyword mentioning token": "TypeError: SentenceTransformer.__init__() got an unexpected keyword argument 'use_auth_token'",
    }

    def test_not_auth_not_network(self):
        for name, text in self.CASES.items():
            with self.subTest(case=name):
                info = classify_text(text, "start")
                self.assertNotIn(info.category, ("auth", "network", "colab_runtime", "file_transfer"), (info.category, info.code))
                self.assertFalse(info.transient)  # a removed API never gets better by retrying

    def test_real_auth_and_network_failures_keep_their_labels(self):
        self.assertEqual(classify_text("GatedRepoError: 401 Client Error. Cannot access gated repo", "load").code, "model_access_denied")
        net = classify_text("requests.exceptions.ConnectionError: HTTPSConnectionPool(host='huggingface.co', port=443)", "load")
        self.assertEqual((net.category, net.transient), ("network", True))

    def test_the_new_library_findings_are_recognised_as_api_problems(self):
        snap = rc.LibrarySnapshot({"sentence_transformers": "3.0"}, {}, None, (), (),
                                  {"sentence_transformers": {"SentenceTransformer": ("use_auth_token",)}})
        found = rc.check_library_compat(EMBED_APP, snap)
        self.assertEqual([f.kind for f in found], ["unsupported_kwarg"])
        info = classify_text(found[0].message, "preflight")
        self.assertNotIn(info.category, ("auth", "network"))
        self.assertNotEqual(info.code, "unrecognized")


class TestCallKeywordCollection(unittest.TestCase):
    def test_forms_that_are_followed_and_skipped(self):
        code = textwrap.dedent('''
            import transformers as tf
            import torch
            from transformers import A, B as C
            from transformers.models.x import Deep
            from diffusers import P
            tf.Cfg(a=1)
            tf.Auto.from_pretrained("x", b=2)
            A(c=3)
            C.make(d=4)
            Deep(e=5)
            torch.thing(f=6)
            P(**opts)
            A(1, 2)
        ''')
        got = rc.library_call_keywords(code)
        flat = {(lib, d, k) for lib, t in got.items() for d, kws in t.items() for k in kws}
        self.assertEqual(flat, {("transformers", "Cfg", "a"), ("transformers", "Auto.from_pretrained", "b"),
                                ("transformers", "A", "c"), ("transformers", "B.make", "d")})

    def test_syntax_error_means_nothing_to_probe(self):
        self.assertEqual(rc.library_probe_calls("def ("), {})

    def test_probe_script_validates_names_before_embedding_them(self):
        script = rc.render_library_probe_script(["transformers"], {}, False,
                                                {"transformers": {"A.b": ["ok", "bad; import os"], "x; y": ["z"]}})
        self.assertTrue(script.isascii())
        self.assertNotIn("import os", script)
        self.assertNotIn("x; y", script)


if __name__ == "__main__":
    unittest.main()
