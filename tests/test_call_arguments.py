"""Regression tests: duplicate keyword arguments (notably `revision`) in generated loader code are rejected statically."""
import pytest

from m2g.validate import ValidationError, validate_app_code

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


def test_clean_loader_passes():
    validate_app_code(_app(
        "from transformers import pipeline\n"
        "kwargs = dict(model=MODEL_ID, revision=REVISION, token=HF_TOKEN)\n"
        "return pipeline(TASK, model_kwargs={'use_safetensors': USE_SAFETENSORS}, **kwargs)"))


def test_revision_explicit_and_in_dict_splat_rejected():
    with pytest.raises(ValidationError, match="revision"):
        validate_app_code(_app(
            "from transformers import pipeline\n"
            "kwargs = dict(model=MODEL_ID, revision=REVISION)\n"
            "return pipeline(TASK, revision=REVISION, **kwargs)"))


def test_revision_added_by_subscript_then_splat_rejected():
    with pytest.raises(ValidationError, match="revision"):
        validate_app_code(_app(
            "from transformers import pipeline\n"
            "kwargs = {'model': MODEL_ID}\n"
            "kwargs['revision'] = REVISION\n"
            "return pipeline(TASK, revision=REVISION, **kwargs)"))


def test_revision_direct_and_in_model_kwargs_rejected():
    with pytest.raises(ValidationError, match="model_kwargs"):
        validate_app_code(_app(
            "from transformers import pipeline\n"
            "return pipeline(TASK, model=MODEL_ID, revision=REVISION, model_kwargs={'revision': REVISION})"))


def test_revision_splat_plus_model_kwargs_rejected():
    with pytest.raises(ValidationError, match="model_kwargs"):
        validate_app_code(_app(
            "from transformers import pipeline\n"
            "kw = dict(model=MODEL_ID, revision=REVISION)\n"
            "mk = {'revision': REVISION}\n"
            "return pipeline(TASK, model_kwargs=mk, **kw)"))


def test_two_splats_with_same_key_rejected():
    with pytest.raises(ValidationError, match="revision"):
        validate_app_code(_app(
            "from transformers import pipeline\n"
            "a = dict(revision=REVISION)\n"
            "b = dict(revision=REVISION)\n"
            "return pipeline(TASK, **a, **b)"))


def test_unresolved_splat_with_revision_rejected():
    with pytest.raises(ValidationError, match="unresolvable"):
        validate_app_code(_app(
            "from transformers import pipeline\n"
            "return pipeline(TASK, revision=REVISION, **os.environ)"))


def test_unresolved_splat_without_guarded_keyword_allowed():
    validate_app_code(_app(
        "from transformers import pipeline\n"
        "extra = dict(os.environ)\n"
        "return pipeline(TASK, model=MODEL_ID, **extra)"))
