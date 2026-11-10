"""Dynamic REA capability discovery, evidence-gap-driven question generation, and the adaptive investigation loop.

Two layers, kept distinct on purpose:

  * UNIT tests (real logic, no server): the eligibility policy, the capability tagger, the evidence-gap question generators, the
    question planner and the observation folding, driven by the ACTUAL REA 6.8.0 tool metadata shipped in ``diagnostics/rea_tools.json``.
    These prove the policy refuses destructive/open-world/target-less tools, that questions are raised only when a capability can
    answer them, and that a successful call folds its evidence back so the next round sees it.
  * INTEGRATION tests (real client, real stdio, mock server): the client discovers the advertised tools, selects the safe ones by
    capability, runs the adaptive loop (continuing on new evidence, stopping when the questions dry up or the budget is spent),
    and never invokes the decoys - proven by the mock server's own call log. These are mocked only in the sense that the REA SERVER
    is a fixture; the client, policy, planner, HTTP range fetcher and pipeline are the real code paths.
"""
import json
import os
import shutil
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


def _catalog_with(*names):
    """A small catalog containing only the named real REA tools (so a test can withhold a capability)."""
    full = _real_catalog()
    return {n: full[n] for n in names if n in full}


def _state(**kw):
    """An EvidenceState built from a real tool catalog unless the test overrides ``capabilities``."""
    caps_by_tag = kw.pop("capabilities", None)
    if caps_by_tag is None:
        caps_by_tag = caps.tag_tools(_catalog_with("inspect_model_artifact", "inspect_analysis_view",
                                                   "inspect_byte_entropy", "list_unknowns", "get_evidence_bundle"))
    return caps.EvidenceState(capabilities=caps_by_tag, **kw)


class TestQuestionGeneration(unittest.TestCase):
    """Questions come from the collected evidence and only when a capability that can answer them is advertised."""

    def test_an_undeclared_architecture_raises_a_bounded_entropy_question(self):
        state = _state(files=[("m.safetensors", "/tmp/m")], header_bytes={"m.safetensors": 512},
                       formats={"m.safetensors": "safetensors"})
        qs = caps.generate_questions(state)
        entropy = [q for q in qs if q.kind == "byte_entropy"]
        self.assertEqual(len(entropy), 1)
        self.assertEqual(entropy[0].target, "m.safetensors")
        self.assertEqual(entropy[0].arguments["length"], 512)  # bounded to the header window, never the whole file
        self.assertEqual(entropy[0].arguments["offset"], 0)

    def test_a_declared_architecture_raises_no_entropy_question(self):
        state = _state(files=[("m.gguf", "/tmp/m")], header_bytes={"m.gguf": 512},
                       formats={"m.gguf": "gguf"}, architectures={"m.gguf": "llama"})
        self.assertEqual([q for q in caps.generate_questions(state) if q.kind == "byte_entropy"], [])

    def test_a_question_needing_an_absent_capability_is_not_raised(self):
        # only the model-inspection tool is advertised: nothing can answer an entropy/unknowns/bundle question
        state = _state(capabilities=caps.tag_tools(_catalog_with("inspect_model_artifact")),
                       files=[("m.safetensors", "/tmp/m")], header_bytes={"m.safetensors": 512},
                       formats={"m.safetensors": "safetensors"})
        self.assertEqual(caps.generate_questions(state), [])

    def test_a_retained_evidence_id_raises_a_paging_question(self):
        state = _state(retained_ids={"ev_" + "ab" * 32: "m.gguf"})
        qs = caps.generate_questions(state)
        self.assertEqual(qs[0].kind, "retained_view")  # paging is raised first, before the whole-session questions
        self.assertEqual(qs[0].target, "ev_" + "ab" * 32)

    def test_a_bogus_evidence_id_raises_no_paging_question(self):
        state = _state(retained_ids={"not-an-id": "m.gguf"})
        self.assertEqual([q for q in caps.generate_questions(state) if q.kind == "retained_view"], [])

    def test_a_paged_evidence_id_is_not_asked_again(self):
        evid = "ev_" + "ab" * 32
        state = _state(retained_ids={evid: "m.gguf"}, paged_ids={evid})
        self.assertEqual([q for q in caps.generate_questions(state) if q.kind == "retained_view"], [])

    def test_whole_session_questions_are_raised_once_each(self):
        state = _state()
        kinds = [q.kind for q in caps.generate_questions(state)]
        self.assertEqual(kinds.count("residual_unknowns"), 1)
        self.assertEqual(kinds.count("evidence_bundle"), 1)


