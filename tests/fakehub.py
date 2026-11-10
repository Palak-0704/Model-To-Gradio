"""Offline test doubles: a fake HTTPS transport that serves canned Hugging Face / GitHub responses.

Parameter counts and config values in PRESETS are approximations of well-known public models, written by hand
for deterministic offline tests. They are NOT live data.
"""
import json

from m2g.config import Settings
from m2g.security import SafeFetcher, NetworkError

import fake_opencode

fake_opencode.install()  # every test that can reach code generation imports this module: it must never start the real OpenCode

PUBLIC = lambda host, port: ["93.184.216.34"]
SHA = "a" * 40


class FakeWeb:
    def __init__(self):
        self.routes = {}
        self.calls = []

    def add(self, url, body=b"", status=200, headers=None, error=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.routes[url] = error if error else (status, headers or {}, body)

    def __call__(self, parts, ips, headers, timeout, max_bytes):
        url = f"https://{parts.hostname}{parts.path}" + (f"?{parts.query}" if parts.query else "")
        self.calls.append(url)
        r = self.routes.get(url, (404, {}, b""))
        if isinstance(r, Exception):
            raise r
        return r

    def fetcher(self, **settings):
        return SafeFetcher(Settings(**settings), resolver=PUBLIC, transport=self)


def add_hf_model(web, repo_id, *, pipeline_tag=None, library_name="transformers", tags=(), files=None, config=None,
                 tokenizer_config=None, model_index=None, readme="# model card", safetensors_total=None, gated=False,
                 adapter_config=None, sha=SHA, license="apache-2.0"):
    files = files if files is not None else [("config.json", 800), ("model.safetensors", 300_000_000), ("README.md", 200)]
    names = {n for n, _ in files}
    meta = {"id": repo_id, "sha": sha, "pipeline_tag": pipeline_tag, "library_name": library_name,
            "tags": list(tags) + [f"license:{license}"], "gated": gated, "private": False, "downloads": 1000, "likes": 10,
            "cardData": {"license": license}, "siblings": [{"rfilename": n, "size": s} for n, s in files]}
    if safetensors_total:
        meta["safetensors"] = {"parameters": {"F32": safetensors_total}, "total": safetensors_total}
    web.add(f"https://huggingface.co/api/models/{repo_id}?blobs=true", meta)
    base = f"https://huggingface.co/{repo_id}/resolve/{sha}/"
    for fname, payload in (("config.json", config), ("tokenizer_config.json", tokenizer_config), ("model_index.json", model_index),
                           ("adapter_config.json", adapter_config)):
        if payload is not None and fname in names:
            web.add(base + fname, payload)
    if "README.md" in names:
        web.add(base + "README.md", readme)


W = lambda n, s: (n, s)
ST = [("config.json", 800), ("model.safetensors", 1), ("README.md", 100)]


def _files(size, extra=()):
    return [("config.json", 800), ("model.safetensors", size), ("README.md", 100), *extra]


PRESETS = {
    "sst2": dict(repo_id="distilbert/distilbert-base-uncased-finetuned-sst-2-english", pipeline_tag="text-classification",
                 config={"architectures": ["DistilBertForSequenceClassification"], "model_type": "distilbert",
                         "id2label": {"0": "NEGATIVE", "1": "POSITIVE"}}, safetensors_total=66_955_010,
                 files=_files(268_000_000)),
    "chat": dict(repo_id="Qwen/Qwen2.5-0.5B-Instruct", pipeline_tag="text-generation", tags=["chat", "conversational"],
                 config={"architectures": ["Qwen2ForCausalLM"], "model_type": "qwen2"},
                 tokenizer_config={"chat_template": "{% for m in messages %}{{ m.content }}{% endfor %}"},
                 safetensors_total=494_032_768, files=_files(988_000_000, [("tokenizer_config.json", 7000)])),
    "gpt2": dict(repo_id="openai-community/gpt2", pipeline_tag="text-generation",
                 config={"architectures": ["GPT2LMHeadModel"], "model_type": "gpt2"}, safetensors_total=137_022_720,
                 files=_files(548_000_000)),
    "ner": dict(repo_id="dslim/bert-base-NER", pipeline_tag="token-classification",
                config={"architectures": ["BertForTokenClassification"], "model_type": "bert"}, safetensors_total=108_312_579,
                files=_files(433_000_000)),
    "qa": dict(repo_id="distilbert/distilbert-base-cased-distilled-squad", pipeline_tag="question-answering",
               config={"architectures": ["DistilBertForQuestionAnswering"], "model_type": "distilbert"},
               safetensors_total=65_192_450, files=_files(261_000_000)),
    "mask": dict(repo_id="google-bert/bert-base-uncased", pipeline_tag="fill-mask",
                 config={"architectures": ["BertForMaskedLM"], "model_type": "bert"}, safetensors_total=110_106_428,
                 files=_files(440_000_000)),
    "zs": dict(repo_id="facebook/bart-large-mnli", pipeline_tag="zero-shot-classification",
               config={"architectures": ["BartForSequenceClassification"], "model_type": "bart"},
               safetensors_total=407_344_133, files=_files(1_630_000_000)),
    "sum": dict(repo_id="facebook/bart-large-cnn", pipeline_tag="summarization",
                config={"architectures": ["BartForConditionalGeneration"], "model_type": "bart"},
                safetensors_total=406_291_456, files=_files(1_630_000_000)),
    "t5": dict(repo_id="google-t5/t5-small", pipeline_tag="translation",
               config={"architectures": ["T5ForConditionalGeneration"], "model_type": "t5"}, safetensors_total=60_506_624,
               files=_files(242_000_000, [("spiece.model", 791_656)])),
    "minilm": dict(repo_id="sentence-transformers/all-MiniLM-L6-v2", pipeline_tag="sentence-similarity",
                   library_name="sentence-transformers", tags=["sentence-transformers"],
                   config={"architectures": ["BertModel"], "model_type": "bert"}, safetensors_total=22_713_216,
                   files=_files(91_000_000, [("modules.json", 300)])),
    "whisper": dict(repo_id="openai/whisper-tiny", pipeline_tag="automatic-speech-recognition",
                    config={"architectures": ["WhisperForConditionalGeneration"], "model_type": "whisper"},
                    safetensors_total=37_760_640, files=_files(151_000_000)),
    "audioclf": dict(repo_id="superb/hubert-base-superb-er", pipeline_tag="audio-classification",
                     config={"architectures": ["HubertForSequenceClassification"], "model_type": "hubert"},
                     safetensors_total=94_568_452, files=_files(378_000_000)),
    "tts": dict(repo_id="facebook/mms-tts-eng", pipeline_tag="text-to-speech",
                config={"architectures": ["VitsModel"], "model_type": "vits"}, safetensors_total=36_300_000,
                files=_files(145_000_000)),
    "vit": dict(repo_id="google/vit-base-patch16-224", pipeline_tag="image-classification",
                config={"architectures": ["ViTForImageClassification"], "model_type": "vit"}, safetensors_total=86_567_656,
                files=_files(346_000_000)),
    "detr": dict(repo_id="facebook/detr-resnet-50", pipeline_tag="object-detection",
                 config={"architectures": ["DetrForObjectDetection"], "model_type": "detr", "use_timm_backbone": True},
                 safetensors_total=41_522_240, files=_files(167_000_000)),
    "seg": dict(repo_id="nvidia/segformer-b0-finetuned-ade-512-512", pipeline_tag="image-segmentation",
                config={"architectures": ["SegformerForSemanticSegmentation"], "model_type": "segformer"},
                safetensors_total=3_751_000, files=_files(15_000_000)),
    "blip": dict(repo_id="Salesforce/blip-image-captioning-base", pipeline_tag="image-to-text",
                 config={"architectures": ["BlipForConditionalGeneration"], "model_type": "blip"},
                 safetensors_total=247_414_588, files=_files(990_000_000)),
    "vqa": dict(repo_id="dandelin/vilt-b32-finetuned-vqa", pipeline_tag="visual-question-answering",
                config={"architectures": ["ViltForQuestionAnswering"], "model_type": "vilt"}, safetensors_total=111_000_000,
                files=_files(470_000_000)),
    "sd": dict(repo_id="stable-diffusion-v1-5/stable-diffusion-v1-5", pipeline_tag="text-to-image", library_name="diffusers",
               model_index={"_class_name": "StableDiffusionPipeline", "_diffusers_version": "0.21.0"},
               config=None, files=[("model_index.json", 541), ("unet/diffusion_pytorch_model.safetensors", 3_440_000_000),
                                   ("vae/diffusion_pytorch_model.safetensors", 335_000_000),
                                   ("text_encoder/model.safetensors", 492_000_000), ("README.md", 100)]),
    "big7b": dict(repo_id="meta-llama/Llama-2-7b-chat-hf", pipeline_tag="text-generation", gated=True,
                  config={"architectures": ["LlamaForCausalLM"], "model_type": "llama"},
                  tokenizer_config={"chat_template": "{{ messages }}"}, safetensors_total=6_738_415_616,
                  files=_files(13_500_000_000, [("tokenizer_config.json", 800)])),
    "huge70b": dict(repo_id="acme/giant-70b", pipeline_tag="text-generation",
                    config={"architectures": ["LlamaForCausalLM"], "model_type": "llama"}, safetensors_total=70_000_000_000,
                    files=_files(140_000_000_000)),
    "remote": dict(repo_id="acme/custom-arch", pipeline_tag="text-generation",
                   config={"architectures": ["CustomForCausalLM"], "model_type": "custom",
                           "auto_map": {"AutoModelForCausalLM": "modeling_custom.CustomForCausalLM"}},
                   safetensors_total=1_000_000_000, files=_files(2_000_000_000, [("modeling_custom.py", 5000)])),
    "pickle": dict(repo_id="acme/old-model", pipeline_tag="text-classification",
                   config={"architectures": ["BertForSequenceClassification"], "model_type": "bert"},
                   files=[("config.json", 800), ("pytorch_model.bin", 440_000_000), ("README.md", 100)]),
    "gguf": dict(repo_id="acme/model-GGUF", pipeline_tag="text-generation", library_name="gguf", tags=["gguf"],
                 config=None, files=[("model-q4_k_m.gguf", 4_000_000_000), ("README.md", 100)]),
    "gptq": dict(repo_id="acme/model-GPTQ", pipeline_tag="text-generation",
                 config={"architectures": ["LlamaForCausalLM"], "model_type": "llama",
                         "quantization_config": {"quant_method": "gptq", "bits": 4}},
                 safetensors_total=1_100_000_000, files=_files(4_000_000_000)),
    "bnb4": dict(repo_id="acme/model-bnb-4bit", pipeline_tag="text-generation",
                 config={"architectures": ["LlamaForCausalLM"], "model_type": "llama",
                         "quantization_config": {"quant_method": "bitsandbytes", "load_in_4bit": True}},
                 safetensors_total=4_000_000_000, files=_files(5_000_000_000)),
    "lora": dict(repo_id="acme/my-lora", pipeline_tag="text-generation", library_name="peft",
                 adapter_config={"base_model_name_or_path": "meta-llama/Llama-2-7b-hf", "peft_type": "LORA"},
                 files=[("adapter_config.json", 500), ("adapter_model.safetensors", 20_000_000), ("README.md", 100)]),
    "notag": dict(repo_id="acme/seq-cls", pipeline_tag=None,
                  config={"architectures": ["RobertaForSequenceClassification"], "model_type": "roberta"},
                  safetensors_total=125_000_000, files=_files(500_000_000)),
    "conflict": dict(repo_id="acme/conflict", pipeline_tag="image-classification",
                     config={"architectures": ["BertForSequenceClassification"], "model_type": "bert"},
                     safetensors_total=110_000_000, files=_files(440_000_000)),
    "unknown": dict(repo_id="acme/mystery", pipeline_tag=None, library_name=None,
                    config={"model_type": "mystery"}, safetensors_total=1_000_000, files=_files(4_000_000)),
    "unsupported_task": dict(repo_id="acme/rl-agent", pipeline_tag="reinforcement-learning", library_name="stable-baselines3",
                             config=None, files=[("agent.zip", 1_000_000), ("README.md", 100)]),
}


def hf(web, key, **override):
    spec = dict(PRESETS[key])
    spec.update(override)
    add_hf_model(web, spec.pop("repo_id"), **spec)
    return PRESETS[key]["repo_id"] if "repo_id" in PRESETS[key] else None


def add_github_repo(web, repo_id, *, readme="# Cool AI\nTorch model.", requirements="torch>=2.0\ntransformers\n", tree=None,
                    api_status=200, branch="main", description="A repo", topics=("deep-learning",), extra_files=None):
    api = f"https://api.github.com/repos/{repo_id}"
    if api_status != 200:
        web.add(api, status=api_status)
        base = "HEAD"
    else:
        web.add(api, {"default_branch": branch, "description": description, "topics": list(topics), "language": "Python",
                      "license": {"spdx_id": "MIT"}, "private": False, "archived": False})
        base = branch
        tree = tree if tree is not None else ["README.md", "requirements.txt", "main.py"]
        entries = [e if isinstance(e, tuple) else (e, 100) for e in tree]
        web.add(f"{api}/git/trees/{branch}?recursive=1",
                {"truncated": False, "tree": [{"path": p, "type": "blob", "size": s} for p, s in entries]})
    raw = f"https://raw.githubusercontent.com/{repo_id}/{base}/"
    web.add(raw + "README.md", readme)
    if requirements is not None:
        web.add(raw + "requirements.txt", requirements)
    for name, body in (extra_files or {}).items():
        web.add(raw + name, body)
    return raw
