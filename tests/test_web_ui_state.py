"""UI behaviour of ``m2g/web.py``: what the user is told, what a refresh or a new analysis clears, and stale-run protection.

Result rendering and run tracking are plain functions tested directly. The Gradio wiring is exercised against the recording
stub in ``fake_gradio.py`` (NOT the real Gradio). No network, no Colab, nothing is executed from any repository.
"""
import ast
import dataclasses
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from m2g import colab_workflow as cw
from m2g import web
from m2g.colab_panel import ColabPanel
from m2g.config import Settings
from m2g.pipeline import BuildResult, run_pipeline
from fake_gradio import events_named, fake_gradio
from fakehub import FakeWeb, PRESETS, add_github_repo, add_hf_model
from test_colab_workflow import FakeCli
from test_github_generation import UNSUPPORTED_LINKED, github_repo_for, readme_for

UNAVAILABLE = "Gradio App Generation Not Available"
GENERATED = "Analysis completed and Gradio app generated"
HF = "https://huggingface.co/"


def repos(*keys):
    """ONE fake web that serves several GitHub repos (each linked to its preset's Hugging Face model); returns (web, urls)."""
    web_, urls = FakeWeb(), []
    for key in keys:
        spec = dict(PRESETS[key])
        model_id = spec.pop("repo_id")
        owner, name = model_id.split("/")
        add_hf_model(web_, model_id, **spec)
        add_github_repo(web_, f"{owner}/{name}", readme=readme_for(model_id, name), requirements="torch>=2.0\ntransformers\n")
        urls.append(f"https://github.com/{owner}/{name}")
    return web_, urls


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.settings = Settings(output_dir=Path(self.tmp.name) / "out")

    def panel(self):
        cli = FakeCli()
        launcher = cw.ColabLauncher(cli, verify=lambda url, auth: (True, "ok"), sleep=lambda s: None, poll_interval=0,
                                    url_retries=1, url_retry_delay=0, remote_dir=str(Path(self.tmp.name) / "remote"))
        return ColabPanel(cli=cli, launcher=launcher, settings_factory=Settings)