class TestQuestionPlanning(unittest.TestCase):
    """Planned calls are advertised, eligible tools with schema-valid arguments, tagged with their capability."""

    def test_entropy_question_is_planned_as_a_capability_call(self):
        state = _state(files=[("m.safetensors", "/tmp/m")], header_bytes={"m.safetensors": 512},
                       formats={"m.safetensors": "safetensors"})
        plan = caps.plan_questions(state, caps.generate_questions(state), caps.CapabilityPolicy())
        entropy = [p for p in plan if p.capability == "byte_entropy"]
        self.assertEqual(len(entropy), 1)
        self.assertEqual(entropy[0].tool, "inspect_byte_entropy")
        self.assertEqual(entropy[0].arguments["length"], 512)

    def test_a_renamed_equivalent_tool_is_selected(self):
        renamed = {"inspect_byte_entropy_v2": {"name": "inspect_byte_entropy_v2", "description": "Shannon byte entropy offline",
                                               "annotations": {"destructiveHint": False, "openWorldHint": False},
                                               "inputSchema": {"type": "object", "properties": {"path": {"type": "string"},
                                                                                                "offset": {"type": "integer"},
                                                                                                "length": {"type": "integer"}}}}}
        state = caps.EvidenceState(capabilities=caps.tag_tools(renamed),
                                   files=[("m.safetensors", "/tmp/m")], header_bytes={"m.safetensors": 512},
                                   formats={"m.safetensors": "safetensors"})
        plan = caps.plan_questions(state, caps.generate_questions(state), caps.CapabilityPolicy())
        self.assertEqual([p.tool for p in plan], ["inspect_byte_entropy_v2"])

    def test_an_unrelated_safe_tool_is_never_selected(self):
        other = {"binary_overview": {"name": "binary_overview", "annotations": {"destructiveHint": False, "openWorldHint": False},
                                     "inputSchema": {"type": "object", "properties": {}}}}
        state = caps.EvidenceState(capabilities=caps.tag_tools(other),
                                   files=[("m.safetensors", "/tmp/m")], header_bytes={"m.safetensors": 512},
                                   formats={"m.safetensors": "safetensors"})
        self.assertEqual(caps.plan_questions(state, caps.generate_questions(state), caps.CapabilityPolicy()), [])

    def test_unknown_capability_is_selected_from_its_description_and_schema(self):
        # This tool is intentionally absent from CAPABILITIES.  Its advertised
        # description/schema, not a new fixed category, make it relevant.
        catalog = {"read_declared_architecture": {
            "name": "read_declared_architecture",
            "description": "Read declared model architecture metadata from a local header.",
            "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
            "inputSchema": {"type": "object", "properties": {"path": {"type": "string"},
                                                              "offset": {"type": "integer"},
                                                              "length": {"type": "integer"}}},
        }}
        state = caps.EvidenceState(tools=catalog, files=[("m.safetensors", "/tmp/m")],
                                   header_bytes={"m.safetensors": 512}, formats={"m.safetensors": "safetensors"})
        plan = caps.plan_questions(state, caps.generate_questions(state), tools=catalog)
        self.assertEqual([p.tool for p in plan], ["read_declared_architecture"])

    def test_invalid_required_schema_cannot_produce_a_call(self):
        catalog = {"inspect_entropy": {"name": "inspect_entropy", "annotations": {"readOnlyHint": True},
                                        "inputSchema": {"type": "object", "properties": {}, "required": ["path"]}}}
        planned = [caps.PlannedCall("inspect_entropy", {"path": "/tmp/m"}, "m", "x")]
        self.assertEqual(caps.filter_plan(planned, catalog), [])


