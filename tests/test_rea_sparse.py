"""Focused tests for the header-only REA inspection that uses SPARSE scratch files (mock REA server, no network, no real REA).

Why sparse files: the real REA 6.8.0 validates the tensor offsets declared in a safetensors header against the file's LOGICAL size, so
a file that holds only the header is rejected ("range [..) is outside the data buffer", see diagnostics/rea_live_verification.json).
The platform therefore creates a scratch file whose logical size equals the remote file's real size (from a validated Content-Range),
writes only the genuine header into it, and leaves the tensor region as a hole that uses no disk space.

What is covered here: Content-Range validation, the two range requests, header validation, sparse-file creation and measurement,
the end-to-end stage (success, malformed files, HTTP errors, timeouts, REA failures, unexpected errors) and cleanup in every outcome.
What is NOT covered here (a mock cannot prove it): that the REAL REA accepts these files and does not read the hole.
That is scripts/verify_rea_live.py (checks A and C).
"""
import json
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from fakehub import PUBLIC, SHA
from test_rea_integration import CMD, HEADER, SIZE, Base, RangeWeb, st_bytes

from m2g import rea_client as rc
from m2g.config import Settings
from m2g.security import HTTPStatusError, NetworkError, SafeFetcher, SecurityError


def blob_for(header):
    """8 length bytes + JSON header (no tensor data)."""
    js = json.dumps(header).encode()
    return len(js).to_bytes(8, "little") + js


def header_for_total(total):
    """Header (8 length bytes + JSON) of ONE uint8 tensor that fills a file of exactly ``total`` bytes."""
    for n in range(32, 512):
        k = total - 8 - n
        js = json.dumps({"w": {"dtype": "U8", "shape": [k], "data_offsets": [0, k]}}, separators=(",", ":")).encode()
        if len(js) == n:
            return n.to_bytes(8, "little") + js
    raise AssertionError("no header length is consistent with this total size")


def fetcher_for(web_):
    return SafeFetcher(Settings(), resolver=PUBLIC, transport=web_)


# --------------------------------------------------------------------------- Content-Range


class TestContentRange(unittest.TestCase):
    def test_valid_and_case_insensitive(self):
        self.assertEqual(rc._content_range_total({"Content-Range": "bytes 0-7/100"}, 0, 7), 100)
        self.assertEqual(rc._content_range_total({"content-range": " bytes 8-15/16 "}, 8, 15), 16)

    def test_rejected(self):
        cases = {
            "missing": ({}, 0, 7),
            "unknown total": ({"content-range": "bytes 0-7/*"}, 0, 7),
            "garbage": ({"content-range": "items 0-7/100"}, 0, 7),
            "other range": ({"content-range": "bytes 0-8/100"}, 0, 7),
            "total too small": ({"content-range": "bytes 0-7/7"}, 0, 7),
            "implausible total": ({"content-range": f"bytes 0-7/{(1 << 40) + 1}"}, 0, 7),
            "ends beyond the total": ({"content-range": "bytes 8-15/12"}, 8, 15),
            "negative total": ({"content-range": "bytes 0-7/-5"}, 0, 7),
        }
        for label, (headers, first, last) in cases.items():
            with self.subTest(label), self.assertRaises(rc.ReaError):
                rc._content_range_total(headers, first, last)


# --------------------------------------------------------------------------- the two range requests


