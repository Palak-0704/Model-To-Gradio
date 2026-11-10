"""Regression tests for runtime/library compatibility of generated apps.

The failure that motivated them: an app generated with ``pipeline('question-answering')`` failed on Colab because the runtime had a newer
transformers in which that pipeline task no longer exists. The platform noticed nothing before deployment, and the failure could be
reported with the wrong cause (a Google sign-in problem). These tests cover the SHARED machinery, not one model:

  * error classification: a library-compatibility error is recognised from its original text and never becomes an auth / CLI error;
  * ``runtime_compat``: the probe that runs on the runtime, the static check of the app against what the runtime really has;
  * ``preflight``: an app whose task or imported name does not exist on the runtime is blocked BEFORE it is deployed, with the cause;
  * dependency selection from the APIs the code uses (loader options such as ``device_map`` need accelerate);
  * the generator request tells the model how runtime compatibility works.

Nothing here starts OpenCode, Colab or a model; the runtime is replaced by fake modules and probe lines.
"""
import contextlib
import io
import json
import sys
import types
import unittest
from unittest import mock

from m2g import gradio_gen as gg
from m2g import runtime_compat as rc
from m2g.colab_cli import CliResult
from m2g.colab_workflow import classify_cli_failure
from m2g.error_classify import CATEGORIES, classify_failure, classify_text, compat_error
from m2g.preflight import render_probe_runner, run_preflight
from m2g.validate import symbols_to_requirements
from test_analyzer import analyze_preset

QA_TRACEBACK = '''Traceback (most recent call last):
  File "/content/m2g_app/app.py", line 52, in get_model
    _pipeline = transformers.pipeline(**load_kwargs)
  File "/usr/local/lib/python3.12/dist-packages/transformers/pipelines/__init__.py", line 700, in pipeline
    raise KeyError(
KeyError: "Unknown task question-answering, available tasks are ['audio-classification', 'token-classification', 'text-generation']"
'''

IMPORT_TRACEBACK = '''Traceback (most recent call last):
  File "/usr/local/lib/python3.12/dist-packages/google/oauth2/credentials.py", line 10, in <module>
    token = None
  File "/content/m2g_app/app.py", line 12, in <module>
    from transformers import AutoModelForVision2Seq
ImportError: cannot import name 'AutoModelForVision2Seq' from 'transformers' (/usr/local/lib/python3.12/dist-packages/transformers/__init__.py)
'''


def _app(task="question-answering", names="AutoTokenizer"):
    return (f"import gradio as gr\nimport transformers\nfrom transformers import {names}\n\n"
            f"TASK = {task!r}\n_pipe = None\n\n\n"
            "def get_model():\n    global _pipe\n    if _pipe is None:\n        _pipe = transformers.pipeline(TASK, model='acme/m')\n"
            "    return _pipe\n\n\n"
            "def predict(text):\n    return get_model()(text)\n\n\n"
            "demo = gr.Interface(fn=predict, inputs=[gr.Textbox()], outputs=[gr.Textbox()])\n")


def _snap(tasks=("text-classification", "token-classification"), missing=None, aliases=()):
    return rc.LibrarySnapshot({"transformers": "9.9.9"}, {k: tuple(v) for k, v in (missing or {}).items()},
                              None if tasks is None else tuple(tasks), tuple(aliases))


def _lib_line(tasks, missing=None):
    return "M2G_LIB_API " + json.dumps({"versions": {"transformers": "9.9.9", "torch": "2.9.0"}, "missing": missing or {},
                                        "tasks": tasks, "aliases": [], "errors": []})


_GRADIO_LINE = "M2G_GRADIO_API " + json.dumps({
    "version": "6.30.0", "missing": [],
    "classes": {"Interface": {"params": ["fn", "inputs", "outputs"], "var_kw": True, "max_positional": 3},
                "Textbox": {"params": [], "var_kw": True, "max_positional": 0}}})


def _probe(*lines):
    return {"rc": 0, "out": "\n".join(lines) + "\n", "err": ""}


