"""GGUF header-only REA inspection and the optional retained-Evidence metadata page.

Mock REA server, mock Hub transport, no network, no real REA. These tests prove:
  * a GGUF weight file is inspected header-only (magic + KV block + tensor table), never the tensor data;
  * the window that is fetched is bounded by ``rea_client.MAX_GGUF_WINDOW_BYTES`` even for a many-GiB remote file;
  * the facts REA's header declares (tensor count, parameters, dtypes, architecture) reach the analysis and the combined report;
  * ``inspect_analysis_view`` is called only when the model-inspection result advertises a retained parent id, and a failing or
    absent view call never changes the stage's verdict.
"""
import json
import struct
import unittest

import fakehub  # noqa: F401  (also installs the fake OpenCode: nothing here may start the real one)
from fakehub import FakeWeb, PUBLIC, SHA
from test_rea_integration import CMD, Base, hf_info

from m2g import analyzer as an
from m2g import rea_client as rc
from m2g.config import Settings
from m2g.security import SafeFetcher, parse_source_url


def _gstr(s):
    b = s.encode()
    return struct.pack("<Q", len(b)) + b


def gguf_bytes(tensors, metadata=None, version=3, align=32):
    """A real GGUF v3 header (magic, version, counts, KV block, tensor table) padded to ``align``; no tensor data."""
    metadata = metadata or {}
    out = b"GGUF" + struct.pack("<I", version) + struct.pack("<Q", len(tensors)) + struct.pack("<Q", len(metadata))
    for key, (t, v) in metadata.items():
        out += _gstr(key) + struct.pack("<I", t)
        if t == 8:  # GGUF_TYPE_STRING
            out += _gstr(v)
        elif t == 4:  # GGUF_TYPE_UINT32
            out += struct.pack("<I", v)
        else:
            raise ValueError("unsupported test metadata type")
    for name, (dims, gt) in tensors.items():
        out += _gstr(name) + struct.pack("<I", len(dims))
        for d in dims:
            out += struct.pack("<Q", d)
        out += struct.pack("<I", gt) + struct.pack("<Q", 0)
    return out + b"\x00" * ((-len(out)) % align)


HEADER = gguf_bytes({"token_embd.weight": ([4, 3], 0), "blk.0.attn_q.weight": ([2, 2], 1)},
                    {"general.architecture": (8, "llama"), "general.alignment": (4, 32)})
PARAMS = 4 * 3 + 2 * 2


class GgufWeb(FakeWeb):
    """FakeWeb that serves a .gguf file with HTTP range semantics (206 + Content-Range) and records every range asked for."""

    def __init__(self, blob, total, content_range="ok"):
        super().__init__()
        self.blob, self.total, self.content_range = blob, total, content_range
        self.ranges = []

    def __call__(self, parts, ips, headers, timeout, max_bytes):
        if parts.path.endswith(".gguf") and "/resolve/" in parts.path:
            rng = headers.get("Range")
            if not rng:
                return 200, {}, self.blob
            a, b = (int(x) for x in rng[len("bytes="):].split("-"))
            self.ranges.append((a, b))
            return 206, {"content-range": f"bytes {a}-{b}/{self.total}"}, self.blob[a:b + 1]
        return super().__call__(parts, ips, headers, timeout, max_bytes)


def fetcher_for(web_, **settings):
    return SafeFetcher(Settings(**settings), resolver=PUBLIC, transport=web_)


