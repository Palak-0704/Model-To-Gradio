"""Capability model for dynamic, model-specific Gradio generation.

A *capability* is something the evidence says a model can do (a Hugging Face task name or, when the task is unknown, a modality
description). Capabilities are evidence-based facts handed to OpenCode, which designs the interface itself. Nothing in this
module GATES generation: ``TASK_IO`` (analyzer.py) is only a reusable reference of component names, and a task that has no
reference entry is still a valid capability. Unknown does not mean supported: every capability carries its confidence, its
strongest evidence tier and, where relevant, an advisory.

This module imports nothing from the rest of the package so that analyzer.py, gradio_gen.py and the tests can all use it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

# Tasks Hugging Face itself uses as pipeline tags/tags. Used ONLY to recognise a tag as a task hint; never as an allow-list.
HF_TASK_VOCAB = frozenset({
    "text-generation", "text2text-generation", "summarization", "translation", "text-classification", "token-classification",
    "question-answering", "fill-mask", "zero-shot-classification", "feature-extraction", "sentence-similarity",
    "image-classification", "zero-shot-image-classification", "object-detection", "image-segmentation", "image-to-text",
    "visual-question-answering", "automatic-speech-recognition", "audio-classification", "text-to-speech", "text-to-audio",
    "text-to-image", "image-text-to-text", "text-to-video", "image-to-image", "depth-estimation", "document-question-answering",
    "table-question-answering", "multiple-choice", "reinforcement-learning", "zero-shot-object-detection", "any-to-any",
    "video-classification", "image-to-video", "image-feature-extraction", "mask-generation", "keypoint-detection",
    "unconditional-image-generation", "audio-to-audio", "voice-activity-detection", "text-ranking", "video-text-to-text",
    "visual-document-retrieval", "text-to-3d", "image-to-3d", "tabular-classification", "tabular-regression",
    "time-series-forecasting", "robotics", "graph-ml", "text-retrieval", "image-text-to-image",
})

TASK_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_.:+-]{0,63}$")
MAX_INTERFACES = 4  # most interfaces/tabs one generated app is asked to build
CO_SUPPORT_MIN_SCORE = 0.6  # minimum evidence score for a second capability to get its own interface
MODALITY_ONLY_CONFIDENCE = 0.35  # a capability known only from modalities is never more certain than this
CHAT_TASKS = frozenset({"text-generation", "image-text-to-text"})

# Notes for tasks the platform has no verified reference interface for. These are ADVISORIES, never refusal reasons.
TASK_ADVISORIES: Dict[str, str] = {
    "image-text-to-text": "vision-language chat needs the model's own processor and prompt format; OpenCode designs it from the evidence",
    "text-to-video": "video generation is heavy (GPU, long runs); the interface is designed from the evidence and unverified",
    "image-to-image": "image-to-image/inpainting/ControlNet pipelines need extra inputs; the interface is designed from the evidence",
    "depth-estimation": "no reference output rendering exists; OpenCode designs how the depth map is shown",
    "document-question-answering": "document QA may need OCR dependencies that are not on the allow-list; verify before relying on it",
    "table-question-answering": "no standard reference interface; OpenCode designs the table input from the evidence",
    "multiple-choice": "no standard pipeline exists; OpenCode designs the input handling from the evidence",
    "reinforcement-learning": "reinforcement-learning agents are usually not transformers/diffusers models; loading may not be possible",
    "zero-shot-object-detection": "no reference interface; OpenCode designs the candidate-label input from the evidence",
    "any-to-any": "multi-modal any-to-any models vary widely; the interface is designed from the evidence and unverified",
}

# Advisory for a capability that is a task-conditioned MODE of the primary model (for example selected by a generation task token):
# it is a way of driving the same model, so it consumes the same input as the primary capability.
MODE_ADVISORY = ("this capability is a task-conditioned mode of the primary model (selected through a generation task token); it uses "
                 "the same input as the primary capability, and OpenCode designs how the mode is selected from the evidence")

EXTRA_TASK_MODALITIES: Dict[str, Tuple[Tuple[str, ...], Tuple[str, ...]]] = {
    "video-classification": (("video",), ("text",)), "image-to-video": (("image",), ("video",)),
    "image-feature-extraction": (("image",), ("text",)), "mask-generation": (("image",), ("image",)),
    "unconditional-image-generation": ((), ("image",)), "audio-to-audio": (("audio",), ("audio",)),
    "text-to-3d": (("text",), ()), "image-to-3d": (("image",), ()), "video-text-to-text": (("video", "text"), ("text",)),
    "zero-shot-object-detection": (("image", "text"), ("image",)), "text-ranking": (("text",), ("text",)),
    "text-retrieval": (("text",), ("text",)), "image-text-to-image": (("image", "text"), ("image",)),
}

_COMPONENT_FOR_MODALITY = {"text": "gr.Textbox", "image": "gr.Image", "audio": "gr.Audio", "video": "gr.Video"}


def valid_task_name(name: Any) -> bool:
    return isinstance(name, str) and bool(TASK_NAME_RE.match(name))


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(name).lower()).strip("_") or "capability"


def is_inference_function_name(name: str) -> bool:
    """predict / chat_fn, or predict_<x> / chat_fn_<x> for an app with one interface per capability."""
    return name in ("predict", "chat_fn") or name.startswith(("predict_", "chat_fn_"))


def suggested_components(modalities: Iterable[str]) -> List[str]:
    return [_COMPONENT_FOR_MODALITY[m] for m in modalities if m in _COMPONENT_FOR_MODALITY]


@dataclass
class Capability:
    name: str
    confidence: float = 0.0
    tier: str = "heuristic"  # strongest evidence tier: authoritative | structural | heuristic
    role: str = "primary"  # primary | co_supported (own interface) | conflicting | hint (never built)
    basis: str = "task"  # task | modality
    evidence: List[Dict[str, Any]] = field(default_factory=list)
    input_modalities: List[str] = field(default_factory=list)
    output_modalities: List[str] = field(default_factory=list)
    known_reference: bool = False  # a reusable TASK_IO reference exists; informational only
    aliases: List[str] = field(default_factory=list)  # equivalent task names merged into this capability
    chat: bool = False
    advisory: Optional[str] = None
    # Detection status: "supported" (firm, non-heuristic evidence for a recognised task) or "uncertain" (weak/heuristic-only evidence,
    # unknown task name, modality-only, or contradicted). ``status_reason`` says why. Each ``evidence`` entry carries its tier, signal,
    # source file/API, detail, weight and the task it supports.
    status: str = "uncertain"
    status_reason: str = ""
    # Certainty of the detection (three-way, derived from the evidence only; see certainty_for): "confirmed" (explicitly declared by
    # model metadata), "inferred" (supported by indirect evidence such as the architecture) or "uncertain" (insufficient or
    # conflicting evidence). It is additive: ``status`` keeps its meaning and is what decides which interfaces are built.
    certainty: str = "uncertain"
    certainty_reason: str = ""
    # Input/output requirements the repository metadata itself states for this capability, each as {"value", "source"}. A requirement
    # the metadata does not state is absent, never assumed.
    requirements: Dict[str, Any] = field(default_factory=dict)

    @property
    def builds_interface(self) -> bool:
        return self.role in ("primary", "co_supported")


_TIER_RANK = {"authoritative": 0, "structural": 1, "heuristic": 2}


def _ev_dict(e: Any) -> Dict[str, Any]:
    return {"tier": e.tier, "signal": e.signal, "source": e.source, "detail": e.detail, "weight": e.weight,
            "task": getattr(e, "task", "")}


SUPPORT_MIN_CONFIDENCE = 0.6  # a capability needs at least this confidence AND non-heuristic evidence to be "supported"


def detection_status(name: str, confidence: float, tier: str, role: str, basis: str, recognised: bool) -> Tuple[str, str]:
    """(status, reason) for one capability. Tags/keywords alone, unknown task names, modality-only and contradicted capabilities
    stay "uncertain"; model names and README text are never evidence at all."""
    if basis != "task":
        return "uncertain", "only input/output modalities are known; the task itself is not established"
    if role == "conflicting":
        return "uncertain", "contradicted by the primary capability's evidence"
    if tier == "heuristic":
        return "uncertain", "only heuristic evidence (tags/keywords); a weak signal alone does not prove a capability"
    if not recognised:
        return "uncertain", f"'{name}' is not a recognised task name, so it is not treated as established"
    if role == "hint" and confidence >= SUPPORT_MIN_CONFIDENCE:
        return "uncertain", "kept as a hint only (a generic architecture-head mapping that does not override the primary capability's explicit declaration, or the interface limit was reached); not built"
    if role == "hint" or confidence < SUPPORT_MIN_CONFIDENCE:
        return "uncertain", f"evidence is too weak (confidence {round(confidence, 2)} < {SUPPORT_MIN_CONFIDENCE})"
    return "supported", f"backed by {tier} evidence (confidence {round(confidence, 2)})"


# Signals that DECLARE a task outright (the Hub pipeline_tag, a diffusers pipeline class). Everything else, however firm, is an
# inference from how the model is built (architecture head, config fields, library) and is never reported as confirmed.
CONFIRMING_SIGNALS = frozenset({"pipeline_tag", "diffusers_pipeline_class"})
# Signal name for architecture evidence that comes from a suffix shared by several modalities (ForSequenceClassification exists for text,
# audio and vision models alike). It is still structural evidence, but a generic mapping alone is not a contradiction of an explicit task.
GENERIC_ARCH_SIGNAL = "architecture_generic"


def certainty_for(status: str, evidence: List[Dict[str, Any]], status_reason: str = "") -> Tuple[str, str]:
    """(certainty, reason): confirmed | inferred | uncertain.
    uncertain  the detection status is not "supported" (weak/heuristic-only, unknown task name, modality-only or contradicted evidence)
    confirmed  supported AND at least one authoritative entry that explicitly declares the task (CONFIRMING_SIGNALS)
    inferred   supported, but only by indirect evidence (architecture, config fields, library, model card results)"""
    if status != "supported":
        return "uncertain", status_reason or "the evidence is insufficient or conflicting"
    declared = sorted({str(e.get("signal")) for e in evidence if e.get("tier") == "authoritative" and e.get("signal") in CONFIRMING_SIGNALS})
    if declared:
        return "confirmed", "explicitly declared by " + ", ".join(declared)
    signals = sorted({str(e.get("signal")) for e in evidence if e.get("tier") != "heuristic"})
    if not signals:  # e.g. a hand-built/legacy analysis that records a task but none of the evidence behind it
        return "uncertain", "no supporting evidence entries are recorded for this capability"
    return "inferred", "derived from " + ", ".join(signals) + "; no explicit task declaration"


def _weak_mapping_only(es: List[Any], primary: "Capability") -> bool:
    """True when the competing task rests only on generic architecture-suffix evidence AND the primary capability is explicitly declared
    (Hub pipeline_tag / diffusers class). Such a competitor is a weak mapping, not contradictory evidence."""
    firm = [e for e in es if e.tier != "heuristic"]
    declared = any(e.get("tier") == "authoritative" and e.get("signal") in CONFIRMING_SIGNALS for e in primary.evidence)
    return declared and bool(firm) and all(e.signal == GENERIC_ARCH_SIGNAL for e in firm)


_REQ_SCALARS = {"image": ("image_size", "num_channels", "patch_size"), "audio": ("sampling_rate", "num_mel_bins")}
_REQ_SUBCONFIGS = {"image": ("vision_config",), "audio": ("audio_config", "speech_config")}


def _req_value(v: Any) -> Any:
    """A plain number, or a short list of integers (e.g. image_size [h, w]); anything else is not copied."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, (list, tuple)) and 0 < len(v) <= 4 and all(isinstance(x, int) and not isinstance(x, bool) for x in v):
        return list(v)
    return None


