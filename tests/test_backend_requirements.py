"""Regression tests for the Whisper inference errors and the platform-wide "no artificial restrictions" rule.

  * ``model_kwargs are not used by the model: ['use_safetensors']``: a loading option reached inference.
  * ``more than 3000 mel input features (> 30 seconds)``: long audio needs backend handling, not a UI limit.

OpenCode is never started and no model is loaded. A reference app (what a correct OpenCode reply looks like) is checked by the
real static validator and its inference functions are run against a recording fake pipeline with stub gradio/torch modules.
These tests show the platform's request and validator behave as intended; they do not show that a live OpenCode run or a real
Whisper download works (that still needs Colab).
"""
import ast
import sys
import unittest
from unittest import mock

from m2g import gradio_gen as gg
from m2g.validate import ValidationError, validate_app_code
from test_analyzer import analyze_preset
from test_gradio_gen import _speech_analysis, make_gradio, make_torch

_HEAD = '''"""t"""
import os
import gradio as gr
import torch

MODEL_ID = 'acme/speech'
REVISION = '0123456789abcdef0123456789abcdef01234567'
TASK = 'automatic-speech-recognition'
TRUST_REMOTE_CODE = False
USE_SAFETENSORS = True
HF_TOKEN = None
DEVICE = -1
DTYPE = torch.float32
'''

_SETUP = '''
_pipe = None


def get_model():
    global _pipe
    if _pipe is None:
        from transformers import pipeline
        _pipe = pipeline(TASK, model=MODEL_ID, revision=REVISION, token=HF_TOKEN, trust_remote_code=TRUST_REMOTE_CODE,
                         device=DEVICE, model_kwargs={"use_safetensors": LOAD_SAFETENSORS})
    return _pipe
'''

_UI = '''

def _run(audio_path, task):
    if not audio_path:
        raise gr.Error("Please provide audio.")
    result = get_model()(audio_path, return_timestamps=True, generate_kwargs={"task": task})
    text = _extract_speech_text(result)
    if not text:
        raise gr.Error("No speech was recognised.")
    return text


def predict_automatic_speech_recognition(audio):
    return _run(audio, "transcribe")


def predict_translation(audio):
    return _run(audio, "translate")


tab_a = gr.Interface(fn=predict_automatic_speech_recognition, inputs=[gr.Audio(type="filepath")], outputs=[gr.Textbox()])
tab_b = gr.Interface(fn=predict_translation, inputs=[gr.Audio(type="filepath")], outputs=[gr.Textbox()])
demo = gr.TabbedInterface([tab_a, tab_b], tab_names=["Transcribe", "Translate"], title=MODEL_ID)
'''


def reference_app(setup=_SETUP, ui=_UI):
    return _HEAD + "\n" + setup + "\n\n" + gg._SPEECH_TEXT_HELPER + ui


class FakePipe:
    def __init__(self, result):
        self.result, self.calls = result, []

    def __call__(self, audio, **kwargs):
        self.calls.append((audio, kwargs))
        return self.result


def load_app(code, pipe):
    """Run the app's module-level code with stub gradio/torch; get_model is replaced by the fake pipeline."""
    ns = {"__name__": "generated_app"}
    with mock.patch.dict(sys.modules, {"gradio": make_gradio(), "torch": make_torch()}):
        exec(compile(code, "app.py", "exec"), ns)
    ns["get_model"] = lambda: pipe
    return ns


class TestSpeechTextExtraction(unittest.TestCase):
    def helper(self):
        ns = {}
        exec(gg._SPEECH_TEXT_HELPER, ns)
        return ns["_extract_speech_text"]

    def test_text_only_result(self):
        self.assertEqual(self.helper()({"text": " hello world "}), "hello world")

    def test_text_with_timestamp_chunks(self):
        r = {"text": " one two", "chunks": [{"text": " one", "timestamp": (0.0, 1.0)}, {"text": " two", "timestamp": (1.0, 2.0)}]}
        self.assertEqual(self.helper()(r), "one two")

    def test_chunks_only_result(self):
        r = {"chunks": [{"text": " one", "timestamp": (0.0, 1.0)}, {"text": "", "timestamp": (1.0, None)}, {"text": " two"}]}
        self.assertEqual(self.helper()(r), "one two")

    def test_list_of_results_and_empty_or_malformed(self):
        fn = self.helper()
        self.assertEqual(fn([{"text": "a"}, {"text": "b"}]), "a b")
        for bad in (None, {}, [], {"text": "  "}, {"chunks": "x"}, 3):
            self.assertEqual(fn(bad), "", bad)

    def test_helper_is_valid_module_level_def_only(self):
        self.assertEqual([type(n).__name__ for n in ast.parse(gg._SPEECH_TEXT_HELPER).body], ["FunctionDef"])


