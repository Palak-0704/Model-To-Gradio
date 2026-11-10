"""Offline stand-in for OpenCode. The real adapter would start `opencode.exe` and spend model credits; tests never do that.

``FakeOpenCodeAdapter`` has the one method the generator uses, ``run(request_text)``. It reads the JSON facts block out of the
request (exactly what the real model is given) and answers with the two code blocks the generator asks for. This verifies the
platform's side (request building, section extraction, assembly, rejection, validation, pipeline wiring). It does NOT show
that the real model writes good code: that needs a live OpenCode run.

``install()`` replaces the generator's default adapter factory, so every test that reaches code generation (they all import
fakehub, which calls install()) gets the fake instead of the real CLI.
"""
import json
import re

from m2g import gradio_gen as gg
from m2g.opencode_cli import OpenCodeResult

_FENCE = "`" * 3


def facts_of(request_text):
    m = re.search(_FENCE + r"json m2g:facts\n(.*?)\n" + _FENCE, request_text, re.S)
    if not m:
        raise AssertionError("request has no m2g:facts block")
    return json.loads(m.group(1))


def default_setup(facts):
    lib = facts.get("library")
    if lib == "diffusers":
        load = ("        from diffusers import DiffusionPipeline\n\n"
                "        _model = DiffusionPipeline.from_pretrained(\n"
                "            MODEL_ID, revision=REVISION, token=HF_TOKEN, torch_dtype=DTYPE, use_safetensors=USE_SAFETENSORS\n"
                "        )\n")
    elif lib == "sentence-transformers":
        load = ("        from sentence_transformers import SentenceTransformer\n\n"
                "        _model = SentenceTransformer(\n"
                "            MODEL_ID, revision=REVISION, token=HF_TOKEN, trust_remote_code=TRUST_REMOTE_CODE\n"
                "        )\n")
    else:
        load = ("        from transformers import pipeline\n\n"
                "        _model = pipeline(\n"
                "            TASK, model=MODEL_ID, revision=REVISION, token=HF_TOKEN,\n"
                "            trust_remote_code=TRUST_REMOTE_CODE, device=DEVICE,\n"
                "        )\n")
    return ("_model = None\n\n\n"
            "def get_model():\n"
            '    """Load the model once, on first use."""\n'
            "    global _model\n"
            "    if _model is None:\n" + load +
            "    return _model\n")


def _components(items):
    return ", ".join("%s(label=%r)" % (i["component"], i["name"]) for i in items)


def _cap_components(cap):
    ref = cap.get("reference_components")
    if ref:
        return ref["inputs"], ref["outputs"]
    sug = cap.get("suggested_components") or {}  # no TASK_IO reference: derive components from the capability's modalities
    return ([{"name": "input", "component": (sug.get("inputs") or ["gr.Textbox"])[0]}],
            [{"name": "output", "component": (sug.get("outputs") or ["gr.Textbox"])[0]}])


def _multi_ui(facts):
    """Horizontal-tabs multi-capability app used to test the generator contract: one gr.Blocks, one gr.Tabs, one gr.Tab per
    capability, each tab with its own components, action button and handler."""
    handlers, tabs = [], []
    for i, cap in enumerate(facts["capabilities"]):
        ins, outs = _cap_components(cap)
        handlers.append("def %s(*args):\n"
                        "    if not args or args[0] is None or (isinstance(args[0], str) and not args[0].strip()):\n"
                        '        raise gr.Error("Input is empty.")\n'
                        "    return get_model()(args[0], task=%r)\n" %
                        (cap["function_name"], cap["function_name"]))
        in_vars = ["in_%d_%d" % (i, j) for j in range(len(ins))]
        out_vars = ["out_%d_%d" % (i, j) for j in range(len(outs))]
        body = ["gr.Markdown(%r)" % cap["name"]]
        body += ["%s = %s(label=%r)" % (v, c["component"], c["name"]) for v, c in zip(in_vars, ins)]
        body += ["%s = %s(label=%r)" % (v, c["component"], c["name"]) for v, c in zip(out_vars, outs)]
        body += ["run_%d = gr.Button('Run')" % i,
                 "run_%d.click(fn=%s, inputs=[%s], outputs=[%s])" % (i, cap["function_name"], ", ".join(in_vars), ", ".join(out_vars))]
        tabs.append("            with gr.Tab(%r):\n" % cap["name"] + "\n".join("                " + line for line in body))
    return (
        "\n\n".join(handlers) + "\n\n"
        "def build_demo():\n"
        "    with gr.Blocks(title=MODEL_ID) as demo:\n"
        "        gr.Markdown('# ' + MODEL_ID)\n"
        "        with gr.Tabs():\n"
        "%s\n"
        "    return demo\n\n\n"
        "demo = build_demo()\n" % "\n".join(tabs)
    )


def default_ui(facts):
    if facts.get("multi_capability"):
        return _multi_ui(facts)
    ins, outs = facts["expected_inputs"], facts["expected_outputs"]
    if not ins:
        ins, outs = _cap_components(facts["capabilities"][0])
    names = [i["name"] for i in ins]
    if facts.get("chat_model"):
        params = "".join(", " + n for n in names[1:])
        return ("def chat_fn(message, history%s):\n"
                "    if not str(message).strip():\n"
                '        raise gr.Error("Please type a message.")\n'
                "    return str(get_model()(message))\n\n\n"
                "demo = gr.ChatInterface(fn=chat_fn, additional_inputs=[%s], title=MODEL_ID)\n"
                % (params, _components(ins[1:])))
    first = names[0]
    return ("def predict(%s):\n"
            "    if %s is None or (isinstance(%s, str) and not %s.strip()):\n"
            '        raise gr.Error("Input is empty.")\n'
            "    return get_model()(%s)\n\n\n"
            "demo = gr.Interface(fn=predict, inputs=[%s], outputs=[%s], title=MODEL_ID)\n"
            % (", ".join(names), first, first, first, first, _components(ins), _components(outs)))


def reply(setup, ui):
    return "%spython m2g:setup\n%s\n%s\n\n%spython m2g:ui\n%s\n%s\n" % (_FENCE, setup.strip("\n"), _FENCE, _FENCE, ui.strip("\n"), _FENCE)


def default_reply(request_text):
    facts = facts_of(request_text)
    return reply(default_setup(facts), default_ui(facts))


class FakeOpenCodeAdapter:
    """``text`` overrides the reply; ``error`` (+ ``message``) simulates an OpenCode failure such as 'exit_nonzero'."""

    def __init__(self, text=None, error=None, message=""):
        self.text, self.error, self.message = text, error, message
        self.requests = []

    @property
    def calls(self):
        return len(self.requests)

    def run(self, request_text):
        self.requests.append(request_text)
        res = OpenCodeResult(returncode=0)
        if self.error:
            res.returncode, res.error, res.message = 1, self.error, self.message or "simulated failure"
            return res
        res.text_parts = [self.text if self.text is not None else default_reply(request_text)]
        return res


def install():
    gg._new_adapter = lambda: FakeOpenCodeAdapter()
