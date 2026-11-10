"""Evidence-based multi-capability detection from task-conditioning tokens and registered heads.

The detection reads CONFIG FIELDS (generation_config.json ``task_to_id``, config.json ``auto_map``), never model names, repository
names, README text or an architecture name list. Every test model below uses invented names on purpose.
"""
import json
import unittest

from m2g import analyzer as an
from m2g import gradio_gen as gg
from m2g.capabilities import build_model_evidence
from m2g.inspector import RepoInfo, inspect_url
from m2g.security import SourceRef
from fakehub import FakeWeb, SHA, add_hf_model

HF = SourceRef("huggingface", "acme/model", "acme", "model", None, "https://huggingface.co/acme/model")
WEIGHTS = [("model.safetensors", 1_000_000), ("config.json", 500), ("generation_config.json", 300)]

SPEECH_CFG = {"architectures": ["AcmeForSpeechSeq2Seq"], "model_type": "acme_speech"}
TOKENS = {"task_to_id": {"transcribe": 1, "translate": 2}, "is_multilingual": True}


def info(**kw):
    kw.setdefault("files", list(WEIGHTS))
    return RepoInfo(source=HF, revision_sha="a" * 40, license="mit", **kw)


def named(caps):
    return {c.name: c for c in caps}


class TestTaskTokenModes(unittest.TestCase):
    def test_speech_model_gets_a_second_supported_capability(self):
        caps = an.detect_capabilities(info(pipeline_tag="automatic-speech-recognition", config=SPEECH_CFG, generation_config=TOKENS))
        by = named(caps)
        self.assertEqual([c.name for c in caps], ["automatic-speech-recognition", "translation"])
        t = by["translation"]
        self.assertEqual((t.role, t.status), ("co_supported", "supported"))
        self.assertTrue(t.builds_interface)
        # a mode of an audio model takes audio, not the text->text of the standalone translation task
        self.assertEqual((t.input_modalities, t.output_modalities), (["audio"], ["text"]))
        self.assertIn("task-conditioned mode", t.advisory)

    def test_provenance_and_confidence_are_kept(self):
        t = named(an.detect_capabilities(info(pipeline_tag="automatic-speech-recognition", config=SPEECH_CFG,
                                              generation_config=TOKENS)))["translation"]
        ev = t.evidence[0]
        self.assertEqual((ev["signal"], ev["source"], ev["tier"], ev["task"]), ("generation_task_token", "generation_config.json",
                                                                                 "structural", "translation"))
        self.assertIn("translate", ev["detail"])
        self.assertEqual(t.confidence, 0.6)
        self.assertEqual(t.tier, "structural")

    def test_a_token_naming_the_primary_task_corroborates_without_a_duplicate(self):
        caps = an.detect_capabilities(info(pipeline_tag="automatic-speech-recognition", config=SPEECH_CFG, generation_config=TOKENS))
        primary = caps[0]
        self.assertEqual([c.name for c in caps].count("automatic-speech-recognition"), 1)
        self.assertIn("generation_task_token", {e["signal"] for e in primary.evidence})
        self.assertEqual(primary.role, "primary")

    def test_works_for_a_text_model_with_different_modalities(self):
        cfg = {"architectures": ["AcmeForCausalLM"], "model_type": "acme_lm"}
        caps = named(an.detect_capabilities(info(pipeline_tag="text-generation", config=cfg,
                                                 generation_config={"task_to_id": {"summarize": 7}})))
        self.assertEqual(caps["summarization"].input_modalities, ["text"])
        self.assertEqual(caps["summarization"].status, "supported")

    def test_works_without_any_architecture_or_model_type_name(self):
        caps = an.detect_capabilities(info(pipeline_tag="automatic-speech-recognition", generation_config=TOKENS))
        self.assertEqual([c.name for c in caps], ["automatic-speech-recognition", "translation"])

    def test_unrecognised_token_keys_are_ignored_not_guessed(self):
        caps = an.detect_capabilities(info(pipeline_tag="automatic-speech-recognition", config=SPEECH_CFG,
                                           generation_config={"task_to_id": {"frobnicate": 1, "sing": 2}}))
        self.assertEqual([c.name for c in caps], ["automatic-speech-recognition"])

    def test_analysis_task_stays_the_primary_and_capabilities_hold_all(self):
        a = an.analyze(info(pipeline_tag="automatic-speech-recognition", config=SPEECH_CFG, generation_config=TOKENS))
        self.assertEqual(a.task, "automatic-speech-recognition")
        self.assertEqual(a.task, a.capabilities[0].name)
        self.assertEqual([c.name for c in a.capabilities], ["automatic-speech-recognition", "translation"])
        json.dumps(a.to_dict())  # the new Evidence field stays serialisable

    def test_generator_facts_describe_both_capabilities(self):
        a = an.analyze(info(pipeline_tag="automatic-speech-recognition", config=SPEECH_CFG, generation_config=TOKENS))
        facts = gg._facts(a)
        self.assertTrue(facts["multi_capability"])
        caps = {c["name"]: c for c in facts["capabilities"]}
        self.assertEqual(caps["translation"]["input_modalities"], ["audio"])
        self.assertEqual(caps["translation"]["function_name"], "predict_translation")
        self.assertEqual(facts["model_evidence"]["generation"]["task_tokens"], ["transcribe", "translate"])


