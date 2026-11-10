"""Unit tests for the claim-level validation engine (m2g.claims).

Every conclusion must carry its evidence, an explicit status and a confidence derived only from that evidence. These tests
exercise the real functions: no mocks, and the honesty rules (conflicts are kept, a lone heuristic is never a fact, missing
evidence is UNKNOWN rather than a guess) are asserted directly.
"""
import unittest
from types import SimpleNamespace

from m2g import claims as C


def ref(id_, directness=C.STRUCTURAL, value="v", source_kind="python_analyzer", location="l", agrees=True):
    return C.EvidenceRef(id_, "field", source_kind, directness, value=value, detail="d", location=location, agrees=agrees)


class TestValidate(unittest.TestCase):
    def test_direct_evidence_with_no_contradiction_is_verified(self):
        claim = C.validate("task", "text-classification", [ref("a", C.DIRECT)])
        self.assertEqual(claim.status, C.VERIFIED)
        self.assertGreater(claim.confidence, 0.8)
        self.assertEqual(claim.supporting, ["a"])
        self.assertEqual(claim.contradicting, [])

    def test_structural_evidence_is_supported(self):
        self.assertEqual(C.validate("task", "t", [ref("a", C.STRUCTURAL)]).status, C.SUPPORTED)

    def test_heuristic_only_is_inferred_and_never_a_fact(self):
        claim = C.validate("task", "t", [ref("a", C.HEURISTIC)])
        self.assertEqual(claim.status, C.INFERRED)
        self.assertLessEqual(claim.confidence, 0.4)

    def test_no_evidence_is_unknown_with_zero_confidence(self):
        claim = C.validate("task", None, [])
        self.assertEqual(claim.status, C.UNKNOWN)
        self.assertEqual(claim.confidence, 0.0)
        self.assertTrue(claim.unknowns)

    def test_conflict_is_preserved_and_penalised(self):
        claim = C.validate("task", "t", [ref("a", C.DIRECT, agrees=True), ref("b", C.STRUCTURAL, value="u", location="l2", agrees=False)])
        self.assertEqual(claim.status, C.CONFLICTED)
        self.assertEqual(claim.supporting, ["a"])
        self.assertEqual(claim.contradicting, ["b"])
        self.assertIn("disagree", claim.explanation)
        self.assertTrue(claim.confidence_basis["conflict"])

    def test_independent_corroboration_raises_confidence(self):
        one = C.validate("t", "v", [ref("a", C.STRUCTURAL)])
        two = C.validate("t", "v", [ref("a", C.STRUCTURAL, location="l1"), ref("b", C.STRUCTURAL, location="l2")])
        self.assertGreater(two.confidence, one.confidence)

    def test_duplicate_observations_do_not_corroborate(self):
        dup = C.validate("t", "v", [ref("a", C.STRUCTURAL, location="same"), ref("b", C.STRUCTURAL, location="same")])
        self.assertEqual(dup.confidence_basis["independent_support"], 1)

    def test_coverage_gap_lowers_confidence(self):
        base = C.validate("t", "v", [ref("a", C.STRUCTURAL)])
        gapped = C.validate("t", "v", [ref("a", C.STRUCTURAL)], coverage_gap=True)
        self.assertLess(gapped.confidence, base.confidence)

    def test_confidence_is_bounded(self):
        many = [ref(f"a{i}", C.DIRECT, location=f"l{i}") for i in range(20)]
        self.assertLessEqual(C.validate("t", "v", many).confidence, 1.0)


class TestBuildClaimsFromAnalysis(unittest.TestCase):
    def _analysis(self):
        ev = SimpleNamespace(signal="config_architectures", source="config.json", detail="TextClassification", weight=0.9,
                             tier="structural", task="text-classification")
        return SimpleNamespace(task="text-classification", task_confidence=0.8, capabilities=[], task_evidence=[ev],
                               dependency_evidence={"torch": "declared in requirements.txt"}, frameworks=["PyTorch"],
                               native={}, evidence=None, uncertainties=[], library="pytorch", revision="abc")

    def test_claims_carry_evidence_ids_and_status(self):
        claims = {c.field: c for c in C.build_claims(self._analysis())}
        self.assertEqual(claims["task"].value, "text-classification")
        self.assertEqual(claims["task"].status, C.SUPPORTED)
        self.assertTrue(claims["task"].supporting)
        self.assertEqual(claims["frameworks"].value, ["PyTorch"])

    def test_unknown_fields_are_reported_not_guessed(self):
        a = self._analysis()
        a.task = None
        claims = {c.field: c for c in C.build_claims(a)}
        self.assertEqual(claims["task"].status, C.UNKNOWN)

    def test_source_analysis_contributes_loading_and_io_claims(self):
        src = SimpleNamespace(
            load_sites=[SimpleNamespace(framework="transformers", family="from_pretrained", call="AutoModel.from_pretrained",
                                        module="m.py", line=3, model_id="x/y")],
            infer_sites=[{"kind": "generate", "call": "model.generate", "module": "m.py", "line": 9}],
            io_contract={"inputs": [{"modality": "text", "detail": "parameter 'text'", "source": "m.py:predict()"}],
                         "outputs": [{"kind": "text", "detail": "decode call", "source": "m.py:decode()"}]})
        claims = {c.field: c for c in C.build_claims(self._analysis(), src=src)}
        self.assertEqual(claims["loading_strategy"].value, ["transformers"])
        self.assertEqual(claims["inputs"].value, ["text"])
        self.assertEqual(claims["outputs"].value, ["text"])


