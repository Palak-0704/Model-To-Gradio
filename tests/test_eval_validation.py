"""Regression tests: PyTorch's ``model.eval()`` is allowed, the dynamic-code builtins stay forbidden.

The validator used to look only at the called NAME (``_call_name`` returns ``eval`` for ``model.eval()`` as well as for the builtin
``eval(src)``), so a legitimate generated app was rejected with "Generated app calls forbidden function 'eval'". ``validate_app_code``
(app.py) and ``validate_notebook`` (code cells) now share one rule, ``_is_forbidden_call``, and both are covered here.
"""
import ast
import json

import pytest

from m2g.validate import ValidationError, _is_forbidden_call, validate_app_code, validate_notebook

_HEAD = '''"""t"""
import os
import gradio as gr
import torch

MODEL_ID = 'a/b'
REVISION = '0123456789abcdef0123456789abcdef01234567'
TASK = 'text-classification'
TRUST_REMOTE_CODE = False
USE_SAFETENSORS = True
HF_TOKEN = None
DEVICE = -1
'''
_TAIL = '''

def predict(x):
    return x


demo = gr.Interface(fn=predict, inputs=[gr.Textbox()], outputs=[gr.Textbox()])
'''


def _app(body):
    return _HEAD + "\n\ndef get_model():\n" + "".join("    " + ln + "\n" for ln in body.strip("\n").splitlines()) + _TAIL


def _call(src):
    node = ast.parse(src, mode="eval").body
    assert isinstance(node, ast.Call)
    return node


# ------------------------------------------------------------------------------------------------ the predicate itself

ALLOWED_EVAL_MODE = [
    "model.eval()",
    "self.model.eval()",
    "model.to(DEVICE).eval()",
    "AutoModel.from_pretrained(MODEL_ID).eval()",
    "models[0].eval()",
    "torch.nn.Linear(1, 1).eval()",
]


@pytest.mark.parametrize("src", ALLOWED_EVAL_MODE)
def test_eval_mode_method_call_is_not_forbidden(src):
    assert not _is_forbidden_call(_call(src))


STILL_FORBIDDEN = [
    "eval('1')", "eval()", "exec('1')", "compile('1', 'x', 'exec')", "__import__('os')",
    "builtins.eval('1')", "builtins.eval()", "__builtins__.eval('1')", "__builtins__.eval()", "__builtin__.eval()",
    "importlib.import_module('builtins').eval()",
    "model.eval('1')", "model.eval(src)", "model.eval(*args)", "model.eval(**kwargs)", "model.eval(True)",
    # every other existing entry of FORBIDDEN_CALLS is untouched
    "os.system('x')", "torch.load('p')", "pickle.loads(b'')", "shutil.rmtree('x')", "os.remove('x')", "os.unlink('x')",
    "re.compile('x')", "urllib.request.urlopen('u')",
]


@pytest.mark.parametrize("src", STILL_FORBIDDEN)
def test_dangerous_calls_stay_forbidden(src):
    assert _is_forbidden_call(_call(src))


def test_allowed_names_do_not_loosen_eval():
    allowed = frozenset({"load", "loads", "remove"})  # what a notebook cell is allowed to call
    assert not _is_forbidden_call(_call("torch.load('p')"), allowed)
    assert _is_forbidden_call(_call("eval('1')"), allowed)
    assert _is_forbidden_call(_call("builtins.eval('1')"), allowed)
    assert not _is_forbidden_call(_call("model.eval()"), allowed)


# ------------------------------------------------------------------------------------------------ app.py

def test_app_with_model_eval_validates():
    validate_app_code(_app(
        "from transformers import AutoModelForSequenceClassification\n"
        "model = AutoModelForSequenceClassification.from_pretrained(MODEL_ID, revision=REVISION)\n"
        "model.eval()\n"
        "return model"))


def test_app_with_attribute_and_chained_eval_validates():
    validate_app_code(_app(
        "from transformers import AutoModel\n"
        "def _warm(self):\n"
        "    self.model.eval()\n"
        "    return self\n"
        "model = AutoModel.from_pretrained(MODEL_ID, revision=REVISION).to('cpu').eval()\n"
        "return model"))


APP_BLOCKED = [
    ("eval('1')", "eval"),
    ("exec('1')", "exec"),
    ("compile('1', 'x', 'exec')", "compile"),
    ("__import__('os')", "__import__"),
    ("__builtins__.eval('1')", "eval"),
    ("__builtins__.eval()", "eval"),
    ("model = None\nmodel.eval('1')", "eval"),
    ("model = None\nmodel.eval(*[])", "eval"),
    ("model = None\nmodel.eval()\neval('1')", "eval"),  # a legitimate .eval() next to the builtin must not hide it
    ("os.system('x')", "system"),
    ("torch.load('p')", "load"),
]


@pytest.mark.parametrize("body,name", APP_BLOCKED)
def test_app_dangerous_calls_still_rejected(body, name):
    with pytest.raises(ValidationError, match=f"forbidden function '{name}'"):
        validate_app_code(_app(body))


# ------------------------------------------------------------------------------------------------ notebook cells

def _code(src):
    return {"cell_type": "code", "metadata": {}, "source": src.splitlines(keepends=True), "outputs": [], "execution_count": None}


def _notebook(cell_src="print('launch')\n", app=None):
    app = app or _app("model = None\nreturn model")
    return {
        "nbformat": 4, "nbformat_minor": 5, "metadata": {},
        "cells": [
            {"cell_type": "markdown", "metadata": {}, "source": ["# t"]},
            _code("import sys\nprint(sys.version)\n"),
            _code("%pip install -q gradio\n"),
            _code("%%writefile app.py\n" + app),
            _code(cell_src),
        ],
    }


def test_baseline_notebook_validates():
    validate_notebook(json.loads(json.dumps(_notebook())))


@pytest.mark.parametrize("cell_src", [
    "import torch\nmodel = torch.nn.Linear(1, 1)\nmodel.eval()\n",
    "def f(self):\n    self.model.eval()\n",
])
def test_notebook_cell_with_model_eval_validates(cell_src):
    validate_notebook(_notebook(cell_src))


def test_notebook_embedded_app_with_model_eval_validates():
    validate_notebook(_notebook(app=_app(
        "from transformers import AutoModel\n"
        "model = AutoModel.from_pretrained(MODEL_ID, revision=REVISION)\n"
        "model.eval()\n"
        "return model")))


@pytest.mark.parametrize("cell_src", [
    "eval('1')\n",
    "exec('1')\n",
    "compile('1', 'x', 'exec')\n",
    "__builtins__.eval('1')\n",
    "__builtins__.eval()\n",
    "import importlib\nimportlib.import_module('builtins').eval()\n",
    "import torch\nmodel = torch.nn.Linear(1, 1)\nmodel.eval('1')\n",
    "import torch\nmodel = torch.nn.Linear(1, 1)\nmodel.eval()\neval('1')\n",
])
def test_notebook_cell_dangerous_calls_still_rejected(cell_src):
    with pytest.raises(ValidationError, match="forbidden call"):
        validate_notebook(_notebook(cell_src))


@pytest.mark.parametrize("body,name", [("eval('1')", "eval"), ("exec('1')", "exec"), ("__builtins__.eval()", "eval")])
def test_notebook_embedded_app_dangerous_calls_still_rejected(body, name):
    with pytest.raises(ValidationError, match=f"forbidden function '{name}'"):
        validate_notebook(_notebook(app=_app(body)))