class TestGenerationEligibilityMessages(Base):
    def test_unsupported_model_says_generation_is_unavailable_and_why(self):
        for key in ("pickle", *UNSUPPORTED_LINKED):
            with self.subTest(key):
                web_, gh_url, gh = github_repo_for(key)
                for url in (HF + PRESETS[key]["repo_id"], gh_url):
                    if key == "pickle" and url.startswith(HF):
                        continue  # a direct HF URL of a pickle-only model is a generatable candidate (runtime pickle loading stays off)
                    summary, code, reqs, files = web.handle_submit(url, self.settings, web_.fetcher())
                    self.assertIn(UNAVAILABLE, summary)
                    self.assertIn(web.UNAVAILABLE_TEXT, summary)
                    self.assertIn("**No working Gradio app was generated.** Only `analysis.json` was written", summary)
                    self.assertIn("Analysis completed, but no app was generated.", summary)
                    self.assertNotIn(GENERATED, summary)
                    self.assertNotIn("Analysis failed", summary)
                    self.assertEqual((code, reqs), ("", ""))
                    self.assertEqual([Path(f).name for f in files], ["analysis.json"])

    def test_the_exact_refusal_reasons_are_listed(self):
        web_, url, _ = github_repo_for("pickle")
        res = run_pipeline(url, self.settings, web_.fetcher())
        banner = web.generation_banner(res)
        self.assertEqual(web.generation_state(res), web.UNAVAILABLE)
        self.assertTrue(res.analysis.reasons)
        for reason in res.analysis.reasons:
            self.assertIn("- " + web._m(reason, 700), banner)
        self.assertIn("pickle", banner)

    def test_overview_shows_status_task_model_framework_inputs_outputs(self):
        web_, url, _ = github_repo_for("sst2")
        summary = web.handle_submit(url, self.settings, web_.fetcher())[0]
        for want in ("### Analysis result", "- **Status:** `supported_with_warnings`", "- **Task:** `text-classification`",
                     "- **Framework:** `transformers`", "- **Inputs:** text (gr.Textbox)", "- **Outputs:** labels (gr.Label)",
                     "- **Model / repository:** `", "(GitHub repository)", "- **Model used by the app:** `" + PRESETS["sst2"]["repo_id"]):
            self.assertIn(want, summary)
        self.assertIn("**Warnings:**", summary)

    def test_unavailable_overview_does_not_pretend_inputs_exist(self):
        web_, url, _ = github_repo_for("pickle")
        summary = web.handle_submit(url, self.settings, web_.fetcher())[0]
        self.assertIn("- **Inputs:** not available", summary)
        self.assertIn("(not used for generation)", summary)

    def test_supported_model_shows_the_generated_status(self):
        web_, url, _ = github_repo_for("sst2")
        for target in (HF + PRESETS["sst2"]["repo_id"], url):
            summary, code, reqs, files = web.handle_submit(target, self.settings, web_.fetcher())
            self.assertIn(GENERATED, summary)
            self.assertIn("A working Gradio app was generated", summary)
            self.assertIn("`app.py`", summary)
            self.assertNotIn(UNAVAILABLE, summary)
            self.assertNotIn("Analysis failed", summary)
            self.assertTrue(code and reqs)
            self.assertEqual(len(files), 6)  # app, loader, requirements, launcher, notebook, analysis

    def test_a_failed_analysis_is_its_own_state(self):
        web_, _, _ = github_repo_for("sst2")
        summary, code, reqs, files = web.handle_submit("http://localhost/x/y", self.settings, web_.fetcher())
        self.assertIn("Analysis failed", summary)
        self.assertIn("Failed at stage", summary)
        self.assertIn("No analysis and no Gradio app were produced.", summary)
        self.assertNotIn(UNAVAILABLE, summary)
        self.assertNotIn(GENERATED, summary)
        self.assertEqual((code, reqs, files), ("", "", []))

    def test_the_three_outcomes_are_told_apart(self):
        web_, url, _ = github_repo_for("sst2")
        ok = run_pipeline(url, self.settings, web_.fetcher())
        web2, url2, _ = github_repo_for("pickle")
        refused = run_pipeline(url2, self.settings, web2.fetcher())
        failed = run_pipeline("http://localhost/x/y", self.settings, web_.fetcher())
        self.assertEqual([web.generation_state(r) for r in (ok, refused, failed)], [web.GENERATED, web.UNAVAILABLE, web.FAILED])

    def test_untrusted_text_cannot_inject_markup_into_the_message(self):
        web_, url, _ = github_repo_for("pickle")
        res = run_pipeline(url, self.settings, web_.fetcher())
        evil = "<img src=x onerror=alert(1)> [click](http://evil.example)"
        res.analysis = dataclasses.replace(res.analysis, reasons=[evil])
        banner = web.generation_banner(res)
        self.assertNotIn("<img", banner)
        self.assertNotIn("](http://evil.example)", banner)

    def test_a_refusal_without_a_recorded_reason_is_still_explained(self):
        web_, url, _ = github_repo_for("pickle")
        res = run_pipeline(url, self.settings, web_.fetcher())
        res.analysis = dataclasses.replace(res.analysis, reasons=[])
        self.assertIn("No specific reason was recorded", web.generation_banner(res))


