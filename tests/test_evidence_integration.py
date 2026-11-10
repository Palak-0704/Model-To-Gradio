"""Integration of the Phase 1 evidence package into analysis and generation. No network: bundles are built by hand."""
from __future__ import annotations

import json

from m2g.analyzer import analyze
from m2g.colab_gen import _evidence_md
from m2g.evidence import (COLLECTED, INACCESSIBLE, INVALID, MISSING, SKIPPED, EvidenceBundle, EvidenceItem, Provenance)
from m2g.evidence_bridge import (EvidenceContext, attach_to_info, build_evidence_digest, evidence_uncertainties,
                                 fill_missing_metadata, gather_evidence)
from m2g.gradio_gen import _facts, build_request
from m2g.inspector import RepoInfo
from m2g.security import parse_source_url

SHA = "a" * 40
CFG = {"model_type": "bert", "architectures": ["BertForSequenceClassification"], "hidden_size": 32, "num_hidden_layers": 2,
       "vocab_size": 100}


def _prov(key: str = "config.json", revision: str = SHA) -> Provenance:
    return Provenance("huggingface_repo_file", f"https://huggingface.co/acme/tiny/resolve/{revision}/{key}", "http_get", revision,
                      "2026-01-01T00:00:00Z")


def _item(key: str, status: str, data=None, error=None, kind: str = "json") -> EvidenceItem:
    return EvidenceItem(key, kind, status, _prov(key.split(":", 1)[-1]), data=data, error=error)


def _info(config=None, **kw) -> RepoInfo:
    return RepoInfo(source=parse_source_url("https://huggingface.co/acme/tiny"), revision_sha=SHA, pipeline_tag="text-classification",
                    library_name="transformers", license="mit", files=[("config.json", 500), ("model.safetensors", 4_000_000)],
                    config=config, **kw)


def _bundle(*items: EvidenceItem, resolved: str = SHA) -> EvidenceBundle:
    b = EvidenceBundle(repo_id="acme/tiny", resolved_revision=resolved, collected_at="2026-01-01T00:00:00Z")
    for i in items:
        b.add(i)
    return b


# ---------------------------------------------------------------- filling gaps


def test_collected_evidence_fills_only_missing_fields_with_provenance():
    info = _info(config=None, tokenizer_config={"chat_template": "x"})
    b = _bundle(_item("file:config.json", COLLECTED, CFG), _item("file:tokenizer_config.json", COLLECTED, {"other": 1}))
    filled = fill_missing_metadata(info, b)
    assert info.config == CFG
    assert info.tokenizer_config == {"chat_template": "x"}  # already known: never overwritten
    assert [f["field"] for f in filled] == ["config"]
    assert filled[0]["from"] == "file:config.json" and filled[0]["revision"] == SHA and filled[0]["location"]


def test_unusable_evidence_never_fills_anything():
    info = _info(config=None)
    for status in (MISSING, INACCESSIBLE, INVALID, SKIPPED):
        assert fill_missing_metadata(info, _bundle(_item("file:config.json", status, None, "boom"))) == []
        assert info.config is None


def test_evidence_from_another_revision_is_not_used():
    info = _info(config=None)
    assert fill_missing_metadata(info, _bundle(_item("file:config.json", COLLECTED, CFG), resolved="b" * 40)) == []
    assert info.config is None


def test_github_repository_without_linked_model_fills_nothing():
    info = RepoInfo(source=parse_source_url("https://github.com/acme/repo"))
    assert fill_missing_metadata(info, _bundle(_item("file:config.json", COLLECTED, CFG))) == []


# ---------------------------------------------------------------- never raises


def test_collector_failure_becomes_a_skipped_item_not_an_exception():
    def boom(*_a):
        raise RuntimeError("network down")

    bundle = gather_evidence(_info(CFG), None, None, None, boom)
    item = bundle.get("evidence:collection")
    assert item is not None and item.status == SKIPPED and "network down" in item.error


# ---------------------------------------------------------------- digest


def test_digest_keeps_status_provenance_and_errors_and_lists_limitations_first():
    tensor = EvidenceItem("tensors:file:model.safetensors", "tensor-header", COLLECTED, _prov("model.safetensors"),
                          data={"format": "safetensors", "parameters": 42, "tensor_count": 3, "dtypes": {"F32": 3},
                                "name_patterns": [{"pattern": "layer.{n}.w", "count": 2, "dtype": "F32", "example_shape": [2, 2]}]})
    b = _bundle(tensor, _item("file:config.json", COLLECTED, CFG), _item("file:tokenizer_config.json", INACCESSIBLE, None, "HTTP 503"),
                _item("file:preprocessor_config.json", MISSING, None, "not found"))
    d = build_evidence_digest(b, [{"field": "config"}])
    json.dumps(d)  # JSON-safe
    keys = list(d["items"])
    assert keys[0] == "file:tokenizer_config.json"  # unusable evidence first
    assert d["items"]["file:tokenizer_config.json"]["status"] == INACCESSIBLE
    assert d["items"]["file:tokenizer_config.json"]["error"] == "HTTP 503"
    assert d["items"]["file:config.json"]["revision"] == SHA and d["items"]["file:config.json"]["source"] == "huggingface_repo_file"
    ts = d["items"]["tensors:file:model.safetensors"]["tensor_summary"]
    assert ts["parameters"] == 42 and ts["header_only"] is True and ts["name_patterns"][0]["pattern"] == "layer.{n}.w"
    assert any("tokenizer_config.json: inaccessible" in x for x in d["limitations"])
    assert not any("preprocessor_config" in x for x in d["limitations"])  # missing = not in the repository, not a limitation
    assert d["filled_from_evidence"] == [{"field": "config"}]


