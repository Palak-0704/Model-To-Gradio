"""Phase 4.2 backend hardening: per-model locks, staged writes with rollback, generation fingerprints, the package manifest,
manifest verification before a launch and fingerprint-based reuse of an existing package (OpenCode skipped).

Everything is offline: the fake Hugging Face transport (fakehub) and the fake OpenCode adapter it installs. No model, process or network.
"""
import contextlib
import json
import multiprocessing
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from m2g import gradio_gen as gg
from m2g import local_runner, package_store as ps, pipeline, web_api
from m2g.config import Settings
from m2g.gradio_gen import GenerationError
from m2g.local_runner import LocalRunError
from m2g.local_script import launcher_filename
from m2g.pipeline import run_pipeline
from m2g.security import SecurityError, parse_source_url
from fakehub import FakeWeb, hf, PRESETS
from test_analyzer import analyze_preset

SLUG = "owner__model"
REV = "a" * 40
FILES = {
    "app.py": "from loader import get_model\n",
    "loader.py": "def get_model():\n    return None\n",
    "requirements.txt": "gradio\n",
    "analysis.json": "{}\n",
    "colab_notebook.ipynb": "{}\n",
    launcher_filename(SLUG): "@echo off\nexit /b 0\n",
}


def manifest_for(files, **kw):
    args = dict(slug=SLUG, source_repo_id="owner/model", source_kind="huggingface", model_id="owner/model", revision=REV,
                task="text-classification", fingerprint="f" * 64, generator="g" * 64)
    args.update(kw)
    return ps.build_manifest(files, **args)


def make_package(base, files=FILES, **kw):
    with ps.model_lock(base, SLUG):
        return ps.write_package(base, SLUG, {**files, ps.MANIFEST_NAME: manifest_for(files, **kw)})


def _process_lock_attempt(base, slug, ready, release, result, timeout=2.0, raises=False):
    """Spawn-safe worker for the Windows process-lock tests."""
    try:
        with ps.model_lock(base, slug, timeout=timeout):
            ready.set()
            if raises:
                raise RuntimeError("worker failure")
            result.put("acquired")
            release.wait(10)
    except ps.PackageBusyError:
        result.put("busy")
        ready.set()
    except RuntimeError:
        result.put("raised")
        ready.set()


def _process_pipeline_run(base, start, result):
    """Spawn-safe real pipeline worker using the existing offline transport/OpenCode fixture."""
    start.wait(10)
    web = FakeWeb()
    hf(web, "sst2")
    res = run_pipeline("https://huggingface.co/" + PRESETS["sst2"]["repo_id"],
                       Settings(output_dir=Path(base)), web.fetcher(), evidence=False)
    result.put((res.ok, res.error, res.reused_package, res.out_dir.name if res.out_dir else ""))


def snapshot(folder):
    return {p.name: p.read_bytes() for p in Path(folder).iterdir() if p.is_file()}


@contextlib.contextmanager
def lock_held_elsewhere(base, slug):
    """Another thread holds the model lock (a lock is re-entrant per thread, so the test thread itself cannot play that role)."""
    ready, release = threading.Event(), threading.Event()

    def hold():
        with ps.model_lock(base, slug):
            ready.set()
            release.wait(15)

    thread = threading.Thread(target=hold, daemon=True)
    thread.start()
    assert ready.wait(5)
    try:
        yield
    finally:
        release.set()
        thread.join(5)


def lockable_from_other_thread(base, slug):
    result = []

    def attempt():
        try:
            with ps.model_lock(base, slug, timeout=0.5):
                result.append(True)
        except ps.PackageBusyError:
            result.append(False)

    thread = threading.Thread(target=attempt, daemon=True)
    thread.start()
    thread.join(5)
    return result == [True]


class TmpCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve() / "out"


class TestModelLocks(TmpCase):
    def test_the_lock_is_reentrant_for_one_thread(self):
        with ps.model_lock(self.base, SLUG, timeout=0.5):
            with ps.model_lock(self.base, SLUG, timeout=0.5):
                pass

    def test_another_thread_cannot_take_a_held_lock_and_gets_a_clear_error(self):
        with ps.model_lock(self.base, SLUG):
            self.assertFalse(lockable_from_other_thread(self.base, SLUG))
        self.assertTrue(lockable_from_other_thread(self.base, SLUG))

    def test_other_models_are_independent(self):
        with ps.model_lock(self.base, SLUG):
            self.assertTrue(lockable_from_other_thread(self.base, "other__model"))

    def test_the_lock_is_released_when_the_body_raises(self):
        with self.assertRaises(RuntimeError):
            with ps.model_lock(self.base, SLUG):
                raise RuntimeError("boom")
        self.assertTrue(lockable_from_other_thread(self.base, SLUG))

    def test_a_busy_lock_raises_package_busy_error_naming_the_model(self):
        with lock_held_elsewhere(self.base, SLUG):
            with self.assertRaises(ps.PackageBusyError) as ctx:
                with ps.model_lock(self.base, SLUG, timeout=0.1):
                    pass
        self.assertIn(SLUG, str(ctx.exception))
        self.assertIsInstance(ctx.exception, OSError)  # reported by the pipeline as a write-stage failure

    def test_the_key_ignores_how_the_folder_is_spelled(self):
        self.assertEqual(ps.lock_key(self.base, SLUG), ps.lock_key(self.base / ".." / "out", SLUG))

    def test_write_package_requires_the_combined_model_lock(self):
        with self.assertRaises(ps.PackageStoreError):
            ps.write_package(self.base, SLUG, {"notes.txt": "x"})