class TestErrorsAreClassifiedByTheirRealCause(unittest.TestCase):
    def test_removed_pipeline_task_is_a_compatibility_error(self):
        info = classify_text(QA_TRACEBACK, "start")
        self.assertEqual((info.category, info.code), ("compatibility", "pipeline_task_unsupported"))
        self.assertIn("question-answering", info.title)
        self.assertFalse(info.transient)
        self.assertEqual(info.max_retries, 0)
        self.assertIn("AutoModel", info.recovery)  # an actionable fix, not just a label

    def test_unimportable_name_is_a_compatibility_error_not_a_missing_package(self):
        info = classify_text(IMPORT_TRACEBACK, "start")
        self.assertEqual((info.category, info.code), ("compatibility", "removed_or_renamed_api"))
        self.assertIn("AutoModelForVision2Seq", info.title)
        self.assertIn("transformers", info.recovery)

    def test_module_attribute_error_of_a_known_library_is_compatibility(self):
        info = classify_text("AttributeError: module 'transformers' has no attribute 'AutoModelForVision2Seq'", "load")
        self.assertEqual(info.code, "removed_or_renamed_api")

    def test_ordinary_missing_package_keeps_its_own_label(self):
        info = classify_text("ModuleNotFoundError: No module named 'some_other_package'", "load")
        self.assertEqual((info.category, info.code), ("dependency", "missing_module"))
        self.assertIsNone(compat_error("ImportError: cannot import name 'x' from 'unrelated_pkg'"))

    def test_compatibility_is_a_known_category(self):
        self.assertIn("compatibility", CATEGORIES)

    def test_a_model_error_is_never_reported_as_a_google_sign_in_problem(self):
        detail = QA_TRACEBACK + "\ngoogle/oauth2/credentials.py token refresh frames mention token and oauth\n"
        info = classify_failure("needs_auth", "Google rejected the Colab CLI's credentials.", detail, "start")
        self.assertEqual(info.category, "compatibility")
        self.assertNotEqual(info.category, "auth")
        self.assertIn("question-answering", info.title)

    def test_a_real_sign_in_failure_is_still_an_auth_error(self):
        info = classify_failure("needs_auth", "Google rejected the Colab CLI's credentials.",
                                "google.auth.exceptions.RefreshError: invalid_grant: Token has been expired or revoked.", "runtime")
        self.assertEqual((info.category, info.code), ("auth", "google_auth"))

    def test_preflight_blocked_by_a_library_is_not_labelled_as_a_gradio_problem(self):
        msg = ("Preflight blocked the launch: The app uses a library API that the versions installed on the runtime do not have:\n"
               "  app.py:3:0: transformers 9.9.9 installed on the runtime does not provide the pipeline task 'question-answering'.")
        info = classify_failure("preflight_failed", msg, "", "preflight")
        self.assertEqual((info.category, info.code), ("compatibility", "pipeline_task_unsupported"))
        gradio = classify_failure("preflight_failed", "gr.ChatInterface() does not accept 'type' in Gradio 6.30.0", "", "preflight")
        self.assertEqual(gradio.category, "preflight")

    def test_unrecognised_incompatibility_kind_still_gets_a_compatibility_label(self):
        info = classify_failure("app_incompatible", "something unusual", "", "runtime")
        self.assertEqual((info.category, info.code), ("compatibility", "app_incompatible"))


class TestCliFailureClassificationOrder(unittest.TestCase):
    def cli(self, stderr):
        return CliResult(["colab", "exec"], 1, "", stderr, 1.0)

    def test_import_error_from_the_app_is_not_a_broken_cli_or_a_sign_in_failure(self):
        kind, msg = classify_cli_failure(self.cli(IMPORT_TRACEBACK))
        self.assertEqual(kind, "app_incompatible")
        self.assertIn("AutoModelForVision2Seq", msg)
        self.assertNotIn("sign in", msg.lower())

    def test_removed_pipeline_task_from_the_runtime_is_not_a_sign_in_failure(self):
        kind, msg = classify_cli_failure(self.cli(QA_TRACEBACK))
        self.assertEqual(kind, "app_incompatible")
        self.assertIn("question-answering", msg)

    def test_genuine_cli_breakage_and_auth_failures_are_unchanged(self):
        self.assertEqual(classify_cli_failure(self.cli("ModuleNotFoundError: No module named 'google_colab_cli'"))[0], "cli_broken")
        self.assertEqual(classify_cli_failure(self.cli("google.auth.exceptions.RefreshError: invalid_grant: expired or revoked"))[0],
                         "needs_auth")


