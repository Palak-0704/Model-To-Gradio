"""Evidence-based analysis: task, inputs/outputs, dependencies, hardware needs, support status.

Every conclusion carries the evidence it came from. Nothing here executes repository code; the analyzer only
reads the metadata structures produced by m2g.inspector.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .config import Settings
from .capabilities import (
    EXTRA_TASK_MODALITIES, HF_TASK_VOCAB, TASK_ADVISORIES, Capability, build_capabilities, build_model_evidence, capability_requirements,
    CONFIRMING_SIGNALS, GENERIC_ARCH_SIGNAL, certainty_for, config_modalities, detection_status, valid_task_name,
)
from .inspector import PICKLE_EXTS, WEIGHT_EXTS, RepoInfo
from .inference_contract import build_contract, select_loader
from .security import SecurityError, SourceRef, redact_secrets, validate_pip_requirement, validate_repo_id

# --------------------------------------------------------------------------- task tables

# task -> inputs / outputs (Gradio component class names): a REUSABLE REFERENCE of component names. It does not decide which tasks
# or capabilities a model may have; a task without an entry here is still a valid capability (see m2g/capabilities.py).
TASK_IO: Dict[str, Dict[str, List[Tuple[str, str]]]] = {
    "text-generation": {"in": [("prompt", "Textbox"), ("max_new_tokens", "Slider"), ("temperature", "Slider"), ("top_p", "Slider")],
                        "out": [("generated_text", "Textbox")]},
    "text-generation:chat": {"in": [("message", "ChatInterface"), ("max_new_tokens", "Slider"), ("temperature", "Slider")],
                             "out": [("reply", "ChatInterface")]},
    "text2text-generation": {"in": [("text", "Textbox"), ("max_new_tokens", "Slider")], "out": [("generated_text", "Textbox")]},
    "summarization": {"in": [("text", "Textbox"), ("max_new_tokens", "Slider")], "out": [("summary", "Textbox")]},
    "translation": {"in": [("text", "Textbox"), ("src_lang", "Textbox"), ("tgt_lang", "Textbox"), ("max_new_tokens", "Slider")],
                    "out": [("translation", "Textbox")]},
    "text-classification": {"in": [("text", "Textbox")], "out": [("labels", "Label")]},
    "token-classification": {"in": [("text", "Textbox")], "out": [("entities", "HighlightedText")]},
    "question-answering": {"in": [("question", "Textbox"), ("context", "Textbox")], "out": [("answer", "Textbox"), ("score", "Number")]},
    "fill-mask": {"in": [("text", "Textbox")], "out": [("predictions", "Label")]},
    "zero-shot-classification": {"in": [("text", "Textbox"), ("candidate_labels", "Textbox"), ("multi_label", "Checkbox")],
                                 "out": [("labels", "Label")]},
    "feature-extraction": {"in": [("text_a", "Textbox"), ("text_b", "Textbox")], "out": [("cosine_similarity", "Number")]},
    "sentence-similarity": {"in": [("text_a", "Textbox"), ("text_b", "Textbox")], "out": [("cosine_similarity", "Number")]},
    "image-classification": {"in": [("image", "Image")], "out": [("labels", "Label")]},
    "zero-shot-image-classification": {"in": [("image", "Image"), ("candidate_labels", "Textbox")], "out": [("labels", "Label")]},
    "object-detection": {"in": [("image", "Image"), ("threshold", "Slider")], "out": [("detections", "AnnotatedImage")]},
    "image-segmentation": {"in": [("image", "Image")], "out": [("segments", "AnnotatedImage")]},
    "image-to-text": {"in": [("image", "Image")], "out": [("caption", "Textbox")]},
    "visual-question-answering": {"in": [("image", "Image"), ("question", "Textbox")], "out": [("answers", "Label")]},
    "automatic-speech-recognition": {"in": [("audio", "Audio")], "out": [("transcript", "Textbox")]},
    "audio-classification": {"in": [("audio", "Audio")], "out": [("labels", "Label")]},
    "text-to-speech": {"in": [("text", "Textbox")], "out": [("audio", "Audio")]},
    "text-to-audio": {"in": [("text", "Textbox")], "out": [("audio", "Audio")]},
    "text-to-image": {"in": [("prompt", "Textbox"), ("negative_prompt", "Textbox"), ("steps", "Slider"), ("guidance_scale", "Slider"),
                             ("seed", "Number")], "out": [("image", "Image")]},
}
SUPPORTED_TASKS = sorted(t for t in TASK_IO if ":" not in t)
DIFFUSERS_TASKS = {"text-to-image"}
IMAGE_TASKS = {"image-classification", "zero-shot-image-classification", "object-detection", "image-segmentation", "image-to-text",
               "visual-question-answering", "text-to-image"}
AUDIO_TASKS = {"automatic-speech-recognition", "audio-classification", "text-to-speech", "text-to-audio"}

# Formerly a refusal list. These tasks are now ADVISORIES only (a warning, never a refusal reason); the name is kept for imports.
KNOWN_UNSUPPORTED_TASKS = TASK_ADVISORIES
_GPU_HEAVY_TASKS = {"text-to-image", "image-to-image", "text-to-video", "image-to-video", "text-to-3d", "image-to-3d"}

# architecture suffix -> task (weight 0.7)
_ARCH_SUFFIX = [
    ("ForCausalLM", "text-generation"), ("LMHeadModel", "text-generation"),
    ("ForSequenceClassification", "text-classification"), ("ForTokenClassification", "token-classification"),
    ("ForMaskedLM", "fill-mask"), ("ForSpeechSeq2Seq", "automatic-speech-recognition"), ("ForCTC", "automatic-speech-recognition"),
    ("ForAudioClassification", "audio-classification"), ("ForImageClassification", "image-classification"),
    ("ForObjectDetection", "object-detection"), ("ForSemanticSegmentation", "image-segmentation"),
    ("ForInstanceSegmentation", "image-segmentation"), ("ForUniversalSegmentation", "image-segmentation"),
    ("ForDepthEstimation", "depth-estimation"), ("ForImageTextToText", "image-text-to-text"),
    ("ForVision2Seq", "image-to-text"), ("ForMultipleChoice", "multiple-choice"),
    ("ForTextToSpeech", "text-to-speech"), ("ForZeroShotObjectDetection", "zero-shot-object-detection"),
]
_SPEECH_S2S_TYPES = {"whisper", "speech_to_text", "speech-encoder-decoder", "speech_encoder_decoder"}
# Config sections that declare an audio component. A conditional-generation head that carries one is not assumed to be text2text.
_AUDIO_CFG_KEYS = ("audio_config", "speech_config", "audio_encoder")
# Architecture suffixes that exist for several modalities (audio/vision models also have ForSequenceClassification, ...). Evidence from
# them alone never overrides an explicit declaration; see _generic_only() and capabilities.GENERIC_ARCH_SIGNAL.
_GENERIC_ARCH_SUFFIXES = ("ForSequenceClassification", "ForTokenClassification", "ForMaskedLM", "ForMultipleChoice",
                          "ForQuestionAnswering", "ForQuestionAnsweringSimple")
_CAPTION_TYPES = {"blip", "blip-2", "pix2struct", "git", "vision-encoder-decoder", "vision_encoder_decoder", "kosmos-2", "instructblip"}
_LANG_CODE_TYPES = {"m2m_100", "nllb", "nllb_moe", "mbart", "seamless_m4t"}
_SENTENCEPIECE_FILES = ("spiece.model", "sentencepiece.bpe.model", "tokenizer.model", "source.spm", "sentencepiece.model")
_SENTENCEPIECE_TYPES = {"t5", "mt5", "marian", "albert", "xlm-roberta", "camembert", "mbart", "m2m_100", "nllb", "pegasus", "bart_sp", "xlnet"}
_TIMM_TYPES = {"detr", "conditional_detr", "deformable_detr", "table-transformer", "yolos_timm"}
_BAD_QUANT = {"gptq", "awq", "aqlm", "hqq", "eetq", "fbgemm_fp8", "compressed-tensors", "quanto", "higgs", "torchao", "mxfp4", "fp8"}
_UNSUPPORTED_LIBS = {"timm", "spacy", "sklearn", "scikit-learn", "fasttext", "keras", "stable-baselines3", "flair", "allennlp",
                     "asteroid", "espnet", "nemo", "speechbrain", "pyannote-audio", "onnx", "gguf", "llama-cpp-python", "mlx",
                     "peft", "open_clip", "ultralytics", "sample-factory", "fastai", "paddlepaddle", "tf-keras", "coreml",
                     "transformers.js", "ctranslate2", "adapter-transformers", "setfit", "bertopic"}
_MIN_TRANSFORMERS = (4, 44, 0)
# A GitHub repository is only turned into an app through its linked Hugging Face model when that model's task is established
# at least this firmly (an authoritative pipeline_tag or a recognised architecture). Tag/keyword-only evidence is not enough.
_MIN_LINKED_TASK_CONFIDENCE = 0.6

# Task pairs that describe the same thing: they agree with each other instead of conflicting.
_EQUIV = {frozenset(p) for p in (
    ("feature-extraction", "sentence-similarity"), ("text-to-speech", "text-to-audio"),
    ("text-generation", "text2text-generation"), ("summarization", "text2text-generation"),
    ("translation", "text2text-generation"), ("zero-shot-classification", "text-classification"),
)}

# Evidence tiers, strongest first. authoritative = declared metadata (Hub pipeline_tag, diffusers class); structural = derived
# from config/architecture or from static (ast) facts about the source; heuristic = keyword/tag text, never enough alone.
_SIGNAL_TIER = {"pipeline_tag": "authoritative", "diffusers_pipeline_class": "authoritative", "library": "authoritative",
                "architecture": "structural", "tag": "heuristic", "task_specific_params": "structural",
                "vision_language_config": "structural", "generation_task_token": "structural", "auto_map": "structural",
                "card_eval_task": "structural", GENERIC_ARCH_SIGNAL: "structural"}

# task -> (input modalities, output modalities)
_T, _I, _A = ("text",), ("image",), ("audio",)
_TASK_MODALITIES: Dict[str, Tuple[Tuple[str, ...], Tuple[str, ...]]] = {
    **{t: (_T, _T) for t in ("text-generation", "text2text-generation", "summarization", "translation", "text-classification",
                            "token-classification", "question-answering", "fill-mask", "zero-shot-classification",
                            "feature-extraction", "sentence-similarity")},
    "image-classification": (_I, _T), "zero-shot-image-classification": (("image", "text"), _T), "object-detection": (_I, _T),
    "image-segmentation": (_I, _I), "image-to-text": (_I, _T), "visual-question-answering": (("image", "text"), _T),
    "image-text-to-text": (("image", "text"), _T), "document-question-answering": (("image", "text"), _T),
    "automatic-speech-recognition": (_A, _T), "audio-classification": (_A, _T), "text-to-speech": (_T, _A),
    "text-to-audio": (_T, _A), "text-to-image": (_T, _I), "image-to-image": (("image", "text"), _I),
    "text-to-video": (_T, ("video",)), "depth-estimation": (_I, _I),
}


for _k, _v in EXTRA_TASK_MODALITIES.items():
    _TASK_MODALITIES.setdefault(_k, _v)


def modalities_for_task(task: Optional[str]) -> Tuple[List[str], List[str]]:
    ins, outs = _TASK_MODALITIES.get(task or "", ((), ()))
    return list(ins), list(outs)

# --------------------------------------------------------------------------- data classes


@dataclass
class Evidence:
    signal: str
    source: str
    detail: str
    weight: float
    tier: str = "structural"  # authoritative | structural | heuristic
    task: str = ""  # the capability/task this evidence supports ("" when it supports none in particular)
    # (input, output) modalities when the evidence describes a MODE of the primary model (it then uses the primary's input rather
    # than the standalone task's); None for ordinary task evidence.
    modalities: Optional[Tuple[Tuple[str, ...], Tuple[str, ...]]] = None


@dataclass
class HardwareAssessment:
    params: Optional[int] = None
    params_source: str = "unknown"
    weights_gb_fp16: Optional[float] = None
    est_vram_gb_fp16: Optional[float] = None
    est_vram_gb_4bit: Optional[float] = None
    recommended_runtime: str = "unknown"
    runs_locally_cpu: bool = False
    fits_free_colab_t4: Optional[bool] = None
    suggest_4bit: bool = False
    needs_gpu: bool = False
    exceeds_colab: bool = False
    notes: List[str] = field(default_factory=list)


@dataclass
class Analysis:
    source: SourceRef
    revision: Optional[str] = None
    task: Optional[str] = None
    task_confidence: float = 0.0
    task_evidence: List[Evidence] = field(default_factory=list)
    library: str = "unknown"  # ML stack: transformers | diffusers | sentence-transformers | pytorch | tensorflow | jax | unknown
    inputs: List[Dict[str, str]] = field(default_factory=list)
    outputs: List[Dict[str, str]] = field(default_factory=list)
    dependencies: List[str] = field(default_factory=list)
    dependency_evidence: Dict[str, str] = field(default_factory=dict)
    hardware: HardwareAssessment = field(default_factory=HardwareAssessment)
    status: str = "unsupported"  # supported | supported_with_warnings | unsupported | partial
    reasons: List[str] = field(default_factory=list)  # why unsupported / partial
    generation_blocked_reasons: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    chat: bool = False
    trust_remote_code: bool = False
    quantization: Optional[str] = None
    load_in_4bit_recommended: bool = False
    gated: bool = False
    ui_hints: Dict[str, Any] = field(default_factory=dict)
    linked_hf_models: List[str] = field(default_factory=list)
    entrypoint_candidates: List[str] = field(default_factory=list)
    requirement_flags: List[str] = field(default_factory=list)
    repo_requirements: List[str] = field(default_factory=list)
    license: Optional[str] = None
    min_transformers: str = "4.44.0"
    use_safetensors: bool = True
    # hosting platform (where the code/weights live) is kept apart from `library` (the ML framework/loading stack)
    host: str = "huggingface"  # huggingface | github
    frameworks: List[str] = field(default_factory=list)  # ML frameworks with evidence, e.g. PyTorch, transformers
    uncertainties: List[str] = field(default_factory=list)  # things the analyzer could not establish
    input_modalities: List[str] = field(default_factory=list)
    output_modalities: List[str] = field(default_factory=list)
    primary_model: Optional[str] = None  # the repository's own Hugging Face model, when one could be identified
    # The Hugging Face model the generated app actually loads. None for a Hugging Face analysis (then `source` IS that model).
    # For a GitHub analysis it is set only when the repository's own linked model was identified AND passed the full Hugging Face
    # analysis; the GitHub code itself is never run, installed or imported.
    model_source: Optional[SourceRef] = None
    # Result of the REA inspection stage as plain data (status, executed tools, findings, log); None when the stage did not run.
    rea: Optional[Dict[str, Any]] = None
    # Unified Python-analyzer + REA report (see build_combined_analysis). The two source results stay in their own fields above;
    # this is derived data and is None only for an Analysis that was not produced by analyze().
    combined_analysis: Optional[Dict[str, Any]] = None
    # Evidence-based capabilities (primary first). Each says what the evidence supports and with what confidence; roles decide which
    # get their own interface. ``task`` above is kept for backward compatibility and equals the primary capability's name.
    capabilities: List[Capability] = field(default_factory=list)
    # Bounded, whitelisted digest of config/tokenizer/file/diffusers facts the generator may use to design controls and loading.
    model_evidence: Dict[str, Any] = field(default_factory=dict)
    # Digest of the Phase 1 evidence collection (m2g.evidence_bridge.build_evidence_digest): per-item status, provenance and errors,
    # tensor-header summaries, limitations and which metadata fields were filled from evidence. None when collection did not run.
    evidence: Optional[Dict[str, Any]] = None
    # Single provenance-preserving ledger for repository, analyzer, and REA facts.
    evidence_ledger: List[Dict[str, Any]] = field(default_factory=list)
    investigation_coverage: Dict[str, Any] = field(default_factory=dict)
    # Where the model comes from: "huggingface" (a Hub model analysed directly), "github-linked-hf" (a GitHub repository generated through its
    # own linked Hugging Face model) or "github-native" (an AI model repository on GitHub that stands on its own evidence; no Hugging Face
    # model is needed or invented, and ``model_source`` stays None).
    origin: str = "huggingface"
    # Digest of what a github-native analysis rests on, kept in three honest classes: ``confirmed`` (observed in the repository
    # metadata), ``inferred`` (reasoned from that evidence) and ``unknown`` (could not be established; never assumed). Empty otherwise.
    native: Dict[str, Any] = field(default_factory=dict)
    # Normalized, provider-neutral repository representation (m2g.repo_model.RepositoryModel.to_dict()): identity, revision, file /
    # source / config / dependency / artifact inventories, coverage, gaps and missing evidence. None until analyze() builds it.
    repository: Optional[Dict[str, Any]] = None
    # Claim-level validation (m2g.claims.claims_report): one entry per conclusion with its supporting/contradicting evidence ids, an
    # explicit status and a confidence derived only from the evidence. None until analyze() builds it.
    claims: Optional[Dict[str, Any]] = None

    @property
    def model_ref(self) -> SourceRef:
        """Where the model to load lives: the linked Hugging Face model for a GitHub analysis, otherwise ``source``."""
        return self.model_source or self.source

    @property
    def generatable(self) -> bool:
        """Generation is possible only when at least one capability that builds an interface is supported by firm evidence; a non-empty
        capability list of uncertain entries is not enough (same predicate as gradio_gen.interface_capabilities). A legacy analysis that
        carries only ``task`` keeps the old rule."""
        if self.origin == "github-native":  # scaffold generation: eligibility is the evidence gate recorded in ``native``, not a firm task
            return (self.status == "supported_with_warnings" and not self.reasons and not self.generation_blocked_reasons
                    and bool((self.native or {}).get("eligible")))
        return (self.status in ("supported", "supported_with_warnings")
                and not self.generation_blocked_reasons
                and (any(c.builds_interface and c.status == "supported" for c in self.capabilities)
                     if self.capabilities else bool(self.task)))

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------------- helpers


def effective_capabilities(a: Analysis) -> List[Capability]:
    """The analysis' capabilities; for an Analysis that only carries the legacy ``task`` (older JSON, hand-built objects) one
    primary capability is derived from it so nothing depends on which form it is in."""
    if a.capabilities:
        return list(a.capabilities)
    if a.task:
        ins, outs = modalities_for_task(a.task)
        status, why = detection_status(a.task, a.task_confidence, "structural", "primary", "task", a.task in TASK_IO or a.task in HF_TASK_VOCAB)
        certainty, certainty_why = certainty_for(
            status, [{"tier": e.tier, "signal": e.signal} for e in a.task_evidence if e.task in ("", a.task)], why)
        return [Capability(name=a.task, confidence=a.task_confidence, tier="structural", role="primary", input_modalities=ins,
                           output_modalities=outs, known_reference=a.task in TASK_IO, chat=bool(a.chat), status=status,
                           status_reason=why, certainty=certainty, certainty_reason=certainty_why)]
    return []


def _ver_tuple(v: str) -> Optional[Tuple[int, int, int]]:
    m = re.match(r"^(\d+)\.(\d+)(?:\.(\d+))?", str(v or ""))
    return (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)) if m else None


def _add(cands: Dict[str, float], ev: List[Evidence], task: str, signal: str, source: str, detail: str, w: float) -> None:
    cands[task] = cands.get(task, 0.0) + w
    ev.append(Evidence(signal, source, detail, w, _SIGNAL_TIER.get(signal, "structural"), task))


def _arch_task(arch: str, model_type: str, cfg: Optional[Dict[str, Any]] = None) -> Optional[str]:
    if arch.endswith("ForQuestionAnswering") or arch.endswith("ForQuestionAnsweringSimple"):
        return "visual-question-answering" if model_type in {"vilt", "blip", "blip-2", "visual_bert", "lxmert"} else "question-answering"
    if arch.endswith("ForConditionalGeneration") or arch.endswith("ForConditionalGenerationModel"):
        if model_type in _SPEECH_S2S_TYPES:
            return "automatic-speech-recognition"
        if model_type in _CAPTION_TYPES:
            return "image-to-text"
        if isinstance(cfg, dict) and isinstance(cfg.get("vision_config"), dict) and isinstance(cfg.get("text_config"), dict):
            return "image-text-to-text"  # config says: vision tower + language model (LLaVA, PaliGemma, Idefics, Qwen-VL ...)
        if isinstance(cfg, dict) and any(isinstance(cfg.get(k), dict) for k in _AUDIO_CFG_KEYS):
            return None  # audio sub-config present: text2text-generation would be a guess, so the task stays unestablished (uncertain)
        return "text2text-generation"
    if arch in {"VitsModel", "BarkModel", "SpeechT5ForTextToSpeech"}:
        return "text-to-speech"
    if arch == "CLIPModel" or arch == "SiglipModel":
        return "zero-shot-image-classification"
    if arch == "VisionEncoderDecoderModel":
        return "image-to-text"
    for suffix, task in _ARCH_SUFFIX:
        if arch.endswith(suffix):
            return task
    if arch.endswith("Model") and not arch.endswith(("ForCausalLM",)):
        return "feature-extraction"
    return None


def _diffusers_task(cls: str) -> Optional[str]:
    if any(k in cls for k in ("Img2Img", "Inpaint", "ControlNet", "Upscale", "Video", "Instruct", "Depth2Img", "Pix2Pix")):
        return "image-to-image"
    if cls.endswith("Pipeline") and any(k in cls for k in ("StableDiffusion", "Flux", "PixArt", "Kandinsky", "AuraFlow", "Lumina",
                                                            "Sana", "Hunyuan", "Cascade", "Wuerstchen", "UnCLIP", "Lmd", "AmusedPipeline")):
        return "text-to-image"
    return None


def _is_generic_arch(arch: str, task: str) -> bool:
    """Is this architecture->task mapping one that exists for several modalities (so it proves little on its own)?"""
    return arch.endswith(_GENERIC_ARCH_SUFFIXES) or (arch.endswith(("ForConditionalGeneration", "ForConditionalGenerationModel"))
                                                     and task == "text2text-generation")


def _audio_conditional_head(arch: str, cfg: Optional[Dict[str, Any]]) -> bool:
    return (arch.endswith(("ForConditionalGeneration", "ForConditionalGenerationModel")) and isinstance(cfg, dict)
            and any(isinstance(cfg.get(k), dict) for k in _AUDIO_CFG_KEYS))


def _generic_only(ev: List[Evidence], task: str) -> bool:
    """True when every non-heuristic evidence entry for ``task`` is a generic architecture-suffix mapping."""
    firm = [e for e in ev if e.task == task and e.tier != "heuristic"]
    return bool(firm) and all(e.signal == GENERIC_ARCH_SIGNAL for e in firm)


def _co_supported(top: str, other: str, ev: List[Evidence]) -> bool:
    """Is ``other`` a second capability of the same model rather than a contradiction of ``top``? Yes when it rests on at least one
    non-heuristic evidence entry AND its input modalities overlap the primary's (so one UI can sensibly offer both)."""
    if not any(e.task == other and e.tier != "heuristic" for e in ev):
        return False
    a_in, b_in = modalities_for_task(top)[0], modalities_for_task(other)[0]
    return bool(a_in and b_in and set(a_in) & set(b_in))