def capability_requirements(c: Capability, cfg: Optional[Dict[str, Any]], tokenizer_config: Optional[Dict[str, Any]] = None,
                            preprocessor_config: Optional[Dict[str, Any]] = None
                            ) -> Dict[str, Any]:
    """Input/output requirements the repository's own config files state for this capability, as {key: {"value", "source"}}.
    Only values really present in config.json (and its vision/audio sub-configs), tokenizer_config.json and preprocessor_config.json are
    reported; config.json takes precedence, and one requirement has ONE key (the image size is always ``image_image_size``, whichever
    file states it; its source says which). They are chosen by the capability's evidence-derived
    modalities; nothing is assumed, so a requirement the metadata does not state is simply absent."""
    cfg = cfg if isinstance(cfg, dict) else {}
    tok = tokenizer_config if isinstance(tokenizer_config, dict) else {}
    pre = preprocessor_config if isinstance(preprocessor_config, dict) else {}
    req: Dict[str, Any] = {}

    def put(key: str, value: Any, source: str) -> None:
        if value is not None and key not in req:
            req[key] = {"value": value, "source": source}

    for modality, keys in _REQ_SCALARS.items():
        if modality not in c.input_modalities:
            continue
        scopes = [("config.json", cfg)] + [(f"config.json {n}", cfg[n]) for n in _REQ_SUBCONFIGS[modality] if isinstance(cfg.get(n), dict)]
        for key in keys:
            for source, scope in scopes:
                v = _req_value(scope.get(key))
                if v is not None:
                    put(f"{modality}_{key}", v, source)
                    break
    # preprocessor_config.json states what the feature extractor / image processor itself expects. config.json is read first and put()
    # keeps the first value, so this only fills what config.json does not state (e.g. Whisper's sampling_rate lives only here). The image
    # size uses the same key as the config.json one, so it is never reported twice; shortest_edge is a different requirement.
    if pre:
        if "audio" in c.input_modalities:
            put("audio_sampling_rate", _req_value(pre.get("sampling_rate")), "preprocessor_config.json")
            put("audio_feature_size", _req_value(pre.get("feature_size")), "preprocessor_config.json")
        if "image" in c.input_modalities:
            size = pre.get("size")
            if isinstance(size, dict):
                hw = [size[k] for k in ("height", "width") if k in size]
                put("image_image_size", _req_value(hw), "preprocessor_config.json size")
                put("image_shortest_edge", _req_value(size.get("shortest_edge")), "preprocessor_config.json size")
            else:
                put("image_image_size", _req_value(size), "preprocessor_config.json size")
    if "text" in c.input_modalities:
        n = tok.get("model_max_length")
        if isinstance(n, int) and not isinstance(n, bool) and 0 < n < 10 ** 9:
            put("max_input_tokens", n, "tokenizer_config.json model_max_length")
        else:
            put("max_input_tokens", _req_value(cfg.get("max_position_embeddings")), "config.json max_position_embeddings")
    if c.chat and tok.get("chat_template"):
        put("chat_template_present", True, "tokenizer_config.json chat_template")
    id2label = cfg.get("id2label")
    if c.name.endswith("classification") and isinstance(id2label, dict) and id2label:
        put("label_count", len(id2label), "config.json id2label")
        if not all(re.fullmatch(r"LABEL_\d+", str(v)) for v in id2label.values()):  # default placeholder names say nothing
            put("label_sample", [_s(v, 40) for v in list(id2label.values())[:12] if isinstance(v, str)], "config.json id2label")
    return req