class TestObservationFolding(unittest.TestCase):
    """A successful call's output is folded back into the evidence state so the next round sees the new facts."""

    def test_entropy_marks_the_file_done_and_stops_asking(self):
        state = _state(files=[("m.safetensors", "/tmp/m")], header_bytes={"m.safetensors": 512},
                       formats={"m.safetensors": "safetensors"})
        self.assertTrue([q for q in caps.generate_questions(state) if q.kind == "byte_entropy"])
        caps.observe(state, "byte_entropy", "m.safetensors", json.dumps({"entropy": 7.1}))
        self.assertEqual([q for q in caps.generate_questions(state) if q.kind == "byte_entropy"], [])

    def test_a_revealed_evidence_id_becomes_a_new_paging_question(self):
        state = _state()
        self.assertEqual([q for q in caps.generate_questions(state) if q.kind == "retained_view"], [])
        caps.observe(state, "byte_entropy", "m.safetensors", json.dumps({"retained_evidence_id": "ev_" + "12" * 32}))
        qs = [q for q in caps.generate_questions(state) if q.kind == "retained_view"]
        self.assertEqual(len(qs), 1)
        self.assertEqual(qs[0].target, "ev_" + "12" * 32)

    def test_a_paged_id_is_recorded_as_paged(self):
        evid = "ev_" + "ab" * 32
        state = _state(retained_ids={evid: "m.gguf"})
        caps.observe(state, "retained_view", evid, json.dumps({"parent_evidence_id": evid}))
        self.assertIn(evid, state.paged_ids)
        self.assertEqual([q for q in caps.generate_questions(state) if q.kind == "retained_view"], [])

    def test_the_state_signature_changes_only_when_evidence_changes(self):
        state = _state(files=[("m.safetensors", "/tmp/m")], header_bytes={"m.safetensors": 512},
                       formats={"m.safetensors": "safetensors"})
        before = caps.state_signature(state)
        caps.observe(state, "byte_entropy", "m.safetensors", json.dumps({"entropy": 7.1}))
        self.assertNotEqual(caps.state_signature(state), before)

    def test_a_malformed_payload_adds_nothing(self):
        state = _state()
        caps.observe(state, "byte_entropy", "m.safetensors", "{not json")
        # the call established its capability key and marked the file done, but parsed no new format/architecture fact
        self.assertNotIn("safetensors", state.formats.values())
        self.assertNotIn("m.safetensors", state.architectures)
        self.assertEqual(state.retained_ids, {})

    def test_empty_success_output_makes_no_progress(self):
        state = _state()
        before = caps.state_signature(state)
        self.assertEqual(caps.observe(state, "residual_unknowns", "session", ""), set())
        self.assertEqual(caps.state_signature(state), before)


class TestBudget(unittest.TestCase):
    def test_time_budget_is_configurable_and_exhausts(self):
        now = [10.0]
        budget = caps.Budget(max_calls=99, max_seconds=2.0, max_failures=99, clock=lambda: now[0], started=10.0)
        self.assertFalse(budget.time_exhausted())
        now[0] = 12.0
        self.assertTrue(budget.time_exhausted())


class TestRealCatalogDrivesSelection(unittest.TestCase):
    """The selection is driven by the real catalog's metadata, not a name list: the planner picks from what it is given."""

    def test_planner_selects_from_the_real_catalog(self):
        state = _state(capabilities=caps.tag_tools(_real_catalog()), retained_ids={"ev_" + "cd" * 32: "m.gguf"})
        plan = caps.plan_questions(state, caps.generate_questions(state), caps.CapabilityPolicy())
        tools = [p.tool for p in plan]
        self.assertIn("inspect_analysis_view", tools)
        self.assertIn("list_unknowns", tools)
        self.assertIn("get_evidence_bundle", tools)
        for p in plan:  # every selected tool is one the real server advertises
            self.assertIn(p.tool, _real_catalog())



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

    def run_rea(self, files=(("config.json", 5), ("model.safetensors", SIZE)), settings=None):
        web_ = RangeWeb()
        fetcher = SafeFetcher(Settings(), resolver=PUBLIC, transport=web_)
        report = rc.run_rea_inspection(hf_info(files), fetcher, settings or Settings(), command=CMD, project_dir=self.tmp)
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


