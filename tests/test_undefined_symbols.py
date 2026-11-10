"""Regression tests for generated apps that use a library symbol without importing it (``BitsAndBytesConfig`` was referenced bare).

Root causes covered here (OpenCode is replaced by FakeOpenCodeAdapter; nothing is executed or downloaded):
  * the request told the model to use "a 4-bit BitsAndBytesConfig" without saying that the platform header does NOT import it;
  * a star import (``from transformers import *``) made pyflakes downgrade the problem to a non-fatal "may be undefined";
  * ``requirements.txt`` was derived from import statements only, so quantization code never got ``bitsandbytes``;
  * an undefined-name failure did not say which module exports the missing name.
The static validation is unchanged in strength: every case below that must be rejected is rejected by the real validator.
"""
import unittest

from m2g import gradio_gen as gg
from m2g.validate import KNOWN_SYMBOL_MODULES, ValidationError, symbols_to_requirements, validate_app_code
from fake_opencode import FakeOpenCodeAdapter, default_ui, facts_of, reply
from test_analyzer import analyze_preset

_BNB_REQ = "bitsandbytes>=0.43.0"

# What the failing generation looked like: the quantization config is used inside get_model() but never imported.
_BARE_SETUP = '''
_model = None


def get_model():
    global _model
    if _model is None:
        from transformers import pipeline
        quant = None
        if LOAD_IN_4BIT:
            quant = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_compute_dtype=DTYPE)
        _model = pipeline(TASK, model=MODEL_ID, revision=REVISION, token=HF_TOKEN, trust_remote_code=TRUST_REMOTE_CODE,
                          device=DEVICE, model_kwargs={"use_safetensors": USE_SAFETENSORS, "quantization_config": quant})
    return _model
'''

_FROM_IMPORT_SETUP = _BARE_SETUP.replace("        from transformers import pipeline\n",
                                         "        from transformers import BitsAndBytesConfig, pipeline\n")

_QUALIFIED_SETUP = '''import transformers

_model = None


def get_model():
    global _model
    if _model is None:
        quant = None
        if LOAD_IN_4BIT:
            quant = transformers.BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_compute_dtype=DTYPE)
        _model = transformers.pipeline(TASK, model=MODEL_ID, revision=REVISION, token=HF_TOKEN,
                                       trust_remote_code=TRUST_REMOTE_CODE, device=DEVICE,
                                       model_kwargs={"use_safetensors": USE_SAFETENSORS, "quantization_config": quant})
    return _model
'''


def _ui(a):
    return default_ui(facts_of(gg.build_request(a)))


def _generate(a, setup):
    return gg.generate_app(a, adapter=FakeOpenCodeAdapter(text=reply(setup, _ui(a))))


class TestUndefinedLibrarySymbolIsRejected(unittest.TestCase):
    def test_bare_bitsandbytesconfig_is_rejected_with_an_actionable_message(self):
        a = analyze_preset("sst2")
        with self.assertRaises(gg.GenerationError) as cm:
            _generate(a, _BARE_SETUP)
        msg = str(cm.exception)
        self.assertIn("undefined name 'BitsAndBytesConfig'", msg)
        self.assertIn("from transformers import BitsAndBytesConfig", msg)
        self.assertIn("transformers.BitsAndBytesConfig", msg)

    def test_the_real_validator_rejects_it_directly(self):
        code = gg._build_header(analyze_preset("sst2")) + "\n" + _BARE_SETUP + "\n" + _ui(analyze_preset("sst2"))
        with self.assertRaisesRegex(ValidationError, "BitsAndBytesConfig"):
            validate_app_code(code)

    def test_star_import_cannot_hide_an_undefined_name(self):
        a = analyze_preset("sst2")
        with self.assertRaises(gg.GenerationError) as cm:
            _generate(a, "from transformers import *\n" + _BARE_SETUP)
        self.assertIn("star import", str(cm.exception))

    def test_every_known_library_symbol_used_bare_is_rejected_with_its_module(self):
        a = analyze_preset("sst2")
        for name, mod in sorted(KNOWN_SYMBOL_MODULES.items()):
            setup = ("_model = None\n\n\ndef get_model():\n    global _model\n    if _model is None:\n"
                     f"        _model = {name}\n    return _model\n")
            with self.subTest(symbol=name):
                with self.assertRaises(gg.GenerationError) as cm:
                    _generate(a, setup)
                self.assertIn(f"undefined name '{name}'", str(cm.exception))
                self.assertIn(f"from {mod} import {name}", str(cm.exception))

    def test_a_name_that_is_not_a_known_symbol_is_still_rejected(self):
        a = analyze_preset("sst2")
        setup = "_model = None\n\n\ndef get_model():\n    global _model\n    return SomethingNeverImported\n"
        with self.assertRaises(gg.GenerationError) as cm:
            _generate(a, setup)
        self.assertIn("undefined name 'SomethingNeverImported'", str(cm.exception))