def _gather_task_evidence(info: RepoInfo) -> Tuple[Dict[str, float], List[Evidence], List[str]]:
    """Task evidence from the Hub pipeline_tag, config.json architectures, model_index.json, library and tags (no model names, no
    README text). Shared by detect_task() and detect_capabilities() so both read the same facts."""
    cands: Dict[str, float] = {}
    ev: List[Evidence] = []
    notes: List[str] = []
    tag = info.pipeline_tag
    if tag and valid_task_name(tag):
        _add(cands, ev, tag, "pipeline_tag", "Hub API", f"pipeline_tag={tag}", 1.0)
    elif tag:
        notes.append("The Hub pipeline_tag is not a valid task identifier and was ignored.")
    cfg = info.config or {}
    mtype = str(cfg.get("model_type", ""))
    arch_tasks: List[str] = []
    for i, arch in enumerate((cfg.get("architectures") or [])[:3]):  # every recognised head is evidence of a capability
        t = _arch_task(str(arch), mtype, cfg)
        if t and t not in arch_tasks:
            arch_tasks.append(t)
            w = 0.4 if t == "feature-extraction" else 0.7  # a bare *Model head is weak evidence
            sig = GENERIC_ARCH_SIGNAL if _is_generic_arch(str(arch), t) else "architecture"
            _add(cands, ev, t, sig, "config.json", f"architectures[{i}]={arch} (model_type={mtype or '?'})", w)
        elif not t and _audio_conditional_head(str(arch), cfg):
            notes.append(f"architectures[{i}]={arch} is a conditional-generation head with an audio sub-config; its task is not established "
                         "from the architecture alone and stays uncertain.")
    if info.model_index:
        cls = str(info.model_index.get("_class_name", ""))
        t = _diffusers_task(cls)
        if t:
            _add(cands, ev, t, "diffusers_pipeline_class", "model_index.json", f"_class_name={cls}", 0.9)
    if info.library_name == "sentence-transformers" or "sentence-transformers" in info.tags:
        _add(cands, ev, "sentence-similarity", "library", "Hub API", "library_name/tag = sentence-transformers", 0.8)
    for t in info.tags:
        if t in HF_TASK_VOCAB or t in TASK_IO:
            if t != tag:
                _add(cands, ev, t, "tag", "Hub API", f"tag '{t}'", 0.3)
    return cands, ev, notes


def _rank_tasks(cands: Dict[str, float], ev: List[Evidence], notes: List[str]) -> Tuple[str, float]:
    """The top task and its confidence; appends a note when equally firm, incompatible evidence competes with it."""
    # on equal scores a task backed by a modality-specific head outranks one backed only by a generic suffix
    ranked = sorted(cands.items(), key=lambda kv: (-kv[1], _generic_only(ev, kv[0])))
    top, top_score = ranked[0]
    # normalise equivalent tasks so they agree rather than conflict
    rivals = [(t, s) for t, s in ranked[1:] if s >= 0.6 and frozenset((top, t)) not in _EQUIV and not _co_supported(top, t, ev)]
    # A rival backed only by a generic architecture suffix does not contradict an explicit, recognised declaration of the top task.
    declared = top in HF_TASK_VOCAB and any(e.task == top and e.tier == "authoritative" and e.signal in CONFIRMING_SIGNALS for e in ev)
    weak = [r for r in rivals if declared and _generic_only(ev, r[0])]
    conflicts = [r for r in rivals if r not in weak]
    if weak:
        notes.append(f"'{weak[0][0]}' is only suggested by a generic architecture-head mapping; the explicit declaration '{top}' is kept "
                     "(not treated as a conflict).")
    conf = min(1.0, top_score)
    if conflicts:
        notes.append(f"Evidence conflict: '{top}' (score {top_score:.1f}) vs '{conflicts[0][0]}' (score {conflicts[0][1]:.1f}); using '{top}'.")
        conf *= 0.6
    return top, round(conf, 2)


def detect_task(info: RepoInfo) -> Tuple[Optional[str], float, List[Evidence], List[str]]:
    """Single primary task (kept for backward compatibility; multi-capability detection lives in detect_capabilities())."""
    cands, ev, notes = _gather_task_evidence(info)
    if not cands:
        return None, 0.0, ev, ["No task evidence found (no pipeline_tag, recognised architecture or diffusers class).", *notes]
    top, conf = _rank_tasks(cands, ev, notes)
    return top, conf, ev, notes


# generation_config.json ``task_to_id`` keys are free-form strings. A key is understood only when it is itself an HF task name or a
# plain verb for one of these tasks; any other key is ignored, never guessed. This reads a config FIELD, not a model name.
_TASK_TOKEN_VERBS = {"transcribe": "automatic-speech-recognition", "translate": "translation", "summarize": "summarization"}
_CARD_TASK_WEIGHT = 0.45  # model-index task: indirect evidence, deliberately under capabilities.CO_SUPPORT_MIN_SCORE (0.6)


def _task_from_token(key: str) -> Optional[str]:
    k = key.strip().lower().replace("_", "-")
    return k if k in HF_TASK_VOCAB else _TASK_TOKEN_VERBS.get(k)


def _tsp_task(key: str) -> Optional[str]:
    k = key.lower()
    return "translation" if k.startswith("translation") else (k if k in HF_TASK_VOCAB else None)


def _has_strong(ev: List[Evidence], task: str) -> bool:
    return any(e.task == task and e.tier != "heuristic" for e in ev)


def _supplementary_evidence(info: RepoInfo, ev: List[Evidence], primary: Optional[str] = None) -> Dict[str, float]:
    """Extra capability evidence beyond the single-task signals, appended to ``ev``; returns its per-task score.
    Sources: config.json ``task_specific_params`` (tasks the model's authors configured) and vision-language configs
    (``vision_config`` + ``text_config``) with tokenizer/generation files. Model names and README text are never used."""
    extra: Dict[str, float] = {}

    def add(task: str, signal: str, source: str, detail: str, w: float, modalities: Any = None) -> None:
        extra[task] = extra.get(task, 0.0) + w
        ev.append(Evidence(signal, source, detail, w, _SIGNAL_TIER.get(signal, "structural"), task, modalities))

    cfg = info.config or {}
    tsp = cfg.get("task_specific_params")
    if isinstance(tsp, dict):
        seen: set = set()
        for key in list(tsp)[:16]:
            t = _tsp_task(str(key))
            if t and t not in seen and valid_task_name(t):
                seen.add(t)
                add(t, "task_specific_params", "config.json", f"task_specific_params.{str(key)[:40]}", 0.6)
    # Task-conditioning tokens (generation_config.json task_to_id): the modes the model was configured to be steered between. A mode is
    # a way of driving the SAME model, so it takes the primary capability's input; its output comes from the task. Without a primary
    # capability with known input modalities nothing is inferred (the mode cannot be placed).
    gen = getattr(info, "generation_config", None)
    task_tokens = gen.get("task_to_id") if isinstance(gen, dict) else None
    if isinstance(task_tokens, dict):
        primary_in = modalities_for_task(primary)[0] if primary else []
        seen_modes: set = set()
        for key in list(task_tokens)[:16]:
            t = _task_from_token(str(key))
            if not t or t in seen_modes or not valid_task_name(t):
                continue
            seen_modes.add(t)
            detail = f"generation_config.json task_to_id[{str(key)[:40]!r}]: task-conditioning token"
            if t == primary:
                add(t, "generation_task_token", "generation_config.json", detail + " (same as the primary capability)", 0.6)
            elif primary_in:
                mode_out, primary_out = modalities_for_task(t)[1], modalities_for_task(primary)[1]
                if mode_out and primary_out and not set(mode_out) & set(primary_out):
                    # A mode steers the SAME model, so it cannot produce a different kind of output than the primary. The token is
                    # still recorded, but without inherited modalities: the ordinary role logic then reports it as conflicting.
                    add(t, "generation_task_token", "generation_config.json",
                        detail + f" (its {'/'.join(mode_out)} output is incompatible with the primary's {'/'.join(primary_out)} output)", 0.6)
                else:
                    add(t, "generation_task_token", "generation_config.json",
                        detail + " (a mode of the primary model; it takes the primary's input)", 0.6,
                        (tuple(primary_in), tuple(mode_out)))
    # Tasks the model card itself reports evaluation results for (model-index). Author-declared but indirect: a model is often evaluated
    # on neighbouring tasks, so the weight stays BELOW CO_SUPPORT_MIN_SCORE. Alone it can only be a hint (reported, never built) or a
    # low-confidence primary; it never creates an interface by itself, and it only counts when it names a recognised Hub task.
    seen_card: set = set()
    for t in list(getattr(info, "card_tasks", None) or [])[:16]:
        if isinstance(t, str) and t in HF_TASK_VOCAB and t not in seen_card and valid_task_name(t):
            seen_card.add(t)
            add(t, "card_eval_task", "model card (model-index)", f"model-index lists evaluation results for task '{t}'", _CARD_TASK_WEIGHT)
    # Heads the repository itself registers (config.json auto_map AutoModelFor*): each is a declared way to use the weights.
    auto_map = cfg.get("auto_map")
    if isinstance(auto_map, dict):
        seen_heads: set = set()
        for key in list(auto_map)[:16]:
            k = str(key)
            t = _arch_task(k, str(cfg.get("model_type", ""))) if k.startswith("AutoModelFor") else None
            if t and t not in seen_heads and valid_task_name(t):
                seen_heads.add(t)
                add(t, "auto_map", "config.json", f"auto_map.{k[:60]} (head registered by the repository)", 0.6)
    if isinstance(cfg.get("vision_config"), dict) and isinstance(cfg.get("text_config"), dict):
        base = {n.lower().rsplit("/", 1)[-1] for n in info.file_names()}
        if not _has_strong(ev, "image-text-to-text"):
            add("image-text-to-text", "vision_language_config", "config.json", "vision_config + text_config: vision tower with a language model", 0.6)
        if not _has_strong(ev, "text-generation") and (info.tokenizer_config or base & {"generation_config.json", "tokenizer.json", "tokenizer.model"}):
            add("text-generation", "vision_language_config", "config.json + tokenizer/generation files",
                "text_config language model with its own tokenizer/generation settings", 0.6)
    return extra


