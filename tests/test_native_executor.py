import json
import subprocess
import unittest
from pathlib import Path


class TestGeneratedOnnxFixture(unittest.TestCase):
    def test_real_onnxruntime_generated_style_callback_returns_actual_output(self):
        root = Path(__file__).resolve().parents[1]
        py = root / ".onnx-runtime" / "Scripts" / "python.exe"
        if not py.is_file(): self.skipTest("project-local ONNX Runtime environment absent")
        result = subprocess.run([str(py), str(Path(__file__).with_name("onnx_generated_app_fixture.py")), str(root)],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["output"], {"output": [3.0]})


if __name__ == "__main__": unittest.main()