class TestRegisteredHeads(unittest.TestCase):
    CFG = {"architectures": ["AcmeForCausalLM"], "model_type": "acme_lm",
           "auto_map": {"AutoModelForCausalLM": "m.A", "AutoModelForSequenceClassification": "m.B", "AutoModel": "m.C",
                        "AutoTokenizer": "m.D"}}

    def test_each_declared_head_is_a_capability_with_provenance(self):
        caps = named(an.detect_capabilities(info(pipeline_tag="text-generation", config=self.CFG)))
        self.assertEqual(caps["text-classification"].status, "supported")
        ev = [e for e in caps["text-classification"].evidence if e["signal"] == "auto_map"]
        self.assertEqual(ev[0]["source"], "config.json")
        self.assertIn("AutoModelForSequenceClassification", ev[0]["detail"])

    def test_bare_automodel_and_non_model_entries_add_nothing(self):
        names = [c.name for c in an.detect_capabilities(info(pipeline_tag="text-generation", config=self.CFG))]
        self.assertEqual(sorted(names), ["text-classification", "text-generation"])


class TestWeakAndMissingEvidence(unittest.TestCase):
    def test_architecture_alone_does_not_imply_extra_tasks(self):
        caps = an.detect_capabilities(info(pipeline_tag="automatic-speech-recognition", config=SPEECH_CFG))
        self.assertEqual([c.name for c in caps], ["automatic-speech-recognition"])

    def test_tags_stay_uncertain_next_to_a_token_mode(self):
        caps = named(an.detect_capabilities(info(pipeline_tag="automatic-speech-recognition", config=SPEECH_CFG,
                                                 generation_config=TOKENS, tags=["summarization"])))
        self.assertEqual(caps["translation"].status, "supported")
        self.assertEqual(caps["summarization"].status, "uncertain")
        self.assertFalse(caps["summarization"].builds_interface)

    def test_model_and_repository_names_and_readme_are_not_evidence(self):
        src = SourceRef("huggingface", "acme/whisper-translate", "acme", "whisper-translate", None, "u")
        i = RepoInfo(source=src, files=list(WEIGHTS), pipeline_tag="automatic-speech-recognition", config=SPEECH_CFG,
                     readme="This model transcribes and translates speech.")
        self.assertEqual([c.name for c in an.detect_capabilities(i)], ["automatic-speech-recognition"])

    def test_missing_or_malformed_generation_config_changes_nothing(self):
        for gen in (None, {}, {"task_to_id": None}, {"task_to_id": []}, {"task_to_id": "translate"}, {"task_to_id": {}},
                    ["translate"], "x"):
            caps = an.detect_capabilities(info(pipeline_tag="automatic-speech-recognition", config=SPEECH_CFG, generation_config=gen))
            self.assertEqual([c.name for c in caps], ["automatic-speech-recognition"], repr(gen))

    def test_no_primary_means_a_mode_cannot_be_placed(self):
        self.assertEqual(an.detect_capabilities(info(generation_config=TOKENS)), [])

    def test_primary_with_unknown_input_modalities_gets_no_inherited_mode(self):
        caps = an.detect_capabilities(info(pipeline_tag="quantum-vibe-detection", generation_config=TOKENS))
        self.assertEqual([c.name for c in caps], ["quantum-vibe-detection"])

    def test_single_task_models_are_unchanged(self):
        cfg = {"architectures": ["AcmeForImageClassification"], "model_type": "acme_vit"}
        caps = an.detect_capabilities(info(pipeline_tag="image-classification", config=cfg))
        self.assertEqual([c.name for c in caps], ["image-classification"])
        self.assertEqual(caps[0].status, "supported")
        self.assertNotIn("generation", build_model_evidence(cfg, None, None, [], []))

    def test_existing_signals_still_work_next_to_the_new_ones(self):
        cfg = {"architectures": ["AcmeForCausalLM"], "model_type": "x", "task_specific_params": {"summarization": {}}}
        caps = named(an.detect_capabilities(info(pipeline_tag="text-generation", config=cfg,
                                                 generation_config={"task_to_id": {"translate": 1}})))
        self.assertEqual({"text-generation", "summarization", "translation"}, set(caps))
        self.assertEqual({c.status for c in caps.values()}, {"supported"})