class TestProcessModelLocks(TmpCase):
    def setUp(self):
        super().setUp()
        self.ctx = multiprocessing.get_context("spawn")

    def worker(self, slug=SLUG, *, raises=False):
        ready, release, result = self.ctx.Event(), self.ctx.Event(), self.ctx.Queue()
        proc = self.ctx.Process(target=_process_lock_attempt,
                                args=(str(self.base), slug, ready, release, result, 1.0, raises))
        proc.start()
        self.assertTrue(ready.wait(10))
        return proc, release, result

    def finish(self, proc, release):
        release.set()
        proc.join(15)
        self.assertEqual(proc.exitcode, 0)

    def test_two_processes_cannot_lock_the_same_slug(self):
        first, release, first_result = self.worker()
        self.assertEqual(first_result.get(timeout=3), "acquired")
        second, release2, second_result = self.worker()
        self.assertEqual(second_result.get(timeout=3), "busy")
        self.finish(second, release2)
        self.finish(first, release)

    def test_two_processes_can_lock_different_slugs(self):
        first, release1, first_result = self.worker("first__model")
        second, release2, second_result = self.worker("second__model")
        self.assertEqual(first_result.get(timeout=3), "acquired")
        self.assertEqual(second_result.get(timeout=3), "acquired")
        self.finish(first, release1)
        self.finish(second, release2)

    def test_process_lock_is_released_after_an_exception(self):
        failed, release, failed_result = self.worker(raises=True)
        self.assertEqual(failed_result.get(timeout=3), "raised")
        self.finish(failed, release)
        with ps.model_lock(self.base, SLUG, timeout=1.0):
            pass

    def test_concurrent_process_pipelines_share_one_safe_package(self):
        start, result = self.ctx.Event(), self.ctx.Queue()
        workers = [self.ctx.Process(target=_process_pipeline_run, args=(str(self.base), start, result)) for _ in range(2)]
        for worker in workers:
            worker.start()
        start.set()
        outcomes = [result.get(timeout=60) for _ in workers]
        for worker in workers:
            worker.join(60)
            self.assertEqual(worker.exitcode, 0)
        self.assertTrue(all(ok for ok, _, _, _ in outcomes), outcomes)
        self.assertTrue(any(reused for _, _, reused, _ in outcomes), outcomes)
        self.assertEqual(len({slug for _, _, _, slug in outcomes}), 1)
        self.assertEqual(ps.verify_package(self.base / outcomes[0][3]).state, "ok")

    @staticmethod
    def _resolve_hook(hook):
        """Patch Path.resolve so ``hook(path)`` answers for the paths it returns something for; every other path resolves normally."""
        real = Path.resolve

        def patched(path, *args, **kwargs):
            answer = hook(path)
            return answer if answer is not None else real(path, *args, **kwargs)

        return mock.patch.object(Path, "resolve", patched)

    def test_the_lock_file_is_never_resolved_so_a_transient_windows_resolution_cannot_fail_containment(self):
        # Windows' realpath looks a path up twice and keeps a \\?\ prefix when a concurrent process changes the path between the two
        # lookups (the lock folder/file being created or held by the other process). The path then no longer looks like a child of the
        # output folder. This is a deterministic stand-in for that outcome: any attempt to resolve a lock file returns a foreign path.
        elsewhere = Path(self.tmp.name).resolve() / "elsewhere"
        with self._resolve_hook(lambda p: elsewhere / p.name if p.name.endswith(".lock") else None):
            with ps.model_lock(self.base, SLUG, timeout=1.0):
                pass
        self.assertTrue((self.base / ps.LOCK_DIRNAME / (SLUG + ".lock")).is_file())
        self.assertFalse(elsewhere.exists())

    def test_a_lock_folder_that_resolves_outside_the_output_folder_is_still_refused(self):
        elsewhere = Path(self.tmp.name).resolve() / "elsewhere"
        with self._resolve_hook(lambda p: elsewhere if p.name == ps.LOCK_DIRNAME else None):
            with self.assertRaises(SecurityError):
                with ps.model_lock(self.base, SLUG, timeout=1.0):
                    self.fail("the lock must not be taken for a folder outside the output folder")
        self.assertFalse(elsewhere.exists())
        self.assertTrue(lockable_from_other_thread(self.base, SLUG))  # nothing was left held

    def test_a_link_in_place_of_the_lock_file_is_refused(self):
        real = Path.is_symlink
        with mock.patch.object(Path, "is_symlink", lambda p: p.name.endswith(".lock") or real(p)):
            with self.assertRaises(SecurityError):
                with ps.model_lock(self.base, SLUG, timeout=1.0):
                    self.fail("the lock must not be taken through a link")
        self.assertTrue(lockable_from_other_thread(self.base, SLUG))

    def test_process_writer_lock_blocks_a_local_launch(self):
        out = make_package(self.base)
        holder, release, result = self.worker()
        self.assertEqual(result.get(timeout=3), "acquired")
        with mock.patch.object(local_runner, "LAUNCH_LOCK_WAIT_S", 0.1):
            with self.assertRaises(LocalRunError) as ctx:
                local_runner.start_local_app(object(), self.base, out.name, Settings(output_dir=self.base))
        self.assertEqual(ctx.exception.status, 409)
        self.finish(holder, release)

    def test_process_writer_lock_blocks_the_colab_api_launch_gate(self):
        make_package(self.base)
        holder, release, result = self.worker()
        self.assertEqual(result.get(timeout=3), "acquired")
        try:
            responses = []
            handler = object.__new__(web_api.ApiHandler)
            handler._json_error = lambda code, message: responses.append((code, message))
            handler._json_ok = lambda *args, **kwargs: responses.append((200, args[0] if args else {}))
            real_lock = ps.model_lock

            def quick_lock(*args, **kwargs):
                kwargs["timeout"] = 0.1
                return real_lock(*args, **kwargs)

            with mock.patch("m2g.web_api._get_settings", return_value=Settings(output_dir=self.base)), \
                    mock.patch.object(ps, "model_lock", side_effect=quick_lock):
                handler._handle_api_post(f"/api/colab/{SLUG}/start", {})
            self.assertEqual(responses[0][0], 409)
            self.assertIn("being analysed or generated", responses[0][1])
        finally:
            self.finish(holder, release)