def detect_capabilities(info: RepoInfo) -> List[Capability]:
    """All evidence-supported capabilities of a model, primary first. Independent of detect_task(): it reads the repository facts
    itself, so a second capability never depends on the single-task result. Each capability carries confidence, evidence
    (tier/signal/source/detail), input/output modalities and a detection status (supported | uncertain)."""
    cands, ev, notes = _gather_task_evidence(info)
    top: Optional[str] = None
    conf = 0.0
    if cands:
        top, conf = _rank_tasks(cands, ev, notes)
    extra = _supplementary_evidence(info, ev, primary=top)
    if top is None and extra:
        top, conf = _rank_tasks(extra, ev, [])
    fallback = config_modalities(info.config or {}, info.file_names(), info.model_index)
    return build_capabilities(ev, top, conf, equiv=_EQUIV, modalities=modalities_for_task, reference_tasks=TASK_IO,
                              chat=bool((info.tokenizer_config or {}).get("chat_template")), fallback=fallback)


def _estimate_params(info: RepoInfo) -> Tuple[Optional[int], str]:
    if info.safetensors_total_params:
        return info.safetensors_total_params, "Hub safetensors metadata"
    wb = info.weights_bytes()
    if wb:
        return int(wb / 2), "estimated from weight-file size (assuming 16-bit)"
    cfg = info.config or {}
    h = cfg.get("hidden_size") or cfg.get("d_model") or cfg.get("dim")
    layers = cfg.get("num_hidden_layers") or cfg.get("n_layer") or cfg.get("num_layers") or cfg.get("n_layers")
    vocab = cfg.get("vocab_size") or 0
    if isinstance(h, int) and isinstance(layers, int):
        return int(12 * layers * h * h + vocab * h), "rough config-based estimate"
    return None, "unknown"


def assess_hardware(info: RepoInfo, task: Optional[str], quant_ok: bool) -> HardwareAssessment:
    hw = HardwareAssessment()
    params, src = _estimate_params(info)
    hw.params, hw.params_source = params, src
    heavy = task in _GPU_HEAVY_TASKS or bool(info.model_index)
    hw.needs_gpu = heavy
    if not params:
        hw.recommended_runtime = "Unknown (model size not found in repository metadata)"
        hw.notes.append("Model size could not be determined from metadata; no hardware requirement is assumed.")
        return hw
    gb = params * 2 / 1e9
    hw.weights_gb_fp16 = round(gb, 2)
    hw.est_vram_gb_fp16 = round(gb * 1.2 + 1.0, 1)
    hw.est_vram_gb_4bit = round(params * 0.5 / 1e9 * 1.2 + 1.0, 1)
    vram = hw.est_vram_gb_fp16
    hw.runs_locally_cpu = params <= 1_000_000_000 and not heavy
    hw.fits_free_colab_t4 = vram <= 13.5
    if params < 700_000_000 and not heavy:
        hw.recommended_runtime = "CPU is fine for small inputs; Colab T4 GPU for speed"
    elif vram <= 13.5:
        hw.recommended_runtime = "Colab free T4 GPU (16 GB)"
        hw.needs_gpu = True
    elif quant_ok and hw.est_vram_gb_4bit <= 13.5:
        hw.recommended_runtime = "Colab T4 GPU with 4-bit quantization (bitsandbytes), or L4/A100 in fp16"
        hw.suggest_4bit, hw.needs_gpu = True, True
    elif vram <= 22:
        hw.recommended_runtime = "Colab L4 (24 GB) or A100 (Colab Pro)"
        hw.needs_gpu = True
    elif vram <= 38:
        hw.recommended_runtime = "Colab A100 40 GB (Colab Pro/Pro+)"
        hw.needs_gpu = True
    elif quant_ok and hw.est_vram_gb_4bit <= 38:
        hw.recommended_runtime = "Colab A100 with 4-bit quantization"
        hw.suggest_4bit, hw.needs_gpu = True, True
    else:
        hw.recommended_runtime = "Does not fit a single Colab GPU"
        hw.exceeds_colab, hw.needs_gpu = True, True
    if hw.runs_locally_cpu is False and not hw.exceeds_colab:
        hw.notes.append("Do not run locally; use the generated Colab notebook (no large model is downloaded on this machine).")
    hw.notes.append("VRAM figures are estimates: 16-bit weights x 1.2 + 1 GB overhead; actual use depends on batch/sequence length.")
    return hw


def colab_capacity_warning(hw: HardwareAssessment, quant_ok: bool) -> str:
    """Warning text for a model whose estimated VRAM does not fit a single Colab GPU.

    This is a DEPLOYMENT/HARDWARE advisory, not an analysis or generation blocker: the Gradio app is model-specific and does not depend
    on Colab, so it can still run on any machine with enough GPU memory. It therefore belongs in ``warnings`` and never in ``reasons``
    (a non-empty ``reasons`` makes the analysis ``unsupported`` and blocks generation)."""
    return (f"Estimated {hw.est_vram_gb_fp16} GB VRAM exceeds a single Colab GPU"
            + (f" even with 4-bit ({hw.est_vram_gb_4bit} GB)" if quant_ok else "")
            + ". The app is still generated, but this model will not fit Colab's GPUs; run it on a machine with enough GPU memory.")


def _dependencies(info: RepoInfo, task: Optional[str], library: str, hw: HardwareAssessment, quant: Optional[str], tf_min: str,
                  in_mods: Tuple[str, ...] = (), out_mods: Tuple[str, ...] = (), tasks: Tuple[str, ...] = ()
                  ) -> Tuple[List[str], Dict[str, str]]:
    deps: Dict[str, str] = {}
    why: Dict[str, str] = {}

    def need(req: str, reason: str) -> None:
        name = re.split(r"[<>=!~\[]", req)[0]
        if name not in deps:
            deps[name] = req
            why[name] = reason

    need("gradio>=5.0", "UI")
    need("torch>=2.1.0", "inference backend")
    need("safetensors>=0.4.3", "safe weight loading")
    need("numpy", "array handling")
    if library == "diffusers":
        need("diffusers>=0.30.0", "model_index.json present (diffusers pipeline)")
        need("transformers>=" + tf_min, "diffusers text encoders")
        need("accelerate>=0.30.0", "diffusers device placement")
    else:
        need("transformers>=" + tf_min, "config.json architecture / pipeline task" + (f" (config records transformers {info.config.get('transformers_version')})" if info.config and info.config.get("transformers_version") else ""))
        need("accelerate>=0.30.0", "dtype/device placement")
    if library == "sentence-transformers":
        need("sentence-transformers>=3.0.0", "library_name=sentence-transformers")
    cfg = info.config or {}
    mtype = str(cfg.get("model_type", ""))
    files = set(info.file_names())
    all_tasks = {t for t in (task, *tasks) if t}
    mods = set(in_mods) | set(out_mods)
    if all_tasks & IMAGE_TASKS or "image" in mods:
        need("pillow", "image input/output (task or modality evidence)")
    if all_tasks & {"object-detection", "image-segmentation"} and (mtype in _TIMM_TYPES or cfg.get("use_timm_backbone")):
        need("timm", f"model_type={mtype} uses a timm backbone")
    if all_tasks & AUDIO_TASKS or "audio" in mods:
        need("soundfile", "audio input/output (task or modality evidence)")
        need("librosa", "audio input/output (task or modality evidence)")
    if any(f in files for f in _SENTENCEPIECE_FILES) or mtype in _SENTENCEPIECE_TYPES:
        need("sentencepiece", "SentencePiece tokenizer file/model_type")
        need("protobuf", "required with sentencepiece tokenizers")
    if mtype == "marian":
        need("sacremoses", "Marian translation tokenizers")
    if quant == "bitsandbytes" or hw.suggest_4bit:
        need("bitsandbytes>=0.43.0", "bitsandbytes quantization" if quant else "4-bit loading for large model")
    reqs = [validate_pip_requirement(r) for r in deps.values()]
    return reqs, why


# --------------------------------------------------------------------------- main analysis (Hugging Face)


def analyze_hf(info: RepoInfo, settings: Optional[Settings] = None) -> Analysis:
    settings = settings or Settings()
    a = Analysis(source=info.source, revision=info.revision_sha or info.source.revision, license=info.license, gated=info.gated)
    cfg = info.config or {}
    mtype = str(cfg.get("model_type", ""))
    reasons, warnings = a.reasons, a.warnings

    task, conf, ev, notes = detect_task(info)
    a.task, a.task_confidence, a.task_evidence = task, conf, ev
    warnings += notes

    # Evidence-based capabilities (what the model can do) and the config-derived facts the generator designs from. Neither is
    # limited by TASK_IO: an unknown task is still a capability, with its own (possibly low) confidence.
    a.capabilities = detect_capabilities(info)
    for c in a.capabilities:  # per-capability input/output requirements that the repository's own config files actually state
        c.requirements = capability_requirements(c, cfg, info.tokenizer_config, getattr(info, "preprocessor_config", None))
    prim = a.capabilities[0] if a.capabilities else None
    if task is None and prim is not None and prim.basis == "task":  # task evidence came only from config-level capability evidence
        task, conf = prim.name, prim.confidence
        a.task, a.task_confidence = task, conf
        warnings[:] = [w for w in warnings if not w.startswith("No task evidence found")]
    a.model_evidence = build_model_evidence(cfg, info.tokenizer_config, info.model_index, info.file_names(), info.tags, info.readme,
                                            generation_config=getattr(info, "generation_config", None))
    iface = [c for c in a.capabilities if c.builds_interface]

    # library / loading stack
    if info.model_index or (task in DIFFUSERS_TASKS and info.library_name == "diffusers"):
        a.library = "diffusers"
    elif info.library_name == "sentence-transformers" or "sentence-transformers" in info.tags:
        a.library = "sentence-transformers"
    elif cfg or info.library_name in (None, "transformers"):
        a.library = "transformers"

    # --- blocking checks
    if not a.capabilities:
        reasons.append("No capability evidence was found (no pipeline_tag, recognised architecture, diffusers class, task tag or "
                       "config/file modality hints), so there is nothing to design an interface from.")
    elif not any(c.status == "supported" for c in iface):
        reasons.append("No capability is established by firm evidence, so no interface is generated and the analysis is reported only: "
                       + "; ".join(f"{c.name} ({c.status_reason})" for c in a.capabilities[:4]))
    if info.adapter_config is not None or info.library_name == "peft":
        base = (info.adapter_config or {}).get("base_model_name_or_path", "unknown base model")
        reasons.append(f"Adapter-only (LoRA/PEFT) repository; it cannot run without its base model ({base}). Analyse the merged/base model instead.")
    if info.library_name and info.library_name.lower() in _UNSUPPORTED_LIBS and not info.model_index:
        reasons.append(f"Library '{info.library_name}' is not supported (only transformers, diffusers and sentence-transformers)"
                       + (f"; the repository's task '{task}' cannot be served with them." if task else "."))
    if info.has_gguf and not (info.has_safetensors or info.has_pickle_weights):
        reasons.append("GGUF-only repository (llama.cpp format); not loadable with transformers pipelines.")
    elif info.has_onnx and not (info.has_safetensors or info.has_pickle_weights):
        reasons.append("ONNX-only repository; ONNX runtimes are not supported in v1.")
    elif info.files and not (info.has_safetensors or info.has_pickle_weights or info.has_gguf or info.has_onnx):
        reasons.append("No model weight files were found in the repository listing.")
    quant = None
    qc = cfg.get("quantization_config")
    if isinstance(qc, dict):
        quant = str(qc.get("quant_method", "unknown")).lower()
        a.quantization = quant
        if quant in _BAD_QUANT or (quant != "bitsandbytes" and quant != "unknown"):
            reasons.append(f"Quantization method '{quant}' needs extra runtimes that this platform does not configure.")
        elif quant == "bitsandbytes":
            warnings.append("Pre-quantized with bitsandbytes: requires an NVIDIA GPU runtime (not CPU).")
    remote = bool(cfg.get("auto_map")) or "custom_code" in info.tags
    if remote:
        if settings.allow_remote_code:
            a.trust_remote_code = True
            warnings.append("DANGER: this repo needs trust_remote_code=True, which executes Python from the repository. "
                            "Enabled only because M2G_ALLOW_REMOTE_CODE is set; review the code first and run it ONLY in Colab.")
        else:
            reasons.append("Model requires trust_remote_code=True (custom code in the repo, config.auto_map). "
                           "Blocked by default; enable explicitly with M2G_ALLOW_REMOTE_CODE=1 after reviewing the repo's code.")
    a.ui_hints["weight_format"] = info.weight_inventory()
    if info.has_pickle_weights and not info.has_verified_safetensors:
        # Fact for the generated loader (not a refusal): this revision has no verified safetensors weight set.
        a.ui_hints["safetensors_unavailable"] = True
        _loadable = [n for n in info.file_names() if "pytorch_model" in n.lower().rsplit("/", 1)[-1]
                     and n.lower().endswith((".bin", ".bin.index.json"))]
        if not _loadable:  # from_pretrained() only looks for the standard names; other pickle files cannot be loaded by it, consent or not
            warnings.append("The pickle-based files in this repository (.pt/.pth/.ckpt/other .bin) do not use a standard weight file name "
                            "(pytorch_model*.bin), which from_pretrained() looks for. Loading will probably fail even when pickle "
                            "loading is permitted.")
        if settings.allow_pickle_weights:
            warnings.append("Only pickle-based weights (.bin/.pt) exist; loading them can execute arbitrary code. Enabled by M2G_ALLOW_PICKLE_WEIGHTS.")
            a.use_safetensors = False
        else:
            # Candidate generation is not deserialization. The generated loader keeps the runtime consent boundary
            # (LOAD_SAFETENSORS) and refuses to unpickle unless the person explicitly opts in later. A GitHub repository that
            # only LINKS to this model is stricter: see _check_linked_model, which refuses adoption without verified safetensors.
            if info.safetensors_indexes:
                warnings.append(
                    "A safetensors index exists but its complete shard set could not be verified in this repository; "
                    "pickle-based weights are the only verified format. Unpickling can execute arbitrary code, so the generated app "
                    "refuses to load them by default and loading will fail with a clear explanation. To permit it for a repository you "
                    "have reviewed and trust, set M2G_ALLOW_PICKLE_WEIGHTS=1 when you start the app (a public model is not automatically safe).")
            else:
                warnings.append(
                    "Only pickle-based weights (.bin/.pt/.ckpt) exist; unpickling can execute arbitrary code. The app is still "
                    "generated, but it refuses to load them by default and loading will fail with a clear explanation. To permit it "
                    "for a repository you have reviewed and trust, set M2G_ALLOW_PICKLE_WEIGHTS=1 when you start the app "
                    "(a public model is not automatically safe).")
    elif info.has_verified_safetensors:
        a.use_safetensors = True
    if a.library == "diffusers" and not info.model_index:
        reasons.append("Diffusers task without model_index.json (single-file checkpoint); a diffusers folder layout is required.")
    if info.private:
        reasons.append("Private repositories are not supported.")

    # --- chat / hints
    tc = info.tokenizer_config or {}
    if task == "text-generation" and tc.get("chat_template"):
        a.chat = True
        a.ui_hints["chat"] = True
    a.ui_hints["needs_lang_codes"] = mtype in _LANG_CODE_TYPES
    if task == "translation" and mtype in {"t5", "mt5", "umt5"}:
        a.ui_hints["translation_prefix_hint"] = "T5 models expect a task prefix, e.g. 'translate English to German: ...'"
    a.ui_hints["model_type"] = mtype

    # --- transformers floor from config
    tv = _ver_tuple(str(cfg.get("transformers_version", ""))) if cfg else None
    floor = max(_MIN_TRANSFORMERS, tv) if tv else _MIN_TRANSFORMERS
    a.min_transformers = ".".join(str(x) for x in floor)

    # --- hardware & dependencies
    quant_ok = a.library == "transformers" and task in {"text-generation", "text2text-generation", "summarization", "translation"} and quant is None
    a.hardware = assess_hardware(info, task, quant_ok)
    a.load_in_4bit_recommended = a.hardware.suggest_4bit
    if a.hardware.exceeds_colab:
        warnings.append(colab_capacity_warning(a.hardware, quant_ok))  # advisory only; `a.hardware.exceeds_colab` stays True for callers
    if task in TASK_IO:  # legacy fields: the reusable reference components of the primary task, when one exists
        key = "text-generation:chat" if (task == "text-generation" and a.chat) else task
        a.inputs = [{"name": n, "component": f"gr.{k}"} for n, k in TASK_IO[key]["in"]]
        a.outputs = [{"name": n, "component": f"gr.{k}"} for n, k in TASK_IO[key]["out"]]
    if a.capabilities:
        mods_in = tuple(dict.fromkeys(m for c in iface for m in c.input_modalities))
        mods_out = tuple(dict.fromkeys(m for c in iface for m in c.output_modalities))
        a.dependencies, a.dependency_evidence = _dependencies(info, task, a.library, a.hardware, quant, a.min_transformers,
                                                              mods_in, mods_out, tuple(c.name for c in iface))

    # --- warnings
    if info.gated:
        warnings.append("Gated model: accept its terms on the Hub and provide HF_TOKEN (Colab: Secrets) to download weights.")
    if not info.license:
        warnings.append("No license declared in the model card.")
    if a.capabilities and conf < 0.6:
        warnings.append(f"Low task confidence ({conf}); verify the generated UI matches the model.")
    for c in iface:  # advisories: what the platform cannot vouch for. Never a refusal.
        if c.advisory:
            warnings.append(f"Capability '{c.name}' (confidence {c.confidence}): {c.advisory}")
    for c in a.capabilities:
        if c.role == "co_supported":
            a.uncertainties.append(f"Additional capability '{c.name}' (confidence {c.confidence}) is supported by independent evidence "
                                   "and gets its own interface.")
        elif c.role in ("hint", "conflicting"):
            a.uncertainties.append(f"Possible capability '{c.name}' ({c.role}, confidence {c.confidence}) is not built: the evidence is "
                                   + ("weak or heuristic." if c.role == "hint" else "contradicted by the primary capability."))
    a.ui_hints["interfaces"] = [c.name for c in iface]
    if not cfg and a.library == "transformers":
        warnings.append("config.json could not be read; architecture-specific checks were skipped.")
    for w in info.warnings:
        warnings.append(w)
    if a.hardware.params_source.startswith(("estimated", "rough")):
        warnings.append(f"Parameter count is a {a.hardware.params_source}.")

    a.host = "huggingface"
    a.frameworks = [a.library, "PyTorch"] if a.library != "unknown" else ["PyTorch"]  # generated apps run on PyTorch
    a.input_modalities = list(dict.fromkeys(m for c in iface for m in c.input_modalities))
    a.output_modalities = list(dict.fromkeys(m for c in iface for m in c.output_modalities))
    a.primary_model = info.source.repo_id
    a.uncertainties += [n for n in notes]
    if a.hardware.params_source.startswith(("estimated", "rough")) or not a.hardware.params:
        a.uncertainties.append(f"Parameter count: {a.hardware.params_source}.")
    if task and conf < 0.6:
        a.uncertainties.append(f"Task confidence is low ({conf}).")

    a.status = "unsupported" if reasons else ("supported_with_warnings" if warnings else "supported")
    return a