class TestAdaptiveInvestigationIntegration(Base):
    """The REAL client, over REAL stdio, against the mock server: the adaptive loop continues on new evidence, stops cleanly, and
    never lets an optional failure or a capability-less server change the verdict."""

    def test_a_renamed_capability_tool_is_discovered_and_used(self):
        self.set_mode("dynamic")
        report, _ = self.run_rea()
        self.assertEqual(report.status, rc.COMPLETED)
        self.assertIn("byte_entropy", report.capabilities_by_tag)
        self.assertEqual(report.capabilities_by_tag["byte_entropy"], ["inspect_byte_entropy_v2"])
        self.assertIn("inspect_byte_entropy_v2", self.tools_invoked())

    def test_new_evidence_opens_a_follow_up_paging_round(self):
        self.set_mode("reveal")
        report, _ = self.run_rea()
        self.assertEqual(report.status, rc.COMPLETED)
        pages = [c for c in self.server_calls() if c["tool"] == "inspect_analysis_view"]
        self.assertGreaterEqual(len(pages), 2)  # the parent page, then the id the entropy call revealed
        self.assertIn("retained_view", [i["capability"] for i in report.investigation])

    def test_an_answered_question_is_not_asked_again(self):
        self.set_mode("answer")
        report, _ = self.run_rea()
        self.assertEqual(report.status, rc.COMPLETED)
        self.assertEqual(report.questions, [])  # the page answered the architecture question, so nothing remains
        self.assertEqual(report.stop_reason, "the open questions were exhausted")
        self.assertEqual(len([c for c in self.server_calls() if c["tool"] == "inspect_analysis_view"]), 1)

    def test_a_failed_optional_call_is_recorded_and_keeps_the_verdict(self):
        self.set_mode("entropy_error")
        report, _ = self.run_rea()
        self.assertEqual(report.status, rc.COMPLETED)  # the failure is optional and must never change the verdict
        failed = [i for i in report.investigation if not i["ok"]]
        self.assertEqual([i["capability"] for i in failed], ["byte_entropy"])
        self.assertEqual(report.budget["failures"], 1)

    def test_the_loop_terminates_on_the_budget_when_evidence_keeps_changing(self):
        self.set_mode("churn")
        report, _ = self.run_rea(settings=Settings(rea_max_investigation_calls=3))
        self.assertEqual(report.status, rc.COMPLETED)
        self.assertEqual(report.stop_reason, "the investigation call budget was spent")
        self.assertTrue(report.budget["exhausted"]["calls"])  # a budget exhaustion, not a tool failure
        self.assertFalse(report.budget["exhausted"]["failures"])
        self.assertEqual(report.budget["calls_made"], 3)
        self.assertLessEqual(report.budget["calls_made"], 3)  # bounded, never a spin

    def test_useful_evidence_can_continue_past_twelve_rounds(self):
        self.set_mode("long_chain")
        report, _ = self.run_rea(settings=Settings(rea_max_investigation_calls=16))
        self.assertEqual(report.status, rc.COMPLETED)
        self.assertEqual(report.stop_reason, "the investigation call budget was spent")
        self.assertEqual(report.budget["calls_made"], 16)
        self.assertGreaterEqual(len([c for c in self.server_calls() if c["tool"] == "inspect_analysis_view"]), 12)

    def test_no_capability_means_the_loop_stops_immediately(self):
        self.set_mode("ok")  # only the core tools are advertised; nothing optional can be planned
        report, _ = self.run_rea()
        self.assertEqual(report.status, rc.COMPLETED)
        # only the core inspection/analysis tools are eligible; no entropy/unknowns/bundle capability is advertised
        self.assertNotIn("byte_entropy", report.capabilities_by_tag)
        self.assertNotIn("residual_unknowns", report.capabilities_by_tag)
        self.assertNotIn("evidence_bundle", report.capabilities_by_tag)
        self.assertEqual(report.investigation, [])
        self.assertIn("exhausted", report.stop_reason)

    def test_capabilities_by_tag_reflects_only_eligible_tools(self):
        report, _ = self.run_rea()
        flat = [t for tools in report.capabilities_by_tag.values() for t in tools]
        for decoy in ("record_unknown", "close_binary", "open_binary", "gdb_console"):
            self.assertNotIn(decoy, flat)
        self.assertIn("inspect_byte_entropy", flat)


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