class TestStagedWrite(TmpCase):
    def test_files_are_written_exactly_and_nothing_is_left_in_staging(self):
        out = make_package(self.base)
        for name, text in FILES.items():
            self.assertEqual((out / name).read_bytes(), ps.disk_bytes(name, text), name)
        self.assertEqual((out / launcher_filename(SLUG)).read_bytes().count(b"\n"), (out / launcher_filename(SLUG)).read_bytes().count(b"\r\n"))
        self.assertFalse((self.base / ps.STAGING_DIRNAME).exists())

    def test_an_existing_package_is_replaced_by_the_new_one(self):
        out = make_package(self.base)
        make_package(self.base, {**FILES, "app.py": "from loader import get_model  # v2\n"})
        self.assertIn("v2", (out / "app.py").read_text(encoding="utf-8"))
        self.assertEqual(ps.verify_package(out).state, "ok")

    def test_unchanged_files_are_not_touched(self):
        make_package(self.base)
        real = ps._replace_with_retry
        with mock.patch.object(ps, "_replace_with_retry", side_effect=real) as replace:
            with ps.model_lock(self.base, SLUG):
                ps.write_package(self.base, SLUG, {**FILES, ps.MANIFEST_NAME: (self.base / SLUG / ps.MANIFEST_NAME).read_text(encoding="utf-8")})
        self.assertEqual(replace.call_count, 0)

    def test_a_failure_part_way_restores_the_previous_package(self):
        out = make_package(self.base)
        before = snapshot(out)
        new = {**FILES, "app.py": "from loader import get_model  # v2\n", "loader.py": "def get_model():\n    return 2\n", "notes.txt": "new\n"}
        real, calls = ps._replace_with_retry, {"n": 0}

        def flaky(src, dst, *args, **kw):
            calls["n"] += 1
            if calls["n"] == 3:  # app.py and loader.py were replaced, the third replace fails
                raise PermissionError("locked by another program")
            return real(src, dst, *args, **kw)

        with mock.patch.object(ps, "_replace_with_retry", side_effect=flaky):
            with self.assertRaises(ps.PackageCommitError) as ctx:
                with ps.model_lock(self.base, SLUG):
                    ps.write_package(self.base, SLUG, new)
        self.assertIn("previous package was restored", str(ctx.exception))
        self.assertEqual(snapshot(out), before)  # byte-for-byte the old package, and the new notes.txt is gone
        self.assertEqual(ps.verify_package(out).state, "ok")
        self.assertFalse((self.base / ps.STAGING_DIRNAME).exists(), "staging and backup folders are cleaned up")

    def test_originals_are_kept_when_a_rollback_cannot_finish(self):
        out = make_package(self.base)
        real, calls = ps._replace_with_retry, {"n": 0}

        def flaky(src, dst, *args, **kw):
            calls["n"] += 1
            if calls["n"] == 2:
                raise PermissionError("locked")
            return real(src, dst, *args, **kw)

        with mock.patch.object(ps, "_replace_with_retry", side_effect=flaky), mock.patch.object(ps, "_rollback", return_value=["app.py"]):
            with self.assertRaises(ps.PackageCommitError) as ctx:
                with ps.model_lock(self.base, SLUG):
                    ps.write_package(self.base, SLUG, {**FILES, "app.py": "x = 1\n", "loader.py": "y = 2\n"})
        self.assertIn("could not be restored", str(ctx.exception))
        self.assertTrue(any(p.name.endswith(".bak") for p in (self.base / ps.STAGING_DIRNAME).iterdir()))
        self.assertTrue(out.is_dir())

    def test_a_staging_failure_leaves_the_live_package_alone(self):
        out = make_package(self.base)
        before = snapshot(out)
        with mock.patch.object(ps, "disk_bytes", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                with ps.model_lock(self.base, SLUG):
                    ps.write_package(self.base, SLUG, {**FILES, "app.py": "changed\n"})
        self.assertEqual(snapshot(out), before)

    def test_unsafe_artifact_names_are_refused(self):
        for bad in ("../evil.py", "sub/app.py", "..", "a b.py", ""):
            with self.assertRaises(ps.PackageStoreError, msg=bad):
                with ps.model_lock(self.base, SLUG):
                    ps.write_package(self.base, SLUG, {bad: "x"})

    def test_a_link_or_folder_in_place_of_an_artifact_is_not_replaced(self):
        out = make_package(self.base)
        (out / "notes.txt").mkdir()
        with self.assertRaises(ps.PackageCommitError):
            with ps.model_lock(self.base, SLUG):
                ps.write_package(self.base, SLUG, {"notes.txt": "x"})

    def test_a_transient_permission_error_is_retried_and_a_persistent_one_is_raised(self):
        with mock.patch.object(ps.os, "replace", side_effect=[PermissionError("scanner"), None]) as replace, \
                mock.patch.object(ps.time, "sleep"):
            ps._replace_with_retry(Path("a"), Path("b"))
        self.assertEqual(replace.call_count, 2)
        with mock.patch.object(ps.os, "replace", side_effect=PermissionError("held")), mock.patch.object(ps.time, "sleep"):
            with self.assertRaises(PermissionError):
                ps._replace_with_retry(Path("a"), Path("b"), attempts=3)


class TestFingerprints(unittest.TestCase):
    def test_the_same_inputs_give_the_same_value_and_each_input_matters(self):
        base = ps.generation_fingerprint("request", "gen", "owner/model", REV)
        self.assertEqual(base, ps.generation_fingerprint("request", "gen", "owner/model", REV))
        self.assertRegex(base, r"^[0-9a-f]{64}$")
        for other in (("request 2", "gen", "owner/model", REV), ("request", "gen2", "owner/model", REV),
                      ("request", "gen", "owner/other", REV), ("request", "gen", "owner/model", "b" * 40),
                      ("request", "gen", "owner/model", None)):
            self.assertNotEqual(base, ps.generation_fingerprint(*other), other)

    def test_the_generator_fingerprint_is_stable_and_follows_the_settings_that_change_generation(self):
        plain = ps.generator_fingerprint(Settings())
        self.assertRegex(plain or "", r"^[0-9a-f]{64}$")
        self.assertEqual(plain, ps.generator_fingerprint(Settings()))
        self.assertNotEqual(plain, ps.generator_fingerprint(Settings(allow_remote_code=True)))

    def test_an_analysis_that_cannot_be_turned_into_a_request_disables_caching_instead_of_failing(self):
        fingerprint, generator = ps.fingerprints_for(object(), Settings())
        self.assertIsNone(fingerprint)
        self.assertIsNotNone(generator)

    def test_the_real_request_of_an_analysis_gives_a_stable_fingerprint(self):
        a = analyze_preset("sst2")
        first, second = ps.fingerprints_for(a, Settings()), ps.fingerprints_for(analyze_preset("sst2"), Settings())
        self.assertIsNotNone(first[0])
        self.assertEqual(first, second)


class TestManifest(TmpCase):
    def test_it_records_identity_fingerprints_and_a_hash_per_artifact(self):
        doc = json.loads(manifest_for(FILES))
        self.assertEqual(doc["schema"], ps.MANIFEST_SCHEMA)
        self.assertEqual((doc["model"]["model_id"], doc["model"]["revision"]), ("owner/model", REV))
        self.assertEqual((doc["fingerprint"], doc["generator_fingerprint"]), ("f" * 64, "g" * 64))
        self.assertEqual(set(doc["artifacts"]), set(FILES))
        bat = launcher_filename(SLUG)
        self.assertEqual(doc["artifacts"][bat]["sha256"], ps.sha256_bytes(ps.disk_bytes(bat, FILES[bat])))  # hash of the CRLF bytes on disk
        self.assertNotIn(ps.MANIFEST_NAME, doc["artifacts"])

    def test_a_written_package_verifies(self):
        out = make_package(self.base)
        check = ps.verify_package(out, expect_model_id="owner/model", expect_revision=REV)
        self.assertEqual((check.state, check.problems), ("ok", ()))

    def test_a_folder_without_a_manifest_is_a_usable_legacy_package(self):
        out = make_package(self.base)
        (out / ps.MANIFEST_NAME).unlink()
        check = ps.verify_package(out)
        self.assertEqual(check.state, "absent")
        self.assertTrue(check.usable)

    def test_every_kind_of_tampering_is_named(self):
        out = make_package(self.base)
        (out / "app.py").write_text("changed\n", encoding="utf-8")
        (out / "loader.py").unlink()
        check = ps.verify_package(out)
        self.assertEqual(check.state, "invalid")
        self.assertFalse(check.usable)
        text = " ".join(check.problems)
        self.assertIn("app.py changed after it was generated", text)
        self.assertIn("loader.py is missing", text)
        self.assertIn("generate the app again", check.message())

    def test_a_mixed_folder_with_a_new_analysis_is_detected(self):
        out = make_package(self.base)
        (out / "analysis.json").write_text('{"revision": "new"}\n', encoding="utf-8")
        self.assertIn("analysis.json changed", " ".join(ps.verify_package(out).problems))

    def test_the_launcher_is_recorded_but_not_enforced(self):
        out = make_package(self.base)
        (out / launcher_filename(SLUG)).write_bytes(b"rewritten by the backend template\r\n")
        self.assertEqual(ps.verify_package(out).state, "ok")

    def test_model_and_revision_must_match_what_the_code_loads(self):
        out = make_package(self.base)
        self.assertIn("owner/model", " ".join(ps.verify_package(out, expect_model_id="other/model").problems))
        self.assertIn("revision", " ".join(ps.verify_package(out, expect_revision="b" * 40).problems))

    def test_an_unreadable_or_unknown_manifest_is_invalid_not_an_exception(self):
        out = make_package(self.base)
        (out / ps.MANIFEST_NAME).write_text("{not json", encoding="utf-8")
        self.assertEqual(ps.verify_package(out).state, "invalid")
        (out / ps.MANIFEST_NAME).write_text(json.dumps({"schema": "other", "artifacts": {}}), encoding="utf-8")
        self.assertEqual(ps.verify_package(out).state, "invalid")

    def test_the_cache_needs_a_verified_manifest_and_the_same_fingerprint(self):
        out = make_package(self.base)
        sources, note = ps.cached_package_sources(out, "f" * 64, "owner/model", REV)
        self.assertEqual((sources, note), ((FILES["loader.py"], FILES["app.py"]), ""))
        self.assertEqual(ps.cached_package_sources(out, "0" * 64, "owner/model", REV), (None, ""))  # something that feeds OpenCode changed
        self.assertEqual(ps.cached_package_sources(out, None, "owner/model", REV), (None, ""))
        (out / "app.py").write_text("changed\n", encoding="utf-8")
        sources, note = ps.cached_package_sources(out, "f" * 64, "owner/model", REV)
        self.assertIsNone(sources)
        self.assertIn("integrity check", note)

    def test_a_legacy_package_is_never_a_cache(self):
        out = make_package(self.base)
        (out / ps.MANIFEST_NAME).unlink()
        self.assertEqual(ps.cached_package_sources(out, "f" * 64, "owner/model", REV), (None, ""))

    def test_package_is_current_checks_files_generator_and_manifest(self):
        out = make_package(self.base)
        required = pipeline.required_artifacts(SLUG)
        self.assertTrue(ps.package_is_current(out, "g" * 64, required))
        self.assertFalse(ps.package_is_current(out, "h" * 64, required))  # another generator made it
        self.assertFalse(ps.package_is_current(out, None, required))
        (out / "requirements.txt").write_text("changed\n", encoding="utf-8")
        self.assertFalse(ps.package_is_current(out, "g" * 64, required))


class TestJoinPackageCode(unittest.TestCase):
    def test_join_is_the_inverse_of_split(self):
        code = gg.generate_app(analyze_preset("sst2"))
        loader_py, app_py = gg.split_app_code(code)
        self.assertEqual(gg.split_app_code(gg.join_package_code(loader_py, app_py)), (loader_py, app_py))

    def test_a_pair_this_generator_did_not_write_is_refused(self):
        loader_py, app_py = gg.split_app_code(gg.generate_app(analyze_preset("sst2")))
        with self.assertRaises(GenerationError):
            gg.join_package_code(loader_py, "import gradio as gr\n")
        with self.assertRaises(GenerationError):
            gg.join_package_code(loader_py, app_py + "\n# edited\n")


class PipelineCase(TmpCase):
    def run_url(self, key="sst2", **patches):
        web_ = FakeWeb()
        hf(web_, key)
        url = "https://huggingface.co/" + PRESETS[key]["repo_id"]
        res = run_pipeline(url, Settings(output_dir=self.base), web_.fetcher(), evidence=False)
        return res, web_

    def counted(self):
        return mock.patch("m2g.pipeline.generate_app", wraps=gg.generate_app)


class TestPipelineManifestAndReuse(PipelineCase):
    def test_the_package_gets_a_hidden_manifest_and_keeps_its_six_artifacts(self):
        res, _ = self.run_url()
        self.assertTrue(res.ok, res.error)
        self.assertEqual(res.files, sorted(["analysis.json", "app.py", "colab_notebook.ipynb", "loader.py", "requirements.txt",
                                            launcher_filename(res.out_dir.name)]))
        self.assertNotIn(ps.MANIFEST_NAME, res.files)
        check = ps.verify_package(res.out_dir)
        self.assertEqual(check.state, "ok")
        self.assertEqual(check.manifest["fingerprint"], res.fingerprint)
        self.assertEqual(check.manifest["model"]["model_id"], res.analysis.model_ref.repo_id)
        self.assertFalse((self.base / ps.STAGING_DIRNAME).exists())

    def test_an_unchanged_package_is_reused_and_opencode_is_not_called(self):
        first, _ = self.run_url()
        before = snapshot(first.out_dir)
        with self.counted() as generate:
            second, _ = self.run_url()
        self.assertTrue(second.ok, second.error)
        self.assertTrue(second.reused_package)
        self.assertEqual(generate.call_count, 0)
        self.assertEqual(second.fingerprint, first.fingerprint)
        after = snapshot(second.out_dir)
        for name in ("app.py", "loader.py"):  # the reused OpenCode code is byte-for-byte the code of the first run
            self.assertEqual(after[name], before[name], name)
        self.assertIn("OpenCode was not called", " ".join(second.notes))
        self.assertIn("reused", second.steps["generation"]["detail"])

    def test_a_first_run_does_call_opencode(self):
        with self.counted() as generate:
            res, _ = self.run_url()
        self.assertTrue(res.ok, res.error)
        self.assertFalse(res.reused_package)
        self.assertEqual(generate.call_count, 1)

    def test_a_changed_package_is_regenerated_not_reused(self):
        first, _ = self.run_url()
        (first.out_dir / "app.py").write_text((first.out_dir / "app.py").read_text(encoding="utf-8") + "\n# edited\n", encoding="utf-8")
        with self.counted() as generate:
            second, _ = self.run_url()
        self.assertTrue(second.ok, second.error)
        self.assertFalse(second.reused_package)
        self.assertEqual(generate.call_count, 1)
        self.assertIn("integrity check", " ".join(second.notes))
        self.assertNotIn("# edited", (second.out_dir / "app.py").read_text(encoding="utf-8"))
        self.assertEqual(ps.verify_package(second.out_dir).state, "ok")

    def test_a_changed_fingerprint_calls_opencode_again(self):
        self.run_url()
        with self.counted() as generate, mock.patch("m2g.pipeline.fingerprints_for", return_value=("e" * 64, "d" * 64)):
            second, _ = self.run_url()
        self.assertTrue(second.ok, second.error)
        self.assertFalse(second.reused_package)
        self.assertEqual(generate.call_count, 1)

    def test_a_legacy_package_without_a_manifest_is_regenerated_and_gets_one(self):
        first, _ = self.run_url()
        (first.out_dir / ps.MANIFEST_NAME).unlink()
        with self.counted() as generate:
            second, _ = self.run_url()
        self.assertTrue(second.ok, second.error)
        self.assertEqual(generate.call_count, 1)
        self.assertEqual(ps.verify_package(second.out_dir).state, "ok")

    def test_code_that_fails_its_checks_is_not_reused(self):
        self.run_url()
        with self.counted() as generate, mock.patch("m2g.pipeline.finalize_generated_code", side_effect=GenerationError("no longer valid")):
            second, _ = self.run_url()
        self.assertTrue(second.ok, second.error)
        self.assertFalse(second.reused_package)
        self.assertEqual(generate.call_count, 1)
        self.assertIn("could not be reused", " ".join(second.notes))


class TestPipelineProtectsTheExistingPackage(PipelineCase):
    def test_a_failed_regeneration_leaves_the_previous_package_untouched(self):
        first, _ = self.run_url()
        before = snapshot(first.out_dir)
        with mock.patch("m2g.pipeline.fingerprints_for", return_value=("e" * 64, "d" * 64)), \
                mock.patch("m2g.pipeline.generate_app", side_effect=GenerationError("OpenCode could not generate the app (exit_nonzero)")):
            second, _ = self.run_url()
        self.assertFalse(second.ok)
        self.assertIn("exit_nonzero", second.error)
        self.assertEqual(second.failed_step, "generation")
        self.assertTrue(second.kept_existing)
        self.assertEqual(snapshot(first.out_dir), before)
        self.assertEqual(ps.verify_package(first.out_dir).state, "ok")

    def test_a_failed_commit_is_reported_as_a_write_failure_and_keeps_the_package(self):
        first, _ = self.run_url()
        before = snapshot(first.out_dir)
        with mock.patch("m2g.pipeline.fingerprints_for", return_value=("e" * 64, "d" * 64)), \
                mock.patch("m2g.pipeline._write", side_effect=ps.PackageCommitError("The package could not be replaced; the previous package was restored unchanged.")):
            second, _ = self.run_url()
        self.assertFalse(second.ok)
        self.assertEqual((second.stage, second.failed_step), ("write", "writing"))
        self.assertIn("PackageCommitError", second.error)
        self.assertEqual(snapshot(first.out_dir), before)

    def test_a_secret_in_a_commit_error_is_redacted(self):
        token = "hf_" + "Z" * 34
        self.run_url()
        with mock.patch("m2g.pipeline.fingerprints_for", return_value=("e" * 64, "d" * 64)), \
                mock.patch("m2g.pipeline._write", side_effect=ps.PackageCommitError("failed with token " + token)):
            second, _ = self.run_url()
        self.assertNotIn(token, second.error)
        self.assertNotIn(token, second.failure_summary())

    def test_a_new_model_still_saves_analysis_json_as_soon_as_it_exists(self):
        with mock.patch("m2g.pipeline.generate_app", side_effect=GenerationError("OpenCode could not generate the app (exit_nonzero)")):
            res, _ = self.run_url()
        self.assertFalse(res.ok)
        self.assertTrue((res.out_dir / "analysis.json").is_file())
        self.assertFalse(snapshot(res.out_dir).get(ps.MANIFEST_NAME))


class TestPipelineLocking(PipelineCase):
    def slug(self, key="sst2"):
        return pipeline._slug_for_ref(parse_source_url("https://huggingface.co/" + PRESETS[key]["repo_id"]))

    def test_a_run_for_a_model_that_is_being_written_waits_then_reports_busy_without_touching_the_network(self):
        web_ = FakeWeb()
        hf(web_, "sst2")
        url = "https://huggingface.co/" + PRESETS["sst2"]["repo_id"]
        with lock_held_elsewhere(self.base, self.slug()), mock.patch.object(ps, "LOCK_TIMEOUT_S", 0.2):
            res = run_pipeline(url, Settings(output_dir=self.base), web_.fetcher(), evidence=False)
        self.assertFalse(res.ok)
        self.assertIn("Another analysis or generation", res.error)
        self.assertEqual(res.failed_step, "writing")
        self.assertEqual(web_.calls, [], "nothing is fetched for a request that has to wait")

    def test_the_lock_is_released_after_success_and_after_failure(self):
        res, _ = self.run_url()
        self.assertTrue(res.ok, res.error)
        self.assertTrue(lockable_from_other_thread(self.base, self.slug()))
        failed = run_pipeline("https://huggingface.co/" + PRESETS["sst2"]["repo_id"], Settings(output_dir=self.base), FakeWeb().fetcher(),
                              evidence=False)  # nothing registered: the inspection fails
        self.assertIsNotNone(failed.error)
        self.assertTrue(lockable_from_other_thread(self.base, self.slug()))

    def test_a_rejected_url_takes_no_lock_and_creates_nothing(self):
        res = run_pipeline("http://127.0.0.1/a/b", Settings(output_dir=self.base), FakeWeb().fetcher())
        self.assertEqual(res.stage, "parse")
        self.assertFalse(self.base.exists() and any(self.base.iterdir()))

    def test_write_false_runs_do_not_wait_for_the_lock(self):
        web_ = FakeWeb()
        hf(web_, "sst2")
        url = "https://huggingface.co/" + PRESETS["sst2"]["repo_id"]
        with lock_held_elsewhere(self.base, self.slug()):
            res = run_pipeline(url, Settings(output_dir=self.base), web_.fetcher(), write=False, evidence=False)
        self.assertTrue(res.ok, res.error)


class TestLaunchGates(PipelineCase):
    def test_a_pipeline_package_passes_the_local_gate(self):
        res, _ = self.run_url()
        code, requirements = local_runner.verify_generated_app(res.out_dir, Settings(output_dir=self.base))
        self.assertIn("get_model", code)
        self.assertTrue(requirements.strip())

    def test_an_edited_package_is_refused_with_422(self):
        res, _ = self.run_url()
        (res.out_dir / "app.py").write_text((res.out_dir / "app.py").read_text(encoding="utf-8") + "\n# edited by hand\n", encoding="utf-8")
        with self.assertRaises(LocalRunError) as ctx:
            local_runner.verify_generated_app(res.out_dir, Settings(output_dir=self.base))
        self.assertEqual(ctx.exception.status, 422)
        self.assertIn("app.py changed after it was generated", ctx.exception.message)

    def test_a_package_without_a_manifest_still_launches(self):
        res, _ = self.run_url()
        (res.out_dir / ps.MANIFEST_NAME).unlink()
        local_runner.verify_generated_app(res.out_dir, Settings(output_dir=self.base))

    def test_a_launch_while_the_model_is_being_written_answers_409(self):
        res, _ = self.run_url()
        with lock_held_elsewhere(self.base, res.out_dir.name), mock.patch.object(local_runner, "LAUNCH_LOCK_WAIT_S", 0.1):
            with self.assertRaises(LocalRunError) as ctx:
                local_runner.start_local_app(object(), self.base, res.out_dir.name, Settings(output_dir=self.base))
        self.assertEqual(ctx.exception.status, 409)


class TestGenerationJobReuse(PipelineCase):
    def run_job(self, slug, settings, force=False):
        job = web_api._new_job("generation")
        web_api._run_generation_job(job, slug, settings, force)
        return job

    def test_an_unchanged_valid_package_finishes_the_job_without_running_the_pipeline(self):
        res, _ = self.run_url()
        settings = Settings(output_dir=self.base)
        with mock.patch("m2g.pipeline.run_pipeline", side_effect=AssertionError("the pipeline must not run")):
            job = self.run_job(res.out_dir.name, settings)
        self.assertEqual(job.status, "done", job.error)
        self.assertTrue(job.result["reused"])
        self.assertEqual(sorted(job.files), sorted(pipeline.required_artifacts(res.out_dir.name)))

    def test_force_runs_the_pipeline_anyway(self):
        res, _ = self.run_url()
        settings = Settings(output_dir=self.base)
        with mock.patch("m2g.pipeline.run_pipeline", side_effect=RuntimeError("pipeline was called")):
            job = self.run_job(res.out_dir.name, settings, force=True)
        self.assertEqual(job.status, "failed")
        self.assertIn("pipeline was called", job.error)

    def test_a_changed_package_runs_the_pipeline(self):
        res, _ = self.run_url()
        (res.out_dir / "requirements.txt").write_text("gradio\n# edited\n", encoding="utf-8")
        with mock.patch("m2g.pipeline.run_pipeline", side_effect=RuntimeError("pipeline was called")):
            job = self.run_job(res.out_dir.name, Settings(output_dir=self.base))
        self.assertEqual(job.status, "failed")
        self.assertIn("pipeline was called", job.error)

    def test_a_legacy_package_runs_the_pipeline(self):
        res, _ = self.run_url()
        (res.out_dir / ps.MANIFEST_NAME).unlink()
        with mock.patch("m2g.pipeline.run_pipeline", side_effect=RuntimeError("pipeline was called")):
            job = self.run_job(res.out_dir.name, Settings(output_dir=self.base))
        self.assertIn("pipeline was called", job.error)

    def test_unexpected_job_errors_are_redacted(self):
        res, _ = self.run_url()
        token = "hf_" + "Q" * 34
        (res.out_dir / ps.MANIFEST_NAME).unlink()
        with mock.patch("m2g.pipeline.run_pipeline", side_effect=RuntimeError("failed with " + token)):
            job = self.run_job(res.out_dir.name, Settings(output_dir=self.base))
        self.assertNotIn(token, job.error)


if __name__ == "__main__":
    unittest.main()
