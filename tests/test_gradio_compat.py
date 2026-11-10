"""Phase 1: Gradio compatibility checking for ChatInterface, TabbedInterface, Blocks and Interface across Gradio 4/5/6.

The synthetic API in fake_gradio_api.py REALLY raises TypeError for a keyword the version does not take, so each sample is
(a) executed against it and (b) checked statically against a snapshot of the same fake module. The two must always agree: that
is what shows the checker predicts a start-up crash instead of guessing.
"""
import contextlib
import io
import unittest

from m2g import gradio_compat as gc
from fake_gradio_api import VERSIONS, fake_gradio_api, make_gradio, run_app

CHAT = '''import gradio as gr


def chat_fn(message, history):
    return message


demo = gr.ChatInterface(fn=chat_fn, type="messages", title="Chat", description="d")
'''

TABBED = '''import gradio as gr


def f(x):
    return x


a = gr.Interface(fn=f, inputs=gr.Textbox(), outputs=gr.Textbox(), description="A")
b = gr.Interface(fn=f, inputs=gr.Textbox(), outputs=gr.Textbox(), description="B")
demo = gr.TabbedInterface([a, b], tab_names=["A", "B"], title="T", description="not accepted anywhere")
'''

TABBED_THEME = TABBED.replace('description="not accepted anywhere"', "theme=gr.themes.Soft()")

BLOCKS = '''import gradio as gr


def f(x):
    return x


with gr.Blocks(theme=gr.themes.Soft(), css="a{}", title="B") as demo:
    gr.Markdown("# hi")
    with gr.Row():
        t = gr.Textbox(label="in")
        o = gr.Textbox(label="out")
    gr.Button("go").click(f, inputs=t, outputs=o)
'''

INTERFACE = '''import gradio as gr


def f(x):
    return x


demo = gr.Interface(fn=f, inputs=gr.Textbox(), outputs=gr.Label(), title="I", description="d", theme=gr.themes.Soft())
'''

CLEAN = '''import gradio as gr


def f(x):
    return x


demo = gr.Interface(fn=f, inputs=gr.Textbox(label="in"), outputs=gr.Label(), title="ok")
'''

SAMPLES = {"ChatInterface": CHAT, "TabbedInterface": TABBED, "TabbedInterface+theme": TABBED_THEME, "Blocks": BLOCKS,
           "Interface": INTERFACE, "clean": CLEAN}


def snapshot_for(version, code):
    return gc.ApiSnapshot.from_dict(gc.snapshot_from_module(make_gradio(version), gc.gradio_names_used(code)))


def crashes(code, version):
    try:
        run_app(code, version)
    except (TypeError, AttributeError):
        return True
    return False


class TestCheckerPredictsRuntime(unittest.TestCase):
    def test_every_ui_type_on_every_version(self):
        for name, code in SAMPLES.items():
            for version in VERSIONS:
                with self.subTest(sample=name, version=version):
                    findings = gc.check_code(code, snapshot_for(version, code))
                    self.assertEqual(bool(findings), crashes(code, version), [f.message for f in findings])

    def test_the_colab_failure_is_reproduced_and_reported(self):
        # SmolVLM on Colab: "ChatInterface.__init__() got an unexpected keyword argument 'type'" (Gradio 6.30.0)
        with self.assertRaisesRegex(TypeError, "ChatInterface.__init__.*'type'"):
            run_app(CHAT, "6.30.0")
        run_app(CHAT, "5.49.0")  # the same code is fine on 5
        findings = gc.check_code(CHAT, snapshot_for("6.30.0", CHAT))
        self.assertIn("type", [f.kwarg for f in findings])
        self.assertTrue(all(f.class_name == "ChatInterface" for f in findings if f.kwarg == "type"))

    def test_positional_overflow_and_missing_class(self):
        code = "import gradio as gr\ns = gr.Slider(0, 1, 5)\nz = gr.NoSuchThing()\n"
        for version in VERSIONS:
            with self.subTest(version=version):
                kinds = {f.kind for f in gc.check_code(code, snapshot_for(version, code))}
                self.assertEqual(kinds, {"too_many_positional", "missing_class"})
                self.assertTrue(crashes(code, version))

    def test_from_import_and_alias_forms_are_checked(self):
        code = "from gradio import ChatInterface as CI\nimport gradio as g\nx = CI(fn=print, type='messages')\ny = g.Slider(0, 1, 5)\n"
        names = gc.gradio_names_used(code)
        self.assertEqual(names, ["ChatInterface", "Slider"])
        kinds = [(f.class_name, f.kind) for f in gc.check_code(code, snapshot_for("6.30.0", code))]
        self.assertEqual(sorted(kinds), [("ChatInterface", "unsupported_kwarg"), ("Slider", "too_many_positional")])

    def test_non_gradio_calls_are_ignored(self):
        code = "import gradio as gr\nfrom PIL import Image\nimg = Image.open('x')\nImage.new('RGB', (1, 1), type=3)\n"
        self.assertEqual(gc.check_code(code, snapshot_for("6.30.0", code)), [])


class TestStaticFallbackWithoutSnapshot(unittest.TestCase):
    def test_only_certain_removals_are_enforced(self):
        flagged = {(f.class_name, f.kwarg) for f in gc.check_code(CHAT) + gc.check_code(TABBED)}
        self.assertEqual(flagged, {("ChatInterface", "type"), ("TabbedInterface", "description")})
        self.assertEqual(gc.check_code(BLOCKS), [])  # unknown without the real signature: never guessed
        self.assertEqual(gc.check_code(INTERFACE), [])

    def test_known_old_version_does_not_flag_removed_in_6(self):
        self.assertEqual([f for f in gc.check_code(CHAT, snapshot_for("5.49.0", CHAT))], [])