def _advisory(name: str, known: bool, basis: str) -> Optional[str]:
    if basis == "modality":
        return ("The task could not be identified; only input/output modalities are known. A minimal, clearly labelled interface "
                "is designed and the task is NOT assumed.")
    if name in TASK_ADVISORIES:
        return TASK_ADVISORIES[name]
    if not known:
        return "No reference interface exists for this task; OpenCode designs one from the evidence (unverified)."
    return None


def build_capabilities(evidence: List[Any], primary: Optional[str], primary_conf: float, *, equiv: Any,
                       modalities: Callable[[Optional[str]], Tuple[List[str], List[str]]], reference_tasks: Any,
                       chat: bool = False, fallback: Optional[Tuple[List[str], List[str], List[str]]] = None,
                       max_interfaces: int = MAX_INTERFACES) -> List[Capability]:
    """Group task evidence by task and decide each capability's role.

    primary          the top task (always the first capability)
    co_supported     non-heuristic evidence >= CO_SUPPORT_MIN_SCORE and overlapping input modalities with the primary
    conflicting      equally firm evidence for a task with a disjoint/unknown input modality: reported, never built
    hint             heuristic-only or weak evidence: reported, never built
    Equivalent tasks (``equiv`` pairs) are merged into one capability."""
    by: Dict[str, List[Any]] = {}
    for e in evidence:
        t = getattr(e, "task", "")
        if t and valid_task_name(t):
            by.setdefault(t, []).append(e)
    scores = {t: sum(float(e.weight) for e in es) for t, es in by.items()}
    order = sorted(by, key=lambda t: (t != primary, -scores[t], t))
    caps: List[Capability] = []
    for t in order:
        es = by[t]
        host = next((c for c in caps if c.builds_interface and frozenset((c.name, t)) in equiv), None)
        if host is not None:
            host.aliases.append(t)
            host.evidence += [_ev_dict(e) for e in es]
            continue
        strong = any(e.tier != "heuristic" for e in es)
        tier = min((e.tier for e in es), key=lambda x: _TIER_RANK.get(x, 3))
        ins, outs = modalities(t)
        # Evidence may carry its own (input, output) modalities when the capability is a mode of the primary model: the task table
        # describes the standalone task (translation = text -> text), but a mode of an audio model takes audio.
        inherited = next((e.modalities for e in es if getattr(e, "modalities", None)), None)
        if inherited:
            ins, outs = list(inherited[0]), list(inherited[1])
        if not caps:
            role, conf = "primary", float(primary_conf)
        else:
            conf = round(min(1.0, scores[t]), 2)
            first_in = modalities(caps[0].name)[0]
            if not strong or scores[t] < CO_SUPPORT_MIN_SCORE:
                role = "hint"
            elif first_in and ins and set(first_in) & set(ins):
                role = "co_supported"
            else:
                role = "hint" if _weak_mapping_only(es, caps[0]) else "conflicting"
            if role == "co_supported" and sum(c.builds_interface for c in caps) >= max_interfaces:
                role = "hint"
        known = t in reference_tasks
        status, why = detection_status(t, conf, tier, role, "task", known or t in HF_TASK_VOCAB)
        caps.append(Capability(status=status, status_reason=why, 
            name=t, confidence=round(conf, 2), tier=tier, role=role, basis="task", evidence=[_ev_dict(e) for e in es][:8],
            input_modalities=list(ins), output_modalities=list(outs), known_reference=known,
            chat=bool(chat and t in CHAT_TASKS and role in ("primary", "co_supported")),
            advisory=(_advisory(t, known, "task") or (MODE_ADVISORY if inherited else None))
            if role in ("primary", "co_supported") else None))
    if not caps and fallback and fallback[0]:
        ins, outs, hints = fallback
        caps.append(Capability(
            name="unclassified-" + "-".join(ins) + "-input", confidence=MODALITY_ONLY_CONFIDENCE, tier="structural", role="primary",
            basis="modality", input_modalities=list(ins), output_modalities=list(outs),
            evidence=[{"tier": "structural", "signal": "config_modality", "source": "config.json / repository files", "detail": h,
                       "weight": 0.1, "task": ""} for h in hints[:4]], advisory=_advisory("", False, "modality"),
            status="uncertain", status_reason=detection_status("", 0.0, "structural", "primary", "modality", False)[1]))
    for c in caps:  # after alias merging, so a capability's certainty reads ALL of its evidence
        c.certainty, c.certainty_reason = certainty_for(c.status, c.evidence, c.status_reason)
    conflicting = [c.name for c in caps if c.role == "conflicting"]
    if conflicting and caps and caps[0].certainty == "confirmed":  # firm evidence for an incompatible task: no longer unqualified
        caps[0].certainty = "inferred"
        caps[0].certainty_reason += "; downgraded because equally firm evidence points to an incompatible task (" + ", ".join(conflicting[:3]) + ")"
    return caps