class TestLibraryProbeOnTheRuntime(unittest.TestCase):
    def fake_transformers(self):
        fake = types.ModuleType("transformers")
        fake.Present = object()
        pipelines = types.ModuleType("transformers.pipelines")
        pipelines.SUPPORTED_TASKS = {"text-classification": {}, "translation": {}}
        pipelines.TASK_ALIASES = {"sentiment-analysis": "text-classification"}
        fake.pipelines = pipelines
        return {"transformers": fake, "transformers.pipelines": pipelines}

    def test_snapshot_reports_missing_names_and_registered_tasks(self):
        with mock.patch.dict(sys.modules, self.fake_transformers()):
            snap = rc.library_snapshot(["transformers"], {"transformers": ["Present", "Gone"]}, True)
        self.assertEqual(snap["missing"], {"transformers": ["Gone"]})
        self.assertEqual(snap["tasks"], ["text-classification", "translation"])
        self.assertEqual(snap["aliases"], ["sentiment-analysis"])
        self.assertIn("transformers", snap["versions"])

    def test_unreadable_tasks_are_reported_as_unknown_not_as_empty(self):
        broken = types.ModuleType("transformers")  # no pipelines at all
        with mock.patch.dict(sys.modules, {"transformers": broken}):
            snap = rc.library_snapshot(["transformers"], {}, True)
        self.assertIsNone(snap["tasks"])
        self.assertTrue(snap["errors"])

    def test_the_shipped_script_prints_one_parseable_line(self):
        script = rc.render_library_probe_script(["transformers"], {"transformers": ["Present", "Gone"]}, True)
        self.assertTrue(script.isascii())
        out = io.StringIO()
        with mock.patch.dict(sys.modules, self.fake_transformers()), contextlib.redirect_stdout(out):
            exec(compile(script, "probe.py", "exec"), {"__name__": "probe"})
        snap = rc.parse_library_probe_output("noise\n" + out.getvalue() + "more noise\n")
        self.assertIsNotNone(snap)
        self.assertEqual(snap.missing, {"transformers": ("Gone",)})
        self.assertEqual(snap.tasks, ("text-classification", "translation"))

    def test_parse_ignores_garbage_and_error_lines(self):
        self.assertIsNone(rc.parse_library_probe_output("nothing here"))
        self.assertIsNone(rc.parse_library_probe_output('M2G_LIB_API {"error": "ImportError"}'))
        self.assertIsNone(rc.parse_library_probe_output("M2G_LIB_API {not json"))

    def test_probe_arguments_follow_what_the_app_uses(self):
        libs, symbols, want_tasks = rc.library_probe_arguments(_app())
        self.assertIn("transformers", libs)
        self.assertEqual(symbols["transformers"], ["AutoTokenizer", "pipeline"])
        self.assertTrue(want_tasks)
        self.assertEqual(rc.library_probe_arguments("def f(:\n")[1:], ({}, False))

    def test_pipeline_alias_and_submodule_import_are_checked(self):
        code = ("from transformers.pipelines import pipeline as hf_pipeline\n"
                "TASK = 'question-answering'\n"
                "def get_model():\n"
                "    return hf_pipeline(TASK, model='acme/m')\n")
        libs, symbols, want_tasks = rc.library_probe_arguments(code)
        self.assertIn("transformers", libs)
        self.assertEqual(symbols["transformers"], ["pipeline"])
        self.assertTrue(want_tasks)
        findings = rc.check_library_compat(code, _snap())
        self.assertEqual([f.name for f in findings], ["question-answering"])

    def test_literal_task_in_expanded_loader_kwargs_is_checked(self):
        code = ("import transformers\n"
                "def get_model():\n"
                "    load_kwargs = {'task': 'question-answering', 'model': 'acme/m'}\n"
                "    return transformers.pipeline(**load_kwargs)\n")
        self.assertEqual([task for task, _ in rc.library_uses(code)[1]], ["question-answering"])
        self.assertEqual(rc.check_library_compat(code, _snap())[0].kind, "unsupported_task")

    def test_names_are_validated_before_they_enter_the_script(self):
        script = rc.render_library_probe_script(["transformers"], {"transformers": ["Ok", "bad name; import os"]}, False)
        self.assertNotIn("import os", script)