class TestRepair(unittest.TestCase):
    def test_cosmetic_keywords_are_removed_and_app_runs_on_every_version(self):
        for name in ("ChatInterface", "TabbedInterface", "TabbedInterface+theme", "Blocks", "Interface"):
            for version in VERSIONS:
                with self.subTest(sample=name, version=version):
                    code = SAMPLES[name]
                    findings = gc.check_code(code, snapshot_for(version, code))
                    fixed, applied, remaining = gc.repair_code(code, findings)
                    self.assertEqual(remaining, [], [f.message for f in remaining])
                    self.assertEqual(len(applied), len(findings))
                    self.assertEqual(gc.check_code(fixed, snapshot_for(version, fixed)), [])
                    run_app(fixed, version)  # must not raise

    def test_untouched_when_nothing_to_fix(self):
        fixed, applied, remaining = gc.repair_code(CLEAN, gc.check_code(CLEAN, snapshot_for("6.30.0", CLEAN)))
        self.assertEqual((fixed, applied, remaining), (CLEAN, [], []))

    def test_behaviour_changing_keywords_are_never_removed(self):
        for code in ("import gradio as gr\ndemo = gr.ChatInterface(fn=print, type='tuples')\n",
                     "import gradio as gr\nt = gr.Textbox(not_a_real_arg=1)\n",
                     "import gradio as gr\nt = gr.Slider(0, 1, 5)\n"):
            with self.subTest(code=code):
                findings = gc.check_code(code, snapshot_for("6.30.0", code))
                self.assertTrue(findings)
                fixed, applied, remaining = gc.repair_code(code, findings)
                self.assertEqual((fixed, applied), (code, []))
                self.assertEqual(len(remaining), len(findings))

    def test_edit_shapes(self):
        cases = {
            "gr.Interface(fn=f, inputs=a, outputs=b, theme='soft')": "gr.Interface(fn=f, inputs=a, outputs=b)",
            "gr.Interface(theme='soft', fn=f, inputs=a, outputs=b)": "gr.Interface(fn=f, inputs=a, outputs=b)",
            "gr.Interface(fn=f, theme='soft', inputs=a, outputs=b)": "gr.Interface(fn=f, inputs=a, outputs=b)",
            "gr.Blocks(theme='soft')": "gr.Blocks()",
            "gr.Blocks(title=\"é, ü\", theme=gr.themes.Soft(primary_hue=\"a\", secondary_hue=\"b\"))": "gr.Blocks(title=\"é, ü\")",
            "gr.Interface(fn=f,\n    inputs=a,\n    theme='soft',\n    outputs=b)": "gr.Interface(fn=f,\n    inputs=a,\n    outputs=b)",
        }
        for src, want in cases.items():
            with self.subTest(src=src):
                code = "import gradio as gr\nx = " + src + "\n"
                findings = gc.check_code(code, snapshot_for("6.30.0", code))
                fixed, applied, _ = gc.repair_code(code, findings)
                self.assertEqual(len(applied), 1)
                self.assertEqual(fixed, "import gradio as gr\nx = " + want + "\n")


class TestProbe(unittest.TestCase):
    def test_probe_script_roundtrip_on_each_version(self):
        for version in VERSIONS:
            with self.subTest(version=version), fake_gradio_api(version) as mod:
                script = gc.render_probe_script(["ChatInterface", "Blocks", "NoSuchThing", "bad name; rm -rf"])
                self.assertNotIn("rm -rf", script)
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    exec(compile(script, "probe.py", "exec"), {})
                parsed = gc.parse_probe_output("noise\n" + out.getvalue() + "more noise\n")
                self.assertEqual(parsed, gc.ApiSnapshot.from_dict(gc.snapshot_from_module(mod, ["ChatInterface", "Blocks", "NoSuchThing"])))
                self.assertEqual(parsed.version, version)
                self.assertIn("NoSuchThing", parsed.missing)
                self.assertEqual("type" in parsed.classes["ChatInterface"].params, parsed.major < 6)

    def test_garbage_probe_output_is_none(self):
        for text in ("", "no marker", gc.PROBE_MARKER + " {not json", gc.PROBE_MARKER + " [1]", gc.PROBE_MARKER + ' {"x": 1}'):
            with self.subTest(text=text):
                self.assertIsNone(gc.parse_probe_output(text))

    def test_syntax_error_has_no_names_and_check_raises(self):
        self.assertEqual(gc.gradio_names_used("def ("), [])
        with self.assertRaises(SyntaxError):
            gc.check_code("def (")


class TestValidatorHook(unittest.TestCase):
    """The static gate every generated app passes before it can be written or deployed."""

    HEAD = ("import os\nimport gradio as gr\nimport torch\n\nMODEL_ID = 'a/b'\nREVISION = '0123456789abcdef0123456789abcdef01234567'\n"
            "TASK = 'text-generation'\nTRUST_REMOTE_CODE = False\nUSE_SAFETENSORS = True\nHF_TOKEN = None\nDEVICE = -1\n\n\n"
            "def get_model():\n    return None\n\n\ndef chat_fn(message, history):\n    return get_model()(message)\n\n\n")

    def test_chat_interface_type_is_rejected_before_deployment(self):
        from m2g.validate import ValidationError, validate_app_code
        with self.assertRaisesRegex(ValidationError, r"ChatInterface\(\) does not accept 'type'"):
            validate_app_code(self.HEAD + 'demo = gr.ChatInterface(fn=chat_fn, type="messages", title=MODEL_ID)\n')

    def test_chat_interface_without_type_passes(self):
        from m2g.validate import validate_app_code
        validate_app_code(self.HEAD + "demo = gr.ChatInterface(fn=chat_fn, title=MODEL_ID)\n")


if __name__ == "__main__":
    unittest.main()