class TestFetchHeader(unittest.TestCase):
    def fetch(self, web_, **kw):
        stats = {}
        result = rc.fetch_safetensors_header(fetcher_for(web_), "acme/tiny", SHA, "model.safetensors", stats=stats, **kw)
        return result, stats

    def test_success_uses_exactly_two_range_requests_and_the_real_size(self):
        web_ = RangeWeb()
        result, stats = self.fetch(web_, expected_size=SIZE)
        n = int.from_bytes(web_.blob[:8], "little")
        self.assertEqual(result.blob, web_.blob[:8 + n])
        self.assertEqual(result.total_size, SIZE)
        self.assertEqual(stats, {"requests": 2, "bytes": 8 + n})
        self.assertEqual(web_.ranges, [(0, 7), (8, 8 + n - 1)])

    def test_the_real_size_comes_from_content_range_not_from_the_body(self):
        total = 3 << 30
        web_ = RangeWeb(blob=header_for_total(total), total=total)  # the server can only send the header; the file is 3 GiB
        result, stats = self.fetch(web_)
        self.assertEqual(result.total_size, total)
        self.assertEqual(stats["bytes"], len(result.blob))
        self.assertLess(len(result.blob), 1024)

    def test_http_errors_propagate_as_fetch_errors(self):
        for status in (401, 403, 404, 416, 429, 500, 503):
            with self.subTest(status=status), self.assertRaises(HTTPStatusError) as ctx:
                self.fetch(RangeWeb(status=status))
            self.assertEqual(ctx.exception.status, status)

    def test_timeout_is_a_network_error(self):
        with self.assertRaises(NetworkError):
            self.fetch(RangeWeb(error=NetworkError("timed out")))

    def test_a_server_that_ignores_range_is_refused(self):
        with self.assertRaisesRegex(rc.ReaError, "did not honour"):
            self.fetch(RangeWeb(status=200))

    def test_content_range_problems_are_refused(self):
        for label, mode in (("missing", "missing"), ("wrong range", "wrong_range"), ("unknown total", "star")):
            with self.subTest(label), self.assertRaises(rc.ReaError):
                self.fetch(RangeWeb(content_range=mode))

    def test_short_body_is_refused(self):
        with self.assertRaisesRegex(rc.ReaError, "returned"):
            self.fetch(RangeWeb(short=True))

    def test_hub_listed_size_must_agree_with_content_range(self):
        with self.assertRaisesRegex(rc.ReaError, "Hub listed"):
            self.fetch(RangeWeb(), expected_size=SIZE + 1)

    def test_size_must_not_change_between_the_two_requests(self):
        web_ = RangeWeb(content_range=lambda a, b: f"bytes {a}-{b}/{SIZE if a == 0 else SIZE + 8}")
        with self.assertRaisesRegex(rc.ReaError, "changed"):
            self.fetch(web_)

    def test_implausible_header_lengths(self):
        cases = {"huge": (1 << 40).to_bytes(8, "little") + bytes(64), "zero": bytes(72),
                 "longer than the file": (1000).to_bytes(8, "little") + b"{}"}
        for label, blob in cases.items():
            with self.subTest(label), self.assertRaises(rc.ReaError):
                self.fetch(RangeWeb(blob=blob))

    def test_unsafe_names_never_reach_the_network(self):
        web_ = RangeWeb()
        fetcher = fetcher_for(web_)
        for name in ("../x.safetensors", "/abs.safetensors", "a\\b.safetensors", "a?b.safetensors", "a%2e.safetensors"):
            with self.subTest(name), self.assertRaises(SecurityError):
                rc.fetch_safetensors_header(fetcher, "acme/tiny", SHA, name)
        with self.assertRaises(SecurityError):
            rc.fetch_safetensors_header(fetcher, "acme/tiny", "bad rev", "m.safetensors")
        self.assertEqual(web_.hits, 0)


# --------------------------------------------------------------------------- header validation


