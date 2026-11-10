"""Offline tests for the Colab CLI adapter (m2g.colab_cli). The real `colab` command is never started: a fake runner
records the argument lists and returns scripted output."""
import subprocess
import sys
import unittest

from m2g import colab_cli as cc


class FakeRunner:
    def __init__(self, responses=None, default=(0, "", "")):
        self.calls = []
        self.responses = list(responses or [])
        self.default = default

    def __call__(self, cmd, *, input="", timeout=120, env=None, on_start=None):
        self.calls.append({"cmd": list(cmd), "input": input, "timeout": timeout, "env": env})
        rc, out, err = self.responses.pop(0) if self.responses else self.default
        return subprocess.CompletedProcess(cmd, rc, out, err)


def make(runner, **kw):
    return cc.ColabCli(["colab"], runner=runner, environ={}, **kw)


class TestArguments(unittest.TestCase):
    def test_auth_flag_goes_before_the_subcommand(self):
        r = FakeRunner()
        make(r).sessions()
        self.assertEqual(r.calls[0]["cmd"], ["colab", "--auth", "oauth2", "sessions"])

    def test_auth_comes_from_environment_and_is_validated(self):
        self.assertEqual(cc.ColabCli(["colab"], runner=FakeRunner(), environ={"COLAB_AUTH": "adc"}).auth, "adc")
        with self.assertRaises(ValueError):
            cc.ColabCli(["colab"], runner=FakeRunner(), environ={"COLAB_AUTH": "password"})

    def test_new_session_requests_a_gpu_the_cli_really_offers(self):
        r = FakeRunner([(0, "  --gpu {T4,L4}  ", ""), (0, "ok", "")])
        make(r).new_session("abc", "t4")
        self.assertEqual(r.calls[1]["cmd"], ["colab", "--auth", "oauth2", "new", "-s", "abc", "--gpu", "T4"])

    def test_unsupported_gpu_is_refused_before_any_runtime_is_requested(self):
        r = FakeRunner([(0, "--gpu {T4}", "")])
        with self.assertRaises(ValueError):
            make(r).new_session("abc", "H100")
        self.assertEqual(len(r.calls), 1)  # only the --help lookup, never `new`

    def test_exec_sends_code_on_stdin_with_an_explicit_timeout(self):
        r = FakeRunner()
        make(r).exec_code("abc", "print(1)", timeout=50)
        call = r.calls[0]
        self.assertEqual(call["cmd"][-5:], ["exec", "-s", "abc", "--timeout", "50"])
        self.assertEqual(call["input"], "print(1)")
        self.assertEqual(call["timeout"], 140)

    def test_non_ascii_code_and_bad_session_names_are_refused(self):
        cli = make(FakeRunner())
        with self.assertRaises(ValueError):
            cli.exec_code("abc", "print('\u00e9')")
        for bad in ("bad name", "x; rm -rf /", "", "-leading", "a" * 80):
            with self.assertRaises(ValueError, msg=bad):
                cli.status(bad)
            with self.assertRaises(ValueError, msg=bad):
                cli.stop(bad)


class TestGpuParsing(unittest.TestCase):
    def test_forms(self):
        self.assertEqual(cc.parse_gpu_choices("--gpu GPU {T4,L4,G4}"), ("T4", "L4", "G4"))
        self.assertEqual(cc.parse_gpu_choices("--gpu T4|L4|G4"), ("T4", "L4", "G4"))
        self.assertEqual(cc.parse_gpu_choices("--gpu  Pick one of T4 or A100"), ("T4", "A100"))
        self.assertEqual(cc.parse_gpu_choices(""), ())

    def test_fallback_is_flagged_as_unverified(self):
        cli = make(FakeRunner([(1, "", "boom")]))
        choices = cli.supported_gpus()
        self.assertFalse(choices.verified)
        self.assertEqual(choices.names, cc.DOCUMENTED_GPUS)


class TestSafety(unittest.TestCase):
    def test_child_environment_drops_secrets_and_forces_utf8(self):
        env = cc.child_environment({"HF_TOKEN": "a", "MY_SERVICE_SECRET": "b", "OPENAI_API_KEY": "c", "PATH": "p"})
        self.assertEqual(env["PATH"], "p")
        for name in ("HF_TOKEN", "MY_SERVICE_SECRET", "OPENAI_API_KEY"):
            self.assertNotIn(name, env)
        self.assertEqual(env["PYTHONUTF8"], "1")

    def test_script_launchers_are_refused(self):
        cli = cc.ColabCli(["C:\\tools\\colab.bat"], runner=FakeRunner(), environ={})
        self.assertFalse(cli.available)
        self.assertTrue(cli.problem)
        self.assertEqual(cli.run(["sessions"]).error, "unavailable")
        self.assertFalse(cli.identify()[0])

    def test_resolve_command(self):
        none = dict(which=lambda n: None, os_name="nt", home="C:\\nope", python_dir="C:\\nope2", exists=lambda p: False)
        self.assertIsNone(cc.resolve_colab_command({}, **none))
        self.assertEqual(cc.resolve_colab_command({"COLAB_COMMAND": '"C:\\Tools\\colab.exe" --x'}, **none),
                         ["C:\\Tools\\colab.exe", "--x"])
        self.assertEqual(cc.resolve_colab_command({}, which=lambda n: "/usr/bin/colab", os_name="posix"), ["/usr/bin/colab"])
        found = cc.resolve_colab_command({}, which=lambda n: None, os_name="nt", home="C:\\h", python_dir="C:\\p",
                                         exists=lambda p: p.name == "colab.exe")
        self.assertTrue(found[0].endswith("colab.exe"))