# --------------------------------------------------------------------------- GitHub analysis

_GH_TASK_KEYWORDS = [
    ("text-to-image", r"text[- ]to[- ]image|stable diffusion|diffusion model"),
    ("automatic-speech-recognition", r"speech recognition|\basr\b|speech[- ]to[- ]text|whisper"),
    ("object-detection", r"object detection|\byolo"),
    ("image-segmentation", r"segmentation"),
    ("image-classification", r"image classification"),
    ("text-generation", r"language model|\bllm\b|chatbot|text generation"),
    ("text-to-speech", r"text[- ]to[- ]speech|\btts\b"),
    ("text-classification", r"text classification|sentiment"),
]
_ENTRY_RE = re.compile(r"^(?:[^/]+/)?(app|main|demo|inference|infer|predict|run|gradio_app|webui|server)\.py$", re.I)
# ML frameworks (package/import name -> label). App frameworks (gradio, flask, ...) are deliberately not listed here.
_FRAMEWORKS = {"torch": "PyTorch", "tensorflow": "TensorFlow", "jax": "JAX", "flax": "Flax", "transformers": "transformers",
               "diffusers": "diffusers", "onnxruntime": "ONNX Runtime", "ultralytics": "Ultralytics YOLO", "keras": "Keras",
               "scikit-learn": "scikit-learn"}
_FRAMEWORK_ALIASES = {"torchvision": "torch", "torchaudio": "torch", "pytorch-lightning": "torch", "lightning": "torch",
                      "pytorch": "torch", "tensorflow-gpu": "tensorflow", "tf-keras": "keras", "sklearn": "scikit-learn",
                      "onnxruntime-gpu": "onnxruntime"}
_LIBRARY_PRIORITY = [("transformers", "transformers"), ("diffusers", "diffusers"), ("PyTorch", "pytorch"),
                     ("TensorFlow", "tensorflow"), ("JAX", "jax"), ("Flax", "jax")]
# Framework labels used by the deep source analysis (its framework names) mapped to the labels the Analyzer already uses.
_FRAMEWORK_LABELS = {"transformers": "transformers", "diffusers": "diffusers", "sentence-transformers": "sentence-transformers",
                     "pytorch": "PyTorch", "tensorflow": "TensorFlow", "onnxruntime": "ONNX Runtime", "peft": "PyTorch",
                     "llama.cpp": "llama.cpp"}
# Static modality hints, matched against class names, imported names, from_pretrained() classes/ids and top-level imports.
_VISION_HINT = re.compile(r"clip|vision|siglip|image|resnet|convnext|swin|deit|dino|torchvision|^pil$|^cv2$|(?:^|[^a-z])vit(?:[^a-z]|$)")
# Library imports alone do not establish a model input: soundfile/librosa/
# torchaudio are routinely used to write or resample generated audio.  Keep
# them as dependency/framework evidence, but require a model/API-shaped name
# before describing the repository's code as accepting audio input.
_AUDIO_HINT = re.compile(r"whisper|wav2vec|hubert|encodec|speech|audio")
_TEXTGEN_HINT = re.compile(r"causallm|lmheadmodel|gpt2|gpt-|llama|mistral|qwen|gemma|(?:^|[^a-z])opt-\d|seq2seq|conditionalgeneration|t5")


def _scan_tokens(scan: List[Dict[str, Any]]) -> List[Tuple[str, str]]:
    toks: List[Tuple[str, str]] = []
    for sc in scan:
        p = sc.get("path", "?")
        for name, bases in (sc.get("classes") or {}).items():
            toks += [(name.lower(), p)] + [(b.lower(), p) for b in bases]
        toks += [(str(i).lower(), p) for i in sc.get("imports") or []]
        toks += [(str(n).split(":")[-1].lower(), p) for n in sc.get("imported_names") or []]
        toks += [(str(c).lower(), p) for c in sc.get("pretrained_classes") or []]
        toks += [(str(i).lower(), p) for i in sc.get("pretrained_ids") or []]
    return toks


def infer_modalities(scan: List[Dict[str, Any]]) -> Tuple[List[str], List[str], List[str]]:
    """Static input/output modalities from ast facts only (nothing executed). Returns (inputs, outputs, evidence strings)."""
    ins: List[str] = []
    outs: List[str] = []
    hints: List[str] = []
    toks = _scan_tokens(scan)
    for label, rx in (("image", _VISION_HINT), ("audio", _AUDIO_HINT)):
        hit = next(((t, p) for t, p in toks if rx.search(t)), None)
        if hit:
            ins.append(label)
            hints.append(f"{label} input suggested by '{hit[0]}' in {hit[1]}")
    hit = next(((t, p) for t, p in toks if _TEXTGEN_HINT.search(t)), None)
    tok_hit = any("tokenizer" in t for t, _ in toks)
    if hit:
        outs.append("text")
        hints.append(f"text generation suggested by '{hit[0]}' in {hit[1]}")
    if hit or tok_hit:
        ins.append("text")
    return ins, outs, hints


def _task_from_modalities(ins: List[str], outs: List[str]) -> Optional[str]:
    if "text" in outs and "image" in ins:
        return "image-text-to-text" if "text" in ins else "image-to-text"
    if "text" in outs and "audio" in ins and "image" not in ins:
        return "automatic-speech-recognition"
    return None


def _detect_frameworks(names: set, scan: List[Dict[str, Any]]) -> Tuple[List[str], Dict[str, str]]:
    found: Dict[str, str] = {}
    for n in sorted(names):
        key = _FRAMEWORK_ALIASES.get(n, n)
        if key in _FRAMEWORKS and _FRAMEWORKS[key] not in found:
            found[_FRAMEWORKS[key]] = f"declared dependency '{n}'"
    for sc in scan:
        for imp in sc.get("imports") or []:
            key = _FRAMEWORK_ALIASES.get(str(imp).lower(), str(imp).lower())
            if key in _FRAMEWORKS and _FRAMEWORKS[key] not in found:
                found[_FRAMEWORKS[key]] = f"imported in {sc.get('path', '?')}"
    return list(found), found


def _library_for(frameworks: List[str]) -> str:
    for label, lib in _LIBRARY_PRIORITY:
        if label in frameworks:
            return lib
    return "unknown"


def _sub_input_modalities(sub: Analysis) -> List[str]:
    """Input modalities of every interface capability of an analysed model (falls back to its legacy task)."""
    mods = [m for c in effective_capabilities(sub) if c.builds_interface for m in c.input_modalities]
    return mods or modalities_for_task(sub.task)[0]


def _check_linked_model(info: RepoInfo, sub: Optional[Analysis], scan_inputs: List[str]) -> Tuple[bool, List[str]]:
    """May the repository's own linked Hugging Face model be used to generate an app? Returns (usable, reasons it is not).

    Everything here works on metadata that was already read through the allow-listed Hub inspector; nothing is fetched,
    imported or executed. On top of the normal Hugging Face verdict (``sub.generatable``) a GitHub repository must also
    have a firmly established task and must not contradict the linked model with what its own source code takes as input.
    """
    ref = info.primary_model_info.source if info.primary_model_info is not None else None
    if sub is None or ref is None or not info.primary_hf_model:
        return False, []  # no identified/readable linked model: the existing 'partial' wording already explains this
    pid = info.primary_hf_model
    url = f"https://huggingface.co/{pid}"
    try:
        validate_repo_id(ref.repo_id)
    except SecurityError:
        return False, ["The linked model id is not a valid Hugging Face repository id."]
    if ref.kind != "huggingface" or ref.repo_id != pid or sub.source.repo_id != pid:
        return False, ["The linked model metadata does not belong to the identified Hugging Face repository."]
    if (info.hf_link_roles.get(pid) or {}).get("role") != "primary":
        return False, [f"{url} was not confirmed as the repository's own model."]
    head = f"The repository's own Hugging Face model appears to be {url}, but "
    if sub.generatable and sub.ui_hints.get("safetensors_unavailable") and sub.use_safetensors:
        # Stricter than a direct Hugging Face URL: that app may be generated with pickle loading disabled at runtime, but a GitHub
        # repository is only trusted through a model whose weights can be loaded without unpickling. This is a property of the
        # linked model's weight files (model-side, fixable by the author or by explicit consent), not a platform fault.
        return False, [head + "it cannot be generated through a GitHub repository: its repository lists only pickle-based weights "
                              "(.bin/.pt/.ckpt) and no verified safetensors weights, and unpickling can execute arbitrary code. "
                              f"To use it anyway, review it and analyse {url} directly with pickle loading explicitly allowed."]
    if not sub.generatable:
        blocked = [*sub.reasons, *sub.generation_blocked_reasons]
        why = "; ".join(r.rstrip(". ") for r in blocked[:5]) or f"status '{sub.status}'"
        if len(blocked) > 5:
            why += f" (+{len(blocked) - 5} more)"
        return False, [head + f"it cannot be generated (its own analysis is '{sub.status}'): {why}"]
    if not sub.revision:
        return False, [head + "it has no pinned commit revision, so the generated app would not be reproducible."]
    if sub.task_confidence < _MIN_LINKED_TASK_CONFIDENCE:
        return False, [head + f"its task ({sub.task}, confidence {sub.task_confidence}) is not established firmly enough to "
                              f"generate an app from a GitHub repository. If you are sure it is the right model, analyse {url} directly."]
    extra = sorted(m for m in set(scan_inputs) if m != "text" and m not in _sub_input_modalities(sub))
    if extra:
        return False, [head + f"the repository's code takes {'/'.join(extra)} input, which that model's task '{sub.task}' does not. "
                              "The model may only be one component of a larger system, so it is not used as the repository's model."]
    return True, []


def _adopt_linked_model(a: Analysis, info: RepoInfo, sub: Analysis) -> None:
    """Make a GitHub analysis generatable THROUGH its linked Hugging Face model, reusing that model's own analysis.

    Task, I/O, dependencies, hardware, quantization and safety flags all come from the Hugging Face analysis (so they pass
    the same checks as a Hugging Face URL). The GitHub repository's own requirements are deliberately NOT adopted: they are
    unreviewed and are never installed.
    """
    a.model_source = info.primary_model_info.source
    a.origin = "github-linked-hf"
    a.revision = sub.revision  # a Hugging Face commit sha, never the GitHub branch name
    a.task, a.task_confidence = sub.task, sub.task_confidence
    a.capabilities = [Capability(**{**asdict(c)}) for c in effective_capabilities(sub)]
    a.model_evidence = dict(sub.model_evidence)
    a.input_modalities, a.output_modalities = list(sub.input_modalities), list(sub.output_modalities)
    a.library = sub.library
    a.inputs, a.outputs = list(sub.inputs), list(sub.outputs)
    a.dependencies, a.dependency_evidence = list(sub.dependencies), dict(sub.dependency_evidence)
    a.hardware = sub.hardware
    a.chat, a.trust_remote_code, a.quantization = sub.chat, sub.trust_remote_code, sub.quantization
    a.load_in_4bit_recommended, a.gated, a.use_safetensors = sub.load_in_4bit_recommended, sub.gated, sub.use_safetensors
    a.generation_blocked_reasons = list(sub.generation_blocked_reasons)
    a.min_transformers = sub.min_transformers
    a.ui_hints.update(sub.ui_hints)
    a.ui_hints["github_license"] = info.license
    a.license = sub.license  # the generated app is for the model, so it carries the model's licence
    a.frameworks = list(dict.fromkeys([*sub.frameworks, *a.frameworks]))
    # Everything that described the GitHub-only (refused) situation is dropped, so a successful adoption carries no stale text:
    # no refusal reasons, no competing-task note about the task that was just replaced, and only evidence for the chosen task.
    a.reasons = []
    a.task_evidence = [e for e in a.task_evidence if e.source.startswith("linked model")]
    a.uncertainties = list(dict.fromkeys(
        [u for u in a.uncertainties if not u.startswith(("Competing task evidence", "README keywords"))] + sub.uncertainties))
    a.warnings = list(dict.fromkeys([
        f"Generated from linked Hugging Face model '{info.primary_hf_model}', found in the repository's README/code: verify it is "
        "the model you want. GitHub code and requirements are not run, imported or installed.",
        *sub.warnings]))
    a.status = "supported_with_warnings"


_GH_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")