class TestInspectorKeepsGenerationConfig(unittest.TestCase):
    def test_generation_config_is_parsed_and_kept(self):
        web = FakeWeb()
        add_hf_model(web, "acme/speech", pipeline_tag="automatic-speech-recognition", config=SPEECH_CFG,
                     files=[("config.json", 800), ("generation_config.json", 300), ("model.safetensors", 1_000_000)])
        web.add(f"https://huggingface.co/acme/speech/resolve/{SHA}/generation_config.json", TOKENS)
        got = inspect_url("https://huggingface.co/acme/speech", fetcher=web.fetcher())
        self.assertEqual(got.generation_config, TOKENS)
        self.assertEqual([c.name for c in an.detect_capabilities(got)], ["automatic-speech-recognition", "translation"])

    def test_absent_generation_config_stays_none(self):
        web = FakeWeb()
        add_hf_model(web, "acme/plain", pipeline_tag="text-classification",
                     config={"architectures": ["AcmeForSequenceClassification"], "model_type": "acme"})
        self.assertIsNone(inspect_url("https://huggingface.co/acme/plain", fetcher=web.fetcher()).generation_config)


class TestContradictoryAndIncompatibleModes(unittest.TestCase):
    def test_token_with_incompatible_output_is_conflicting_and_never_built(self):
        caps = named(an.detect_capabilities(info(pipeline_tag="automatic-speech-recognition", config=SPEECH_CFG,
                                                 generation_config={"task_to_id": {"transcribe": 1, "text-to-image": 2}})))
        bad = caps["text-to-image"]
        self.assertEqual((bad.role, bad.status), ("conflicting", "uncertain"))
        self.assertFalse(bad.builds_interface)
        self.assertIn("incompatible", bad.evidence[0]["detail"])

    def test_conflicting_mode_is_not_given_an_interface_by_the_generator(self):
        a = an.analyze(info(pipeline_tag="automatic-speech-recognition", config=SPEECH_CFG,
                            generation_config={"task_to_id": {"text-to-image": 2}}))
        self.assertEqual([c.name for c in gg.interface_capabilities(a)], ["automatic-speech-recognition"])
        names = [c["name"] for c in gg._facts(a)["not_built_capabilities"]]
        self.assertEqual(names, ["text-to-image"])

    def test_single_structural_mode_does_not_outrank_the_primary(self):
        a = an.analyze(info(pipeline_tag="automatic-speech-recognition", config=SPEECH_CFG, generation_config=TOKENS))
        self.assertEqual(a.capabilities[0].name, "automatic-speech-recognition")
        self.assertEqual(a.capabilities[0].role, "primary")