class TestProperlyImportedSymbolIsAccepted(unittest.TestCase):
    def test_both_import_styles_pass_validation(self):
        for label, setup in (("from-import", _FROM_IMPORT_SETUP), ("qualified", _QUALIFIED_SETUP)):
            with self.subTest(style=label):
                a = analyze_preset("sst2")
                code = _generate(a, setup)
                self.assertIn("BitsAndBytesConfig", code)
                validate_app_code(code)

    def test_requirements_get_bitsandbytes_when_quantization_code_is_used(self):
        for label, setup in (("from-import", _FROM_IMPORT_SETUP), ("qualified", _QUALIFIED_SETUP)):
            with self.subTest(style=label):
                a = analyze_preset("sst2")
                code = _generate(a, setup)
                reqs = gg.generate_requirements(a, code).splitlines()
                self.assertIn(_BNB_REQ + ",<1", reqs)
                self.assertIn("transformers", " ".join(reqs))
                self.assertIn("needed by the symbols used in", a.dependency_evidence["bitsandbytes"])
                self.assertIn(_BNB_REQ, a.dependencies)  # the Colab notebook reads a.dependencies for its %pip line

    def test_no_bitsandbytes_when_the_app_does_not_quantize(self):
        a = analyze_preset("sst2")
        gg.generate_app(a, adapter=FakeOpenCodeAdapter())
        self.assertFalse([r for r in gg.generate_requirements(a).splitlines() if r.startswith("bitsandbytes")])

    def test_an_existing_bitsandbytes_requirement_is_not_duplicated(self):
        a = analyze_preset("sst2")
        a.dependencies = list(a.dependencies) + ["bitsandbytes>=0.45.0"]
        code = _generate(a, _FROM_IMPORT_SETUP)
        reqs = [r for r in gg.generate_requirements(a, code).splitlines() if r.startswith("bitsandbytes")]
        self.assertEqual(reqs, ["bitsandbytes>=0.45.0,<1"])


class TestSymbolsToRequirements(unittest.TestCase):
    def test_detects_class_attribute_and_keyword_forms(self):
        for src in ("def f():\n    return BitsAndBytesConfig(load_in_4bit=True)\n",
                    "import transformers\n\n\ndef f():\n    return transformers.BitsAndBytesConfig()\n",
                    "def f(m):\n    return m.from_pretrained('x', load_in_8bit=True)\n",
                    "from transformers import BitsAndBytesConfig\n"):
            with self.subTest(src=src):
                self.assertEqual(symbols_to_requirements(src), [("bitsandbytes", _BNB_REQ)])

    def test_plain_code_needs_nothing(self):
        self.assertEqual(symbols_to_requirements("import os\n\n\ndef f():\n    return os.getcwd()\n"), [])

    def test_invalid_python_is_a_validation_error(self):
        with self.assertRaises(ValidationError):
            symbols_to_requirements("def f(:\n")


class TestRequestExplainsWhereSymbolsComeFrom(unittest.TestCase):
    def test_every_model_variant_gets_the_import_rules(self):
        for key in ("sst2", "chat", "big7b", "bnb4"):
            with self.subTest(preset=key):
                req = gg.build_request(analyze_preset(key))
                self.assertIn("`BitsAndBytesConfig` is NOT provided by the platform header", req)
                self.assertIn("from transformers import BitsAndBytesConfig", req)
                self.assertIn("transformers.BitsAndBytesConfig", req)
                self.assertIn("Every name you use must be defined or imported in YOUR sections", req)
                self.assertIn("Star imports", req)
                self.assertNotIn("plus a 4-bit BitsAndBytesConfig for LOAD_IN_4BIT", req)  # the ambiguous old wording

    def test_instructions_are_not_specific_to_one_model(self):
        head = gg.build_request(analyze_preset("sst2")).rpartition(gg.DATA_BEGIN)[0]
        self.assertNotIn("SmolLM2", head)
        self.assertIn("never import `bitsandbytes` yourself".lower(), head.lower())

    def test_request_structure_is_preserved(self):
        req = gg.build_request(analyze_preset("sst2"))
        for marker in ("## Section 1", "## Section 2", "## Rules for both sections", "## Output format", gg.DATA_BEGIN, gg.DATA_END):
            self.assertIn(marker, req)
        self.assertEqual(req.count(gg.DATA_BEGIN), 1)


if __name__ == "__main__":
    unittest.main()
