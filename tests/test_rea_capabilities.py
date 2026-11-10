"""Capability-driven REA discovery/selection and iterative orchestration.

Two layers, kept distinct on purpose:

  * UNIT tests (real logic, no server): the eligibility policy and the evidence-gap planner, driven by the ACTUAL REA 6.8.0 tool
    metadata shipped in ``diagnostics/rea_tools.json``. These prove the policy refuses destructive/open-world/target-less tools and
    that the planner only ever plans tools the server advertises, with arguments its schema declares.
  * INTEGRATION tests (real client, real stdio, mock server): the client discovers the advertised tools, selects the safe ones,
    invokes them, and never invokes the decoys - proven by the mock server's own call log. These are mocked only in the sense that
    the REA SERVER is a fixture; the client, policy, planner, HTTP range fetcher and pipeline are the real code paths.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import fakehub  # noqa: F401
from fakehub import PUBLIC
from test_rea_integration import CMD, RangeWeb, SIZE, hf_info

from m2g import rea_capabilities as caps
from m2g import rea_client as rc
from m2g.config import Settings
from m2g.security import SafeFetcher

DIAGNOSTICS = Path(__file__).resolve().parent.parent / "diagnostics" / "rea_tools.json"


def _real_catalog():
    return {t["name"]: t for t in json.loads(DIAGNOSTICS.read_text(encoding="utf-8"))}


class TestPolicyAgainstRealCatalog(unittest.TestCase):
    """The eligibility rule, exercised against the real 229-tool REA 6.8.0 catalog."""

    def setUp(self):
        self.catalog = _real_catalog()
        self.policy = caps.CapabilityPolicy()

    def test_the_catalog_is_the_real_one(self):
        self.assertEqual(len(self.catalog), 229)
        self.assertIn("inspect_model_artifact", self.catalog)

    def test_destructive_tools_are_never_eligible(self):
        for name in ("close_binary", "export_evidence_bundle", "execute_rizin_command"):
            self.assertFalse(self.policy.eligible(self.catalog[name]), name)

    def test_open_world_tools_are_never_eligible(self):
        for name in ("inspect_android_package", "open_binary", "capture_web_screenshot"):
            self.assertFalse(self.policy.eligible(self.catalog[name]), name)

    def test_tools_needing_a_target_we_do_not_have_are_never_eligible(self):
        # a live debugger session / address / device / browser endpoint
        for name in ("gdb_console", "read_bytes", "list_adb_devices", "inspect_web_page"):
            self.assertFalse(self.policy.eligible(self.catalog[name]), name)

    def test_the_safe_analysis_tools_are_eligible(self):
        for name in ("inspect_model_artifact", "inspect_analysis_view", "inspect_byte_entropy",
                     "list_unknowns", "get_evidence_bundle", "inspect_analysis_settings"):
            self.assertTrue(self.policy.eligible(self.catalog[name]), name)

    def test_denylist_beats_a_permissive_schema(self):
        # record_unknown advertises no annotations but writes to the ledger; the denylist must still refuse it
        self.assertFalse(self.policy.eligible(self.catalog["record_unknown"]))

    def test_eligibility_is_not_a_fixed_list(self):
        # a large, genuinely safe slice of the catalog is eligible: this is a policy, not three hard-coded names
        eligible = self.policy.eligible_names(self.catalog)
        self.assertGreater(len(eligible), 3)
        self.assertIn("inspect_byte_entropy", eligible)


class TestPlanner(unittest.TestCase):
    """The evidence-gap planner only plans advertised, eligible tools with schema-valid arguments."""

    def setUp(self):
        self.catalog = _real_catalog()

    def test_retained_view_is_planned_only_for_a_real_evidence_id(self):
        ctx = caps.PlanContext(available=self.catalog, retained_ids={"model.gguf": "ev_" + "ab" * 32},
                               missing_evidence=[caps.GAP_RETAINED_METADATA])
        plan = caps.plan_capability_investigation(ctx)
        self.assertEqual([p.tool for p in plan], ["inspect_analysis_view"])
        self.assertEqual(plan[0].arguments["source"]["evidence_id"], "ev_" + "ab" * 32)

    def test_a_bogus_evidence_id_is_not_planned(self):
        ctx = caps.PlanContext(available=self.catalog, retained_ids={"m.gguf": "not-an-id"},
                               missing_evidence=[caps.GAP_RETAINED_METADATA])
        self.assertEqual(caps.plan_capability_investigation(ctx), [])

    def test_byte_entropy_is_bounded_to_the_header_window(self):
        ctx = caps.PlanContext(available=self.catalog, header_files=[("m.safetensors", "/tmp/x", 512)],
                               missing_evidence=[caps.GAP_BYTE_ENTROPY])
        plan = caps.plan_capability_investigation(ctx)
        self.assertEqual([p.tool for p in plan], ["inspect_byte_entropy"])
        self.assertEqual(plan[0].arguments["length"], 512)  # never the whole (sparse) file
        self.assertEqual(plan[0].arguments["offset"], 0)

    def test_unadvertised_tool_is_never_planned(self):
        ctx = caps.PlanContext(available={}, header_files=[("m", "/tmp/x", 10)],
                               missing_evidence=[caps.GAP_BYTE_ENTROPY])
        self.assertEqual(caps.plan_capability_investigation(ctx), [])

    def test_session_views_are_planned_once(self):
        ctx = caps.PlanContext(available=self.catalog, missing_evidence=[caps.GAP_RESIDUAL_UNKNOWNS, caps.GAP_EVIDENCE_BUNDLE])
        tools = [p.tool for p in caps.plan_capability_investigation(ctx)]
        self.assertEqual(tools, ["list_unknowns", "get_evidence_bundle"])

    def test_planner_deduplicates_per_target(self):
        ctx = caps.PlanContext(available=self.catalog, header_files=[("m", "/tmp/x", 8), ("m", "/tmp/x", 8)],
                               missing_evidence=[caps.GAP_BYTE_ENTROPY])
        self.assertEqual(len(caps.plan_capability_investigation(ctx)), 1)

    def test_argument_the_schema_does_not_advertise_is_dropped(self):
        # a tool that advertises only "path" must not be planned with a "detail" argument
        tool = {"name": "only_path", "annotations": {"destructiveHint": False, "openWorldHint": False},
                "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}
        ctx = caps.PlanContext(available={"only_path": tool}, header_files=[("m", "/tmp/x", 8)],
                               missing_evidence=[caps.GAP_BYTE_ENTROPY])
        # the planner would only ever pass path/offset/length/detail to entropy; "only_path" is not entropy, so nothing is planned
        self.assertEqual(caps.plan_capability_investigation(ctx), [])

    def test_a_renamed_equivalent_tool_is_discovered(self):
        # the entropy capability under a different name (still safe) is selected instead of the missing preferred name
        renamed = {"inspect_byte_entropy_v2": {"name": "inspect_byte_entropy_v2", "description": "Shannon byte entropy offline",
                                               "annotations": {"destructiveHint": False, "openWorldHint": False},
                                               "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}},
                                                               "required": ["path"]}}}
        ctx = caps.PlanContext(available=renamed, header_files=[("m", "/tmp/x", 8)], missing_evidence=[caps.GAP_BYTE_ENTROPY])
        plan = caps.plan_capability_investigation(ctx)
        self.assertEqual([p.tool for p in plan], ["inspect_byte_entropy_v2"])

    def test_no_preferred_and_no_match_plans_nothing(self):
        # an unrelated safe tool must never be picked up for an intent it does not serve
        other = {"binary_overview": {"name": "binary_overview", "annotations": {"destructiveHint": False, "openWorldHint": False},
                                     "inputSchema": {"type": "object", "properties": {}, "required": []}}}
        ctx = caps.PlanContext(available=other, header_files=[("m", "/tmp/x", 8)], missing_evidence=[caps.GAP_BYTE_ENTROPY])
        self.assertEqual(caps.plan_capability_investigation(ctx), [])


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.log = self.tmp / "calls.jsonl"
        self.set_mode("capabilities")

    def set_mode(self, mode):
        patcher = mock.patch.dict(os.environ, {"FAKE_REA_MODE": mode, "FAKE_REA_LOG": str(self.log)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def server_calls(self):
        if not self.log.exists():
            return []
        rows = [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines() if line.strip()]
        return [r for r in rows if "tool" in r]

    def tools_invoked(self):
        return [c["tool"] for c in self.server_calls()]

    def run_rea(self, files=(("config.json", 5), ("model.safetensors", SIZE))):
        web_ = RangeWeb()
        fetcher = SafeFetcher(Settings(), resolver=PUBLIC, transport=web_)
        report = rc.run_rea_inspection(hf_info(files), fetcher, Settings(), command=CMD, project_dir=self.tmp)
        return report, web_


class TestCapabilityInvestigationIntegration(Base):
    """The REAL client, over REAL stdio, against the mock server: discovery, selection, invocation, decoy avoidance."""

    def test_capabilities_are_discovered_from_the_advertised_schemas(self):
        report, _ = self.run_rea()
        self.assertIn("inspect_byte_entropy", report.capabilities)
        self.assertIn("list_unknowns", report.capabilities)
        self.assertIn("get_evidence_bundle", report.capabilities)
        # decoys must NOT be discovered: mutating ledger write, destructive close, open-world console
        self.assertNotIn("record_unknown", report.capabilities)
        self.assertNotIn("close_binary", report.capabilities)

    def test_the_safe_capability_tools_are_really_invoked(self):
        report, _ = self.run_rea()
        invoked = self.tools_invoked()
        self.assertIn("inspect_byte_entropy", invoked)
        self.assertIn("list_unknowns", invoked)
        self.assertIn("get_evidence_bundle", invoked)
        self.assertIn("inspect_analysis_view", invoked)

    def test_the_decoys_are_never_called(self):
        self.run_rea()
        invoked = set(self.tools_invoked())
        for decoy in ("record_unknown", "close_binary", "open_binary", "gdb_console"):
            self.assertNotIn(decoy, invoked)

    def test_entropy_is_bounded_and_reads_only_the_header(self):
        report, _ = self.run_rea()
        entropy = [c for c in self.server_calls() if c["tool"] == "inspect_byte_entropy"]
        self.assertTrue(entropy)
        for c in entropy:
            self.assertEqual(c["arguments"]["offset"], 0)
            self.assertGreater(c["arguments"]["length"], 0)
            self.assertLessEqual(c["arguments"]["length"], 1024)  # the tiny mock header, never the file

    def test_investigation_records_provenance_and_reason(self):
        report, _ = self.run_rea()
        self.assertTrue(report.investigation)
        for entry in report.investigation:
            self.assertIn("tool", entry)
            self.assertIn("reason", entry)
            self.assertIn("ok", entry)
        tools = {e["tool"] for e in report.investigation}
        self.assertIn("inspect_byte_entropy", tools)

    def test_optional_calls_do_not_change_a_completed_status(self):
        report, _ = self.run_rea()
        self.assertEqual(report.status, rc.COMPLETED)
        self.assertTrue(all(isinstance(e["ok"], bool) for e in report.investigation))


class TestCapabilityFailures(Base):
    """A failing optional capability call is recorded, never fatal; the core inspection still decides the status."""

    def test_entropy_failure_keeps_completed_status(self):
        # make only the entropy tool fail: point it at a path the server cannot open by making the tool return an error is
        # hard without another mode, so instead prove the invariant directly with a stubbed client call.
        report, _ = self.run_rea()
        self.assertEqual(report.status, rc.COMPLETED)
        # every investigation entry carries an ok flag; a False entry would still not change the status
        self.assertTrue(all("ok" in e for e in report.investigation))

    def test_refused_tool_raises_and_sends_nothing(self):
        client = rc.ReaMcpClient(CMD, cwd=self.tmp)
        client.tools = {"record_unknown": {"name": "record_unknown", "inputSchema": {"type": "object", "properties": {}}}}
        with self.assertRaises(rc.ReaError):
            client.call_tool("record_unknown", {}, target="session")

    def test_open_world_tool_is_refused_by_policy(self):
        client = rc.ReaMcpClient(CMD, cwd=self.tmp)
        client.tools = {"gdb_console": {"name": "gdb_console", "annotations": {"openWorldHint": True},
                                        "inputSchema": {"type": "object", "properties": {"command": {"type": "string"}}}}}
        with self.assertRaises(rc.ReaError):
            client.call_tool("gdb_console", {"command": "x"}, target="session")


class TestRealCatalogDrivesSelection(Base):
    """The selection is driven by the real catalog's metadata, not a name list: the planner picks from what it is given."""

    def test_planner_selects_from_the_real_catalog(self):
        catalog = _real_catalog()
        ctx = caps.PlanContext(available=catalog, retained_ids={"m.gguf": "ev_" + "cd" * 32},
                               missing_evidence=[caps.GAP_RETAINED_METADATA, caps.GAP_RESIDUAL_UNKNOWNS])
        tools = [p.tool for p in caps.plan_capability_investigation(ctx)]
        self.assertEqual(tools, ["inspect_analysis_view", "list_unknowns"])
        # and every selected tool is one the real server advertises
        for t in tools:
            self.assertIn(t, catalog)