class TestGgufHeaderFetch(unittest.TestCase):
    def test_header_is_parsed_and_the_window_is_bounded(self):
        big_total = 3 << 30  # a 3 GiB GGUF file
        web_ = GgufWeb(HEADER + b"\x00" * ((1 << 20) - len(HEADER)), big_total)  # 1 MiB of "tensor data" is all the server can send
        stats = {}
        remote = rc.fetch_gguf_header(fetcher_for(web_), "acme/tiny", SHA, "model.gguf", stats=stats)
        self.assertEqual(remote.total_size, big_total)
        self.assertTrue(remote.blob.startswith(b"GGUF"))
        self.assertLessEqual(len(remote.blob), rc.MAX_GGUF_WINDOW_BYTES)
        # nothing beyond the first bounded window was requested, and no byte near the end of the 3 GiB file was touched
        self.assertLessEqual(max(b for _, b in web_.ranges), rc.MAX_GGUF_WINDOW_BYTES - 1)
        self.assertEqual(stats["bytes"], 1 + ((1 << 20) - 1))

    def test_summary_reports_tensors_parameters_and_architecture(self):
        summary = rc.summarize_gguf(HEADER, len(HEADER))
        self.assertEqual(summary["format"], "gguf")
        self.assertEqual(summary["tensor_count"], 2)
        self.assertEqual(summary["parameters"], PARAMS)
        self.assertEqual(summary["architecture"], "llama")
        self.assertEqual(summary["dtypes"], {"F16": 1, "F32": 1})
        self.assertLessEqual(summary["header_bytes"], len(HEADER))

    def test_hub_listed_size_must_agree(self):
        web_ = GgufWeb(HEADER, len(HEADER))
        with self.assertRaises(rc.ReaError):
            rc.fetch_gguf_header(fetcher_for(web_), "acme/tiny", SHA, "model.gguf", expected_size=len(HEADER) + 1)

    def test_a_non_gguf_blob_is_rejected(self):
        with self.assertRaises(rc.ReaError):
            rc.summarize_gguf(b"NOPE" + b"\x00" * 40, 44)


class TestGgufInspectionStage(Base):
    def run_gguf(self, files=None, mode="ok"):
        self.set_mode(mode)
        size = len(HEADER) + (1 << 20)  # header + a megabyte the server can send, but a much larger logical size is reported
        files = files or [("model.gguf", size)]
        web_ = GgufWeb(HEADER + b"\x00" * (1 << 20), size)
        fetcher = SafeFetcher(Settings(), resolver=PUBLIC, transport=web_)
        report = rc.run_rea_inspection(hf_info(files), fetcher, Settings(), command=CMD, project_dir=self.tmp)
        return report, web_

    def test_gguf_file_is_inspected_header_only(self):
        report, web_ = self.run_gguf()
        self.assertEqual(report.status, rc.COMPLETED, report.log)
        self.assertEqual(report.tools_invoked, ["binary_session", "inspect_model_artifact"])
        self.assertEqual(report.header_summaries[0]["format"], "gguf")
        self.assertEqual(report.header_summaries[0]["tensor_count"], 2)
        self.assertEqual(report.header_summaries[0]["parameters"], PARAMS)
        self.assertEqual(report.header_summaries[0]["architecture"], "llama")
        # the scratch file has the remote LOGICAL size, so REA validates it like a real file, but the tensor region is a hole
        self.assertEqual(report.header_summaries[0]["logical_size"], len(HEADER) + (1 << 20))
        self.assertLessEqual(report.header_summaries[0]["allocated_bytes"], len(HEADER) + rc.SPARSE_SLACK_BYTES)
        self.assertEqual([c["tool"] for c in self.server_calls()], ["binary_session", "inspect_model_artifact"])
        self.assertEqual(self.server_calls()[1]["arguments"]["hash"], "header")

    def test_no_safetensors_but_a_gguf_file_is_no_longer_not_applicable(self):
        size = len(HEADER) + (1 << 20)
        report, _ = self.run_gguf(files=[("config.json", 5), ("model.gguf", size)])
        self.assertNotEqual(report.status, rc.NOT_APPLICABLE)
        self.assertTrue(report.header_summaries)

    def test_pickle_only_repo_is_still_not_applicable(self):
        report, web_ = self.run_gguf(files=[("pytorch_model.bin", 10)])
        self.assertEqual(report.status, rc.NOT_APPLICABLE)
        self.assertEqual(report.calls, [])
        self.assertEqual(web_.ranges, [])