def _model_like_config(cfg: Any) -> bool:
    """A parsed JSON file that really describes a model (declares an architecture or model_type), not just any config.json."""
    return isinstance(cfg, dict) and any(k in cfg for k in ("architectures", "model_type"))


def _github_native_evidence(info: RepoInfo, a: Analysis) -> Dict[str, Any]:
    """Decide from metadata the inspector already read (nothing fetched, imported or executed) whether a GitHub repository is an AI model
    repository that stands on its own evidence, and record what is confirmed, inferred and unknown.

    Eligible only with a MODEL ARTIFACT (a weight file in the tree or in release assets, or a model-describing config) together with a
    second independent signal (a detected ML framework, or both weights and config). A public repository, README keywords, a task guess
    or an entry-point name never qualify on their own. Missing evidence is reported as unknown, never as proof of anything."""
    def weights(names: Any) -> List[str]:
        return [_plain(n, 160) for n in names if str(n).lower().endswith(WEIGHT_EXTS)][:40]

    tree_w = weights(n for n, _ in info.files)
    rel_w = weights(n for n, _ in info.release_assets)
    allw = tree_w + rel_w
    pickle_w = [n for n in allw if n.lower().endswith(PICKLE_EXTS)]
    safe_w = [n for n in allw if not n.lower().endswith(PICKLE_EXTS)]
    paths = info.config_paths or {}
    cfgs: List[str] = []
    if _model_like_config(info.config):
        cfgs.append(_plain(paths.get("config", "config.json"), 160))
    if isinstance(info.model_index, dict) and "_class_name" in info.model_index:
        cfgs.append(_plain(paths.get("model_index", "model_index.json"), 160))
    fw = list(a.frameworks)
    # Many native repositories use architecture YAML rather than config.json.  A path is
    # structural evidence only; it never authorises loading repository code or weights.
    architecture_files = [_plain(p, 160) for p, _ in info.files
                          if re.search(r"(^|/)[^/]*(?:model|arch|network|config)[^/]*\.(?:ya?ml|json)$", p, re.I)][:20]
    model_defs = [_plain(p, 160) for p, _ in info.files
                  if re.search(r"(^|/)(?:models?/.*|.*(?:model|network).*)\.py$", p, re.I)][:20]
    contract_paths = [_plain(p, 160) for p, _ in info.files
                      if re.search(r"(^|/)(detect|predict|inference|hubconf)\.py$", p, re.I)][:12]
    artifact = bool(allw or cfgs or architecture_files)
    eligible = artifact and bool(fw or (allw and cfgs) or (architecture_files and model_defs))
    signals = [s for s, ok in (("ml-framework", bool(fw)), ("weight-files", bool(allw)), ("model-config", bool(cfgs)),
                               ("architecture-config", bool(architecture_files)), ("model-definition", bool(model_defs))) if ok]

    confirmed: List[str] = []
    if fw:
        confirmed.append("ML framework evidence: " + ", ".join(_plain(f, 40) for f in fw[:6]))
    if allw:
        confirmed.append(f"{len(allw)} weight file(s) listed in the repository tree or release assets (metadata only; nothing downloaded)")
    if cfgs:
        confirmed.append("model config file(s): " + ", ".join(cfgs))
    if architecture_files:
        confirmed.append("architecture configuration file(s): " + ", ".join(architecture_files[:5]))
    if contract_paths:
        confirmed.append("static inference entry-point candidate(s): " + ", ".join(contract_paths[:5]))
    archs = info.config.get("architectures") if isinstance(info.config, dict) else None
    if isinstance(archs, list) and archs:
        confirmed.append("declared architecture(s): " + ", ".join(_plain(x, 60) for x in archs[:4]))
    if _pinned_commit(info):
        confirmed.append("files were read at a pinned commit")
    inferred: List[str] = []
    if a.task:
        tiers = sorted({e.tier for e in a.task_evidence if e.task == a.task}) or ["unknown"]
        inferred.append(f"task '{a.task}' (confidence {a.task_confidence}; evidence tiers: {', '.join(tiers)}); not a verified inference contract")
    if a.input_modalities or a.output_modalities:
        inferred.append(f"modalities in={a.input_modalities or '?'} out={a.output_modalities or '?'} from static source/metadata")
    unknown = ["inference API and input/output contract of the repository's own code (its code is never run)",
               "how the weight files map to a loadable model class",
               "GPU/VRAM requirements (weights are not downloaded)"]
    if not (isinstance(archs, list) and archs):
        unknown.insert(0, "model architecture (no config declares one)")
    if not a.task:
        unknown.append("task")
    missing = []
    if not eligible:
        if not artifact:
            missing.append("no model artifact (weight file in the tree or releases, or a model-describing config)")
        else:
            missing.append("no ML framework detected in dependencies or scanned imports, and weights and a model config are not both present")
    return {
        "eligible": eligible, "scaffold_only": True, "signals": signals, "missing": missing,
        "confirmed": confirmed, "inferred": inferred, "unknown": unknown,
        "weight_files": tree_w[:20], "release_weight_assets": rel_w[:20], "model_config_files": cfgs,
        "architecture_files": architecture_files, "model_definition_files": model_defs, "inference_entrypoints": contract_paths,
        "pickle_weights": pickle_w[:20], "safe_weights": safe_w[:20], "frameworks": fw[:8],
        "entrypoints": [_plain(p, 120) for p in a.entrypoint_candidates[:8]],
        "linked_hf_models": [_plain(m, 120) for m in info.linked_hf_models[:5]],
        "task": a.task, "task_confidence": a.task_confidence,
    }


def _adopt_github_native(a: Analysis, native: Dict[str, Any], linked_why: List[str]) -> None:
    """Mark an eligible independent GitHub model repository as generatable AS A SCAFFOLD. No Hugging Face model is needed or invented
    (``model_source`` stays None), no task/IO contract is asserted beyond the evidence, and the repository's code and requirements are
    never run, imported or installed."""
    a.origin, a.native = "github-native", native
    native["readiness"] = "scaffold"
    native["readiness_reason"] = ("The repository source and weights are not executed or deserialised during inspection; "
                                  "a safe loading contract was not verified.")
    # Documentation is treated as contract evidence only when it explicitly
    # labels both an input and an output.  Keywords and Python variable names
    # remain task hints, never tensor/semantic contracts.
    documented = _documented_native_contract(a, native)
    if documented["inputs"] and documented["outputs"]:
        a.input_modalities, a.output_modalities = documented["inputs"], documented["outputs"]
    contract = build_contract(frameworks=a.frameworks, task=a.task, inputs=a.input_modalities,
                              outputs=a.output_modalities, native=native, revision=a.revision)
    contract["preprocessing"] = documented["preprocessing"]
    contract["postprocessing"] = documented["postprocessing"]
    contract["documented_tensors"] = documented["tensors"]  # documentation only: not verified against the artifact, never used as the contract
    contract["evidence"] += documented["evidence"]
    loader = select_loader(contract)
    native["inference_contract"] = contract
    native["approved_loader"] = loader.name if loader else None
    # These are deliberately separate from runtime_validation.  They record
    # whether static repository evidence is strong enough to ask OpenCode for
    # a candidate implementation; they never assert that an artifact was
    # downloaded, authenticated, loaded, or executed.
    onnx_candidates = [p for p in native.get("safe_weights", []) if p.lower().endswith(".onnx")]
    generation = {
        "artifact_path": onnx_candidates[0] if len(onnx_candidates) == 1 else None,
        "revision": a.revision,
        "adapter_verified": bool(loader),
        "contract_verified": bool(contract.get("inputs") and contract.get("outputs")),
        # The current bounded scanner records names/imports, not semantic
        # transforms.  Do not promote a function called "preprocess" into a
        # verified transform without its declared contract.
        "preprocessing_evidence": bool(documented["preprocessing"]),
        "postprocessing_evidence": bool(documented["postprocessing"]),
        "candidate_count": len(onnx_candidates),
        # Bounded identity of every static ONNX candidate (never downloaded): lets a person choose explicitly when several exist.
        "onnx_candidates": onnx_candidates[:10],
        "artifact_selection": "unique" if len(onnx_candidates) == 1 else ("ambiguous" if onnx_candidates else "none"),
        "documented_tensor_count": len(documented["tensors"]),
    }
    native["generation_evidence"] = generation
    absent = [k for k in ("artifact_path", "revision", "adapter_verified", "contract_verified",
                          "preprocessing_evidence", "postprocessing_evidence") if not generation.get(k)]
    native["generation_reason"] = ("Native OpenCode generation is pending static evidence: " + ", ".join(absent)
                                   if absent else "Static native code-generation evidence is complete; artifact acquisition remains separate.")
    a.model_source = None
    a.status = "supported_with_warnings"
    a.reasons = []
    a.warnings = list(dict.fromkeys([
        "Independent GitHub model repository (no Hugging Face model is used). Only safe scaffolding is generated until its static inference "
        "contract and preprocessing/postprocessing evidence are established. Repository code is never run or installed.",
        *linked_why, *a.warnings]))
    if native["pickle_weights"] and not native["safe_weights"]:
        a.warnings.append("Only pickle-format weights were found (" + ", ".join(native["pickle_weights"][:3]) + "); they are never loaded without "
                          "explicit consent and are not treated as safe.")
    a.uncertainties = list(dict.fromkeys([*a.uncertainties, *("Unknown: " + u for u in native["unknown"])]))


_DOC_IO = re.compile(r"(?im)^\s*(?:input|inputs)\s*[:=-]\s*([^\n]{1,160})$.*?^\s*(?:output|outputs)\s*[:=-]\s*([^\n]{1,160})$", re.S)
_DOC_PRE = re.compile(r"(?im)^\s*(?:#+\s*)?preprocessing\s*[:\n]\s*([^\n]{3,240})")
_DOC_POST = re.compile(r"(?im)^\s*(?:#+\s*)?postprocessing\s*[:\n]\s*([^\n]{3,240})")
# Explicitly labelled tensor declarations, e.g. ``Input name: images`` / ``Output name: logits, shape: [1, 1000]``.  Recorded as DOCUMENTED
# (unverified) facts with provenance; they never replace the artifact's own metadata, which needs authorization to read.
_DOC_TENSOR = re.compile(r"(?im)^\s*[-*]?\s*(input|output)\s+(?:tensor\s+)?name\s*[:=]\s*`?([A-Za-z_][\w./:-]{0,63})`?"
                         r"(?:[^\n]{0,40}?shape\s*[:=]\s*`?(\[[0-9A-Za-z_,\s*?-]{1,60}\])`?)?")


def _documented_native_contract(a: Analysis, native: Dict[str, Any]) -> Dict[str, Any]:
    """Read only explicit labelled documentation facts already fetched by the inspector.

    This intentionally recognises modalities, not tensor names/shapes/dtypes.  Those
    require artifact metadata after explicit authorization.
    """
    text = "\n".join(str(x) for x in (a.ui_hints.get("native_documentation") or []))
    # Inspector-owned README is attached by analyze_github below; hand-built
    # Analysis values have no documentation and therefore remain unknown.
    result = {"inputs": [], "outputs": [], "preprocessing": [], "postprocessing": [], "evidence": [], "tensors": []}
    seen_t: set = set()
    for tm in _DOC_TENSOR.finditer(text):
        key = (tm.group(1).lower(), tm.group(2))
        if key in seen_t or len(result["tensors"]) >= 16:
            continue
        seen_t.add(key)
        result["tensors"].append({"direction": key[0], "name": tm.group(2), "shape_text": tm.group(3), "verified": False,
                                  "source": "README", "revision": a.revision})
    if result["tensors"]:
        result["evidence"].append({"type": "documented_tensors", "source": "README", "revision": a.revision,
                                   "method": "explicit_labelled_documentation", "confidence": "documented_unverified"})
    for match in _DOC_IO.finditer(text):
        low_in, low_out = match.group(1).lower(), match.group(2).lower()
        for word, modality in (("text", "text"), ("image", "image"), ("audio", "audio")):
            if word in low_in and modality not in result["inputs"]: result["inputs"].append(modality)
            if word in low_out and modality not in result["outputs"]: result["outputs"].append(modality)
        if result["inputs"] and result["outputs"]:
            result["evidence"].append({"type": "documented_io", "source": "README", "revision": a.revision,
                                       "method": "explicit_labelled_documentation", "confidence": "structural"})
            break
    for rx, key, kind in ((_DOC_PRE, "preprocessing", "documented_preprocessing"), (_DOC_POST, "postprocessing", "documented_postprocessing")):
        m = rx.search(text)
        if m:
            result[key].append(m.group(1).strip())
            result["evidence"].append({"type": kind, "source": "README", "revision": a.revision,
                                       "method": "explicit_labelled_documentation", "confidence": "structural"})
    return result


def _pinned_commit(info: RepoInfo) -> Optional[str]:
    """The 40-character commit id the inspector read the GitHub repository at, or None. Only a real commit id counts: a branch or tag
    name found in ``commit_sha`` is never taken for a pinned commit."""
    sha = getattr(info, "commit_sha", None)
    return sha if isinstance(sha, str) and _GH_COMMIT_RE.fullmatch(sha) else None


def _plain(value: Any, limit: int) -> str:
    """Repository-derived text as one short, single-line, secret-free string."""
    return redact_secrets(re.sub(r"[\x00-\x1f\x7f]", " ", str(value)))[:limit]


def _github_provenance(info: RepoInfo, commit: Optional[str]) -> Dict[str, Any]:
    """Where a GitHub analysis came from, as plain bounded data for ``Analysis.ui_hints['github_provenance']``: the repository identity
    (as given and as GitHub reports it), the revision asked for and its kind, the commit it was resolved to (None when it was not),
    what could not be read, and where each linked Hugging Face model reference was found. Everything is copied from what the inspector
    recorded; nothing is fetched or decided here."""
    gaps = [{"code": _plain(g.get("code", ""), 60), "message": _plain(g.get("message", ""), 240)}
            for g in (getattr(info, "inspection_gaps", None) or [])[:12] if isinstance(g, dict)]
    sources: Dict[str, List[str]] = {}
    for rid, paths in list((getattr(info, "hf_link_sources", None) or {}).items())[:10]:
        if isinstance(paths, (list, tuple)):
            sources[_plain(rid, 120)] = [_plain(p, 160) for p in paths[:6]]
    return {
        "repo_id": info.source.repo_id,
        "canonical_repo_id": info.canonical_repo_id or info.source.repo_id,
        "requested_revision": info.requested_revision,
        "revision_kind": info.revision_kind,
        "default_branch": _plain(info.default_branch, 100) if info.default_branch else None,
        "commit_sha": commit,
        "revision_pinned": commit is not None,
        "inspection_gaps": gaps,
        "hf_link_sources": sources,
    }


def _gap_uncertainties(gaps: List[Dict[str, str]]) -> List[str]:
    """One uncertainty per inspection gap, with its code and its original message. A gap means the content is UNKNOWN; it is reported,
    never turned into a refusal reason or a generation blocker (those come only from the checks that already exist)."""
    return [f"Inspection gap '{g['code']}': {g['message'].rstrip('. ') or 'part of the repository could not be read'}. "
            "What it would show is unknown and is not assumed." for g in gaps]


