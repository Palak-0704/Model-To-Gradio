"""Unit tests for the approved data-only ONNX adapter; no ONNX file is executed."""
import tempfile
import unittest
import hashlib
from pathlib import Path

from m2g.onnx_runtime import OnnxRuntimeError, OnnxSession, providers, select_artifact, verify_artifact


class Node:
    def __init__(self, name, type_, shape):
        self.name, self.type, self.shape = name, type_, shape


class FakeSession:
    def get_inputs(self): return [Node("image", "tensor(float)", [None, 3, None, None]), Node("scale", "tensor(float)", [1])]
    def get_outputs(self): return [Node("scores", "tensor(float)", [None, 10]), Node("boxes", "tensor(float)", [None, 4])]
    def run(self, names, feeds): return [feeds["scale"], [[0, 0, 1, 1]]]


class FakeOrt:
    def __init__(self): self.calls = []
    def get_available_providers(self): return ["CPUExecutionProvider", "CUDAExecutionProvider"]
    def InferenceSession(self, path, providers): self.calls.append((path, providers)); return FakeSession()


class TestOnnxRuntime(unittest.TestCase):
    def model(self):
        d = tempfile.TemporaryDirectory(); self.addCleanup(d.cleanup)
        path = Path(d.name) / "model.onnx"; path.write_bytes(b"not parsed by fake runtime")
        return path

    def test_metadata_dynamic_dimensions_multiple_io_and_gpu_preference(self):
        ort = FakeOrt(); session = OnnxSession(self.model(), prefer_gpu=True, ort_module=ort)
        self.assertEqual(session.inputs[0].shape, [None, 3, None, None])
        self.assertEqual([x.name for x in session.outputs], ["scores", "boxes"])
        self.assertEqual(ort.calls[0][1], ["CUDAExecutionProvider", "CPUExecutionProvider"])
        self.assertEqual(session.run({"image": object(), "scale": [1.0]})["boxes"], [[0, 0, 1, 1]])

    def test_cpu_fallback_and_exact_input_validation(self):
        class CpuOrt(FakeOrt):
            def get_available_providers(self): return ["CPUExecutionProvider"]
        session = OnnxSession(self.model(), prefer_gpu=True, ort_module=CpuOrt())
        self.assertEqual(session.inputs[1].name, "scale")
        with self.assertRaises(OnnxRuntimeError): session.run({"image": object()})

    def test_invalid_path_and_no_provider_are_rejected(self):
        with self.assertRaises(OnnxRuntimeError): OnnxSession("missing.onnx", ort_module=FakeOrt()).load()
        class Empty:
            def get_available_providers(self): return []
        with self.assertRaises(OnnxRuntimeError): providers(Empty())

    def test_runtime_load_error_is_clear(self):
        class Broken(FakeOrt):
            def InferenceSession(self, path, providers): raise RuntimeError("unsupported operator")
        with self.assertRaisesRegex(OnnxRuntimeError, "unsupported operator"):
            OnnxSession(self.model(), ort_module=Broken()).load()

    def test_explicit_artifact_selection_and_provenance_verification(self):
        path = self.model(); data = path.read_bytes(); digest = hashlib.sha256(data).hexdigest()
        artifact = select_artifact([{"path": "models/net.onnx", "revision": "f" * 40, "size": len(data), "sha256": digest}],
                                   path="models/net.onnx", revision="f" * 40)
        self.assertEqual(verify_artifact(artifact, path), path)
        with self.assertRaises(OnnxRuntimeError):
            select_artifact([], path="models/net.onnx", revision="f" * 40)
        bad = type(artifact)(artifact.path, artifact.revision, artifact.size, "0" * 64)
        with self.assertRaisesRegex(OnnxRuntimeError, "SHA-256"): verify_artifact(bad, path)


if __name__ == "__main__": unittest.main()