class TestOptionalViewPage(Base):
    def run_view(self, mode, files=None):
        self.set_mode(mode)
        size = len(HEADER) + (1 << 20)
        web_ = GgufWeb(HEADER + b"\x00" * (1 << 20), size)
        fetcher = SafeFetcher(Settings(), resolver=PUBLIC, transport=web_)
        report = rc.run_rea_inspection(hf_info(files or [("model.gguf", size)]), fetcher, Settings(), command=CMD, project_dir=self.tmp)
        return report

    def test_view_is_paged_only_when_a_parent_id_is_offered(self):
        report = self.run_view("view")
        self.assertEqual(report.status, rc.COMPLETED)
        tools = [c["tool"] for c in self.server_calls()]
        self.assertEqual(tools, ["binary_session", "inspect_model_artifact", "inspect_analysis_view"])
        view = [c for c in report.calls if c.tool == "inspect_analysis_view"][0]
        self.assertTrue(view.ok)
        self.assertEqual(view.arguments["view"]["kind"], "model-artifact")
        self.assertTrue(view.arguments["source"]["evidence_id"].startswith("ev_"))

    def test_no_view_call_without_a_parent_id(self):
        report = self.run_view("ok")
        self.assertEqual([c["tool"] for c in self.server_calls()], ["binary_session", "inspect_model_artifact"])
        self.assertNotIn("inspect_analysis_view", report.tools_invoked)

    def test_a_failing_view_is_optional_and_keeps_completed(self):
        report = self.run_view("view_error")
        self.assertEqual(report.status, rc.COMPLETED)  # the header inspection is the contract; the view is extra
        view = [c for c in report.calls if c.tool == "inspect_analysis_view"][0]
        self.assertFalse(view.ok)

    def test_server_without_the_view_tool_still_completes(self):
        report = self.run_view("no_view_tool")
        self.assertEqual(report.status, rc.COMPLETED)
        self.assertNotIn("inspect_analysis_view", report.tools_invoked)


class TestGgufReachesAnalysis(Base):
    def test_gguf_facts_reach_the_combined_report(self):
        self.set_mode("ok")
        size = len(HEADER) + (1 << 20)
        web_ = GgufWeb(HEADER + b"\x00" * (1 << 20), size)
        info = hf_info([("model.gguf", size)])
        report = rc.run_rea_inspection(info, SafeFetcher(Settings(), resolver=PUBLIC, transport=web_), Settings(),
                                       command=CMD, project_dir=self.tmp)
        info.rea = report
        a = an.analyze(info, Settings())
        section = a.combined_analysis["weight_format"]
        self.assertEqual(section["formats"], ["gguf"])
        self.assertEqual(section["files"][0]["format"], "gguf")
        self.assertEqual(section["files"][0]["declared_architecture"], "llama")
        self.assertIn("gguf", a.combined_analysis["rea_analysis"]["weight_formats"])
        self.assertTrue(any("GGUF" in w for w in a.combined_analysis["warnings"]))


class TestGgufEndToEnd(Base):
    def test_pipeline_inspects_a_gguf_header_even_though_no_app_is_generated(self):
        from fakehub import add_hf_model
        from m2g.pipeline import run_pipeline
        from pathlib import Path

        self.set_mode("ok")
        size = len(HEADER) + (1 << 20)
        web_ = GgufWeb(HEADER + b"\x00" * (1 << 20), size)
        add_hf_model(web_, "acme/gguf-only", pipeline_tag="text-generation", library_name="gguf", tags=["gguf"],
                     config=None, files=[("model.gguf", size), ("README.md", 100)])
        settings = Settings(output_dir=self.tmp / "out")
        runner = rc.make_runner(settings, command=CMD, project_dir=self.tmp)
        res = run_pipeline("https://huggingface.co/acme/gguf-only", settings, web_.fetcher(output_dir=self.tmp / "out"),
                           write=True, rea=runner)
        # GGUF is not loadable by the generated apps, so no app is produced - but the ANALYSIS still completes with REA evidence.
        self.assertEqual(res.rea.status, rc.COMPLETED, res.rea.log)
        self.assertEqual(res.analysis.status, "unsupported")
        self.assertEqual(res.analysis.rea["status"], "completed")
        self.assertEqual(res.analysis.combined_analysis["weight_format"]["formats"], ["gguf"])
        self.assertIsNotNone(res.out_dir)
        analysis_json = json.loads((Path(res.out_dir) / "analysis.json").read_text(encoding="utf-8"))
        self.assertEqual(analysis_json["rea"]["status"], "completed")
        self.assertEqual(analysis_json["combined_analysis"]["weight_format"]["files"][0]["declared_architecture"], "llama")


if __name__ == "__main__":
    unittest.main()