class TestSummarizeHeader(unittest.TestCase):
    def test_valid(self):
        blob = blob_for(HEADER)
        summary = rc.summarize_header(blob, len(blob) + 32)
        self.assertEqual(summary["tensor_count"], 2)
        self.assertEqual(summary["parameters"], 10)
        self.assertEqual(summary["dtypes"], {"F16": 4, "F32": 6})
        self.assertEqual(summary["data_bytes"], 32)

    def test_declared_data_must_exactly_fill_the_file(self):
        blob = blob_for(HEADER)
        for label, extra in (("header-only file (the original bug)", 0), ("one byte short", 31), ("one byte too long", 33)):
            with self.subTest(label), self.assertRaises(rc.ReaError):
                rc.summarize_header(blob, len(blob) + extra)

    def test_malformed_tensor_tables(self):
        f32 = {"dtype": "F32", "shape": [2, 3], "data_offsets": [0, 24]}
        cases = {
            "gap": {"w": f32, "b": {"dtype": "F16", "shape": [4], "data_offsets": [25, 33]}},
            "overlap": {"w": f32, "b": {"dtype": "F16", "shape": [4], "data_offsets": [20, 28]}},
            "span does not match shape x dtype": {"w": {"dtype": "F32", "shape": [2, 3], "data_offsets": [0, 25]}},
            "negative offset": {"w": {"dtype": "F32", "shape": [2, 3], "data_offsets": [-1, 23]}},
            "reversed offsets": {"w": {"dtype": "F32", "shape": [2, 3], "data_offsets": [24, 0]}},
            "float offsets": {"w": {"dtype": "F32", "shape": [2, 3], "data_offsets": [0.0, 24.0]}},
            "boolean dimension": {"w": {"dtype": "F32", "shape": [True, 3], "data_offsets": [0, 12]}},
            "negative dimension": {"w": {"dtype": "F32", "shape": [-1], "data_offsets": [0, 0]}},
            "missing dtype": {"w": {"shape": [1], "data_offsets": [0, 4]}},
            "entry is not an object": {"w": 5},
            "implausible tensor size": {"w": {"dtype": "U8", "shape": [10 ** 7, 10 ** 7], "data_offsets": [0, 10 ** 14]}},
            "too many dimensions": {"w": {"dtype": "U8", "shape": [1] * 17, "data_offsets": [0, 1]}},
        }
        for label, header in cases.items():
            with self.subTest(label), self.assertRaises(rc.ReaError):
                rc.summarize_header(blob_for(header))

    def test_not_json_not_an_object_and_wrong_lengths(self):
        raw_cases = {"not json": b"{nope", "invalid utf-8": b"{\xff}", "list": b"[1,2]", "string": b'"x"', "leading space": b' {"a":1}'}
        for label, raw in raw_cases.items():
            with self.subTest(label), self.assertRaises(rc.ReaError):
                rc.summarize_header(len(raw).to_bytes(8, "little") + raw)
        good = blob_for(HEADER)
        for label, blob in (("truncated", good[:-1]), ("extra byte", good + b"x"), ("too short", b"123"), ("empty", b"")):
            with self.subTest(label), self.assertRaises(rc.ReaError):
                rc.summarize_header(blob)


# --------------------------------------------------------------------------- sparse files


class SparseBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def make(self, name, header, size):
        path = self.tmp / name
        try:
            return path, rc.create_sparse_file(path, header, size)
        except rc.SparseFileError as exc:
            if os.name == "nt":
                self.fail(f"NTFS sparse files must work on Windows: {exc}")
            self.skipTest(f"this filesystem has no sparse files: {exc}")