class TestStaticCheckAgainstTheRuntime(unittest.TestCase):
    def test_unsupported_pipeline_task_is_reported_with_cause_and_fix(self):
        findings = rc.check_library_compat(_app(), _snap())
        self.assertEqual([(f.kind, f.name) for f in findings], [("unsupported_task", "question-answering")])
        msg = findings[0].message
        self.assertIn("transformers 9.9.9", msg)
        self.assertIn("does not provide the pipeline task 'question-answering'", msg)
        self.assertIn("AutoModel", msg)
        self.assertGreater(findings[0].line, 0)

    def test_supported_aliased_and_parameterised_tasks_pass(self):
        self.assertEqual(rc.check_library_compat(_app(), _snap(tasks=("question-answering",))), [])
        self.assertEqual(rc.check_library_compat(_app("sentiment-analysis"), _snap(aliases=("sentiment-analysis",))), [])
        self.assertEqual(rc.check_library_compat(_app("translation_en_to_fr"), _snap(tasks=("translation",))), [])

    def test_nothing_is_enforced_without_a_usable_snapshot(self):
        self.assertEqual(rc.check_library_compat(_app(), None), [])
        self.assertEqual(rc.check_library_compat(_app(), _snap(tasks=None)), [])

    def test_the_task_is_found_when_the_loader_builds_its_kwargs_in_a_dict(self):
        code = ("import transformers\n\nTASK = 'question-answering'\n\n\ndef get_model():\n"
                "    load_kwargs = {\"task\": TASK, \"model\": 'acme/m'}\n    return transformers.pipeline(**load_kwargs)\n")
        _symbols, tasks = rc.library_uses(code)
        self.assertEqual([t for t, _pos in tasks], ["question-answering"])
        self.assertEqual([f.kind for f in rc.check_library_compat(code, _snap())], ["unsupported_task"])

    def test_an_unused_task_constant_is_not_a_pipeline_request(self):
        code = "import diffusers\n\nTASK = 'text-to-image'\n\n\ndef get_model():\n    return diffusers.DiffusionPipeline\n"
        self.assertEqual(rc.library_uses(code)[1], [])
        self.assertEqual(rc.check_library_compat(code, _snap()), [])

    def test_missing_imported_name_and_missing_attribute_are_both_reported(self):
        code = _app(task="token-classification") + "\n\ndef other():\n    return transformers.AutoModelForVision2Seq\n"
        snap = _snap(missing={"transformers": ["AutoTokenizer", "AutoModelForVision2Seq"]})
        findings = rc.check_library_compat(code, snap)
        self.assertEqual(sorted(f.name for f in findings), ["AutoModelForVision2Seq", "AutoTokenizer"])
        self.assertTrue(all(f.kind == "missing_name" for f in findings))
        for f in findings:
            self.assertEqual(compat_error(f.message).code, "removed_or_renamed_api")  # the same cause label as a runtime traceback

    def test_the_finding_messages_are_recognised_by_the_classifier(self):
        f = rc.check_library_compat(_app(), _snap())[0]
        self.assertEqual(compat_error(f.message).code, "pipeline_task_unsupported")