def _apply_source_analysis(a: Analysis, info: RepoInfo, fw_evidence: Dict[str, str]) -> None:
    """Fold the deep repository-wide source analysis (``info.source_analysis``) into the analysis.

    This only ever ADDS evidence: framework observations from real loading/inference calls, loading sites, entry points, the
    static import/call graphs and an evidence-backed I/O contract digest go into ``a.ui_hints['source_analysis']``. Coverage
    gaps and unresolved imports become uncertainties. Nothing here is executed, imported or assumed: when the analysis did not
    run (Hugging Face repos, or a fetch that produced no modules) the function is a no-op."""
    src = getattr(info, "source_analysis", None)
    if src is None:
        return
    digest = src.to_dict()
    # Keep the digest bounded: the full graphs can be large and the analysis.json must stay readable.
    digest["call_edges"] = digest["call_edges"][:80]
    digest["import_edges"] = digest["import_edges"][:80]
    a.ui_hints["source_analysis"] = digest

    # Frameworks the source really loads/invokes, with their own provenance.
    for fw in src.frameworks:
        if fw in a.frameworks:
            continue
        a.frameworks.append(fw)
    for s in src.load_sites:
        label = _FRAMEWORK_LABELS.get(s.framework, s.framework)
        if label not in fw_evidence:
            fw_evidence[label] = f"loading call {s.call} in {s.module}:{s.line}"

    # Input/output modalities the code itself establishes (preprocessing calls, inference signatures).
    io = src.io_contract or {}
    if io.get("inputs"):
        mods = sorted({i["modality"] for i in io["inputs"] if i.get("modality")})
        if mods and not a.input_modalities:
            a.input_modalities = mods
    if io.get("outputs"):
        mods = sorted({o["kind"] for o in io["outputs"] if o.get("kind")})
        if mods and not a.output_modalities:
            a.output_modalities = mods

    cov = src.coverage or {}
    if cov and not cov.get("complete", True):
        a.uncertainties.append(f"Source analysis covered {cov.get('modules_analysed', 0)} of {cov.get('modules_listed', 0)} listed "
                               f"module(s); {cov.get('modules_not_analysed', 0)} were not analysed (budget or unreadable).")
    for u in src.unresolved_imports[:4]:
        a.uncertainties.append(f"Unresolved import: {u}")
    if src.skipped:
        a.uncertainties.append(f"{len(src.skipped)} module(s) could not be parsed statically (syntax errors or size); their facts are unknown.")


def analyze_github(info: RepoInfo, settings: Optional[Settings] = None) -> Analysis:
    """Evidence-ranked analysis of a GitHub repo. GitHub is the HOST; the ML framework is detected separately.

    Evidence tiers: (1) the repo's own linked Hugging Face model, analysed from Hub metadata; (2) static structure of a few
    source files (ast only) and declared dependencies; (3) README keywords, which can never raise confidence above 0.35.
    Hardware needs are only reported when real model metadata exists; otherwise they are explicitly unknown.

    ``revision`` is the commit the GitHub files were read at when the inspector pinned one, otherwise the branch it read (never
    presented as a commit: ``ui_hints['github_provenance']['revision_pinned']`` says which). When the app is generated through the
    repository's linked Hugging Face model, ``revision`` is that model's commit instead (it is what the app loads) and the GitHub
    commit stays in ``ui_hints['github_provenance']``.
    """
    settings = settings or Settings()
    commit = _pinned_commit(info)
    a = Analysis(source=info.source, revision=commit or info.default_branch or info.source.revision, license=info.license, host="github")
    unc = a.uncertainties

    # --- dependencies (static parse only) and frameworks
    names = {r["name"] for r in info.requirements}
    a.dependencies = sorted(names)
    a.repo_requirements = [r["spec"] for r in info.requirements]
    a.requirement_flags = list(info.requirement_flags)
    a.dependency_evidence = {r["name"]: f"declared in {r.get('source', 'requirements')}" for r in info.requirements}
    a.frameworks, fw_evidence = _detect_frameworks(names, info.source_scan)
    _apply_source_analysis(a, info, fw_evidence)
    a.library = _library_for(a.frameworks)
    a.ui_hints["framework_evidence"] = fw_evidence
    a.linked_hf_models = list(info.linked_hf_models)
    a.primary_model = info.primary_hf_model
    a.ui_hints["hf_link_roles"] = {k: dict(v) for k, v in info.hf_link_roles.items()}
    provenance = _github_provenance(info, commit)
    a.ui_hints["github_provenance"] = provenance
    # README was fetched as bounded metadata by inspector; it is data only and
    # is never executed.  Keep it separate so native contract extraction can
    # accept only explicitly labelled documentation.
    a.ui_hints["native_documentation"] = [info.readme] if info.readme else []
    a.entrypoint_candidates = sorted({p for p, _ in info.files if _ENTRY_RE.match(p)})[:15]

    # --- task evidence
    cands: Dict[str, float] = {}
    sub: Optional[Analysis] = None
    if info.primary_model_info is not None:
        sub = analyze_hf(info.primary_model_info, settings)
        a.ui_hints["primary_model_status"] = sub.status
        a.ui_hints["primary_model_reasons"] = list(sub.reasons[:5])
        if sub.task:
            cands[sub.task] = cands.get(sub.task, 0.0) + sub.task_confidence
            for e in sub.task_evidence:
                a.task_evidence.append(Evidence(e.signal, f"linked model {info.primary_hf_model}: {e.source}", e.detail, e.weight, e.tier, e.task))
            seen_ev = {(e.signal, e.detail, e.task) for e in sub.task_evidence}
            for c in sub.capabilities:  # reuse the linked model's other capability evidence (config-level signals)
                for ed in c.evidence:
                    t, key = ed.get("task") or "", (ed.get("signal"), ed.get("detail"), ed.get("task") or "")
                    if t and key not in seen_ev:
                        seen_ev.add(key)
                        a.task_evidence.append(Evidence(str(ed.get("signal", "")), f"linked model {info.primary_hf_model}: {ed.get('source', '')}",
                                                        str(ed.get("detail", "")), float(ed.get("weight", 0.0)), str(ed.get("tier", "structural")), t))
    elif info.primary_hf_model:
        unc.append(f"Metadata for the linked model '{info.primary_hf_model}' could not be read, so its task and size are unknown.")
    else:
        unc.append("No primary Hugging Face model could be identified from the README or code; the task, where established, comes from "
                   "the repository's own declared configuration, its text and its source structure.")

    ins, outs, hints = infer_modalities(info.source_scan)
    scan_ins = list(ins)  # what the repository's OWN code takes as input, before declared model metadata overrides it
    if sub is not None and sub.task:
        ins, outs = modalities_for_task(sub.task)  # declared metadata beats inference
    elif hints:
        mt = _task_from_modalities(ins, outs)
        if mt:
            cands[mt] = cands.get(mt, 0.0) + 0.6
            a.task_evidence.append(Evidence("source_modalities", "static scan of " + ", ".join(sc["path"] for sc in info.source_scan[:4]),
                                            "; ".join(hints[:3]), 0.6, "structural", mt))
    a.input_modalities, a.output_modalities = ins, outs

    # The repository's OWN config.json (read as bounded metadata by the inspector) may declare architectures / model_type. That is
    # structural, not keyword, evidence: use it for the native task the same way a linked Hugging Face model's config would be used,
    # and let a recognised declaration set the modalities. A generic or unrecognised head adds nothing (never guessed from the name).
    cfg_own = info.config if isinstance(info.config, dict) else {}
    own_arch_tasks: List[str] = []
    if cfg_own and sub is None:
        mtype = str(cfg_own.get("model_type", ""))
        for i, arch in enumerate((cfg_own.get("architectures") or [])[:3]):
            t = _arch_task(str(arch), mtype, cfg_own)
            if t and t not in own_arch_tasks:
                own_arch_tasks.append(t)
                w = 0.4 if t == "feature-extraction" else 0.7
                sig = GENERIC_ARCH_SIGNAL if _is_generic_arch(str(arch), t) else "architecture"
                cands[t] = cands.get(t, 0.0) + w
                a.task_evidence.append(Evidence(sig, "repository config.json",
                                                f"architectures[{i}]={arch} (model_type={mtype or '?'})", w, "structural", t))
        if own_arch_tasks:
            a.ui_hints["model_type"] = mtype or None
            a.ui_hints["own_architecture_tasks"] = own_arch_tasks
            a.ui_hints["declared_architectures"] = [str(x) for x in (cfg_own.get("architectures") or [])[:3]]
            if not (sub is not None and sub.task):
                ins, outs = modalities_for_task(own_arch_tasks[0])
                a.input_modalities, a.output_modalities = ins, outs

    text = " ".join([info.description or "", " ".join(info.topics), " ".join(info.package_keywords), info.readme or ""]).lower()
    # A repository-level AST scan says that *some* code mentions or accepts a
    # modality.  It does not establish the primary model task: demos commonly
    # include audio playback, conversion helpers, or an auxiliary endpoint.
    # Keep keyword evidence in that situation and record the disagreement for
    # ranking/UI instead of silently erasing it.  Exact preprocessing and I/O
    # remain unknown until structural metadata or an authorised runtime check
    # proves them.
    non_text_in = {m for m in ins if m != "text"}
    for task, pat in _GH_TASK_KEYWORDS:
        hits = len(re.findall(pat, text))
        if not hits:
            continue
        task_in = set(modalities_for_task(task)[0])
        if non_text_in and not (non_text_in & task_in):
            unc.append(f"README task evidence for '{task}' ({hits} matches) conflicts with source modality hints "
                       f"({'/'.join(sorted(non_text_in))}). Both were retained; source hints may describe an auxiliary function, "
                       "not the model's primary inference contract.")
        w = min(0.3, 0.1 + 0.03 * hits)
        cands[task] = cands.get(task, 0.0) + w
        a.task_evidence.append(Evidence("readme_keyword", "README/description/topics", f"{hits} match(es) for /{pat}/", round(w, 2), "heuristic", task))

    if cands:
        ranked = sorted(cands.items(), key=lambda kv: -kv[1])
        top, score = ranked[0]
        conf = min(1.0, score)
        if all(e.tier == "heuristic" for e in a.task_evidence):
            conf = min(conf, 0.35)  # keywords alone are never enough
        conflicts = [(t, s) for t, s in ranked[1:] if s >= 0.3 and frozenset((top, t)) not in _EQUIV]
        if conflicts:
            unc.append(f"Competing task evidence: '{top}' (score {score:.2f}) vs '{conflicts[0][0]}' ({conflicts[0][1]:.2f}); using '{top}'.")
            conf *= 0.7
        a.task, a.task_confidence = top, round(conf, 2)
        a.capabilities = build_capabilities(a.task_evidence, top, a.task_confidence, equiv=_EQUIV, modalities=modalities_for_task,
                                            reference_tasks=TASK_IO)
    else:
        unc.append("No task evidence found in the README, description, topics, dependencies or scanned source.")

    # --- hardware: only from real model metadata; never assumed
    if sub is not None and sub.hardware.params:
        a.hardware = assess_hardware(info.primary_model_info, a.task, False)
        a.hardware.notes.insert(0, f"Estimated from the Hugging Face metadata of the linked model '{info.primary_hf_model}'.")
    else:
        a.hardware = HardwareAssessment(
            recommended_runtime="Unknown (not stated in repository metadata)",
            notes=["Model size and GPU needs cannot be determined from a GitHub repository without downloading weights."])
        unc.append("GPU/VRAM requirements are not established; none is assumed.")

    unc += _gap_uncertainties(provenance["inspection_gaps"])  # before adoption, which keeps the uncertainties it finds here

    # --- status and reasons
    # A GitHub repository is never run or wrapped. It yields an app ONLY through its own linked Hugging Face model, and only when
    # that model is identified, readable and passes the complete Hugging Face analysis plus the extra GitHub guards.
    usable, linked_why = _check_linked_model(info, sub, scan_ins)
    if usable:
        _adopt_linked_model(a, info, sub)
    else:
        native = _github_native_evidence(info, a)
        a.native = native  # kept for audit even when not eligible (then ``eligible`` is False and ``missing`` says why)
        if native["eligible"]:
            # Independent GitHub model: a linked Hugging Face model is optional. Why a linked model was NOT used (if one exists) is only a warning.
            note = list(linked_why)
            if a.primary_model and sub is None:
                note.append(f"The linked model https://huggingface.co/{a.primary_model} could not be read and was not used.")
            _adopt_github_native(a, native, note)
        else:
            a.status = "partial"
            a.reasons = ["Arbitrary GitHub code is never executed or auto-wrapped by this platform, so a Gradio UI is not generated from "
                         "the GitHub repository itself.",
                         "The repository does not show enough evidence of being an AI model on its own: " + "; ".join(native["missing"]) + "."]
            if a.primary_model and sub is None:  # identified, but its metadata could not be read
                a.reasons.append(f"The repository's own Hugging Face model appears to be https://huggingface.co/{a.primary_model} - "
                                 "analyse that URL to generate a UI.")
            elif a.primary_model:  # identified and read, but not usable: the reason names the model and says exactly why
                a.reasons += linked_why
            elif a.linked_hf_models:
                a.reasons.append("Hugging Face models are referenced but none could be confirmed as the repository's own: "
                                 + ", ".join(f"https://huggingface.co/{m}" for m in a.linked_hf_models[:5]))
    others = [m for m in a.linked_hf_models if m != a.primary_model]
    if a.primary_model and others:
        a.warnings.append("Other referenced Hugging Face models (backbones/dependencies, not analysed as the repo's own): "
                          + ", ".join(others[:5]))
    if "gradio" in names:
        a.warnings.append("The repository already depends on Gradio; its own app entry point may run directly in Colab.")
    if not usable and a.requirement_flags:
        a.warnings.append("requirements.txt contains risky entries (" + "; ".join(a.requirement_flags) + "); automatic install is disabled by default in the notebook.")
    if not usable and not a.repo_requirements:
        a.warnings.append("No requirements.txt/pyproject/setup.py dependencies could be read.")
    a.warnings += info.warnings
    if not info.license and not usable:
        a.warnings.append("No license detected.")
    a.warnings = list(dict.fromkeys(a.warnings))
    a.reasons = list(dict.fromkeys(a.reasons))
    return a


def _apply_rea(a: Analysis, info: RepoInfo) -> None:
    """Fold the REA inspection result into the analysis: keep it as data, surface problems, cross-check the parameter count.
    It never changes the task, status or generatability: REA only adds evidence and warnings."""
    rep = getattr(info, "rea", None)
    if rep is None:
        return
    data = rep.to_dict() if hasattr(rep, "to_dict") else dict(rep)
    a.rea = data
    a.investigation_coverage["rea"] = {"status": data.get("status"), "inspected": data.get("shards_inspected", 0),
                                         "total": data.get("shards_total", 0), "stop_reason": data.get("stop_reason", "")}
    for call in data.get("calls") or []:
        if isinstance(call, dict) and call.get("ok"):
            a.evidence_ledger.append({"source": str(call.get("target") or "REA"), "location": str(call.get("tool") or ""),
                                      "finding": str(call.get("output") or "REA observation")[:500], "provenance": "REA",
                                      "confidence": 0.8})
    status = str(data.get("status"))
    if status in ("failed", "partial"):
        a.warnings.append(f"REA inspection {status}: {data.get('reason', '')}"[:400])
        if a.status == "supported":  # status was computed before REA ran; a new warning must be reflected in it
            a.status = "supported_with_warnings"
    model = info if info.source.kind != "github" else getattr(info, "primary_model_info", None)
    hub_total = getattr(model, "safetensors_total_params", None) if model is not None else None
    sums = [s.get("parameters") for s in data.get("header_summaries") or [] if isinstance(s.get("parameters"), int)]
    all_files_read = data.get("shards_total") and data.get("shards_inspected") == data.get("shards_total")
    if status == "completed" and all_files_read and hub_total and sums:
        rea_total = sum(sums)
        if abs(rea_total - hub_total) > 0.02 * max(hub_total, 1):
            a.uncertainties.append(f"The safetensors headers inspected with REA declare {rea_total:,} parameters, but the Hugging Face "
                                   f"API reports {hub_total:,}; the hardware estimate may be off.")


# --------------------------------------------------------------------------- combined analysis (Python analyzer + REA)

COMBINED_SCHEMA_VERSION = 1
REA_STATUSES = ("completed", "partial", "failed", "not_invoked", "not_applicable")
PARAM_TOLERANCE = 0.02  # relative difference between two parameter counts that is still treated as agreement (as in _apply_rea)
_WIDE_DTYPES = {"F32", "F64", "I32", "I64", "U32", "U64"}


def _as_int(value: Any) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _is_rea_note(text: Any) -> bool:
    """Notes that _apply_rea wrote into the analysis itself; in the combined report they belong to the REA side."""
    text = str(text)
    return text.startswith("REA inspection ") or "inspected with REA" in text or "REA result could not be merged" in text