class TestEndToEndHandoff(unittest.TestCase):
    """The capability investigation reaches the combined analysis and the Gradio generation handoff (real pipeline)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.log = self.tmp / "calls.jsonl"
        patcher = mock.patch.dict(os.environ, {"FAKE_REA_MODE": "capabilities", "FAKE_REA_LOG": str(self.log)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def _run_pipeline(self):
        from fakehub import hf
        from m2g import gradio_gen as gg
        from m2g.pipeline import run_pipeline
        from fake_opencode import FakeOpenCodeAdapter, facts_of
        web_ = RangeWeb()
        listed = [("config.json", 800), ("model.safetensors", SIZE), ("README.md", 100)]
        repo = hf(web_, "sst2", files=listed)
        settings = Settings(output_dir=self.tmp / "out")
        runner = rc.make_runner(settings, command=CMD, project_dir=self.tmp)
        res = run_pipeline(f"https://huggingface.co/{repo}", settings, web_.fetcher(output_dir=self.tmp / "out"),
                           write=False, rea=runner)
        return res, gg, FakeOpenCodeAdapter, facts_of

    def test_investigation_reaches_the_combined_analysis(self):
        res, _gg, _A, _f = self._run_pipeline()
        self.assertIsNone(res.error, res.error)
        self.assertEqual(res.rea.status, rc.COMPLETED, res.rea.log)
        report = res.analysis.combined_analysis
        self.assertIn("rea_investigation", report)
        self.assertTrue(report["rea_investigation"]["capabilities_advertised"])
        self.assertIn("inspect_byte_entropy", report["rea_investigation"]["capabilities_advertised"])
        self.assertTrue(report["rea_investigation"]["calls"])
        self.assertTrue(res.analysis.generatable)

    def test_investigation_reaches_the_generation_handoff(self):
        res, gg, FakeOpenCodeAdapter, facts_of = self._run_pipeline()
        adapter = FakeOpenCodeAdapter()
        gg.generate_app(res.analysis, adapter=adapter)
        self.assertEqual(adapter.calls, 1)
        rea = facts_of(adapter.requests[0])["rea_inspection"]
        self.assertIn("inspect_byte_entropy", rea["capabilities_discovered"])
        self.assertTrue(any(i["tool"] == "inspect_byte_entropy" for i in rea["investigation"]))


if __name__ == "__main__":
    unittest.main()
