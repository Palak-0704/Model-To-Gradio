"""Executed under the project-local Python 3.12 ONNX Runtime environment."""
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import onnx
from onnx import TensorProto, helper

root = Path(sys.argv[1]); sys.path.insert(0, str(root))
from m2g.native_executor import execute_verified_onnx
from m2g.onnx_runtime import OnnxArtifact

model_path = root / "model_to_gradio" / ".test-tmp" / "deterministic_add.onnx"; model_path.parent.mkdir(parents=True, exist_ok=True)
x = helper.make_tensor_value_info("input", TensorProto.FLOAT, [1])
y = helper.make_tensor_value_info("output", TensorProto.FLOAT, [1])
one = helper.make_tensor("one", TensorProto.FLOAT, [1], [1.0])
graph = helper.make_graph([helper.make_node("Add", ["input", "one"], ["output"])], "add", [x], [y], [one])
model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 18)])
model.ir_version = 13  # supported by the installed ONNX Runtime; part of this explicit fixture contract
onnx.save(model, model_path)
data = model_path.read_bytes(); artifact = OnnxArtifact("fixture.onnx", "a" * 40, len(data), hashlib.sha256(data).hexdigest())

# This is the same shape an OpenCode native response is required to implement:
# evidence-backed preprocessing -> approved executor -> postprocessing -> Gradio callback.
def preprocess(value): return {"input": np.asarray([float(value)], dtype=np.float32)}
def predict(value): return {name: np.asarray(result).tolist() for name, result in execute_verified_onnx(artifact, model_path, preprocess(value)).items()}
actual = predict(2.0)
assert actual == {"output": [3.0]}, actual
print(json.dumps({"output": actual, "runtime": "onnxruntime"}))