class TestRunBehaviour(unittest.TestCase):
    def test_timeout_is_reported_not_raised(self):
        def runner(cmd, **kw):
            raise subprocess.TimeoutExpired(cmd, 1)
        res = make(runner).run(["sessions"])
        self.assertEqual(res.error, "timeout")
        self.assertFalse(res.ok)

    def test_interrupt_blocks_new_calls_until_cleared(self):
        r = FakeRunner()
        cli = make(r)
        cli.interrupt()
        self.assertEqual(cli.run(["sessions"]).error, "cancelled")
        self.assertEqual(r.calls, [])
        cli.clear_interrupt()
        self.assertTrue(cli.run(["sessions"]).ok)

    def test_identify_recognises_the_official_cli_only(self):
        good = make(FakeRunner([(0, "Commands: new exec sessions stop status", "")]))
        self.assertTrue(good.identify()[0])
        other = make(FakeRunner([(0, "Usage: colab [OPTIONS] PATH", "")]))
        ok, msg = other.identify()
        self.assertFalse(ok)
        self.assertIn("official", msg)


class TestCheckStates(unittest.TestCase):
    def state(self, rc, out="", err=""):
        return make(FakeRunner(default=(rc, out, err))).check()[0]

    def test_ok_needs_auth_failed_and_unavailable(self):
        self.assertEqual(self.state(0), "ok")
        self.assertEqual(self.state(1, err="EOFError: EOF when reading a line"), "needs_auth")
        self.assertEqual(self.state(1, err="something unexpected"), "failed")
        cli = make(FakeRunner())
        cli.command = None
        self.assertEqual(cli.check()[0], "unavailable")

    def test_a_crashing_cli_is_not_mistaken_for_a_sign_in_problem(self):
        # Tracebacks mention oauth/token/credential paths; an unsupported platform must still read as 'failed'.
        err = "ModuleNotFoundError: No module named 'fcntl'\n  File colab/oauth/token.py"
        self.assertEqual(self.state(1, err=err), "failed")


class TestLiveOutput(unittest.TestCase):
    """The streaming path (_pump/_run_streaming). Uses the current Python interpreter on tiny inline scripts, never `colab`."""

    def test_lines_are_reported_live_and_the_result_is_complete(self):
        seen = []
        script = "import sys; print('one', flush=True); print('two', file=sys.stderr, flush=True); print('three')"
        res = cc.run_process([sys.executable, "-c", script], timeout=60, on_line=lambda s, ln: seen.append((s, ln)))
        self.assertEqual(res.returncode, 0)
        self.assertIn(("stdout", "one"), seen)
        self.assertIn(("stdout", "three"), seen)
        self.assertIn(("stderr", "two"), seen)
        self.assertEqual(res.stdout.split(), ["one", "three"])
        self.assertEqual(res.stderr.strip(), "two")

    def test_stdin_is_delivered_while_streaming(self):
        seen = []
        res = cc.run_process([sys.executable, "-c", "import sys; print(sys.stdin.read().upper(), end='')"], input="abc\n" * 3,
                             timeout=60, on_line=lambda s, ln: seen.append(ln))
        self.assertEqual(res.returncode, 0)
        self.assertEqual(seen, ["ABC", "ABC", "ABC"])

    def test_a_failing_callback_never_breaks_the_call(self):
        def boom(stream, line):
            raise RuntimeError("callback bug")
        res = cc.run_process([sys.executable, "-c", "print('x')"], timeout=60, on_line=boom)
        self.assertEqual(res.returncode, 0)
        self.assertEqual(res.stdout.strip(), "x")

    def test_timeout_still_kills_the_process_and_keeps_the_partial_output(self):
        script = "import time; print('started', flush=True); time.sleep(60)"
        with self.assertRaises(subprocess.TimeoutExpired) as ctx:
            cc.run_process([sys.executable, "-c", script], timeout=3, on_line=lambda s, ln: None)
        self.assertIn("started", ctx.exception.stdout)

    def test_on_output_is_given_only_to_runners_that_accept_it(self):
        plain = FakeRunner()
        cli = make(plain)
        cli.on_output = lambda s, ln: None
        cli.sessions()  # the plain runner has no on_line parameter: it must not be passed one
        self.assertEqual(len(plain.calls), 1)

        got = {}

        def streaming(cmd, *, input="", timeout=120, env=None, on_start=None, on_line=None):
            got["on_line"] = on_line
            return subprocess.CompletedProcess(cmd, 0, "", "")
        sink = lambda s, ln: None  # noqa: E731
        cli2 = make(streaming)
        cli2.sessions()
        self.assertIsNone(got["on_line"])
        cli2.on_output = sink
        cli2.sessions()
        self.assertIs(got["on_line"], sink)


if __name__ == "__main__":
    unittest.main()
