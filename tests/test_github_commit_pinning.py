import unittest

from m2g import inspector
from m2g.config import Settings
from fakehub import FakeWeb, SHA


class GitHubCommitPinningTests(unittest.TestCase):
    def test_all_github_files_are_fetched_from_resolved_commit(self):
        web = FakeWeb()
        repo = "example/model"
        api = f"https://api.github.com/repos/{repo}"
        raw = f"https://raw.githubusercontent.com/{repo}/{SHA}/"

        web.add(api, {
            "default_branch": "main",
            "description": "Test repository",
            "topics": ["machine-learning"],
            "language": "Python",
            "license": {"spdx_id": "MIT"},
            "private": False,
            "archived": False,
            "fork": False,
        })
        web.add(f"{api}/commits/main", SHA)
        web.add(f"{api}/git/trees/{SHA}?recursive=1", {
            "truncated": False,
            "tree": [
                {"path": "README.md", "type": "blob", "size": 100},
                {"path": "requirements.txt", "type": "blob", "size": 100},
                {"path": "main.py", "type": "blob", "size": 100},
            ],
        })
        web.add(raw + "README.md", "# Example repository")
        web.add(raw + "requirements.txt", "torch>=2.0")
        web.add(raw + "main.py", "def predict(x):\n    return x\n")

        result = inspector.inspect_url(
            f"https://github.com/{repo}",
            web.fetcher(),
            Settings(),
        )

        self.assertEqual(result.commit_sha, SHA)

        tree_calls = [u for u in web.calls if "/git/trees/" in u]
        raw_calls = [u for u in web.calls if "raw.githubusercontent.com/" in u]

        self.assertTrue(tree_calls)
        self.assertTrue(raw_calls)
        self.assertTrue(all(f"/git/trees/{SHA}?" in u for u in tree_calls))
        self.assertTrue(all(f"/{SHA}/" in u for u in raw_calls))
        self.assertFalse(any("/git/trees/main?" in u for u in tree_calls))


if __name__ == "__main__":
    unittest.main()