def _rea_side(rea: Any) -> Dict[str, Any]:
    """What REA really delivered, as plain data. ``usable`` is True only when at least one model file was inspected successfully."""
    if not isinstance(rea, dict):
        return {"status": "not_invoked", "reason": "No REA result is attached to this analysis.", "usable": False, "coverage": "none",
                "files_inspected": 0, "files_total": 0, "server": {}, "tools_executed": [], "files": [], "findings": [],
                "weight_formats": [], "capabilities": [], "capabilities_by_tag": {}, "refusals": {}, "questions": [],
                "investigation": [], "stop_reason": "", "budget": {}, "errors": []}
    calls = [c for c in rea.get("calls") or [] if isinstance(c, dict)]
    ok_targets = {c.get("target") for c in calls if c.get("tool") == "inspect_model_artifact" and c.get("ok") is True}
    files = [{k: s.get(k) for k in ("file", "tensor_count", "parameters", "dtypes", "data_bytes", "logical_size",
                                    "format", "gguf_version", "architecture", "has_chat_template", "header_bytes")}
             for s in rea.get("header_summaries") or [] if isinstance(s, dict) and s.get("file") in ok_targets]
    status = str(rea.get("status") or "not_invoked")[:40]
    usable = status in ("completed", "partial") and bool(files)
    coverage = "none" if not usable else ("complete" if status == "completed" else "partial")
    formats = sorted({str(f.get("format")) for f in files if f.get("format")})
    return {
        "status": status, "reason": str(rea.get("reason") or "")[:300], "usable": usable, "coverage": coverage,
        "files_inspected": _as_int(rea.get("shards_inspected")) or 0, "files_total": _as_int(rea.get("shards_total")) or 0,
        "server": rea.get("server") if isinstance(rea.get("server"), dict) else {},
        "tools_executed": [c.get("tool") for c in calls],
        "files": files,
        "weight_formats": formats,
        "capabilities": [str(c)[:80] for c in (rea.get("capabilities") or [])],
        "capabilities_by_tag": {str(k): [str(t)[:80] for t in v] for k, v in (rea.get("capabilities_by_tag") or {}).items()},
        "refusals": {str(k): str(v)[:160] for k, v in (rea.get("refusals") or {}).items()},
        "questions": [q for q in (rea.get("questions") or []) if isinstance(q, dict)][:20],
        "investigation": [i for i in (rea.get("investigation") or []) if isinstance(i, dict)][:20],
        "stop_reason": str(rea.get("stop_reason") or "")[:200],
        "budget": rea.get("budget") if isinstance(rea.get("budget"), dict) else {},
        "findings": [str(f)[:300] for f in (rea.get("findings") or [])[:6]],
        "errors": [str(c.get("error"))[:200] for c in calls if c.get("error")][:3],
    }


def _rea_weight_format_section(a: Analysis, side: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str], List[Dict[str, Any]]]:
    """REA's weight-file findings beyond the parameter count: the format(s) it actually read, and - for GGUF - the architecture
    the file itself declares. Additive evidence only; the Python task/status is never changed. Returns (section, warnings, conflicts)."""
    section: Dict[str, Any] = {"formats": list(side.get("weight_formats") or []), "files": []}
    warnings: List[str] = []
    conflicts: List[Dict[str, Any]] = []
    py_arch = None
    cfg = (a.model_evidence or {}).get("config") if isinstance(a.model_evidence, dict) else None
    if isinstance(cfg, dict) and isinstance(cfg.get("architectures"), list) and cfg["architectures"]:
        py_arch = str(cfg["architectures"][0])
    for f in side.get("files") or []:
        entry = {"file": f.get("file"), "format": f.get("format"), "header_bytes": _as_int(f.get("header_bytes")),
                 "tensor_count": _as_int(f.get("tensor_count"))}
        declared_arch = f.get("architecture") if isinstance(f.get("architecture"), str) and f.get("architecture") else None
        if declared_arch:
            entry["declared_architecture"] = declared_arch
            entry["declared_chat_template"] = bool(f.get("has_chat_template"))
        section["files"].append(entry)
        # GGUF's general.architecture is a coarse family name (e.g. "llama"), not a transformers class; a mismatch is only worth a
        # note, never a conflict, because the two vocabularies are different.
        if declared_arch and py_arch and declared_arch.lower() not in py_arch.lower() and py_arch.lower() not in declared_arch.lower():
            warnings.append(f"REA read a GGUF file that declares architecture '{declared_arch}', while config.json declares "
                            f"'{py_arch}'; the file and the config may describe different models.")
    if side.get("usable") and "gguf" in (side.get("weight_formats") or []):
        warnings.append("REA inspected a GGUF weight file's header (declarations only): GGUF files are not loadable by this "
                        "platform's generated transformers/diffusers apps, so this is analysis evidence only.")
    return section, warnings, conflicts