class TestReport(unittest.TestCase):
    def test_report_tallies_statuses(self):
        rep = C.claims_report([C.validate("a", "x", [ref("a", C.DIRECT)]), C.validate("b", None, [])])
        self.assertEqual(rep["summary"]["total"], 2)
        self.assertEqual(rep["summary"]["by_status"][C.VERIFIED], 1)
        self.assertIn("b", rep["summary"]["unknown"])
        self.assertEqual(rep["schema_version"], "1.0")


class TestEvidenceBundleRefs(unittest.TestCase):
    """``refs_from_evidence`` must read the evidence digest's ``items`` MAPPING (its real shape), not a list."""

    def _a(self, items):
        a = SimpleNamespace(task=None, task_confidence=0.0, capabilities=[], task_evidence=[], dependency_evidence={},
                            frameworks=[], native={}, uncertainties=[], library="unknown", revision=None)
        a.evidence = {"items": items}
        return a

    def test_the_dict_shape_is_read(self):
        a = self._a({"file:config.json": {"kind": "json", "status": "collected", "location": "config.json"}})
        refs = C.refs_from_evidence(a)
        self.assertEqual([r.id for r in refs], ["ev:file:config.json"])

    def test_the_list_shape_is_also_read(self):
        a = self._a([{"key": "file:config.json", "kind": "json", "status": "collected"}])
        self.assertEqual([r.id for r in C.refs_from_evidence(a)], ["ev:file:config.json"])

    def test_a_collected_tensor_header_yields_a_parameter_and_format_ref(self):
        a = self._a({"tensors:file:model.safetensors": {"kind": "tensor-header", "status": "collected",
                                                        "tensor_summary": {"format": "safetensors", "parameters": 12345}}})
        by_field = {}
        for r in C.refs_from_evidence(a):
            by_field.setdefault(r.field, []).append(r.value)
        self.assertEqual(by_field["parameter_count"], [12345])
        self.assertEqual(by_field["weight_format"], ["safetensors"])

    def test_unusable_items_contribute_nothing(self):
        a = self._a({"file:config.json": {"kind": "json", "status": "inaccessible"}})
        self.assertEqual(C.refs_from_evidence(a), [])


class TestReaRefs(unittest.TestCase):
    """REA's own header read becomes claim evidence; a failed/absent inspection never becomes a 'verified' fact."""

    def _a(self, rea):
        a = SimpleNamespace(task="text-classification", task_confidence=0.9, capabilities=[], task_evidence=[],
                            dependency_evidence={}, frameworks=["PyTorch"], native={}, uncertainties=[], library="pytorch",
                            revision="abc", evidence=None)
        a.rea = rea
        a.hardware = SimpleNamespace(params=1000, params_source="hub")
        return a

    def test_rea_header_summaries_become_parameter_format_and_architecture_refs(self):
        a = self._a({"header_summaries": [{"file": "model.safetensors", "format": "safetensors", "parameters": 1000,
                                           "architecture": "llama"}],
                     "calls": [{"tool": "inspect_model_artifact", "ok": True, "target": "model.safetensors", "output": "ok"}]})
        claims = {c.field: c for c in C.build_claims(a)}
        self.assertEqual(claims["parameter_count"].value, 1000)
        self.assertEqual(claims["parameter_count"].status, C.VERIFIED)  # python + rea agree
        self.assertIn("safetensors", claims["weight_format"].value)
        self.assertIn("llama", claims["architecture"].value)
        self.assertTrue(claims["rea_evidence"].supporting)

    def test_a_disagreement_between_python_and_rea_is_a_conflict(self):
        a = self._a({"header_summaries": [{"file": "model.safetensors", "format": "safetensors", "parameters": 2000}],
                     "calls": [{"tool": "inspect_model_artifact", "ok": True, "target": "model.safetensors", "output": "ok"}]})
        claims = {c.field: c for c in C.build_claims(a)}
        self.assertEqual(claims["parameter_count"].status, C.CONFLICTED)
        self.assertEqual(claims["parameter_count"].supporting, ["py:params"])
        self.assertIn("rea:params:total", claims["parameter_count"].contradicting)

    def test_a_failed_rea_call_contributes_no_reference(self):
        a = self._a({"header_summaries": [], "calls": [{"tool": "inspect_model_artifact", "ok": False, "error": "refused"}]})
        claims = {c.field: c for c in C.build_claims(a)}
        self.assertEqual(claims["rea_evidence"].status, C.UNKNOWN)
        self.assertEqual(claims["rea_evidence"].confidence, 0.0)

    def test_no_rea_result_leaves_the_rea_claims_unknown(self):
        a = self._a(None)
        claims = {c.field: c for c in C.build_claims(a)}
        self.assertEqual(claims["rea_evidence"].status, C.UNKNOWN)
        self.assertEqual(claims["rea_investigation"].status, C.UNKNOWN)


if __name__ == "__main__":
    unittest.main()