class TestPreflightBlocksBeforeDeployment(unittest.TestCase):
    def test_app_with_a_task_the_runtime_lacks_is_blocked_with_the_cause(self):
        res = run_preflight(_app(), _probe(_lib_line(["text-classification"]), _GRADIO_LINE))
        self.assertFalse(res.ok)
        self.assertEqual(res.status, "blocked")
        self.assertIn("question-answering", res.message)
        self.assertIn("does not provide the pipeline task", res.message)
        self.assertTrue(any("transformers 9.9.9" in ln for ln in res.log_lines))  # the installed version is in the log

    def test_failed_library_probe_is_blocked_for_a_library_the_app_uses(self):
        bad_library = _lib_line(["text-classification"]).replace('"errors": []', '"errors": ["pipeline tasks: ImportError"]')
        res = run_preflight(_app(), _probe(bad_library, _GRADIO_LINE))
        self.assertFalse(res.ok)
        self.assertEqual(res.status, "blocked")
        self.assertIn("could not inspect the libraries", res.message)
        self.assertIn("pipeline tasks: ImportError", res.message)  # the installed version is in the log
        self.assertEqual(res.code, _app())  # nothing was rewritten: a different API means different code

    def test_the_blocked_verdict_is_classified_as_compatibility(self):
        res = run_preflight(_app(), _probe(_lib_line(["text-classification"]), _GRADIO_LINE))
        info = classify_failure("preflight_failed", "Preflight blocked the launch: " + res.message, "", "preflight")
        self.assertEqual((info.category, info.code), ("compatibility", "pipeline_task_unsupported"))

    def test_missing_import_is_blocked(self):
        res = run_preflight(_app(task="text-classification"),
                            _probe(_lib_line(["text-classification"], {"transformers": ["AutoTokenizer"]}), _GRADIO_LINE))
        self.assertEqual(res.status, "blocked")
        self.assertIn("AutoTokenizer", res.message)

    def test_compatible_app_passes(self):
        res = run_preflight(_app(), _probe(_lib_line(["question-answering", "text-classification"]), _GRADIO_LINE))
        self.assertTrue(res.ok, res.message)
        self.assertEqual(res.status, "passed")

    def test_a_probe_without_library_information_blocks_an_app_that_uses_libraries(self):
        # Fail closed: the app imports transformers but the runtime returned no library line, so nothing could be verified.
        res = run_preflight(_app(), _probe(_GRADIO_LINE))
        self.assertFalse(res.ok)
        self.assertEqual(res.status, "blocked")
        self.assertIn("could not be verified", res.message)
        self.assertIn("transformers", res.message)
        self.assertEqual(res.code, _app())

    def test_a_probe_without_library_information_is_fine_for_an_app_without_such_libraries(self):
        plain = "import gradio as gr\n\n\ndef f(x):\n    return x\n\n\ndemo = gr.Interface(fn=f, inputs=gr.Textbox(), outputs=gr.Textbox())\n"
        res = run_preflight(plain, _probe(_GRADIO_LINE))
        self.assertTrue(res.ok, res.message)

    def test_the_runtime_runner_carries_both_probes(self):
        runner = render_probe_runner(_app(), "M2G_PF ")
        self.assertTrue(runner.isascii())
        self.assertIn("M2G_LIB_API", runner)
        self.assertIn("M2G_GRADIO_API", runner)


class TestDependenciesFollowTheApisUsed(unittest.TestCase):
    def test_device_map_given_as_a_dict_key_needs_accelerate(self):
        code = 'def f():\n    kw = {}\n    kw["device_map"] = "auto"\n    return kw\n'
        self.assertEqual(symbols_to_requirements(code), [("accelerate", "accelerate>=0.30.0")])

    def test_device_map_keyword_needs_accelerate_and_quantization_needs_bitsandbytes(self):
        code = ("import transformers\n\n\ndef f(m):\n"
                "    q = transformers.BitsAndBytesConfig(load_in_4bit=True)\n"
                "    return transformers.AutoModel.from_pretrained(m, device_map='auto', quantization_config=q)\n")
        self.assertEqual(symbols_to_requirements(code),
                         [("accelerate", "accelerate>=0.30.0"), ("bitsandbytes", "bitsandbytes>=0.43.0")])

    def test_unrelated_strings_do_not_add_packages(self):
        self.assertEqual(symbols_to_requirements('X = "a device_map is a thing"\n'), [])


class TestGeneratorRequestExplainsRuntimeCompatibility(unittest.TestCase):
    def test_request_carries_the_runtime_compatibility_rules(self):
        head = gg.build_request(analyze_preset("sst2")).rpartition(gg.DATA_BEGIN)[0]
        self.assertIn("## Runtime compatibility", head)
        self.assertIn("NEWEST release", head)
        self.assertIn("pipeline tasks", head)
        self.assertIn("AutoModel class and tokenizer", head)
        self.assertNotIn("SmolLM2", head)  # a general rule, not a model-specific patch

    def test_request_structure_is_preserved(self):
        req = gg.build_request(analyze_preset("sst2"))
        for marker in ("## Section 1", "## Section 2", "## Runtime compatibility", "## Rules for both sections", "## Output format",
                       gg.DATA_BEGIN, gg.DATA_END):
            self.assertIn(marker, req)
        self.assertEqual(req.count(gg.DATA_BEGIN), 1)


if __name__ == "__main__":
    unittest.main()
