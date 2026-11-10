import os
import tempfile
import unittest
from pathlib import Path

from m2g import config


class TestConfig(unittest.TestCase):
    def test_project_structure_present(self):
        for rel in ["m2g/__init__.py", "m2g/config.py", "requirements.txt", ".gitignore", ".env.example"]:
            self.assertTrue((config.PROJECT_ROOT / rel).is_file(), rel)

    def test_defaults_are_secure(self):
        s = config.Settings()
        self.assertFalse(s.allow_remote_code)
        self.assertFalse(s.allow_pickle_weights)

    def test_tokens_not_in_repr(self):
        s = config.Settings(hf_token="hf_SECRETSECRETSECRET1234", github_token="ghp_SECRETSECRETSECRET1234")
        self.assertNotIn("SECRET", repr(s))
        self.assertNotIn("SECRET", str(s))

    def test_from_env(self):
        old = dict(os.environ)
        try:
            os.environ["M2G_ALLOW_REMOTE_CODE"] = "1"
            os.environ["HF_TOKEN"] = "hf_x"
            s = config.Settings.from_env()
            self.assertTrue(s.allow_remote_code)
            self.assertEqual(s.hf_token, "hf_x")
        finally:
            os.environ.clear()
            os.environ.update(old)

    def test_write_access_ok_and_probe_cleaned(self):
        with tempfile.TemporaryDirectory() as d:
            ok, detail = config.check_write_access(Path(d) / "nested" / "out")
            self.assertTrue(ok, detail)
            self.assertEqual(list((Path(d) / "nested" / "out").iterdir()), [])

    def test_write_access_failure_reported(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "afile"
            f.write_text("x")
            ok, detail = config.check_write_access(f / "sub")  # parent is a file -> must fail
            self.assertFalse(ok)
            self.assertTrue(detail)


if __name__ == "__main__":
    unittest.main()
