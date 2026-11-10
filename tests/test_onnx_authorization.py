"""Explicit local ONNX acquisition remains separate from metadata inspection."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from m2g import web_api
from m2g.package_store import MANIFEST_NAME, build_manifest


class TestOnnxAuthorization(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.app = self.root / "pkg"; self.app.mkdir()
        files = {"app.py": "x", "loader.py": "x", "requirements.txt": "", "analysis.json": "{}", "colab_notebook.ipynb": "{}"}
        (self.app / MANIFEST_NAME).write_text(build_manifest(files, slug="pkg", source_repo_id="owner/repo", source_kind="github",
            model_id="owner/repo", revision="a" * 40, task=None, fingerprint="f" * 64, generator="g" * 64, readiness="scaffold"), encoding="utf-8")
        self.detail = {"analysis": {"revision": "a" * 40, "native": {"inference_contract": {"revision": "a" * 40,
            "weight_files": ["weights/model.onnx"]}}}}

    def artifact(self):
        p = self.root / "upload.onnx"; p.write_bytes(b"onnx-data"); return p

    def test_single_candidate_can_be_selected_only_during_explicit_acquisition(self):
        choice = web_api._selection_for(self.detail, {"revision": "a" * 40})
        rec = web_api._record_local_onnx(self.app, choice, str(self.artifact()))
        self.assertEqual(rec["verification"], "locally_calculated_only")
        self.assertEqual(rec["source"], "user_provided_local")
        self.assertTrue((self.app / "model.onnx").is_file())
        manifest = json.loads((self.app / MANIFEST_NAME).read_text(encoding="utf-8"))
        self.assertEqual(manifest["readiness"]["state"], "scaffold")

    def test_ambiguous_undeclared_and_revision_mismatch_are_refused(self):
        detail = {"analysis": {"revision": "a" * 40, "native": {"inference_contract": {"revision": "a" * 40,
            "weight_files": ["a.onnx", "b.onnx"]}}}}
        with self.assertRaises(ValueError): web_api._selection_for(detail, {"revision": "a" * 40})
        with self.assertRaises(ValueError): web_api._selection_for(self.detail, {"artifact_path": "other.onnx", "revision": "a" * 40})
        with self.assertRaises(ValueError): web_api._selection_for(self.detail, {"revision": "b" * 40})

    def test_non_onnx_local_input_is_refused(self):
        bad = self.root / "bad.txt"; bad.write_text("x", encoding="utf-8")
        choice = web_api._selection_for(self.detail, {"revision": "a" * 40})
        with self.assertRaises(ValueError): web_api._record_local_onnx(self.app, choice, str(bad))


if __name__ == "__main__": unittest.main()