def _rea_investigation_section(side: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """The dynamic REA investigation beyond the header inspection: the capabilities discovered, the questions the collected
    evidence raised, the capability-selected calls that answered them (retained-Evidence pages, byte entropy, residual-unknown
    ledger, Evidence bundle, ...), and why the adaptive loop stopped. Purely additive: each entry keeps its tool, target, reason
    and question, and a failed optional call is recorded as such rather than dropped. Returns (section, warnings)."""
    inv = [i for i in (side.get("investigation") or []) if isinstance(i, dict)]
    answered = {i.get("question") for i in inv if i.get("ok") and i.get("question")}
    section: Dict[str, Any] = {
        "capabilities_advertised": list(side.get("capabilities") or []),
        "capabilities_by_tag": dict(side.get("capabilities_by_tag") or {}),
        "refused_tools": dict(side.get("refusals") or {}),
        "stop_reason": str(side.get("stop_reason") or ""),
        "budget": dict(side.get("budget") or {}),
        "questions": [{"qid": q.get("qid"), "kind": q.get("kind"), "target": q.get("target"), "text": str(q.get("text") or "")[:200],
                       "answered": q.get("qid") in answered} for q in (side.get("questions") or []) if isinstance(q, dict)],
        "calls": [],
    }
    warnings: List[str] = []
    ok = 0
    for i in inv:
        entry = {"tool": i.get("tool"), "target": i.get("target"), "reason": i.get("reason"), "ok": bool(i.get("ok")),
                 "capability": i.get("capability"), "question": i.get("question"), "detail": str(i.get("detail") or "")[:160]}
        section["calls"].append(entry)
        if entry["ok"]:
            ok += 1
    if inv and ok < len(inv):
        warnings.append(f"{len(inv) - ok} of {len(inv)} optional REA investigation call(s) did not complete; the header "
                        "inspection is unaffected.")
    return section, warnings


def _compare_parameters(a: Analysis, side: Dict[str, Any]) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Python Analyzer parameter count vs the count REA derived from the inspected safetensors headers.

    Complete REA coverage: a relative difference above PARAM_TOLERANCE is a conflict. Partial coverage: REA's sum covers only some of
    the files, so it can only contradict the Python figure by being LARGER than it; otherwise the two are not comparable."""
    py, src = _as_int(a.hardware.params), a.hardware.params_source
    parts = [_as_int(f.get("parameters")) for f in side["files"]]
    rea_total = sum(p for p in parts if p is not None) if side["usable"] and any(p is not None for p in parts) else None
    coverage = side["coverage"] if rea_total is not None else "none"
    cmp: Dict[str, Any] = {"python": py, "python_source": src, "rea": rea_total, "rea_coverage": coverage, "relative_difference": None}
    if rea_total is None:
        cmp["verdict"] = "python_only" if py else "unknown"
        return cmp, []
    if not py:
        cmp["verdict"] = "rea_only"
        return cmp, []
    diff = abs(rea_total - py) / max(py, 1)
    cmp["relative_difference"] = round(diff, 4)
    if coverage == "complete":
        conflicting = diff > PARAM_TOLERANCE
    else:
        conflicting = rea_total > py * (1 + PARAM_TOLERANCE)
        if not conflicting:
            cmp["verdict"] = "not_comparable"
            return cmp, []
    if not conflicting:
        cmp["verdict"] = "agree"
        return cmp, []
    cmp["verdict"] = "conflict"
    detail = (f"Python Analyzer: {py:,} parameters ({src}); REA header inspection ({coverage} coverage): {rea_total:,} parameters; "
              f"difference {diff:.1%}.")
    if coverage != "complete":
        detail += " REA read only part of the files yet already counts more parameters than the Python figure."
    estimated = str(src).startswith(("estimated", "rough"))
    if estimated:
        detail += " The Python figure is an estimate; the REA figure is derived from the file's own header declarations."
        totals: Dict[str, int] = {}
        for f in side["files"]:
            dtypes = f.get("dtypes") if isinstance(f.get("dtypes"), dict) else {}
            for k, v in dtypes.items():
                if _as_int(v) is not None:
                    totals[str(k)] = totals.get(str(k), 0) + v
        dominant = max(totals, key=totals.get) if totals else None
        if str(src).startswith("estimated from weight-file size") and dominant in _WIDE_DTYPES:
            detail += f" The size-based estimate assumes 16-bit weights, but REA reports mostly {dominant}."
    conflict = {"field": "parameter_count", "severity": "medium" if estimated else "high", "python_value": py, "python_source": src,
                "rea_value": rea_total, "rea_coverage": coverage, "relative_difference": round(diff, 4), "detail": detail}
    return cmp, [conflict]


def _repository_section(a: Analysis) -> Dict[str, Any]:
    """The normalized repository section of the report, or a small placeholder when the model was not built."""
    repo = a.repository
    if not isinstance(repo, dict) or not repo:
        return {"available": False, "note": "The normalized repository model was not built for this analysis."}
    out = dict(repo)
    out["available"] = True
    return out


def _contract_section(a: Analysis) -> Dict[str, Any]:
    """The inference contract in one place, from the deepest evidence the analysis holds (PART M #7-12).

    Prefers the repository-native contract (``a.native['inference_contract']``, built from real static evidence) when present,
    otherwise folds the deep source-analysis I/O digest and the capability requirements into the same shape. A field is filled
    only from evidence that produced it; everything not established is listed under ``unknown``. It is never filled from a task
    or architecture name alone."""
    src = a.ui_hints.get("source_analysis") if isinstance(a.ui_hints, dict) else None
    src = src if isinstance(src, dict) else {}
    io = src.get("io_contract") if isinstance(src.get("io_contract"), dict) else {}
    native = a.native if isinstance(a.native, dict) else {}
    nat = native.get("inference_contract") if isinstance(native.get("inference_contract"), dict) else {}
    repo = a.repository if isinstance(a.repository, dict) else {}
    inv = [x for x in (repo.get("artifact_inventory") or []) if isinstance(x, dict)]
    inv_formats = sorted({f".{str(x['format'])}" if x.get("format") and not str(x["format"]).startswith(".") else str(x.get("format"))
                          for x in inv if x.get("format")}) if inv else []
    load_frameworks = [s.get("framework") for s in (src.get("load_sites") or []) if isinstance(s, dict) and s.get("framework")]
    infer_calls = [s.get("call") for s in (src.get("infer_sites") or []) if isinstance(s, dict) and s.get("call")]
    inputs = [i.get("modality") for i in (io.get("inputs") or []) if isinstance(i, dict) and i.get("modality")] or list(a.input_modalities)
    outputs = [o.get("kind") for o in (io.get("outputs") or []) if isinstance(o, dict) and o.get("kind")] or list(a.output_modalities)
    framework = nat.get("framework") or (load_frameworks[0] if load_frameworks else (a.frameworks[0] if a.frameworks else None))
    weight_formats = list(nat.get("weight_formats") or []) or inv_formats
    unknown: List[str] = []
    if not inputs:
        unknown.append("input fields")
    if not outputs:
        unknown.append("output fields")
    if not (load_frameworks or nat.get("weight_files") or inv):
        unknown.append("model loading strategy")
    if not infer_calls and not nat.get("entrypoints"):
        unknown.append("inference entry point")
    return {
        "available": bool(inputs or outputs or load_frameworks or nat or inv),
        "framework": framework,
        "loading": sorted(set(load_frameworks)) or (nat.get("framework") and [nat["framework"]]) or [],
        "inputs": list(dict.fromkeys(inputs)),
        "outputs": list(dict.fromkeys(outputs)),
        "inference_calls": sorted(set(infer_calls))[:12],
        "preprocessing": [p.get("call") for p in (io.get("preprocessing") or []) if isinstance(p, dict)][:12],
        "postprocessing": [p.get("call") for p in (io.get("postprocessing") or []) if isinstance(p, dict)][:12],
        "weight_formats": weight_formats,
        "unknown": unknown,
        "contract_verified": bool(io.get("contract_verified")) or bool(nat.get("inputs") and nat.get("outputs")),
        "source": "repository-native static evidence" if nat else ("source_analysis" if src else "capabilities"),
    }


def _safety_section(a: Analysis, side: Dict[str, Any]) -> Dict[str, Any]:
    """The trust boundary this analysis held, as data (PART M #23).

    Every rule is one the platform always enforces; ``held`` says it was not violated, and the ``detail`` names the evidence.
    ``declared_but_not_executed`` lists repository capabilities that were observed and deliberately NOT run, and
    ``refused_rea_capabilities`` records REA tools the safety policy declined."""
    rea = a.rea if isinstance(a.rea, dict) else {}
    repo = a.repository if isinstance(a.repository, dict) else {}
    native = a.native if isinstance(a.native, dict) else {}
    artifacts = [str(p) for p in (repo.get("artifact_files") or [])]
    pickle_files = [str(p) for p in (native.get("pickle_weights") or [])] or \
                   [p for p in artifacts if p.lower().endswith(PICKLE_EXTS)]
    downloaded = rea.get("bytes_downloaded")
    rules = [
        {"rule": "repository_code_never_executed", "held": True,
         "detail": "repository Python is parsed with the standard-library ast module only; no module is imported, executed or evaluated"},
        {"rule": "no_install_of_repository_dependencies", "held": True,
         "detail": "dependency manifests are read as text; nothing they declare is installed during analysis"},
        {"rule": "weights_never_fully_downloaded", "held": True,
         "detail": "weight files are read by HTTP range requests (headers only)"
                   + (f"; {downloaded} bytes read in total" if isinstance(downloaded, int) else "")},
        {"rule": "trust_remote_code_never_enabled", "held": True,
         "detail": ("the repository declares config.auto_map custom code; it is reported for the user, never enabled"
                    if a.trust_remote_code else "no custom repository code was enabled for loading")},
        {"rule": "repository_content_is_untrusted_data", "held": True,
         "detail": "README, source and config text are treated as data; no instruction in them can change platform behaviour"},
    ]
    declared: List[Dict[str, Any]] = []
    if a.trust_remote_code:
        declared.append({"capability": "trust_remote_code (config.auto_map custom code)", "executed": False,
                         "reason": "executing repository Python is refused"})
    if pickle_files:
        declared.append({"capability": "pickle weight deserialisation", "executed": False,
                         "reason": f"unpickling can execute code; {len(pickle_files)} pickle file(s) are listed and are not loaded"})
    return {"rules": rules, "declared_but_not_executed": declared,
            "refused_rea_capabilities": {str(k): str(v)[:160] for k, v in (side.get("refusals") or {}).items()}}


def _components_section(a: Analysis) -> Dict[str, Any]:
    """Components that are unsupported, inaccessible or unresolved, kept apart from 'absent' (PART M #24).

    ``gaps`` are things that could not be READ (content unknown, never assumed absent); ``missing`` are things the provider
    reported as absent; ``unsupported_formats`` are artifacts the platform cannot safely inspect further; and
    ``unresolved_conclusions`` are conclusions the evidence could not settle."""
    repo = a.repository if isinstance(a.repository, dict) else {}
    native = a.native if isinstance(a.native, dict) else {}
    artifacts = [str(p) for p in (repo.get("artifact_files") or [])]
    unsupported: List[Dict[str, Any]] = []
    for p in artifacts:
        if p.lower().endswith((".onnx", ".pb", ".tflite", ".h5", ".keras", ".msgpack")):
            unsupported.append({"component": p, "reason": "artifact format is listed but not header-inspected by the platform"})
    if native.get("pickle_weights") and not native.get("safe_weights"):
        unsupported.append({"component": "pickle-format weights",
                            "reason": "only pickle-format weights were found; they are never loaded without explicit consent"})
    return {
        "gaps": [dict(g) for g in (repo.get("gaps") or [])],
        "missing": list(repo.get("missing") or []),
        "unsupported_formats": unsupported,
        "unresolved_conclusions": list(dict.fromkeys(a.uncertainties or []))[:12],
        "inspection_coverage": dict(repo.get("coverage") or {}),
    }


def _model_section(a: Analysis) -> Dict[str, Any]:
    """Repository classification, task/subtask, architecture and frameworks in one place (PART M #3-6).

    Every value is read from evidence the analysis already holds: the provider classification, the detected task and its
    evidence, the model type/architecture from the repository config, and the frameworks detected from dependencies/imports.
    A field the evidence did not establish stays ``None`` rather than being guessed from the model's name."""
    me = a.model_evidence if isinstance(a.model_evidence, dict) else {}
    cfg = me.get("config") if isinstance(me.get("config"), dict) else {}
    hints = a.ui_hints if isinstance(a.ui_hints, dict) else {}
    model_type = hints.get("model_type") or cfg.get("model_type")
    archs = cfg.get("architectures") if isinstance(cfg.get("architectures"), list) else []
    if not archs and isinstance(hints.get("declared_architectures"), list):
        archs = hints["declared_architectures"]  # a GitHub-native repo's own config.json architectures
    native = a.native if isinstance(a.native, dict) else {}
    subtask = None
    if a.task == "text-generation" and a.chat:
        subtask = "chat"
    if hints.get("translation_prefix_hint"):
        subtask = "translation"
    classification = {
        "provider": a.host,
        "origin": a.origin,  # huggingface | github-linked-hf | github-native
        "native_eligible": bool(native.get("eligible")) if native else None,
        "signals": list(native.get("signals") or [])[:12],
    }
    return {
        "classification": classification,
        "task": a.task,
        "subtask": subtask,
        "task_confidence": a.task_confidence,
        "architecture": {"model_type": model_type, "architectures": list(archs)},
        "frameworks": list(a.frameworks),
        "library": a.library,
        "modalities": {"input": list(a.input_modalities), "output": list(a.output_modalities)},
    }


def _dependencies_section(a: Analysis) -> Dict[str, Any]:
    """Dependency requirements and where each was found (PART M #13). Nothing is installed during analysis."""
    return {
        "dependencies": list(a.dependencies or []),
        "evidence": {str(k): str(v)[:200] for k, v in (a.dependency_evidence or {}).items()},
        "flags": list(a.requirement_flags or []),
        "min_transformers": a.min_transformers,
        "installed_during_analysis": False,
    }


def _limitations_section(a: Analysis, side: Dict[str, Any], components: Dict[str, Any]) -> List[str]:
    """The analysis's own remaining limitations (PART M #25): what this platform cannot establish or do, as opposed to what this
    particular repository failed to provide. Static and honest; it never claims more than the platform really does."""
    out: List[str] = [
        "Analysis is metadata- and source-static only: repository code is never executed, so runtime behaviour (actual tensor "
        "shapes at inference, real output formatting) is not observed.",
        "Weight files are read by header only; tensor values, quantisation calibration and numerical behaviour are not inspected.",
        "The inference contract is assembled from declared metadata, static call sites and (for GitHub-native repos) documentation; "
        "fields the evidence did not establish are listed as unknown rather than guessed.",
    ]
    if side.get("status") in ("not_invoked", "not_applicable"):
        out.append("REA did not inspect the weight files, so parameter/dtype figures were not independently verified.")
    if side.get("status") == "partial":
        out.append("REA inspected only part of the model's weight files, so its figures cover an incomplete set.")
    if components.get("gaps"):
        out.append("Some repository content could not be read (see unsupported_components.gaps); what it would show is unknown.")
    if a.origin == "github-native":
        out.append("A GitHub-native repository yields a scaffold: the platform verifies structure, artifacts and dependencies, but "
                   "does not certify that the code runs or that a safe loader exists.")
    if a.trust_remote_code:
        out.append("The model requires custom repository code (trust_remote_code); it is reported but never executed or enabled.")
    return out


def _build_claims(a: Analysis) -> Dict[str, Any]:
    """Claim-level validation for this analysis, derived from evidence already on ``a``. Never raises: a failure to build the
    claim set must not invalidate the analysis."""
    try:
        from .claims import build_claims, claims_report
        src = None
        if isinstance(a.ui_hints.get("source_analysis"), dict):
            src = _SourceDigest(a.ui_hints["source_analysis"])
        repo = _RepoDigest(a.repository)
        return claims_report(build_claims(a, repo=repo, src=src))
    except Exception as exc:
        return {"schema_version": "1.0", "claims": [], "summary": {"total": 0, "by_status": {}, "conflicted": [], "unknown": []},
                "error": f"the claim set could not be built ({type(exc).__name__})"}


class _SourceDigest:
    """A minimal read-only view over ``ui_hints['source_analysis']`` so the claim engine can read it without importing the module."""

    def __init__(self, data: Dict[str, Any]) -> None:
        self._d = data

    @property
    def load_sites(self) -> List[Any]:
        return [_LoadSite(s) for s in (self._d.get("load_sites") or []) if isinstance(s, dict)]

    @property
    def infer_sites(self) -> List[Dict[str, Any]]:
        return [s for s in (self._d.get("infer_sites") or []) if isinstance(s, dict)]

    @property
    def io_contract(self) -> Dict[str, Any]:
        return self._d.get("io_contract") or {}


class _LoadSite:
    def __init__(self, d: Dict[str, Any]) -> None:
        self.framework = d.get("framework")
        self.family = d.get("family")
        self.call = d.get("call")
        self.module = d.get("module")
        self.line = d.get("line")
        self.model_id = d.get("model_id")


class _RepoDigest:
    def __init__(self, d: Optional[Dict[str, Any]]) -> None:
        self._d = d or {}

    @property
    def provider(self) -> Optional[str]:
        return self._d.get("provider")

    @property
    def repo_id(self) -> str:
        return str(self._d.get("repo_id") or "")

    @property
    def resolved_revision(self) -> Optional[str]:
        return self._d.get("resolved_revision")


def build_combined_analysis(a: Analysis) -> Dict[str, Any]:
    """One structured report over the Python Analyzer's findings and REA's findings, which stay separate inside it.

    Derived only from fields already in ``a`` (the analyzer's own results and ``a.rea``); nothing new is inspected or invented.
    REA only ever adds evidence, warnings, uncertainties, conflicts and recommendations: a failed, partial or absent REA result
    leaves the Python section exactly as the Analyzer produced it."""
    hw = a.hardware
    side = _rea_side(a.rea)
    claims = _build_claims(a)
    repository = _repository_section(a)
    contract = _contract_section(a)
    safety = _safety_section(a, side)
    components = _components_section(a)
    model = _model_section(a)
    dependencies = _dependencies_section(a)
    limitations = _limitations_section(a, side, components)
    status = side["status"]
    comparison, conflicts = _compare_parameters(a, side)
    weight_format, wf_warnings, wf_conflicts = _rea_weight_format_section(a, side)
    investigation, inv_warnings = _rea_investigation_section(side)
    conflicts = [*conflicts, *wf_conflicts]
    py_warnings = [w for w in a.warnings if not _is_rea_note(w)]
    py_uncertainties = [u for u in a.uncertainties if not _is_rea_note(u)]
    rea_warnings: List[str] = [*wf_warnings, *inv_warnings]
    rea_uncertainties: List[str] = []
    recs: List[str] = []
    reason = side["reason"]
    rea_warnings.extend(w for w in a.warnings if "REA result could not be merged" in str(w))  # written by analyze(), REA-side
    if status == "failed":
        rea_warnings.append(f"REA inspection failed: {reason}")
        recs.append("The REA inspection failed; the Python Analyzer result is unaffected. Re-run the inspection if header-level "
                    "evidence is needed.")
    elif status == "partial":
        rea_warnings.append(f"REA inspection partial: {reason}")
        rea_uncertainties.append(f"REA inspected {side['files_inspected']} of {side['files_total']} weight file(s); its figures describe "
                                 "only part of the model.")
        recs.append("Treat REA's parameter and dtype figures as covering only the inspected weight files.")
    elif status == "not_invoked":
        rea_uncertainties.append("REA did not run, so the weight-file declarations were not independently inspected; this report "
                                 "holds Python Analyzer findings only.")
        recs.append("Enable the REA stage to cross-check the Analyzer's parameter count against the weight-file headers.")
    elif status == "not_applicable":
        rea_uncertainties.append(f"REA is not applicable to this repository ({reason.rstrip('. ')}); this report holds Python Analyzer "
                                 "findings only.")
    elif status == "completed":
        if not side["usable"]:
            rea_warnings.append("REA reports 'completed' but no inspected file summary is available, so its findings are not used.")
    else:
        rea_warnings.append(f"REA reported an unrecognised status '{status}'; its findings are not used.")
    for c in conflicts:
        rea_warnings.append("Conflicting evidence: " + c["detail"])
        rea_uncertainties.append("The parameter count is disputed between the Python Analyzer and REA.")
        basis = (f" The hardware recommendation ({hw.recommended_runtime}; about {hw.est_vram_gb_fp16} GB VRAM in fp16) is based on the "
                 "Python figure.") if hw.est_vram_gb_fp16 else ""
        if str(c["python_source"]).startswith(("estimated", "rough")):
            recs.append("Prefer REA's header-derived parameter count over the Python size-based estimate before relying on the "
                        "hardware recommendation." + basis)
        else:
            recs.append("Check that the Hub metadata and the inspected weight file describe the same weights before relying on either "
                        "parameter count." + basis)
    if a.task and a.task_confidence < 0.6:
        recs.append(f"The Python task confidence is low ({a.task_confidence}); verify that the generated UI matches the model. "
                    "REA's header inspection carries no task evidence.")

    evidence: List[Dict[str, Any]] = [
        {"origin": "python_analyzer", "signal": e.signal, "tier": e.tier, "detail": e.detail, "weight": e.weight}
        for e in sorted(a.task_evidence, key=lambda e: ({"authoritative": 0, "structural": 1}.get(e.tier, 2), -e.weight))[:5]]
    if comparison["python"]:
        evidence.append({"origin": "python_analyzer", "signal": "parameter_count",
                         "detail": f"{comparison['python']:,} parameters ({comparison['python_source']})"})
    for f in side["files"][:3]:
        dtypes = f.get("dtypes") if isinstance(f.get("dtypes"), dict) else {}
        evidence.append({"origin": "rea", "signal": "safetensors_header",
                         "detail": f"{f.get('file')}: {f.get('tensor_count')} tensors, {f.get('parameters')} parameters, dtypes "
                                   + (", ".join(f"{k} ({v})" for k, v in dtypes.items()) or "none")})
    for i in investigation["calls"]:
        if i["ok"]:
            evidence.append({"origin": "rea", "signal": f"rea:{i['tool']}",
                             "detail": f"{i['target']}: {i['detail']}" if i["target"] else str(i["detail"])})

    levels = {"agree": "corroborated", "conflict": "conflicting", "python_only": "python_only", "rea_only": "rea_only",
              "not_comparable": "unverified", "unknown": "unknown"}
    return {
        "schema_version": COMBINED_SCHEMA_VERSION,
        "python_analysis": {
            "status": a.status, "generatable": a.generatable, "task": a.task, "task_confidence": a.task_confidence,
            "capabilities": [{"name": c.name, "role": c.role, "status": c.status, "status_reason": c.status_reason,
                              "certainty": c.certainty, "certainty_reason": c.certainty_reason, "requirements": c.requirements,
                              "confidence": c.confidence, "tier": c.tier,
                              "input_modalities": list(c.input_modalities), "output_modalities": list(c.output_modalities),
                              "evidence": [{k: e.get(k) for k in ("tier", "signal", "source", "detail")} for e in c.evidence[:4]]}
                             for c in effective_capabilities(a)],
            "library": a.library, "parameters": {"count": hw.params, "source": hw.params_source},
            "hardware": {"recommended_runtime": hw.recommended_runtime, "est_vram_gb_fp16": hw.est_vram_gb_fp16},
            "warnings": py_warnings[:8], "uncertainties": py_uncertainties[:6],
        },
        "rea_analysis": side,
        "repository": repository,
        "model": model,
        "inference_contract": contract,
        "dependencies": dependencies,
        "safety": safety,
        "unsupported_components": components,
        "claims": claims,
        "parameter_comparison": comparison,
        "weight_format": weight_format,
        "rea_investigation": investigation,
        "conflicts": conflicts,
        "confidence": {"task": a.task_confidence, "task_basis": "python_analyzer only",
                       "parameter_count": levels.get(comparison["verdict"], "unknown"), "rea_coverage": side["coverage"]},
        "evidence": evidence,
        "warnings": list(dict.fromkeys(py_warnings[:8] + rea_warnings)),
        "uncertainties": list(dict.fromkeys(py_uncertainties[:6] + rea_uncertainties)),
        "recommendations": list(dict.fromkeys(recs)),
        "limitations": limitations,
    }


def _attach_combined(a: Analysis) -> None:
    try:
        a.combined_analysis = build_combined_analysis(a)
        a.claims = a.combined_analysis.get("claims")  # the same claim set the report carries; kept on the Analysis for callers
    except Exception as exc:  # derived data: failing to build it must never invalidate the analysis itself
        a.combined_analysis = {"schema_version": COMBINED_SCHEMA_VERSION, "status": "unavailable",
                               "error": f"The combined report could not be built ({type(exc).__name__}); the Python analysis is unaffected."}


def _attach_evidence_ledger(a: Analysis, info: RepoInfo) -> None:
    """Normalize all analysis inputs into a deduplicated, source-preserving ledger."""
    rows: List[Dict[str, Any]] = [dict(x) for x in getattr(info, "evidence_ledger", []) if isinstance(x, dict)]
    for e in a.task_evidence:
        rows.append({"source": e.source, "location": e.signal, "finding": e.detail, "provenance": e.tier,
                     "confidence": e.weight, "task": e.task})
    # inspection_gaps is GitHub-only provenance (only the GitHub inspector records gaps); a Hugging Face analysis must not
    # inherit them, matching how the repository model gates the same field on the provider.
    if info.source.kind == "github":
        for gap in getattr(info, "inspection_gaps", []):
            if isinstance(gap, dict):
                rows.append({"source": "repository inspection", "location": gap.get("code", "unknown"),
                             "finding": gap.get("message", "unavailable"), "provenance": "unknown", "confidence": 0.0})
    # Merge identical findings while retaining every distinct source/location.
    merged: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for row in rows:
        finding, provenance = str(row.get("finding", "")), str(row.get("provenance", ""))
        key = (finding, provenance)
        if key not in merged:
            merged[key] = row
        else:
            prior = merged[key]
            sources = prior.setdefault("sources", [prior.get("source")])
            source = row.get("source")
            if source not in sources:
                sources.append(source)
            prior["confidence"] = max(float(prior.get("confidence", 0) or 0), float(row.get("confidence", 0) or 0))
    a.evidence_ledger = list(merged.values())
    a.investigation_coverage["repository"] = dict(getattr(info, "inspection_coverage", {}) or {})


def analyze(info: RepoInfo, settings: Optional[Settings] = None) -> Analysis:
    a = analyze_github(info, settings) if info.source.kind == "github" else analyze_hf(info, settings)
    try:  # normalized, provider-neutral repository representation (identity, inventories, coverage, gaps); never fetched or executed
        from .repo_model import from_info
        a.repository = from_info(info, source_analysis=getattr(info, "source_analysis", None)).to_dict()
    except Exception as exc:
        a.warnings.append(f"The normalized repository model could not be built ({type(exc).__name__}); the analysis is unaffected.")
    ctx = getattr(info, "evidence", None)  # collected before analysis (see pipeline); the metadata gaps it filled were already analysed
    if ctx is not None:
        try:
            from .evidence_bridge import attach_evidence
            attach_evidence(a, ctx)  # digest + uncertainties only: never warnings/reasons, so it cannot change status or block generation
        except Exception as exc:
            a.uncertainties.append(f"The collected evidence could not be attached to the analysis ({type(exc).__name__}); the analysis is unaffected.")
    try:
        _apply_rea(a, info)
    except Exception as exc:  # a malformed REA result must never take the Python Analyzer's result down with it
        a.warnings.append(f"The REA result could not be merged into the analysis ({type(exc).__name__}); the Python analysis is unaffected.")
        if a.status == "supported":
            a.status = "supported_with_warnings"
    _attach_evidence_ledger(a, info)
    _attach_combined(a)
    return a


