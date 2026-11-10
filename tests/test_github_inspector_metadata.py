import unittest
from types import SimpleNamespace

from m2g import inspector


class MockFetcher:
    def __init__(self, text_files=None, releases=None):
        self.text_files = text_files or {}
        self.releases = releases if releases is not None else []
        self.text_calls = []
        self.json_calls = []

    def fetch(self, url, *args, **kwargs):
        self.text_calls.append(url)
        return SimpleNamespace(body=self.text_files[url].encode("utf-8"))

    def get_text(self, url, *args, **kwargs):
        self.text_calls.append(url)
        return self.text_files[url]

    def get_json(self, url, *args, **kwargs):
        self.json_calls.append(url)
        return self.releases


def make_info():
    return SimpleNamespace(
        config=None,
        model_index=None,
        preprocessor_config=None,
        tokenizer_config=None,
        generation_config=None,
        config_paths={},
        release_assets=[],
        warnings=[],
        fetched=[],
        inspection_gaps=[],
    )


class GitHubConfigMetadataTests(unittest.TestCase):

    def test_root_config_is_discovered_and_parsed(self):
        repo = "example/model"
        sha = "abc123"
        url = f"https://raw.githubusercontent.com/{repo}/{sha}/config.json"
        fetcher = MockFetcher({url: '{"model_type":"bert","hidden_size":128}'})
        info = make_info()

        inspector._inspect_github_model_configs(
            info, [("config.json", 100)], fetcher, repo, sha
        )

        self.assertEqual(info.config["model_type"], "bert")
        self.assertEqual(info.config["hidden_size"], 128)
        self.assertEqual(info.config_paths["config"], "config.json")
        self.assertEqual(len(fetcher.text_calls), 1)

    def test_shallowest_config_path_is_selected(self):
        repo = "example/model"
        sha = "abc123"
        root = f"https://raw.githubusercontent.com/{repo}/{sha}/config.json"
        nested = f"https://raw.githubusercontent.com/{repo}/{sha}/model/config.json"
        fetcher = MockFetcher({
            root: '{"model_type":"root"}',
            nested: '{"model_type":"nested"}',
        })
        info = make_info()

        inspector._inspect_github_model_configs(
            info,
            [("model/config.json", 100), ("config.json", 100)],
            fetcher, repo, sha,
        )

        self.assertEqual(info.config["model_type"], "root")
        self.assertEqual(info.config_paths["config"], "config.json")

    def test_deep_config_path_is_ignored(self):
        repo = "example/model"
        sha = "abc123"
        fetcher = MockFetcher()
        info = make_info()

        inspector._inspect_github_model_configs(
            info, [("a/b/config.json", 100)], fetcher, repo, sha
        )

        self.assertIsNone(info.config)
        self.assertEqual(fetcher.text_calls, [])

    def test_oversized_config_is_not_fetched(self):
        repo = "example/model"
        sha = "abc123"
        fetcher = MockFetcher()
        info = make_info()

        inspector._inspect_github_model_configs(
            info, [("config.json", inspector._MAX_SMALL_FILE + 1)],
            fetcher, repo, sha,
        )

        self.assertIsNone(info.config)
        self.assertEqual(fetcher.text_calls, [])

    def test_invalid_json_is_ignored(self):
        repo = "example/model"
        sha = "abc123"
        url = f"https://raw.githubusercontent.com/{repo}/{sha}/config.json"
        fetcher = MockFetcher({url: "{invalid json"})
        info = make_info()

        inspector._inspect_github_model_configs(
            info, [("config.json", 100)], fetcher, repo, sha
        )

        self.assertIsNone(info.config)


class GitHubReleaseMetadataTests(unittest.TestCase):

    def test_release_metadata_is_collected_without_asset_downloads(self):
        releases = [{
            "tag_name": "v1.0",
            "name": "Version 1",
            "assets": [
                {"name": "model.safetensors", "size": 1234},
                {"name": "config.json", "size": 100},
            ],
        }]
        fetcher = MockFetcher(releases=releases)
        info = make_info()

        inspector._inspect_github_releases(
            info, "https://api.github.com/repos/example/model",
            fetcher, {},
        )

        self.assertEqual(
            info.release_assets,
            [("v1.0/model.safetensors", 1234), ("v1.0/config.json", 100)],
        )
        self.assertEqual(len(fetcher.json_calls), 1)
        self.assertIn("per_page=5", fetcher.json_calls[0])

    def test_release_and_asset_limits_are_respected(self):
        releases = [
            {
                "tag_name": f"v{i}",
                "assets": [
                    {"name": f"asset-{j}.bin", "size": 100}
                    for j in range(20)
                ],
            }
            for i in range(10)
        ]
        fetcher = MockFetcher(releases=releases)
        info = make_info()

        inspector._inspect_github_releases(
            info, "https://api.github.com/repos/example/model",
            fetcher, {},
        )

        self.assertLessEqual(len(info.release_assets), inspector.MAX_RELEASE_ASSETS)
        self.assertLessEqual(
            len({item[0].split("/")[0] for item in info.release_assets}),
            inspector.MAX_RELEASES,
        )

    def test_malformed_release_response_is_safe(self):
        fetcher = MockFetcher(releases={"unexpected": "response"})
        info = make_info()

        inspector._inspect_github_releases(
            info, "https://api.github.com/repos/example/model",
            fetcher, {},
        )

        self.assertEqual(info.release_assets, [])


if __name__ == "__main__":
    unittest.main()
