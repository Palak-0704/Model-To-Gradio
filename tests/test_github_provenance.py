"""Phase 4.3.2 / 4.3.3: GitHub repository provenance in the analysis (inspector facts -> Analysis -> analysis.json).

What is checked: the commit the GitHub files were read at becomes the analysis revision when there is one; the requested revision, its
kind, the canonical repository id, the inspection gaps and the places a linked Hugging Face model was found all reach the serialised
analysis; gaps are reported but never block generation on their own; Hugging Face analyses are unchanged.

All offline: the fake transport of fakehub.py serves canned GitHub / Hugging Face responses. No network, no weights, and no repository
code is run, imported or installed. The GitHub commit is deliberately a different id (GH_SHA) from the Hugging Face commit
(fakehub.SHA), so the two can never be mistaken for each other.
"""
import json
import tempfile
import unittest
from pathlib import Path

from m2g import analyzer as an
from m2g import inspector as ins
from m2g.config import Settings
from m2g.pipeline import run_pipeline
from fakehub import FakeWeb, PRESETS, SHA, add_github_repo, add_hf_model
from test_analyzer import analyze_preset
from test_github_generation import github_repo_for, readme_for

GH_SHA = "b" * 40  # a GitHub commit (the Hugging Face commit of fakehub is "a" * 40)
TAG_SHA = "c" * 40
ALL_GAP_CODES = tuple(dict.fromkeys((*ins.LINK_BLINDING_GAPS, *ins.CODE_BLIND_GAPS, "revision_unpinned")))


def add_pinned_github_repo(web, repo_id, *, branch="main", ref=None, sha=GH_SHA, readme="# Cool AI\nA torch project.\n",
                           requirements="torch>=2.0\n", tree=None, extra_files=None, full_name=None):
    """A GitHub repository whose revision (``ref``, default: its branch) resolves to ``sha``. The commit lookup, the file tree and every
    file are served ONLY at the commit, so a request for the branch name instead of the commit would find nothing."""
    api = f"https://api.github.com/repos/{repo_id}"
    meta = {"default_branch": branch, "description": "A repo", "topics": ["deep-learning"], "language": "Python",
            "license": {"spdx_id": "MIT"}, "private": False, "archived": False}
    if full_name:
        meta["full_name"] = full_name
    web.add(api, meta)
    ref = ref or branch
    if ref != sha:  # a commit id is used as it is; a branch or tag is resolved through the commits API
        web.add(f"{api}/commits/{ref}", sha)
    tree = tree if tree is not None else ["README.md", "requirements.txt"]
    web.add(f"{api}/git/trees/{sha}?recursive=1", {"truncated": False, "tree": [{"path": p, "type": "blob", "size": 100} for p in tree]})
    raw = f"https://raw.githubusercontent.com/{repo_id}/{sha}/"
    web.add(raw + "README.md", readme)
    if requirements is not None:
        web.add(raw + "requirements.txt", requirements)
    for name, body in (extra_files or {}).items():
        web.add(raw + name, body)


def pinned_repo_for(key, **kw):
    """A pinned GitHub repository (same owner/name as the preset's Hugging Face model) whose README points at that model."""
    spec = dict(PRESETS[key])
    model_id = spec.pop("repo_id")
    owner, name = model_id.split("/")
    web = FakeWeb()
    add_hf_model(web, model_id, **spec)
    add_pinned_github_repo(web, f"{owner}/{name}", readme=readme_for(model_id, name), **kw)
    url = f"https://github.com/{owner}/{name}"
    info = ins.inspect_url(url, web.fetcher())
    return web, url, info, an.analyze(info, Settings())


def prov(a):
    return a.ui_hints["github_provenance"]