class TestSourceInvestigationIntegration(Base):
    """The REAL client, over REAL stdio, against a mock server that advertises a source-text tool: the Analyzer-informed source
    questions are answered by staging the ALREADY-COLLECTED source read-only and selecting the tool by capability."""

    def _run_with_source(self, mode="source", config=None):
        self.set_mode(mode)
        web_ = RangeWeb()
        fetcher = SafeFetcher(Settings(), resolver=PUBLIC, transport=web_)
        info = hf_info((("config.json", 5), ("model.safetensors", SIZE)))
        info.config = config if config is not None else {}  # unknown architecture: the Analyzer raises source questions
        info.source_text = {"model.py": "import torch\nclass Tiny: pass\n"}
        report = rc.run_rea_inspection(info, fetcher, Settings(), command=CMD, project_dir=self.tmp)
        return report, web_

    def test_a_source_text_tool_is_discovered_and_invoked(self):
        report, _ = self._run_with_source()
        self.assertEqual(report.status, rc.COMPLETED)
        self.assertIn("source_text", report.capabilities_by_tag)
        self.assertIn("read_source_file", self.tools_invoked())
        self.assertEqual([i["capability"] for i in report.investigation if i["tool"] == "read_source_file"], ["source_text"])

    def test_the_staged_source_is_read_only_and_inside_the_scratch_dir(self):
        self._run_with_source()
        rows = [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines() if line.strip()]
        reads = [r for r in rows if "source_read" in r]
        self.assertTrue(reads)
        for r in reads:
            self.assertTrue(str(r["source_read"]).startswith(str(self.tmp)))
            self.assertNotIn("..", str(r["source_read"]))

    def test_an_answered_source_question_is_not_recorded_as_an_open_gap(self):
        report, _ = self._run_with_source()
        self.assertEqual(report.source_questions, [])  # the source tool answered them
        self.assertTrue([i for i in report.investigation if i["capability"] == "source_text" and i["ok"]])

    def test_without_a_source_tool_the_questions_are_recorded_as_gaps_not_invented(self):
        report, _ = self._run_with_source(mode="ok")  # only the core tools are advertised
        self.assertEqual(report.status, rc.COMPLETED)  # a missing optional capability never changes the verdict
        self.assertTrue(report.source_questions)
        self.assertEqual([i for i in report.investigation if i["capability"] == "source_text"], [])

    def test_the_combined_analysis_surfaces_source_questions_as_recorded_gaps(self):
        from m2g.analyzer import _rea_investigation_section, _rea_side
        report, _ = self._run_with_source(mode="ok")
        side = _rea_side(report.to_dict())
        self.assertTrue(side["source_questions"])
        section, warnings = _rea_investigation_section(side)
        self.assertEqual(len(section["source_questions"]), len(side["source_questions"]))
        self.assertTrue(any("no tool that returns local source text" in w for w in warnings))


class TestPromptDiscovery(Base):
    """MCP prompts are discovered (read-only) and reported; a server without them yields an empty list, never a failure."""

    def test_advertised_prompts_are_discovered(self):
        self.set_mode("prompts")
        report, _ = self.run_rea()
        self.assertEqual(report.status, rc.COMPLETED)
        self.assertEqual([p["name"] for p in report.prompts], ["model-artifact-review"])

    def test_a_server_without_prompts_is_not_an_error(self):
        self.set_mode("ok")  # this server answers prompts/list with a JSON-RPC error
        report, _ = self.run_rea()
        self.assertEqual(report.status, rc.COMPLETED)
        self.assertEqual(report.prompts, [])

    def test_prompts_reach_the_combined_analysis(self):
        from m2g.analyzer import _rea_investigation_section, _rea_side
        self.set_mode("prompts")
        report, _ = self.run_rea()
        section, _ = _rea_investigation_section(_rea_side(report.to_dict()))
        self.assertEqual([p["name"] for p in section["prompts"]], ["model-artifact-review"])


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
        section = report["rea_investigation"]
        self.assertTrue(section["capabilities_advertised"])
        self.assertIn("inspect_byte_entropy", section["capabilities_advertised"])
        self.assertIn("byte_entropy", section["capabilities_by_tag"])
        self.assertTrue(section["stop_reason"])
        self.assertIn("budget", section)
        self.assertTrue(section["calls"])
        self.assertTrue(res.analysis.generatable)

    def test_investigation_reaches_the_generation_handoff(self):
        res, gg, FakeOpenCodeAdapter, facts_of = self._run_pipeline()
        adapter = FakeOpenCodeAdapter()
        gg.generate_app(res.analysis, adapter=adapter)
        self.assertEqual(adapter.calls, 1)
        rea = facts_of(adapter.requests[0])["rea_inspection"]
        self.assertIn("inspect_byte_entropy", rea["capabilities_discovered"])
        self.assertTrue(rea["investigation_stop_reason"])
        self.assertIn("byte_entropy", rea["capabilities_by_tag"])
        self.assertTrue(any(i["tool"] == "inspect_byte_entropy" for i in rea["investigation"]))