class TestReferenceAppBehaviour(unittest.TestCase):
    def test_reference_app_passes_the_real_validator(self):
        validate_app_code(reference_app())

    def test_task_tokens_and_timestamps_for_both_capabilities(self):
        pipe = FakePipe({"text": " hi", "chunks": [{"text": " hi", "timestamp": (0.0, 1.0)}]})
        ns = load_app(reference_app(), pipe)
        self.assertEqual(ns["predict_automatic_speech_recognition"]("a.wav"), "hi")
        self.assertEqual(ns["predict_translation"]("a.wav"), "hi")
        tasks = [kw["generate_kwargs"]["task"] for _a, kw in pipe.calls]
        self.assertEqual(tasks, ["transcribe", "translate"])
        for _a, kw in pipe.calls:
            self.assertIs(kw["return_timestamps"], True)  # long-form audio needs timestamps

    def test_use_safetensors_is_never_an_inference_argument(self):
        pipe = FakePipe({"text": "x"})
        ns = load_app(reference_app(), pipe)
        ns["predict_translation"]("a.wav")
        for _a, kw in pipe.calls:
            flat = set(kw) | set(kw.get("generate_kwargs", {}))
            self.assertFalse(flat & {"use_safetensors", "revision", "token", "trust_remote_code"})

    def test_short_audio_without_chunks_still_works(self):
        ns = load_app(reference_app(), FakePipe({"text": " short clip"}))
        self.assertEqual(ns["predict_automatic_speech_recognition"]("a.wav"), "short clip")

    def test_long_audio_chunk_result_is_readable(self):
        r = {"text": "", "chunks": [{"text": " first part", "timestamp": (0.0, 30.0)}, {"text": " second part", "timestamp": (30.0, 61.0)}]}
        ns = load_app(reference_app(), FakePipe(r))
        self.assertEqual(ns["predict_translation"]("long.wav"), "first part second part")

    def test_missing_audio_is_a_clear_error_not_a_limit(self):
        ns = load_app(reference_app(), FakePipe({"text": "x"}))
        with self.assertRaises(ns["gr"].Error):
            ns["predict_translation"](None)


class TestValidatorRejectsLoadingLeaksAndInventedLimits(unittest.TestCase):
    def bad_setup(self, call):
        return _SETUP.replace(
            'pipeline(TASK, model=MODEL_ID, revision=REVISION, token=HF_TOKEN, trust_remote_code=TRUST_REMOTE_CODE,\n'
            '                         device=DEVICE, model_kwargs={"use_safetensors": LOAD_SAFETENSORS})', call)

    def test_use_safetensors_passed_directly_to_pipeline_is_rejected(self):
        # exactly what the generated Whisper app did
        setup = self.bad_setup("pipeline(TASK, model=MODEL_ID, revision=REVISION, token=HF_TOKEN, use_safetensors=USE_SAFETENSORS, device=DEVICE)")
        with self.assertRaisesRegex(ValidationError, "use_safetensors"):
            validate_app_code(reference_app(setup=setup))

    def test_use_safetensors_in_model_kwargs_is_accepted(self):
        validate_app_code(reference_app())

    def test_loading_option_in_generate_kwargs_is_rejected(self):
        ui = _UI.replace('generate_kwargs={"task": task}', 'generate_kwargs={"task": task, "use_safetensors": True}')
        with self.assertRaisesRegex(ValidationError, "generate_kwargs"):
            validate_app_code(reference_app(ui=ui))

    def test_audio_duration_limit_is_rejected(self):
        ui = _UI.replace('gr.Audio(type="filepath")', 'gr.Audio(type="filepath", max_length=30)', 1)
        with self.assertRaisesRegex(ValidationError, "invent"):
            validate_app_code(reference_app(ui=ui))

    def test_text_length_and_file_size_limits_are_rejected(self):
        for old, new in (("gr.Textbox()", "gr.Textbox(max_length=500)"), ("gr.Textbox()", "gr.Textbox(max_file_size=10)")):
            with self.subTest(new=new):
                with self.assertRaises(ValidationError):
                    validate_app_code(reference_app(ui=_UI.replace(old, new, 1)))

    def test_a_genuine_limit_enforced_in_the_function_is_preserved(self):
        # evidence-backed limits are not blocked: they live in the inference function with an accurate message
        ui = _UI.replace('    if not audio_path:\n        raise gr.Error("Please provide audio.")\n',
                         '    if not audio_path:\n        raise gr.Error("Please provide audio.")\n'
                         '    if str(audio_path).endswith(".xyz"):\n        raise gr.Error("Documented limitation: .xyz is not supported.")\n')
        validate_app_code(reference_app(ui=ui))


class TestRequestCarriesTheRules(unittest.TestCase):
    def setUp(self):
        self.request = gg.build_request(analyze_preset("sst2"))

    def test_loading_options_rule(self):
        self.assertIn('model_kwargs={"use_safetensors": LOAD_SAFETENSORS}', self.request)
        self.assertIn("LOAD_SAFETENSORS", self.request)
        self.assertIn("generate_kwargs", self.request)

    def test_long_form_and_task_rule(self):
        # these rules belong to speech-recognition requests: a text or image model is not told about audio windows
        request = gg.build_request(_speech_analysis())
        self.assertIn("return_timestamps=True", request)
        self.assertIn("chunk_length_s", request)
        self.assertIn('generate_kwargs={"task": ...}', request)
        for line in gg._SPEECH_TEXT_HELPER.splitlines():
            self.assertIn(line, request)

    def test_speech_rules_are_not_in_requests_for_other_tasks(self):
        for marker in ("return_timestamps=True", "chunk_length_s", "_extract_speech_text"):
            self.assertNotIn(marker, self.request, marker)  # self.request is the text-classification request

    def test_no_artificial_restrictions_rule(self):
        self.assertIn("NO artificial restrictions", self.request)
        self.assertIn("hard-coded maximum audio/video duration", self.request)
        self.assertIn("language list", self.request)
        self.assertIn("An unknown limit is not a", self.request)
        self.assertIn("establishes", self.request)  # genuine, evidence-backed limits remain allowed

    def test_request_structure_is_preserved(self):
        for marker in ("## Section 1", "## Section 2", "## Output format", gg.DATA_BEGIN, gg.DATA_END):
            self.assertIn(marker, self.request)
        self.assertEqual(self.request.count(gg.DATA_BEGIN), 1)


if __name__ == "__main__":
    unittest.main()