# --------------------------------------------------------------------------- config / file based modality evidence


def config_modalities(cfg: Optional[Dict[str, Any]], file_names: Iterable[str], model_index: Optional[Dict[str, Any]]
                      ) -> Tuple[List[str], List[str], List[str]]:
    """Input/output modalities from config.json keys, repository file names and diffusers components. Metadata only.
    Returns (inputs, outputs, evidence sentences)."""
    cfg = cfg if isinstance(cfg, dict) else {}
    base = {str(n).lower().rsplit("/", 1)[-1] for n in file_names}
    ins: List[str] = []
    outs: List[str] = []
    hints: List[str] = []

    def add(lst: List[str], m: str, why: str) -> None:
        if m not in lst:
            lst.append(m)
            hints.append(why)

    if isinstance(cfg.get("vision_config"), dict) or "image_size" in cfg or ("patch_size" in cfg and "num_channels" in cfg):
        add(ins, "image", "config.json has vision settings (vision_config / image_size / patch_size)")
    if isinstance(cfg.get("audio_config"), dict) or isinstance(cfg.get("speech_config"), dict) or "num_mel_bins" in cfg \
            or "sampling_rate" in cfg:
        add(ins, "audio", "config.json has audio settings (audio_config / num_mel_bins / sampling_rate)")
    if base & {"tokenizer.json", "vocab.txt", "vocab.json", "merges.txt", "spiece.model", "tokenizer.model",
               "sentencepiece.bpe.model"} or isinstance(cfg.get("text_config"), dict) or "vocab_size" in cfg:
        add(ins, "text", "tokenizer files / text_config / vocab_size indicate text input")
    comps = {k for k in (model_index or {}) if isinstance(k, str) and not k.startswith("_")}
    if comps:
        if comps & {"unet", "transformer", "prior"} and "vae" in comps:
            add(outs, "image", "diffusers components (unet/transformer + vae) produce images")
        if any(c.startswith("text_encoder") for c in comps):
            add(ins, "text", "diffusers text_encoder component indicates text input")
        if comps & {"image_encoder"}:
            add(ins, "image", "diffusers image_encoder component indicates image input")
        if comps & {"vocoder", "audio_encoder"}:
            add(outs, "audio", "diffusers vocoder/audio component produces audio")
    return ins, outs, hints