class TestOtherModelFamilies(unittest.TestCase):
    def test_diffusers_pipeline_stays_a_single_capability(self):
        i = info(pipeline_tag="text-to-image", model_index={"_class_name": "StableDiffusionPipeline", "unet": ["d", "U"], "vae": ["d", "V"]},
                 generation_config={"task_to_id": {"unknown_mode": 1}})
        self.assertEqual([c.name for c in an.detect_capabilities(i)], ["text-to-image"])

    def test_sentence_embedding_model_stays_a_single_capability(self):
        i = info(pipeline_tag="sentence-similarity", library_name="sentence-transformers", config={"architectures": ["AcmeModel"]})
        self.assertEqual([c.name for c in an.detect_capabilities(i)], ["sentence-similarity"])

    def test_vision_model_with_text_mode_token_keeps_image_input(self):
        cfg = {"architectures": ["AcmeForImageClassification"], "model_type": "acme_vit"}
        caps = named(an.detect_capabilities(info(pipeline_tag="image-classification", config=cfg,
                                                 generation_config={"task_to_id": {"image-to-text": 3}})))
        self.assertEqual(caps["image-to-text"].input_modalities, ["image"])
        self.assertEqual(caps["image-to-text"].status, "supported")


class TestIntegrityAndGeneratorRequest(unittest.TestCase):
    def test_task_that_differs_from_the_primary_capability_is_rejected(self):
        import dataclasses
        a = an.analyze(info(pipeline_tag="automatic-speech-recognition", config=SPEECH_CFG, generation_config=TOKENS))
        with self.assertRaises(gg.GenerationError):
            gg.check_analysis_integrity(dataclasses.replace(a, task="evil-task"))
        gg.check_analysis_integrity(a)  # the untampered analysis still passes

    def test_request_tells_opencode_how_to_treat_a_mode(self):
        a = an.analyze(info(pipeline_tag="automatic-speech-recognition", config=SPEECH_CFG, generation_config=TOKENS))
        req = gg.build_request(a)
        self.assertIn("task-conditioned mode", req)
        self.assertIn("predict_translation", req)
        self.assertIn('"task_tokens"', req)

    def test_combined_report_carries_status_and_provenance(self):
        a = an.analyze(info(pipeline_tag="automatic-speech-recognition", config=SPEECH_CFG, generation_config=TOKENS))
        caps = {c["name"]: c for c in a.combined_analysis["python_analysis"]["capabilities"]}
        self.assertEqual(caps["translation"]["status"], "supported")
        self.assertEqual(caps["translation"]["evidence"][0]["signal"], "generation_task_token")
        self.assertEqual(caps["translation"]["input_modalities"], ["audio"])
        json.dumps(a.combined_analysis)

    def test_uncertain_capability_is_reported_but_not_built(self):
        a = an.analyze(info(pipeline_tag="automatic-speech-recognition", config=SPEECH_CFG, generation_config=TOKENS,
                            tags=["summarization"]))
        caps = {c["name"]: c for c in a.combined_analysis["python_analysis"]["capabilities"]}
        self.assertEqual(caps["summarization"]["status"], "uncertain")
        self.assertNotIn("summarization", [c.name for c in gg.interface_capabilities(a)])


if __name__ == "__main__":
    unittest.main()