class TestRunTrackerAndNewAnalysis(Base):
    def test_a_new_analysis_clears_the_previous_results_at_once(self):
        tracker = web.RunTracker()
        first, _ = web.begin_analysis(tracker, None, HF + "a/one")
        second, view = web.begin_analysis(tracker, first, HF + "b/two")
        self.assertEqual((view.summary, view.app_code, view.requirements, view.files), (web.ANALYZING_MD, "", "", []))
        self.assertIsNone(view.result)
        self.assertEqual(second.sid, first.sid)
        self.assertGreater(second.number, first.number)
        self.assertEqual(second.url, HF + "b/two")

    def test_an_empty_url_asks_for_one_instead_of_pretending_to_analyze(self):
        _, view = web.begin_analysis(web.RunTracker(), None, "   ")
        self.assertEqual(view.summary, web.PASTE_URL_MD)

    def test_only_the_newest_run_is_current(self):
        tracker = web.RunTracker()
        a, _ = web.begin_analysis(tracker, None, HF + "a/one")
        b, _ = web.begin_analysis(tracker, a, HF + "b/two")
        self.assertFalse(tracker.is_current(a))
        self.assertTrue(tracker.is_current(b))
        other, _ = web.begin_analysis(tracker, None, HF + "c/three")  # another browser session is independent
        self.assertTrue(tracker.is_current(b) and tracker.is_current(other))
        tracker.invalidate(b)
        self.assertFalse(tracker.is_current(b))
        self.assertFalse(tracker.is_current(None))

    def test_an_old_analysis_that_finishes_late_cannot_overwrite_the_newer_one(self):
        web_, (url_a, url_b) = repos("sst2", "gpt2")
        release_a, a_started = threading.Event(), threading.Event()
        real = run_pipeline

        def slow_for_a(url, settings, fetcher=None, write=True):
            if url == url_a:
                a_started.set()
                self.assertTrue(release_a.wait(10))
            return real(url, settings, web_.fetcher(), write)

        tracker, out = web.RunTracker(), {}
        with mock.patch.object(web, "run_pipeline", slow_for_a):
            ticket_a, _ = web.begin_analysis(tracker, None, url_a)
            worker = threading.Thread(target=lambda: out.update(a=web.finish_analysis(ticket_a, tracker, self.settings)))
            worker.start()
            self.assertTrue(a_started.wait(10))
            ticket_b, _ = web.begin_analysis(tracker, ticket_a, url_b)  # the user analyzes another model meanwhile
            view_b = web.finish_analysis(ticket_b, tracker, self.settings)
            release_a.set()
            worker.join(10)
        self.assertIsNone(out["a"], "the stale run must show nothing")
        self.assertIsNotNone(view_b)
        self.assertIn(PRESETS["gpt2"]["repo_id"], view_b.summary)
        self.assertNotIn(PRESETS["sst2"]["repo_id"], view_b.summary)

    def test_a_run_replaced_before_it_starts_does_no_work(self):
        tracker = web.RunTracker()
        a, _ = web.begin_analysis(tracker, None, HF + "a/one")
        web.begin_analysis(tracker, a, HF + "b/two")
        with mock.patch.object(web, "run_pipeline") as pipeline:
            self.assertIsNone(web.finish_analysis(a, tracker, self.settings))
        pipeline.assert_not_called()