# --------------------------------------------------------------------------- model evidence for the generator

_CFG_SCALARS = ("model_type", "is_encoder_decoder", "vocab_size", "hidden_size", "num_hidden_layers", "num_attention_heads",
                "max_position_embeddings", "n_positions", "image_size", "num_channels", "patch_size", "num_labels", "problem_type",
                "sampling_rate", "num_mel_bins", "torch_dtype", "dtype", "tie_word_embeddings", "max_length", "d_model")
_SUBCONFIGS = ("text_config", "vision_config", "audio_config", "speech_config", "encoder", "decoder")
_DIFFUSERS_DIRS = {"unet", "vae", "transformer", "text_encoder", "text_encoder_2", "tokenizer", "scheduler", "vocoder",
                   "image_encoder", "controlnet", "safety_checker", "feature_extractor"}


def _s(value: Any, limit: int = 80) -> str:
    return re.sub(r"[\x00-\x1f\x7f]+", " ", str(value))[:limit]


def build_model_evidence(cfg: Optional[Dict[str, Any]], tokenizer_config: Optional[Dict[str, Any]],
                         model_index: Optional[Dict[str, Any]], file_names: Iterable[str], tags: Iterable[str],
                         readme: str = "", generation_config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """A bounded, whitelisted digest of what the repository metadata says about how to load and drive the model. Only simple
    values are copied; every string is shortened and stripped of control characters. Nothing is fetched or executed here."""
    cfg = cfg if isinstance(cfg, dict) else {}
    tok = tokenizer_config if isinstance(tokenizer_config, dict) else {}
    names = [str(n).lower() for n in file_names]
    base = {n.rsplit("/", 1)[-1] for n in names}
    out: Dict[str, Any] = {}
    config: Dict[str, Any] = {}
    for key in _CFG_SCALARS:
        v = cfg.get(key)
        if isinstance(v, (bool, int, float)) or (isinstance(v, str) and v):
            config[key] = v if not isinstance(v, str) else _s(v)
    archs = [_s(a, 60) for a in (cfg.get("architectures") or []) if isinstance(a, str)][:3]
    if archs:
        config["architectures"] = archs
    id2label = cfg.get("id2label")
    if isinstance(id2label, dict) and id2label:
        labels = [_s(v, 40) for v in list(id2label.values())[:12] if isinstance(v, str)]
        config["label_count"] = len(id2label)
        config["label_sample"] = labels
    if cfg.get("auto_map"):
        config["has_auto_map"] = True  # custom code is referenced; trust_remote_code is a platform decision, not a design choice
    subs = {k: _s((cfg[k] or {}).get("model_type", "?"), 40) for k in _SUBCONFIGS if isinstance(cfg.get(k), dict)}
    if subs:
        config["sub_configs"] = subs
    out["config"] = config
    template = tok.get("chat_template")
    out["tokenizer"] = {
        "tokenizer_class": _s(tok.get("tokenizer_class", ""), 60) or None,
        "model_max_length": tok.get("model_max_length") if isinstance(tok.get("model_max_length"), int)
        and 0 < tok.get("model_max_length") < 10 ** 9 else None,
        "chat_template_present": bool(template) or bool(base & {"chat_template.jinja", "chat_template.json"}),
        "chat_template_mentions_system": isinstance(template, str) and "system" in template.lower(),
        "has_bos_token": bool(tok.get("bos_token")), "has_eos_token": bool(tok.get("eos_token")),
    }
    out["files"] = {
        "tokenizer": bool(base & {"tokenizer.json", "vocab.txt", "vocab.json", "merges.txt", "spiece.model", "tokenizer.model"}),
        "preprocessor_config": "preprocessor_config.json" in base, "processor_config": "processor_config.json" in base,
        "generation_config": "generation_config.json" in base, "sharded_safetensors": any(n.endswith(".safetensors.index.json") for n in names),
        "diffusers_folders": sorted({n.split("/")[0] for n in names if "/" in n and n.split("/")[0] in _DIFFUSERS_DIRS}),
    }
    if isinstance(model_index, dict):
        comps: Dict[str, Any] = {}
        for k, v in list(model_index.items())[:30]:
            if isinstance(k, str) and not k.startswith("_") and isinstance(v, (list, tuple)):
                comps[_s(k, 40)] = [_s(x, 60) for x in v[:2]]
        out["diffusers"] = {"pipeline_class": _s(model_index.get("_class_name", ""), 80), "components": comps}
    gen = generation_config if isinstance(generation_config, dict) else {}
    task_tokens = gen.get("task_to_id")
    if isinstance(task_tokens, dict) and task_tokens:  # the task-conditioning tokens the model was configured with (keys only)
        out["generation"] = {"task_tokens": [_s(k, 40) for k in list(task_tokens)[:16]],
                             "is_multilingual": gen.get("is_multilingual") if isinstance(gen.get("is_multilingual"), bool) else None}
    out["tags"] = [_s(t, 40) for t in list(tags)[:25] if isinstance(t, str)]
    out["model_card_excerpt"] = _s(readme, 1800) if isinstance(readme, str) and readme else ""
    return out