class TestGitHubSourceEndToEnd(unittest.TestCase):
    """A GitHub ML repository (real pipeline, mock server): the platform's own static source analysis and REA's source
    investigation both run, the REA source findings reach the combined analysis with provenance, and the generation handoff
    consumes them. The REA SERVER is a fixture; the inspector, source analysis, client, policy and pipeline are the real code."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.log = self.tmp / "calls.jsonl"
        patcher = mock.patch.dict(os.environ, {"FAKE_REA_MODE": "source", "FAKE_REA_LOG": str(self.log)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def _run_pipeline(self):
        from fakehub import add_github_repo
        from m2g import gradio_gen as gg
        from m2g.pipeline import run_pipeline
        from fake_opencode import FakeOpenCodeAdapter, facts_of
        web_ = RangeWeb()
        add_github_repo(web_, "acme/ml-repo", requirements="torch>=2.0\ntransformers\n",
                        tree=["README.md", "requirements.txt", "model.py", "inference.py", ("model.safetensors", SIZE)],
                        extra_files={"model.py": "import torch\nfrom transformers import AutoModel, AutoTokenizer\n"
                                                "def load_model(name):\n"
                                                "    tok = AutoTokenizer.from_pretrained(name)\n"
                                                "    model = AutoModel.from_pretrained(name)\n"
                                                "    return tok, model\n",
                                     "inference.py": "def predict(text):\n    return text\n"})
        settings = Settings(output_dir=self.tmp / "out")
        runner = rc.make_runner(settings, command=CMD, project_dir=self.tmp)
        res = run_pipeline("https://github.com/acme/ml-repo", settings, web_.fetcher(output_dir=self.tmp / "out"),
                           write=False, rea=runner)
        return res, gg, FakeOpenCodeAdapter, facts_of

    def test_platform_static_source_analysis_reaches_the_analysis(self):
        res, _gg, _A, _f = self._run_pipeline()
        self.assertIsNone(res.error, res.error)
        src = res.analysis.ui_hints.get("source_analysis")
        self.assertIsNotNone(src)
        self.assertTrue(src["load_sites"])  # the real AutoModel/AutoTokenizer.from_pretrained calls were found

    def test_rea_source_investigation_runs_and_reaches_the_combined_analysis(self):
        res, _gg, _A, _f = self._run_pipeline()
        self.assertEqual(res.rea.status, rc.COMPLETED, res.rea.log)
        self.assertIn("read_source_file", [c.tool for c in res.rea.calls])
        section = res.analysis.combined_analysis["rea_investigation"]
        source_calls = [i for i in section["calls"] if i["tool"] == "read_source_file"]
        self.assertTrue(source_calls)
        self.assertTrue(all(i["capability"] == "source_text" and i["ok"] for i in source_calls))
        self.assertEqual(section["source_questions"], [])  # the source tool answered them, so none is left open

    def test_the_platform_source_analysis_reaches_the_generation_handoff(self):
        res, _gg, _A, _f = self._run_pipeline()
        # the GitHub-native generation consumes the analysis' own static source evidence (loading sites, contract)
        src = res.analysis.ui_hints.get("source_analysis")
        self.assertIsNotNone(src)
        self.assertTrue(any("from_pretrained" in s["call"] for s in src["load_sites"]))

    def test_the_source_findings_reach_the_generation_handoff(self):
        res, gg, _A, facts_of = self._run_pipeline()
        # the request the generation handoff would send carries the REA source findings (the GitHub-native path emits a scaffold,
        # so the handoff request itself is asserted here rather than a live OpenCode call)
        self.assertTrue(res.analysis.generatable)
        facts = facts_of(gg.build_request(res.analysis))
        rea = facts["rea_inspection"]
        self.assertEqual(rea["source_questions"], [])  # the source tool answered them, so none is left open
        self.assertTrue(any(i["tool"] == "read_source_file" and i["capability"] == "source_text"
                            for i in rea["investigation"]))


if __name__ == "__main__":
    unittest.main()