class TestRefreshResetsTheScreenButNotTheDisk(Base):
    def test_panel_reset_drops_logs_and_a_finished_launch_result(self):
        p = self.panel()
        p.launcher._snap = cw.LaunchSnapshot(state=cw.FAILED, message="boom", logs=["10:00:00 old model line"], diagnostics="trace")
        p.launcher._diag.append("trace")
        p._notice = "old notice"
        self.assertIn("old model line", p.logs_text())
        p.reset_view()
        snap = p.launcher.snapshot()
        self.assertEqual((p.logs_text(), snap.state, snap.message, snap.diagnostics), ("", cw.IDLE, "", ""))
        self.assertEqual(p._notice, "")

    def test_panel_reset_never_touches_a_running_runtime_or_a_launch_in_progress(self):
        p = self.panel()
        p.launcher._snap = cw.LaunchSnapshot(state=cw.RUNNING, session="s1", share_url="https://x.gradio.live", logs=["old"])
        p.reset_view()
        snap = p.launcher.snapshot()
        self.assertEqual((snap.state, snap.session, snap.share_url, snap.logs), (cw.RUNNING, "s1", "https://x.gradio.live", []))
        p.launcher._snap = cw.LaunchSnapshot(state=cw.INSTALLING, message="Installing", logs=["old"])
        p.launcher._busy = True
        p.reset_view()
        snap = p.launcher.snapshot()
        self.assertEqual((snap.state, snap.message, snap.logs), (cw.INSTALLING, "Installing", []))

    def test_panel_reset_keeps_a_cancel_or_stop_that_is_still_running(self):
        p = self.panel()
        p._notice, p._stopping = "Stopping the runtime…", True
        p.reset_view()
        self.assertEqual(p._notice, "Stopping the runtime…")

    def test_refresh_preserves_generated_files_on_disk(self):
        web_, url, _ = github_repo_for("sst2")
        web.handle_submit(url, self.settings, web_.fetcher())
        before = {p: p.read_bytes() for p in self.settings.output_dir.rglob("*") if p.is_file()}
        # 6 package files + the hidden manifest. The per-model process lock file (output/.m2g-locks/<slug>.lock) is deliberately retained
        # by package_store.model_lock outside the model's folder; it is bookkeeping, not a generated artifact, so it is counted apart.
        locks = [p for p in before if p.parent.name == ".m2g-locks"]
        self.assertEqual(len(locks), 1)
        self.assertEqual(len(before) - len(locks), 7)
        p, tracker = self.panel(), web.RunTracker()
        ticket, _ = web.begin_analysis(tracker, None, url)
        tracker.invalidate(ticket)
        p.reset_view()
        self.assertEqual({q: q.read_bytes() for q in self.settings.output_dir.rglob("*") if q.is_file()}, before)

    def test_web_module_never_deletes_anything_from_disk(self):
        tree = ast.parse(Path(web.__file__).read_text(encoding="utf-8"))
        names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)} | {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        self.assertFalse(names & {"rmtree", "remove", "unlink", "rmdir", "removedirs"})


class TestGradioWiring(Base):
    """build_ui run against the recording stub: the registered handlers are called exactly as Gradio would call them."""

    def built(self, events):
        panel = self.panel()
        web.build_ui(self.settings, panel=panel)
        return panel, {n: events_named(events, n) for n in ("_begin", "_analyze", "_page_load")}

    def test_analyze_is_an_instant_clear_followed_by_the_slow_analysis_for_both_triggers(self):
        with fake_gradio() as events:
            _, ev = self.built(events)
        self.assertEqual((len(ev["_begin"]), len(ev["_analyze"]), len(ev["_page_load"])), (2, 2, 1))
        for begin, analyze in zip(ev["_begin"], ev["_analyze"]):
            self.assertIs(analyze.after, begin)
            self.assertGreater(analyze.options.get("concurrency_limit", 1), 1, "a new click must not queue behind an old analysis")

    def test_begin_returns_one_value_per_output_and_clears_everything_old(self):
        with fake_gradio() as events:
            panel, ev = self.built(events)
        panel.launcher._snap = cw.LaunchSnapshot(state=cw.FAILED, message="old failure", logs=["old model log"])
        begin = ev["_begin"][0]
        out = begin.fn(HF + "new/model", None)
        self.assertEqual(len(out), len(begin.outputs))
        summary, code, reqs, files, ticket = out[:5]
        self.assertEqual((summary, code, reqs, files), (web.ANALYZING_MD, "", "", []))
        self.assertEqual(ticket.url, HF + "new/model")
        self.assertEqual(panel.logs_text(), "")
        self.assertEqual(panel.launcher.snapshot().state, cw.IDLE)

    def test_page_load_resets_the_screen_logs_and_run_state(self):
        with fake_gradio() as events:
            panel, ev = self.built(events)
        panel.launcher._snap = cw.LaunchSnapshot(state=cw.RUNNING, session="s1", share_url="https://x.gradio.live", logs=["old"])
        load = ev["_page_load"][0]
        out = load.fn(web.RunTicket("s", 1, "u"))
        self.assertEqual(len(out), len(load.outputs))
        self.assertEqual(out[:5], (web.RESULTS_PLACEHOLDER, "", "", [], None))
        self.assertEqual(panel.logs_text(), "")
        self.assertEqual(panel.launcher.snapshot().state, cw.RUNNING, "a refresh must not forget a live runtime")

    def test_full_click_flow_shows_the_unavailable_message_then_the_generated_message(self):
        web_, (url_bad, url_ok) = repos("pickle", "sst2")
        real = run_pipeline
        with fake_gradio() as events, mock.patch.object(web, "run_pipeline", lambda u, s, fetcher=None, write=True: real(u, s, web_.fetcher(), write)):
            panel, ev = self.built(events)
            begin, analyze = ev["_begin"][0], ev["_analyze"][0]
            seen = []
            ticket = None
            for url in (url_bad, url_ok):
                out = begin.fn(url, ticket)
                ticket = out[4]
                result = analyze.fn(ticket)
                self.assertEqual(len(result), len(analyze.outputs))
                seen.append(result[0])
        self.assertIn(UNAVAILABLE, seen[0])
        self.assertNotIn(GENERATED, seen[0])
        self.assertIn(GENERATED, seen[1])
        self.assertNotIn(UNAVAILABLE, seen[1], "the earlier model's message must not survive into the next result")
        self.assertIn("**Suggested Colab runtime:**", seen[1])

    def test_a_stale_result_updates_nothing_on_the_page(self):
        web_, url_a, _ = github_repo_for("sst2")
        real = run_pipeline
        with fake_gradio() as events, mock.patch.object(web, "run_pipeline", lambda u, s, fetcher=None, write=True: real(u, s, web_.fetcher(), write)):
            _, ev = self.built(events)
            begin, analyze = ev["_begin"][0], ev["_analyze"][0]
            ticket_a = begin.fn(url_a, None)[4]
            begin.fn(url_a, ticket_a)  # a newer click supersedes ticket_a before it ran
            result = analyze.fn(ticket_a)
        self.assertEqual(result, tuple({"__update__": {}} for _ in range(5)))


if __name__ == "__main__":
    unittest.main()