def gap_notes(a):
    return [u for u in a.uncertainties if u.startswith("Inspection gap")]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.settings = Settings(output_dir=Path(self.tmp.name) / "out")

    def written_analysis(self, res):
        return json.loads((res.out_dir / "analysis.json").read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- 1 + 2: the analysis revision


class TestResolvedRevision(Base):
    def test_the_pinned_commit_is_the_revision_of_a_repository_that_yields_no_app(self):
        web = FakeWeb()
        add_pinned_github_repo(web, "acme/plain", readme="# Plain\nNo model link.\n")
        info = ins.inspect_url("https://github.com/acme/plain", web.fetcher())
        a = an.analyze(info)
        self.assertEqual(info.commit_sha, GH_SHA)
        self.assertEqual((a.status, a.generatable), ("partial", False))
        self.assertEqual(a.revision, GH_SHA)
        self.assertEqual(prov(a)["commit_sha"], GH_SHA)
        self.assertTrue(prov(a)["revision_pinned"])
        self.assertEqual((prov(a)["requested_revision"], prov(a)["revision_kind"]), (None, "default-branch"))
        self.assertEqual(prov(a)["inspection_gaps"], [])
        self.assertEqual(gap_notes(a), [])

    def test_a_generated_app_keeps_the_hugging_face_commit_and_the_github_commit_is_recorded(self):
        _, _, info, a = pinned_repo_for("sst2")
        self.assertEqual(info.commit_sha, GH_SHA)
        self.assertTrue(a.generatable, (a.status, a.reasons))
        # what the generated app loads is the Hugging Face model at ITS commit; a GitHub commit can never be a Hugging Face revision
        self.assertEqual(a.revision, SHA)
        self.assertNotEqual(a.revision, GH_SHA)
        self.assertEqual(prov(a)["commit_sha"], GH_SHA)
        self.assertTrue(prov(a)["revision_pinned"])

    def test_an_unresolved_revision_falls_back_to_the_branch_and_is_not_called_pinned(self):
        web = FakeWeb()
        add_github_repo(web, "acme/cool-ai")  # no commit lookup is served: the revision cannot be resolved
        info = ins.inspect_url("https://github.com/acme/cool-ai", web.fetcher())
        a = an.analyze(info)
        self.assertIsNone(info.commit_sha)
        self.assertEqual(a.revision, "main")  # the existing behaviour: the default branch
        self.assertIsNone(prov(a)["commit_sha"])
        self.assertFalse(prov(a)["revision_pinned"])
        self.assertEqual((prov(a)["revision_kind"], prov(a)["default_branch"]), ("default-branch", "main"))

    def test_a_blocked_api_leaves_no_revision_and_no_pinned_claim(self):
        web = FakeWeb()
        add_github_repo(web, "acme/cool-ai", api_status=403)
        a = an.analyze(ins.inspect_url("https://github.com/acme/cool-ai", web.fetcher()))
        self.assertIsNone(a.revision)
        self.assertIsNone(prov(a)["commit_sha"])
        self.assertFalse(prov(a)["revision_pinned"])

    def test_a_branch_or_tag_name_in_commit_sha_is_never_taken_for_a_commit(self):
        web = FakeWeb()
        add_github_repo(web, "acme/cool-ai")
        for bogus in ("main", "v1.0", "a" * 39, "A" * 40, "g" * 40, ""):
            with self.subTest(bogus):
                info = ins.inspect_url("https://github.com/acme/cool-ai", web.fetcher())
                info.commit_sha = bogus
                a = an.analyze(info)
                self.assertEqual(a.revision, "main")
                self.assertIsNone(prov(a)["commit_sha"])
                self.assertFalse(prov(a)["revision_pinned"])

    def test_every_github_file_is_still_read_at_the_resolved_commit(self):
        web = FakeWeb()
        add_pinned_github_repo(web, "acme/plain", tree=["README.md", "requirements.txt", "pkg/model.py"],
                               extra_files={"pkg/model.py": "def predict(x):\n    return x\n"})
        ins.inspect_url("https://github.com/acme/plain", web.fetcher())
        files = [c for c in web.calls if "/git/trees/" in c or "raw.githubusercontent.com/" in c]
        self.assertTrue(files)
        self.assertTrue(all(f"/{GH_SHA}" in c for c in files), files)  # the inspector's pinning is reused, not re-done


# --------------------------------------------------------------------------- 3 + 4: requested revision, kind, canonical identity


class TestRequestedRevisionAndIdentity(Base):
    def test_a_requested_tag_and_its_kind_survive_serialisation(self):
        web = FakeWeb()
        add_pinned_github_repo(web, "acme/proj", ref="v1.0", sha=TAG_SHA)
        info = ins.inspect_url("https://github.com/acme/proj/tree/v1.0", web.fetcher())
        data = json.loads(json.dumps(an.analyze(info).to_dict(), default=str))
        p = data["ui_hints"]["github_provenance"]
        self.assertEqual((p["requested_revision"], p["revision_kind"]), ("v1.0", "requested-ref"))
        self.assertEqual((p["commit_sha"], p["revision_pinned"]), (TAG_SHA, True))
        self.assertEqual(data["revision"], TAG_SHA)

    def test_a_requested_commit_has_the_commit_kind_and_needs_no_lookup(self):
        web = FakeWeb()
        add_pinned_github_repo(web, "acme/proj", ref=GH_SHA, sha=GH_SHA)
        info = ins.inspect_url(f"https://github.com/acme/proj/tree/{GH_SHA}", web.fetcher())
        a = an.analyze(info)
        self.assertEqual((prov(a)["requested_revision"], prov(a)["revision_kind"]), (GH_SHA, "commit"))
        self.assertEqual((prov(a)["commit_sha"], a.revision), (GH_SHA, GH_SHA))
        self.assertFalse([c for c in web.calls if "/commits/" in c])

    def test_the_pipeline_writes_the_provenance_into_analysis_json(self):
        web = FakeWeb()
        add_pinned_github_repo(web, "acme/proj", ref="v1.0", sha=TAG_SHA)
        res = run_pipeline("https://github.com/acme/proj/tree/v1.0", self.settings, web.fetcher())
        self.assertIsNone(res.error)
        self.assertFalse(res.ok)  # no linked model: analysis-only, as before
        data = self.written_analysis(res)
        p = data["ui_hints"]["github_provenance"]
        self.assertEqual((p["requested_revision"], p["revision_kind"], p["commit_sha"]), ("v1.0", "requested-ref", TAG_SHA))
        self.assertEqual((data["revision"], data["source"]["repo_id"]), (TAG_SHA, "acme/proj"))

    def test_a_renamed_repository_keeps_both_identities(self):
        web = FakeWeb()
        add_pinned_github_repo(web, "acme/old-name", full_name="NewOwner/new-name")
        info = ins.inspect_url("https://github.com/acme/old-name", web.fetcher())
        a = an.analyze(info)
        self.assertEqual(info.canonical_repo_id, "NewOwner/new-name")
        self.assertEqual((prov(a)["repo_id"], prov(a)["canonical_repo_id"]), ("acme/old-name", "NewOwner/new-name"))
        self.assertEqual(a.source.repo_id, "acme/old-name")  # the analysed source is still the URL the user gave
        self.assertTrue(any("renamed or transferred" in w for w in a.warnings))  # the inspector's own warning still reaches the analysis

    def test_an_unrenamed_repository_is_its_own_canonical_id(self):
        for full_name in (None, "ACME/Plain"):  # a difference in case only is not a rename
            with self.subTest(full_name):
                web = FakeWeb()
                add_pinned_github_repo(web, "acme/plain", full_name=full_name)
                a = an.analyze(ins.inspect_url("https://github.com/acme/plain", web.fetcher()))
                self.assertEqual((prov(a)["repo_id"], prov(a)["canonical_repo_id"]), ("acme/plain", "acme/plain"))


# --------------------------------------------------------------------------- 5 + 6: inspection gaps


class TestInspectionGaps(Base):
    def test_gaps_recorded_by_the_inspector_appear_with_their_original_text(self):
        web = FakeWeb()
        add_github_repo(web, "acme/cool-ai", api_status=403)
        a = an.analyze(ins.inspect_url("https://github.com/acme/cool-ai", web.fetcher()))
        gap = next(g for g in prov(a)["inspection_gaps"] if g["code"] == "api_unavailable")
        self.assertIn("HTTP 403", gap["message"])
        note = next(u for u in gap_notes(a) if "'api_unavailable'" in u)
        self.assertIn(gap["message"], note)
        self.assertIn("branch, file list and repository metadata are unknown", note)
        self.assertTrue(any("HTTP 403" in w for w in a.warnings))  # the inspector's own warning is not replaced

        web = FakeWeb()
        add_github_repo(web, "acme/cool-ai")  # revision cannot be resolved
        a = an.analyze(ins.inspect_url("https://github.com/acme/cool-ai", web.fetcher()))
        note = next(u for u in gap_notes(a) if "'revision_unpinned'" in u)
        self.assertIn("'main'", note)
        self.assertIn("HTTP 404", note)

    def test_every_declared_gap_code_keeps_its_code_and_message(self):
        web = FakeWeb()
        add_pinned_github_repo(web, "acme/plain")
        info = ins.inspect_url("https://github.com/acme/plain", web.fetcher())
        info.inspection_gaps = [{"code": c, "message": f"message for {c} (HTTP 502)"} for c in ALL_GAP_CODES]
        a = an.analyze(info)
        self.assertEqual(prov(a)["inspection_gaps"], info.inspection_gaps)
        for code in ALL_GAP_CODES:
            self.assertTrue(any(f"'{code}'" in u and f"message for {code} (HTTP 502)" in u for u in gap_notes(a)), code)
        self.assertEqual(len(gap_notes(a)), len(ALL_GAP_CODES))

    def test_gap_and_link_source_text_is_single_line_bounded_and_redacted(self):
        web = FakeWeb()
        add_pinned_github_repo(web, "acme/plain")
        info = ins.inspect_url("https://github.com/acme/plain", web.fetcher())
        token = "hf_" + "A" * 30
        info.inspection_gaps = [{"code": "readme_unavailable", "message": f"first line\nsecond line {token} " + "x" * 400}]
        info.hf_link_sources = {"acme/w": ["README.md\nINJECTED"]}
        a = an.analyze(info)
        message = prov(a)["inspection_gaps"][0]["message"]
        self.assertNotIn("\n", message)
        self.assertNotIn(token, message)
        self.assertIn("[REDACTED]", message)
        self.assertLessEqual(len(message), 240)
        self.assertNotIn("\n", gap_notes(a)[0])
        self.assertEqual(prov(a)["hf_link_sources"], {"acme/w": ["README.md INJECTED"]})

    def test_gaps_change_neither_status_reasons_warnings_nor_generatability(self):
        gaps = [{"code": c, "message": f"message for {c}"} for c in ALL_GAP_CODES]
        web = FakeWeb()
        add_pinned_github_repo(web, "acme/plain", readme="# Plain\nNo model link.\n")
        cases = {"refused repository": ins.inspect_url("https://github.com/acme/plain", web.fetcher()),
                 "generated through the linked model": pinned_repo_for("sst2")[2]}
        for name, info in cases.items():
            with self.subTest(name):
                info.inspection_gaps = []
                base = an.analyze(info)
                info.inspection_gaps = gaps
                with_gaps = an.analyze(info)
                for field in ("status", "generatable", "reasons", "generation_blocked_reasons", "warnings", "revision", "task"):
                    self.assertEqual(getattr(with_gaps, field), getattr(base, field), field)
                self.assertEqual(len(with_gaps.uncertainties), len(base.uncertainties) + len(gaps))
        self.assertTrue(an.analyze(cases["generated through the linked model"]).generatable)

    def test_an_app_is_still_generated_when_the_revision_could_not_be_pinned(self):
        # github_repo_for() serves no commit lookup, so the inspector records the 'revision_unpinned' gap for every such repository
        web, url, gh = github_repo_for("sst2")
        self.assertIn("revision_unpinned", [g["code"] for g in prov(gh)["inspection_gaps"]])
        self.assertTrue(gh.generatable, (gh.status, gh.reasons))
        self.assertEqual((gh.reasons, gh.generation_blocked_reasons), ([], []))
        res = run_pipeline(url, self.settings, web.fetcher())
        self.assertTrue(res.ok, (res.error, res.notes))
        data = self.written_analysis(res)
        self.assertIn("revision_unpinned", [g["code"] for g in data["ui_hints"]["github_provenance"]["inspection_gaps"]])
        self.assertTrue(any(u.startswith("Inspection gap 'revision_unpinned'") for u in data["uncertainties"]))
        self.assertEqual(data["revision"], SHA)  # the app loads the Hugging Face model at its commit

    def test_gaps_never_hide_a_genuine_restriction(self):
        for key, needle in (("pickle", "pickle"), ("remote", "trust_remote_code"), ("gguf", "GGUF")):
            with self.subTest(key):
                _, _, gh = github_repo_for(key)
                self.assertTrue(gap_notes(gh))
                self.assertEqual(gh.status, "partial")
                self.assertFalse(gh.generatable)
                self.assertIn(needle, " ".join(gh.reasons))


# --------------------------------------------------------------------------- 7: where the linked Hugging Face models were found


class TestHuggingFaceLinkSources(Base):
    CODE = ('from transformers import AutoModel\n\nmodel = AutoModel.from_pretrained("acme/in-code")\n'
            'shared = AutoModel.from_pretrained("acme/shared")\n')
    README = "Weights: https://huggingface.co/acme/in-readme and https://huggingface.co/acme/shared\n"

    def test_the_inspector_records_the_file_each_link_was_found_in(self):
        web = FakeWeb()
        add_pinned_github_repo(web, "acme/kit", readme=self.README, requirements=None, tree=["README.md", "pkg/model.py"],
                               extra_files={"pkg/model.py": self.CODE})
        info = ins.inspect_url("https://github.com/acme/kit", web.fetcher())
        self.assertEqual(info.linked_hf_models, ["acme/in-readme", "acme/shared", "acme/in-code"])  # the link list itself is unchanged
        self.assertEqual(info.hf_link_sources, {"acme/in-readme": ["README.md"], "acme/shared": ["README.md", "pkg/model.py"],
                                                "acme/in-code": ["pkg/model.py"]})

    def test_the_analysis_preserves_the_link_sources_without_changing_the_links(self):
        web = FakeWeb()
        add_pinned_github_repo(web, "acme/kit", readme=self.README, requirements=None, tree=["README.md", "pkg/model.py"],
                               extra_files={"pkg/model.py": self.CODE})
        info = ins.inspect_url("https://github.com/acme/kit", web.fetcher())
        a = an.analyze(info)
        self.assertEqual(prov(a)["hf_link_sources"], info.hf_link_sources)
        self.assertEqual(a.linked_hf_models, info.linked_hf_models)
        self.assertIsNone(a.primary_model)  # provenance does not decide which link is the repository's own model
        self.assertEqual(a.status, "partial")
        self.assertFalse([c for c in web.calls if "huggingface.co/api/models" in c])

    def test_link_sources_survive_the_use_of_the_linked_model(self):
        _, _, gh = github_repo_for("sst2")
        model_id = PRESETS["sst2"]["repo_id"]
        self.assertTrue(gh.generatable)
        self.assertEqual(gh.primary_model, model_id)
        self.assertEqual(prov(gh)["hf_link_sources"], {model_id: ["README.md"]})

    def test_the_readme_is_named_by_the_path_it_was_read_from_when_the_api_is_blocked(self):
        web = FakeWeb()
        add_github_repo(web, "acme/cool-ai", api_status=403, readme="See https://huggingface.co/acme/w1\n")
        info = ins.inspect_url("https://github.com/acme/cool-ai", web.fetcher())
        self.assertEqual(info.hf_link_sources, {"acme/w1": ["README.md"]})
        self.assertEqual(prov(an.analyze(info))["hf_link_sources"], {"acme/w1": ["README.md"]})

    def test_a_repository_without_links_has_no_link_sources(self):
        web = FakeWeb()
        add_pinned_github_repo(web, "acme/plain", readme="# Plain\nNo model link.\n")
        info = ins.inspect_url("https://github.com/acme/plain", web.fetcher())
        self.assertEqual(info.hf_link_sources, {})
        self.assertEqual(prov(an.analyze(info))["hf_link_sources"], {})


# --------------------------------------------------------------------------- 8: Hugging Face analyses are unchanged


class TestHuggingFaceAnalysisIsUnchanged(Base):
    def test_a_hugging_face_analysis_carries_no_github_provenance(self):
        a = analyze_preset("sst2")
        self.assertEqual((a.host, a.revision, a.model_source), ("huggingface", SHA, None))
        self.assertNotIn("github_provenance", a.ui_hints)
        self.assertNotIn("github_provenance", json.dumps(a.to_dict(), default=str))
        self.assertEqual(gap_notes(a), [])

    def test_github_provenance_fields_on_a_hugging_face_inspection_are_ignored(self):
        web = FakeWeb()
        from fakehub import hf
        hf(web, "sst2")
        url = "https://huggingface.co/" + PRESETS["sst2"]["repo_id"]
        base = an.analyze(ins.inspect_url(url, web.fetcher()), Settings())
        info = ins.inspect_url(url, web.fetcher())
        info.commit_sha, info.canonical_repo_id = GH_SHA, "other/name"
        info.inspection_gaps = [{"code": "api_unavailable", "message": "must not appear"}]
        info.hf_link_sources = {"acme/w": ["README.md"]}
        self.assertEqual(an.analyze(info, Settings()).to_dict(), base.to_dict())

    def test_a_hugging_face_run_still_generates_and_pins_its_own_commit(self):
        web = FakeWeb()
        from fakehub import hf
        hf(web, "sst2")
        res = run_pipeline("https://huggingface.co/" + PRESETS["sst2"]["repo_id"], self.settings, web.fetcher())
        self.assertTrue(res.ok, (res.error, res.notes))
        data = self.written_analysis(res)
        self.assertEqual(data["revision"], SHA)
        self.assertNotIn("github_provenance", data["ui_hints"])


if __name__ == "__main__":
    unittest.main()