def test_uncertainties_for_unreadable_evidence_only():
    b = _bundle(_item("file:a.json", INACCESSIBLE, None, "HTTP 503"), _item("file:b.json", INVALID, None, "bad json"),
                _item("file:c.json", MISSING, None, "nope"), _item("files:not_fetched", SKIPPED, None, "weights are never fetched", "file-selection"))
    text = " ".join(evidence_uncertainties(b))
    assert "file:a.json" in text and "file:b.json" in text and "file:c.json" not in text and "not_fetched" not in text


# ---------------------------------------------------------------- analysis


def test_evidence_problems_never_change_status_or_generatability():
    plain = analyze(_info(CFG))
    info = _info(CFG)
    attach_to_info(info, _bundle(_item("file:tokenizer_config.json", INACCESSIBLE, None, "HTTP 503"),
                                 _item("evidence:collection", SKIPPED, None, "collector crashed", "collector-error")))
    with_ev = analyze(info)
    assert (with_ev.status, with_ev.generatable, with_ev.reasons, with_ev.warnings) == (plain.status, plain.generatable, plain.reasons, plain.warnings)
    assert with_ev.evidence["limitations"] and any("tokenizer_config.json" in u for u in with_ev.uncertainties)
    assert plain.evidence is None
    json.dumps(with_ev.to_dict(), default=str)


def test_evidence_fills_a_config_the_inspection_missed_and_analysis_uses_it():
    without = analyze(_info(None))
    info = _info(None)
    attach_to_info(info, _bundle(_item("file:config.json", COLLECTED, CFG)))
    a = analyze(info)
    assert a.ui_hints["model_type"] == "bert" and without.ui_hints["model_type"] == ""
    assert a.evidence["filled_from_evidence"][0]["field"] == "config"


def test_security_restrictions_still_apply_to_evidence_filled_metadata():
    info = _info(None)
    attach_to_info(info, _bundle(_item("file:config.json", COLLECTED, {**CFG, "auto_map": {"AutoModel": "x.Y"}})))
    a = analyze(info)  # default settings: trust_remote_code is not allowed
    assert any("trust_remote_code" in r for r in a.reasons) and a.trust_remote_code is False


# ---------------------------------------------------------------- downstream generation


def test_opencode_request_carries_the_evidence_and_the_reading_rules():
    info = _info(CFG)
    attach_to_info(info, _bundle(_item("file:config.json", COLLECTED, CFG), _item("file:tokenizer_config.json", INVALID, None, "bad json")))
    a = analyze(info)
    facts = _facts(a)
    assert facts["evidence"]["items"]["file:tokenizer_config.json"]["status"] == INVALID
    request = build_request(a)
    assert "Only `collected` items are facts" in request and '"evidence"' in request


def test_analysis_without_evidence_is_reported_as_not_collected():
    facts = _facts(analyze(_info(CFG)))
    assert facts["evidence"]["status"] == "not_collected"


def test_notebook_title_states_evidence_basis_and_gaps():
    info = _info(CFG)
    attach_to_info(info, _bundle(_item("file:config.json", COLLECTED, CFG), _item("file:tokenizer_config.json", INACCESSIBLE, None, "HTTP 503")))
    md = _evidence_md(analyze(info))
    assert "1 collected" in md and "1 inaccessible" in md and "unknown, not assumed" in md and "tokenizer_config.json" in md
    assert "not run" in _evidence_md(analyze(_info(CFG)))


# ---------------------------------------------------------------- github-native end to end


def test_github_only_repository_collects_its_own_files_end_to_end():
    """A GitHub repository with no linked Hugging Face model is inspected, its OWN files collected through
    raw.githubusercontent.com, analysed, and its evidence reaches analysis.json — without ever downloading weights."""
    import sys
    sys.path.insert(0, "tests")
    from fakehub import FakeWeb, add_github_repo
    from m2g.inspector import inspect_url
    from m2g.evidence_bridge import gather_evidence, attach_to_info as attach

    web = FakeWeb()
    add_github_repo(web, "acme/netzero", readme="# Acme NetZero\nA small PyTorch sequence-classification model.",
                    requirements="torch>=2.0\ntransformers\n",
                    tree=["README.md", "requirements.txt", "main.py", "config.json", "checkpoints/netzero.safetensors"],
                    extra_files={"main.py": "import torch\nfrom transformers import AutoModelForSequenceClassification\n",
                                 "config.json": json.dumps(CFG)})
    info = inspect_url("https://github.com/acme/netzero", web.fetcher())
    assert info.source.kind == "github"
    assert info.primary_model_info is None  # no linked model: this is the github-only path

    bundle = gather_evidence(info, web.fetcher(), None, None)
    attach(info, bundle)
    a = analyze(info)

    assert bundle.get("github:files").status == COLLECTED
    assert bundle.get("file:README.md").status == COLLECTED
    assert bundle.get("file:requirements.txt").status == COLLECTED
    assert a.repository["provider"] == "github"
    assert a.evidence["source_kind"] == "github"
    # No weight file was ever downloaded whole: every safetensors request is a bounded range read.
    weight_calls = [c for c in web.calls if c.endswith(".safetensors")]
    assert all("raw.githubusercontent.com" in c for c in weight_calls)
