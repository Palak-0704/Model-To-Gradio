"""Multi-capability detection: evidence-supported capabilities with confidence, evidence, modalities and status."""
import unittest
from unittest import mock

from m2g import analyzer as an
from m2g.inspector import RepoInfo
from m2g.security import SourceRef

HF = SourceRef("huggingface", "acme/model", "acme", "model", None, "https://huggingface.co/acme/model")
GH = SourceRef("github", "acme/repo", "acme", "repo", None, "https://github.com/acme/repo")
WEIGHTS = [("model.safetensors", 1_000_000), ("config.json", 500)]


def hf_info(**kw):
    kw.setdefault("files", list(WEIGHTS))
    return RepoInfo(source=HF, revision_sha="a" * 40, license="mit", **kw)


def by_name(caps):
    return {c.name: c for c in caps}


LLM_CFG = {"architectures": ["LlamaForCausalLM"], "model_type": "llama", "vocab_size": 32000,
           "task_specific_params": {"summarization": {"max_length": 128}}}
VLM_CFG = {"architectures": ["LlavaForConditionalGeneration"], "model_type": "llava",
           "vision_config": {"model_type": "clip_vision_model"}, "text_config": {"model_type": "llama"}}


class TestCapabilityDetection(unittest.TestCase):
    def test_text_generation_and_summarization_both_detected(self):
        caps = by_name(an.detect_capabilities(hf_info(pipeline_tag="text-generation", config=LLM_CFG)))
        self.assertIn("text-generation", caps)
        self.assertIn("summarization", caps)
        for name in ("text-generation", "summarization"):
            c = caps[name]
            self.assertEqual(c.status, "supported", (name, c.status_reason))
            self.assertEqual((c.input_modalities, c.output_modalities), (["text"], ["text"]))
            self.assertTrue(all({"tier", "signal", "source", "detail", "weight"} <= set(e) for e in c.evidence))
        self.assertEqual(caps["summarization"].evidence[0]["source"], "config.json")

    def test_vision_language_model_detects_image_and_text_capabilities(self):
        info = hf_info(config=VLM_CFG, tokenizer_config={"chat_template": "{{ messages }}"})
        caps = an.detect_capabilities(info)
        named = by_name(caps)
        self.assertEqual(caps[0].name, "image-text-to-text")
        self.assertIn("image", named["image-text-to-text"].input_modalities)
        self.assertIn("text-generation", named)
        self.assertEqual({c.status for c in caps}, {"supported"})

    def test_vision_language_config_without_architecture_is_still_found(self):
        cfg = {"model_type": "x", "vision_config": {}, "text_config": {}}
        caps = an.detect_capabilities(hf_info(config=cfg))
        self.assertEqual(caps[0].name, "image-text-to-text")
        self.assertEqual(caps[0].evidence[0]["signal"], "vision_language_config")

    def test_single_capability_model_yields_one(self):
        cfg = {"architectures": ["BertForSequenceClassification"], "model_type": "bert"}
        caps = an.detect_capabilities(hf_info(pipeline_tag="text-classification", config=cfg))
        self.assertEqual([c.name for c in caps], ["text-classification"])
        self.assertEqual(caps[0].status, "supported")

    def test_weak_tags_alone_do_not_prove_a_capability(self):
        caps = by_name(an.detect_capabilities(hf_info(pipeline_tag="text-generation", config=LLM_CFG | {"task_specific_params": {}},
                                                      tags=["translation", "summarization"])))
        self.assertEqual(caps["text-generation"].status, "supported")
        for name in ("translation", "summarization"):
            self.assertEqual(caps[name].status, "uncertain", name)
            self.assertFalse(caps[name].builds_interface, name)
        only_tag = an.detect_capabilities(hf_info(tags=["summarization"]))
        self.assertEqual([c.status for c in only_tag], ["uncertain"])

    def test_unknown_capability_stays_uncertain(self):
        caps = an.detect_capabilities(hf_info(pipeline_tag="quantum-vibe-detection"))
        self.assertEqual(caps[0].name, "quantum-vibe-detection")
        self.assertEqual(caps[0].status, "uncertain")

    def test_model_name_and_readme_are_never_evidence(self):
        src = SourceRef("huggingface", "acme/summarizer-translator-vision", "acme", "summarizer-translator-vision", None, "u")
        info = RepoInfo(source=src, files=list(WEIGHTS), readme="This model does summarization, translation and image captioning.")
        self.assertEqual(an.detect_capabilities(info), [])

    def test_independent_of_detect_task(self):
        info = hf_info(pipeline_tag="text-generation", config=LLM_CFG)
        with mock.patch.object(an, "detect_task", side_effect=AssertionError("must not be called")):
            names = [c.name for c in an.detect_capabilities(info)]
        self.assertEqual(names[0], "text-generation")
        self.assertIn("summarization", names)

    def test_analysis_task_remains_primary_capability(self):
        for info in (hf_info(pipeline_tag="text-generation", config=LLM_CFG),
                     hf_info(config=VLM_CFG, tokenizer_config={"chat_template": "x"})):
            a = an.analyze(info)
            self.assertEqual(a.task, an.detect_task(info)[0])
            self.assertEqual(a.task, a.capabilities[0].name)
            self.assertEqual(a.capabilities[0].role, "primary")
        self.assertIn("status", a.to_dict()["capabilities"][0])

    def test_linked_model_evidence_is_reused_for_github(self):
        model = hf_info(pipeline_tag="text-generation", config=LLM_CFG)
        info = RepoInfo(source=GH, default_branch="main", primary_hf_model="acme/model", primary_model_info=model,
                        linked_hf_models=["acme/model"])
        a = an.analyze_github(info)
        self.assertEqual(a.task, "text-generation")
        named = by_name(a.capabilities)
        self.assertIn("summarization", named)
        self.assertEqual(named["summarization"].status, "supported")

    def test_github_readme_keywords_stay_uncertain(self):
        info = RepoInfo(source=GH, default_branch="main", readme="A text classification and sentiment toolkit.")
        a = an.analyze_github(info)
        self.assertTrue(a.capabilities)
        self.assertEqual({c.status for c in a.capabilities}, {"uncertain"})


if __name__ == "__main__":
    unittest.main()
