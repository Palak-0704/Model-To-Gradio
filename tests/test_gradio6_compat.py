"""Regression tests: generated apps must be compatible with Gradio 6 (Colab installed 6.30.0).

Whisper failed at start-up with ``TypeError: TabbedInterface.__init__() got an unexpected keyword argument 'description'``.
The fix is at generator level: the OpenCode request forbids those keywords and the static validator rejects them, so a
bad reply raises GenerationError instead of becoming an app that crashes on Colab. Nothing generated is executed here.
"""
import unittest

from m2g import gradio_gen as gg
from m2g.validate import ValidationError, validate_app_code
from fakehub import FakeWeb  # noqa: F401  (importing fakehub installs the fake OpenCode adapter)
from fake_opencode import FakeOpenCodeAdapter, default_setup, facts_of, reply
from test_analyzer import analyze_preset

_HEAD = '''"""t"""
import os
import gradio as gr
import torch

MODEL_ID = 'a/b'
REVISION = '0123456789abcdef0123456789abcdef01234567'
TASK = 'automatic-speech-recognition'
TRUST_REMOTE_CODE = False
USE_SAFETENSORS = True
HF_TOKEN = None
DEVICE = -1


def get_model():
    return None


def predict_transcription(audio):
    return str(audio)


def predict_translation(audio):
    return str(audio)


tab_a = gr.Interface(fn=predict_transcription, inputs=[gr.Audio()], outputs=[gr.Textbox()], description="Transcribe")
tab_b = gr.Interface(fn=predict_translation, inputs=[gr.Audio()], outputs=[gr.Textbox()], description="Translate")
'''


def _app(tabbed_call):
    return _HEAD + "demo = " + tabbed_call + "\n"


class TestTabbedInterfaceKwargs(unittest.TestCase):
    def test_supported_keywords_pass(self):
        validate_app_code(_app("gr.TabbedInterface([tab_a, tab_b], tab_names=['Transcribe', 'Translate'], title=MODEL_ID)"))

    def test_positional_tab_names_and_title_pass(self):
        validate_app_code(_app("gr.TabbedInterface([tab_a, tab_b], ['Transcribe', 'Translate'], MODEL_ID)"))

    def test_description_is_rejected(self):
        # the exact failure seen on Colab with Gradio 6.30.0
        with self.assertRaisesRegex(ValidationError, "TabbedInterface.*description"):
            validate_app_code(_app("gr.TabbedInterface([tab_a, tab_b], tab_names=['A', 'B'], title=MODEL_ID, description='x')"))

    def test_other_interface_only_keywords_are_rejected(self):
        for kw in ("article='x'", "theme='soft'", "css='a{}'", "js='x'", "head='x'"):
            with self.subTest(kw=kw):
                with self.assertRaisesRegex(ValidationError, "TabbedInterface"):
                    validate_app_code(_app(f"gr.TabbedInterface([tab_a, tab_b], tab_names=['A', 'B'], {kw})"))

    def test_too_many_positional_arguments_are_rejected(self):
        with self.assertRaisesRegex(ValidationError, "positional"):
            validate_app_code(_app("gr.TabbedInterface([tab_a, tab_b], ['A', 'B'], MODEL_ID, 'soft')"))

    def test_description_on_each_inner_interface_is_fine(self):
        # both tabs and their inference callbacks stay intact; descriptions live on the inner gr.Interface objects
        validate_app_code(_app("gr.TabbedInterface([tab_a, tab_b], tab_names=['A', 'B'])"))


class TestGeneratorRejectsIncompatibleReply(unittest.TestCase):
    def test_request_states_the_gradio_6_rule(self):
        request = gg.build_request(analyze_preset("sst2"))
        self.assertIn("gr.TabbedInterface accepts ONLY interface_list, tab_names, title and analytics_enabled", request)
        self.assertIn("Never pass", request)

    def test_generate_app_refuses_tabbed_interface_with_description(self):
        a = analyze_preset("sst2")
        setup = default_setup(facts_of(gg.build_request(a)))
        ui = ("def predict(text):\n"
              "    return get_model()(text)\n\n\n"
              "tab = gr.Interface(fn=predict, inputs=[gr.Textbox()], outputs=[gr.Textbox()])\n"
              "demo = gr.TabbedInterface([tab], tab_names=['A'], description='x')\n")
        with self.assertRaisesRegex(gg.GenerationError, "TabbedInterface"):
            gg.generate_app(a, adapter=FakeOpenCodeAdapter(text=reply(setup, ui)))


if __name__ == "__main__":
    unittest.main()
