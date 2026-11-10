import unittest

from m2g.native_readiness import NativeReadinessError, readiness_from_validation, require_end_to_end, require_generation_prerequisites


class TestNativeReadiness(unittest.TestCase):
    def valid(self):
        return {"artifact_verified": True, "model_loaded": True, "preprocessing_verified": True,
                "inference_executed": True, "postprocessing_verified": True, "real_outputs_verified": True,
                "fixture_evidence": True, "fixture_kind": "documented", "synthetic_input": False}

    def test_only_real_end_to_end_evidence_is_ready(self):
        self.assertEqual(readiness_from_validation(self.valid())[0], "ready")

    def test_session_or_random_tensor_is_not_working_inference(self):
        partial = self.valid(); partial["inference_executed"] = False
        self.assertEqual(readiness_from_validation(partial)[0], "scaffold")
        random = self.valid(); random["synthetic_input"] = True
        with self.assertRaises(NativeReadinessError): require_end_to_end(random)

    def test_generation_can_precede_runtime_but_never_ready_transition(self):
        pending = self.valid()
        pending.update({"artifact_path": "weights/model.onnx", "revision": "a" * 40,
                        "adapter_verified": True, "contract_verified": True,
                        "preprocessing_evidence": True, "postprocessing_evidence": True,
                        "model_loaded": False, "inference_executed": False, "real_outputs_verified": False})
        require_generation_prerequisites(pending)
        self.assertEqual(readiness_from_validation(pending)[0], "scaffold")

    def test_artifact_authorization_is_not_a_code_generation_requirement(self):
        pending = {"artifact_path": "weights/model.onnx", "revision": "a" * 40,
                   "adapter_verified": True, "contract_verified": True,
                   "preprocessing_evidence": True, "postprocessing_evidence": True}
        require_generation_prerequisites(pending)
        self.assertEqual(readiness_from_validation(pending)[0], "scaffold")


if __name__ == "__main__": unittest.main()