class TestSparseFiles(SparseBase):
    def test_logical_size_header_at_the_start_and_a_hole_after_it(self):
        header, size = bytes(range(1, 101)), 1 << 30
        path, allocated = self.make("big.bin", header, size)
        self.assertEqual(path.stat().st_size, size)
        self.assertLessEqual(allocated, len(header) + rc.SPARSE_SLACK_BYTES)
        with open(path, "rb") as fh:
            self.assertEqual(fh.read(100), header)
            fh.seek(size // 2)
            self.assertEqual(fh.read(16), bytes(16))
            fh.seek(size - 16)
            self.assertEqual(fh.read(32), bytes(16))  # 16 bytes remain: the logical end of file

    def test_invalid_requests_create_nothing(self):
        path = self.tmp / "x.bin"
        for header, size in ((b"", 10), (b"abc", 2), (b"abc", rc.MAX_REMOTE_FILE_BYTES + 1)):
            with self.subTest(size=size), self.assertRaises(rc.SparseFileError):
                rc.create_sparse_file(path, header, size)
        self.assertFalse(path.exists())

    def test_an_existing_file_is_never_overwritten_or_deleted(self):
        path = self.tmp / "keep.bin"
        path.write_bytes(b"precious")
        with self.assertRaises(rc.SparseFileError):
            rc.create_sparse_file(path, b"abc", 1000)
        self.assertEqual(path.read_bytes(), b"precious")

    def test_a_file_that_is_not_sparse_is_detected_and_removed(self):
        path = self.tmp / "dense.bin"
        with mock.patch.object(rc, "allocated_bytes", return_value=10 ** 9):
            with self.assertRaisesRegex(rc.SparseFileError, "not sparse"):
                rc.create_sparse_file(path, b"abc", 4096)
        self.assertFalse(path.exists())

    def test_an_unmeasurable_allocation_is_refused_and_the_file_removed(self):
        path = self.tmp / "unknown.bin"
        with mock.patch.object(rc, "allocated_bytes", return_value=None):
            with self.assertRaisesRegex(rc.SparseFileError, "measured"):
                rc.create_sparse_file(path, b"abc", 4096)
        self.assertFalse(path.exists())

    def test_the_support_probe_runs_once_per_folder_and_cleans_up(self):
        base = self.tmp / "probe"
        base.mkdir()
        probes, real = [], rc.create_sparse_file

        def counting(path, header, size):
            probes.append(path)
            return real(path, header, size)

        with mock.patch.object(rc, "create_sparse_file", counting):
            first, second = rc.sparse_supported(base), rc.sparse_supported(base)
        self.assertEqual(first, second)
        self.assertEqual(len(probes), 1)
        self.assertEqual(list(base.iterdir()), [])
        if os.name == "nt":
            self.assertEqual(first, (True, ""))

    def test_an_unsupported_filesystem_is_reported_with_its_reason_and_leaves_no_probe(self):
        base = self.tmp / "probe"
        base.mkdir()
        with mock.patch.object(rc, "allocated_bytes", return_value=10 ** 9):
            ok, why = rc.sparse_supported(base)
        self.assertFalse(ok)
        self.assertIn("not sparse", why)
        self.assertEqual(list(base.iterdir()), [])


# --------------------------------------------------------------------------- the whole stage


class TestEndToEnd(Base):
    def leftovers(self):
        base = self.tmp / ".m2g" / "rea"
        return sorted(p.name for p in base.iterdir()) if base.exists() else []

    def assert_failed_before_rea(self, report, status=rc.FAILED):
        self.assertEqual(report.status, status, report.log)
        self.assertEqual(report.calls, [])
        self.assertFalse(self.log.exists(), "REA must not even be started")
        self.assertEqual(self.leftovers(), [])

    # ---- success
    def test_the_scratch_file_has_the_remote_size_and_rea_reads_only_the_header(self):
        report, _ = self.run_rea()
        self.assertEqual(report.status, rc.COMPLETED, report.log)
        fact = self.server_file_facts()[0]
        n = fact["header_bytes"]
        self.assertEqual(fact["file_size"], SIZE)
        self.assertGreater(SIZE, 8 + n)
        self.assertEqual(fact["bytes_read"], 8 + n)
        self.assertEqual(fact["hash"], "header")
        self.assertEqual(report.header_summaries[0]["logical_size"], SIZE)
        self.assertEqual((report.http_requests, report.bytes_downloaded), (2, 8 + n))
        self.assertEqual(self.leftovers(), [])

    def test_a_one_gib_file_costs_a_header_not_a_gigabyte(self):
        total = 1 << 30
        blob = header_for_total(total)
        web_ = RangeWeb(blob=blob, total=total)
        report, _ = self.run_rea(files=[("config.json", 5), ("model.safetensors", total)], web_=web_)
        if report.status == rc.NOT_APPLICABLE and os.name != "nt":
            self.skipTest(report.reason)
        self.assertEqual(report.status, rc.COMPLETED, report.log)
        fact = self.server_file_facts()[0]
        self.assertEqual((fact["file_size"], fact["bytes_read"]), (total, len(blob)))
        self.assertLessEqual(report.header_summaries[0]["allocated_bytes"], len(blob) + rc.SPARSE_SLACK_BYTES)
        if fact["allocated_bytes"] is not None:  # measured by the mock server (POSIX only)
            self.assertLessEqual(fact["allocated_bytes"], len(blob) + rc.SPARSE_SLACK_BYTES)
        self.assertLess(max(b for _, b in web_.ranges), len(blob))  # nothing beyond the header was requested
        self.assertEqual(report.bytes_downloaded, len(blob))
        self.assertEqual(self.leftovers(), [])

    def test_header_only_files_are_rejected_but_sparse_files_with_the_real_size_are_accepted(self):
        blob = st_bytes()[:8 + int.from_bytes(st_bytes()[:8], "little")]
        header_only, sparse = self.tmp / "header_only.safetensors", self.tmp / "sparse.safetensors"
        header_only.write_bytes(blob)
        try:
            rc.create_sparse_file(sparse, blob, SIZE)
        except rc.SparseFileError as exc:
            if os.name == "nt":
                self.fail(f"NTFS sparse files must work on Windows: {exc}")
            self.skipTest(str(exc))
        with rc.ReaMcpClient(CMD, cwd=self.tmp) as client:
            client.list_tools()
            rejected = client.call_tool(rc.MODEL_TOOL, {"path": str(header_only), "hash": "header", "detail": "summary"})
            accepted = client.call_tool(rc.MODEL_TOOL, {"path": str(sparse), "hash": "header", "detail": "summary"})
        self.assertFalse(rejected.ok)
        self.assertIn("outside the data buffer", rejected.error)
        self.assertTrue(accepted.ok, accepted.error)

    # ---- nothing sparse available
    def test_no_sparse_support_downloads_nothing_and_starts_nothing(self):
        web_ = RangeWeb()
        with mock.patch.object(rc, "sparse_supported", return_value=(False, "no sparse here")):
            report, _ = self.run_rea(web_=web_)
        self.assert_failed_before_rea(report, rc.NOT_APPLICABLE)
        self.assertIn("no sparse here", report.reason)
        self.assertEqual((web_.hits, web_.ranges), (0, []))

    def test_a_filesystem_that_is_not_really_sparse_is_detected_before_any_download(self):
        web_ = RangeWeb()
        with mock.patch.object(rc, "allocated_bytes", return_value=10 ** 9):
            report, _ = self.run_rea(web_=web_)
        self.assert_failed_before_rea(report, rc.NOT_APPLICABLE)
        self.assertEqual(web_.hits, 0)

    def test_failure_to_create_a_shard_file_is_not_applicable_and_cleans_up(self):
        real = rc.create_sparse_file

        def flaky(path, header, size):
            if Path(path).name.startswith("shard_"):
                raise rc.SparseFileError("disk full")
            return real(path, header, size)

        with mock.patch.object(rc, "create_sparse_file", flaky):
            report, _ = self.run_rea()
        self.assert_failed_before_rea(report, rc.NOT_APPLICABLE)

    # ---- malformed remote files
    def test_malformed_remote_files_never_reach_rea(self):
        f32 = {"dtype": "F32", "shape": [2, 3], "data_offsets": [0, 24]}
        bad_json = b"{nope"
        cases = {
            "invalid json": len(bad_json).to_bytes(8, "little") + bad_json,
            "not an object": len(b"[1,2]").to_bytes(8, "little") + b"[1,2]",
            "gap in the offsets": blob_for({"w": f32, "b": {"dtype": "F16", "shape": [4], "data_offsets": [25, 33]}}) + bytes(33),
            "data shorter than declared": blob_for(HEADER) + bytes(31),
            "header only (no data at all)": blob_for(HEADER),
            "implausible header length": (1 << 40).to_bytes(8, "little") + bytes(100),
        }
        for label, blob in cases.items():
            with self.subTest(label):
                report, _ = self.run_rea(files=[("model.safetensors", len(blob))], web_=RangeWeb(blob=blob))
                self.assert_failed_before_rea(report)

    def test_inconsistent_sizes_never_reach_rea(self):
        cases = {
            "hub lists a different size": dict(files=[("model.safetensors", SIZE + 5)], web_=RangeWeb()),
            "content-range total lies (hub size unknown)": dict(files=[("model.safetensors", None)], web_=RangeWeb(total=SIZE + 1000)),
            "content-range missing": dict(files=[("model.safetensors", SIZE)], web_=RangeWeb(content_range="missing")),
            "server ignores range": dict(files=[("model.safetensors", SIZE)], web_=RangeWeb(status=200)),
            "short body": dict(files=[("model.safetensors", SIZE)], web_=RangeWeb(short=True)),
        }
        for label, kw in cases.items():
            with self.subTest(label):
                report, _ = self.run_rea(**kw)
                self.assert_failed_before_rea(report)

    # ---- HTTP errors and timeouts
    def test_http_errors_never_reach_rea(self):
        for status in (401, 403, 404, 500):
            with self.subTest(status=status):
                report, _ = self.run_rea(web_=RangeWeb(status=status))
                self.assert_failed_before_rea(report)
                self.assertEqual(report.http_requests, 0)

    def test_a_timeout_fails_cleanly_and_is_not_retried_for_every_shard(self):
        files = [(f"model-0000{i}-of-00003.safetensors", SIZE) for i in range(1, 4)]
        web_ = RangeWeb(error=NetworkError("timed out"))
        report, _ = self.run_rea(files=files, web_=web_)
        self.assert_failed_before_rea(report)
        self.assertEqual(web_.hits, 1, "the dead-host circuit breaker must stop the remaining shards")

    # ---- cleanup after REA-side problems
    def test_rea_errors_leave_no_scratch_files(self):
        for mode in ("tool_error", "crash"):
            with self.subTest(mode):
                self.set_mode(mode)
                report, _ = self.run_rea()
                self.assertEqual(report.status, rc.FAILED)
                self.assertEqual(self.leftovers(), [])

    def test_a_hanging_rea_times_out_and_leaves_no_scratch_files(self):
        self.set_mode("hang")
        started = time.monotonic()
        report, _ = self.run_rea(factory=lambda cmd, cwd: rc.ReaMcpClient(cmd, cwd=cwd, call_timeout=1.5))
        self.assertEqual(report.status, rc.FAILED)
        self.assertIn("did not answer", report.reason)
        self.assertEqual(self.leftovers(), [])
        self.assertLess(time.monotonic() - started, 20)

    def test_an_unexpected_exception_is_reported_and_leaves_no_scratch_files(self):
        def boom(cmd, cwd):
            raise RuntimeError("boom")

        report, _ = self.run_rea(factory=boom)
        self.assertEqual(report.status, rc.FAILED)
        self.assertIn("boom", report.reason)
        self.assertEqual(self.leftovers(), [])

    # ---- crashed earlier runs
    def test_stale_scratch_folders_are_swept_and_fresh_ones_are_kept(self):
        base = self.tmp / ".m2g" / "rea"
        base.mkdir(parents=True)
        stale, fresh = base / "hdr_stale", base / "hdr_fresh"
        for folder in (stale, fresh):
            folder.mkdir()
            (folder / "shard_0.safetensors").write_bytes(b"x")
        old = time.time() - 2 * rc.STALE_SCRATCH_S
        os.utime(stale, (old, old))
        report, _ = self.run_rea()
        self.assertEqual(report.status, rc.COMPLETED, report.log)
        self.assertFalse(stale.exists())
        self.assertTrue(fresh.exists())


if __name__ == "__main__":
    unittest.main()
