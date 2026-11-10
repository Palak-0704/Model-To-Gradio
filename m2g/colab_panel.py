"""Framework-free controller behind the "Compute: run on Google Colab" panel.

Every method returns plain strings / tuples so the logic can be unit-tested without Gradio. The Gradio wiring lives in
``m2g.web``. Nothing here is simulated: states come from real CLI calls, and the public URL comes only from
``ColabLauncher`` after the runtime reported it AND it answered an HTTP request from this machine.
"""
from __future__ import annotations

import html as _html
import os
import threading
import time
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple
from urllib.parse import quote

from .colab_auth import REVOKE_URL, ColabSignIn, SignInError, SignInPrompt, remove_saved_sign_in
from .colab_cli import DOCUMENTED_GPUS, WINDOWS_NOTE, ColabCli
from .colab_workflow import (ACTIVE_STATES, CANCELLED, CHECKING, CPU_LABEL, CREATING, FAILED, HARDWARE, INSTALLING,
                             PREPARING, RUNNING, STARTING, STOPPED, UPLOADING, WAITING, ColabLauncher,
                             LaunchSnapshot, suggest_gpu)
from .config import Settings

PUBLIC_LINK_WARNING = (
    "Anyone who has this link can use the app (and your Colab compute) until the runtime stops. It is temporary "
    "(Gradio share links expire and end when the runtime stops or is reclaimed) - never treat it as private or permanent "
    "unless you set a password below."
)


CANCELLING_NOTICE = "Cancelling… the runtime will be released."

# The launcher's fine-grained states, grouped into four steps for the compact progress display.
_STEPS = ("Preparing", "Starting runtime", "Installing", "Launching app")
_STEP_OF = {PREPARING: 0, CHECKING: 0, CREATING: 1, HARDWARE: 1, INSTALLING: 2, UPLOADING: 3, STARTING: 3, WAITING: 3}


@dataclass(frozen=True)
class CardView:
    """Everything the compact Colab card needs: the HTML plus which actions make sense right now."""
    html: str
    connected: bool
    busy: bool          # a launch is in progress (show progress + Cancel)
    running: bool       # the app is up and its URL was verified (show Stop runtime)
    has_runtime: bool   # a runtime exists that Stop runtime can release
    can_launch: bool    # False only when the connection is known to be unusable
    signing_in: bool = False
    checking: bool = False
    stopping: bool = False
    # Button states, all derived from the real backend state (never hardcoded in the UI):
    signin_enabled: bool = True
    signin_label: str = "Sign in with Google"
    check_enabled: bool = True
    check_label: str = "Check status"
    stop_enabled: bool = False
    stop_label: str = "Stop Colab runtime"
    launch_enabled: bool = True
    launch_label: str = "Launch on Colab"
    url: str = ""        # the verified public link, or "" until every check has passed
    url_html: str = ""   # the dedicated Live Gradio URL box


def _short(text: str, limit: int = 180) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _e(text: object) -> str:
    return _html.escape(str(text), quote=True)


URL_PLACEHOLDER = "Your verified demo URL will appear here"


def verified_url(s: LaunchSnapshot) -> str:
    """The public link, only when the launcher reached RUNNING (which it sets only after the model reported loaded AND the URL
    answered a real request from this PC) and the app's own example did not fail. Nothing else ever yields a link."""
    url = s.share_url or ""
    if s.state == RUNNING and url.startswith("https://") and s.model_loaded and s.selftest in ("ok", "none"):
        return url
    return ""


def url_box_html(s: LaunchSnapshot, url: str) -> str:
    """Content of the Live Gradio URL box: the clickable link when verified, otherwise the placeholder."""
    head = '<div class="m2g-urlbox-label">Live Gradio URL</div>'
    if url:
        tested = ("The model is loaded and the app's example ran successfully." if s.selftest == "ok" else
                  "The model is loaded. This app has no built-in example, so inference was not tested; try it at the link.")
        who = ("Password-protected. Temporary link." if s.auth_protected else
               "Anyone with this link can use the app until the runtime stops.")
        return (f'{head}<a class="m2g-url" href="{_e(url)}" target="_blank" rel="noopener noreferrer">{_e(url)}</a>'
                f'<div class="m2g-sub">{tested}</div><div class="m2g-sub">{who}</div>')
    warn = ('<div class="m2g-warn">The app is up, but its built-in example failed, so the link is not shown. See View logs.</div>'
            if s.state == RUNNING and s.selftest == "fail" else "")
    return f'{head}<div class="m2g-url-placeholder">{URL_PLACEHOLDER}</div>{warn}'


def _md_url(url: str) -> str:
    return quote(url, safe=":/?&=%#@+,;~-._")  # keeps the URL clickable and neutralises ( ) [ ] and spaces


class ColabPanel:
    def __init__(self, cli: Optional[ColabCli] = None, launcher: Optional[ColabLauncher] = None,
                 signin_factory: Optional[Callable[[ColabCli], ColabSignIn]] = None,
                 remove_sign_in: Callable[[], Tuple[bool, str]] = remove_saved_sign_in,
                 settings_factory: Callable[[], Settings] = Settings.from_env):
        self.cli = cli or ColabCli()
        self.launcher = launcher or ColabLauncher(self.cli)
        self._signin_factory = signin_factory or (lambda c: ColabSignIn(c))
        self._remove_sign_in = remove_sign_in
        self._settings_factory = settings_factory
        self._signin: Optional[ColabSignIn] = None
        self._prompt: Optional[SignInPrompt] = None
        self._lock = threading.RLock()
        self.cli_identity: Tuple[Optional[bool], str] = (None, "Not checked yet.")
        self.google: Tuple[str, str, float] = ("unknown", "Not checked yet.", 0.0)
        self._notice = ""
        self._notice_bad = False   # style the notice as an error (set by a failed stop)
        self._checking = False     # a status check is running
        self._stopping = False     # a stop is running
        self._stop_thread: Optional[threading.Thread] = None
        self._stop_generation = 0
        self._cancel_requested = False  # a mid-launch stop (cancel) was requested and the launch has not ended yet
        self._initial_done = False
        self._version = ""

    # ------------------------------------------------------------------ GPU choices
    def gpu_choices(self) -> Tuple[List[str], str]:
        """Dropdown labels + an honest note. Names come from the installed CLI's own --help when it can be read."""
        if self.cli.available and self.cli_identity[0]:
            choices = self.cli.supported_gpus()
            note = (f"Accelerators the installed CLI accepts ({choices.source}). Choosing one REQUESTS it; whether your "
                    "account can get it right now is decided by Google, and the real hardware is verified with nvidia-smi "
                    "after the runtime is created.")
            return [CPU_LABEL, *choices.names], note
        return ([CPU_LABEL, *DOCUMENTED_GPUS],
                "Documented accelerator names (the CLI has not been checked yet). Click 'Check Colab CLI & connection'.")

    def suggestion(self, hw: object) -> Tuple[str, str]:
        names, _ = self.gpu_choices()
        return suggest_gpu(hw, [n for n in names if n != CPU_LABEL])

    # ------------------------------------------------------------------ connection / sign-in
    def check_connection(self) -> str:
        with self._lock:
            ok, msg = self.cli.identify()
            self.cli_identity = (ok, msg)
            if not ok:
                self.google = ("unavailable", msg, time.time())
                self._notice = ""
                return self.status_markdown()
            self._version = self.cli.version()
            state, message = self.cli.check()
            self.google = (state, message, time.time())
            self.cli.supported_gpus(refresh=True)
            self._notice = ""
        return self.status_markdown()

    def begin_sign_in(self) -> Tuple[str, bool]:
        """Returns (status markdown, show the code box)."""
        with self._lock:
            if self._signin is not None:
                self._signin.cancel()
            self._signin = self._signin_factory(self.cli)
            self._prompt = None
            try:
                self._prompt = self._signin.begin()
                self._notice = "Sign-in started. Open the link, approve access on Google's page, then paste the code below."
            except SignInError as exc:
                self._notice = f"Sign-in could not start: {exc}"
                self._signin = None
                return self.status_markdown(), False
        return self.status_markdown(), True

    def submit_code(self, code: str) -> Tuple[str, bool]:
        with self._lock:
            if self._signin is None:
                self._notice = "No sign-in is waiting for a code. Click 'Sign in with Google' first."
                return self.status_markdown(), False
            ok, message = self._signin.submit(code)  # the code itself is never logged or stored by this app
            self._notice = message
            self._signin, self._prompt = None, None
            if ok:
                self.google = ("ok", message, time.time())
            else:
                self.google = ("needs_auth", message, time.time())
        return self.status_markdown(), False

    def cancel_sign_in(self) -> Tuple[str, bool]:
        with self._lock:
            if self._signin is not None:
                self._signin.cancel()
            self._signin, self._prompt = None, None
            self._notice = "Sign-in cancelled."
        return self.status_markdown(), False

    def disconnect_google(self) -> Tuple[str, bool]:
        """Stop any runtime, cancel a pending sign-in, delete the CLI's saved sign-in, then re-check for real."""
        notes: List[str] = []
        snap = self.launcher.snapshot()
        if self.launcher.busy or snap.session:
            self.launcher.stop() if not self.launcher.busy else self.launcher.cancel()
            self.launcher.wait(60)
            snap = self.launcher.snapshot()
            notes.append("Runtime stopped." if not snap.session else f"A runtime named {snap.session} may still be running.")
        with self._lock:
            if self._signin is not None:
                self._signin.cancel()
            self._signin, self._prompt = None, None
        ok, message = self._remove_sign_in()
        notes.append(message)
        state, detail = self.cli.check() if self.cli.available else ("unavailable", "CLI not found.")
        with self._lock:
            self.google = (state, detail, time.time())
        if state == "ok":
            notes.append("The CLI still reports an authenticated session (it may keep credentials elsewhere, or use "
                         f"`--auth adc`). To revoke access fully, visit {REVOKE_URL}.")
        self._notice = " ".join(notes)
        return self.status_markdown(), False

    # ------------------------------------------------------------------ run control
    def launch(self, app_code: str, requirements_text: str, gpu_label: str, user: str = "", password: str = "") -> str:
        if not (app_code or "").strip():
            self._notice = "Generate an app first (paste a model URL above and click 'Analyze and generate')."
            return self.status_markdown()
        auth = (user or "", password or "") if (user or password) else None
        started = self.launcher.start_async(app_code, requirements_text or "", gpu_label, settings=self._settings_factory(),
                                            auth=auth)
        self._notice = ("" if started else "A launch is already in progress or a runtime is still running. Stop it first.")
        return self.status_markdown()

    def cancel_launch(self) -> str:
        if self.launcher.busy:
            self.launcher.cancel()
            self._notice = CANCELLING_NOTICE
        else:
            self._notice = "No launch is in progress."
        return self.status_markdown()

    def begin_stop(self) -> None:
        """Mark a stop as in progress so the button disables at once (the stop itself runs in ``stop_runtime``)."""
        with self._lock:
            self._stopping = True

    def request_stop(self) -> str:
        """Request a stop without occupying the Gradio event worker.

        ``colab stop`` can take up to the CLI timeout. The UI must remain
        responsive while it runs, and the worker owns the flag cleanup so an
        abandoned/chained callback cannot leave the card stuck in
        ``Stopping…``.
        """
        with self._lock:
            if self._stopping:
                return self.status_markdown()
            self._stopping = True
            self._stop_generation += 1
            generation = self._stop_generation

        def worker() -> None:
            try:
                self.stop_runtime()
            except Exception as exc:
                # Keep the error visible while ensuring the lifecycle flag is
                # released even for an unexpected launcher exception.
                with self._lock:
                    self._notice = f"Stopping failed: {type(exc).__name__}: {_short(str(exc), 180)}"
                    self._notice_bad = True
            finally:
                with self._lock:
                    if generation == self._stop_generation:
                        self._stopping = False

        thread = threading.Thread(target=worker, daemon=True, name="m2g-colab-stop")
        with self._lock:
            self._stop_thread = thread
        thread.start()
        return self.status_markdown()

    def stop_runtime(self) -> str:
        """Stop ONLY the runtime this app created and tracks (the launcher knows its own session). Mid-launch it cancels the
        launch instead, which releases the runtime. With nothing tracked it does nothing: unrelated sessions are never touched."""
        was_busy = self.launcher.busy
        with self._lock:
            self._stopping = True
        try:
            self.launcher.stop()
        finally:
            with self._lock:
                self._stopping = False
        if was_busy:
            self._cancel_requested = True
        snap = self.launcher.snapshot()
        failed = bool(snap.session) and snap.message.startswith("Stopping failed")
        self._notice = _short(snap.message, 240) if failed else ""
        self._notice_bad = failed
        return self.status_markdown()

    def begin_check(self) -> None:
        """Mark a status check as in progress so the button disables at once (the check runs in ``check_status``)."""
        self._checking = True

    def check_status(self) -> str:
        """A real authentication check; when this app has a running runtime it is also verified to be alive. Skipped while a
        launch is in progress so it can never interfere with it."""
        if self.launcher.busy:
            self._checking = False
            return self.status_markdown()
        self._checking = True
        self._notice_bad = False
        try:
            md = self.check_connection()
            if self.launcher.snapshot().state == RUNNING:
                self.launcher.refresh()
                md = self.status_markdown()
            return md
        finally:
            self._checking = False

    def initial_check(self) -> str:
        """Run once when the page opens so the badge shows the real state instead of 'not checked'."""
        if self._initial_done:
            return self.status_markdown()
        self._initial_done = True  # one attempt only, even if it raises
        if self.cli_identity[0] is None and self.google[0] == "unknown":
            return self.check_status()
        return self.status_markdown()

    def check_alive(self) -> str:
        self.launcher.refresh()
        self._notice = ""
        return self.status_markdown()

    def reset_view(self) -> None:
        """Clear what an earlier page or analysis left on screen: log lines, a finished launch's result and stale notices.

        Never touches a launch in progress, a tracked runtime, a running stop/check or a pending sign-in, and never deletes
        any file on disk.
        """
        with self._lock:
            self.launcher.reset_view()
            if not (self._stopping or self._cancel_requested or self._checking):
                self._notice, self._notice_bad = "", False

    def logs_text(self) -> str:
        return "\n".join(self.launcher.snapshot().logs[-250:])

    # ------------------------------------------------------------------ rendering
    def status_markdown(self) -> str:
        snap: LaunchSnapshot = self.launcher.snapshot()
        lines = ["### Google Colab (official Colab CLI)"]
        ident_ok, ident_msg = self.cli_identity
        if ident_ok is None:
            lines.append("**Colab CLI:** not checked yet - click *Check Colab CLI & connection*.")
        elif ident_ok:
            cmd = " ".join(self.cli.command or [])
            lines.append(f"**Colab CLI:** found (`{cmd}`)" + (f", version `{self._version}`" if self._version else ""))
        else:
            lines += [f"**Colab CLI:** not usable. {ident_msg}"]
        state, message, when = self.google
        if ident_ok:
            stamp = time.strftime("%H:%M:%S", time.localtime(when)) if when else ""
            if state == "ok":
                lines.append(f"**Google:** connected - verified by a real `colab sessions` call at {stamp}.")
            elif state == "needs_auth":
                lines.append(f"**Google:** not connected. {message}")
            elif state != "unknown":
                lines.append(f"**Google:** check failed. {message}")
        if os.name == "nt":
            lines.append(f"\n> Note: {WINDOWS_NOTE}")
        if self._signin is not None and self._prompt is not None and self._signin.waiting:
            link = _md_url(self._prompt.url)
            lines += ["", "**Step 1.** Open this Google page and approve access:", "", f"[Open Google sign-in]({link})", "",
                      "```", self._prompt.url, "```",
                      "Permissions requested by the CLI: " + (", ".join(f"`{s.rsplit('/', 1)[-1]}`" for s in self._prompt.scopes) or "(not shown)"),
                      "", "**Step 2.** Copy the code Google shows and paste it into *Authorization code* below, then click *Submit code*. "
                          "No password is ever entered here."]
        if self._notice:
            lines += ["", self._notice]
        lines += ["", self._runtime_markdown(snap)]
        return "\n".join(lines)

    @staticmethod
    def _runtime_markdown(s: LaunchSnapshot) -> str:
        if s.state == "idle" and not s.logs:
            return "**Runtime:** none. Choose a GPU and click *Launch model on Colab*."
        out = [f"**Runtime state:** `{s.state}`" + (f" - session `{s.session}`" if s.session else "")]
        if s.message:
            out.append(s.message)
        if s.gpu_requested:
            hw = s.hardware or {}
            gpu = hw.get("gpu")
            det = (f"**{gpu}** ({round((hw.get('gpu_mem_mib') or 0) / 1024, 1)} GiB VRAM)" if gpu
                   else ("no GPU" if hw else "not verified yet"))
            out.append(f"**Requested:** {s.gpu_requested} (a request) - **Detected on the runtime (nvidia-smi):** {det}"
                       + (f" - RAM {hw.get('ram_gib')} GiB - free disk {hw.get('disk_free_gib')} GiB" if hw else ""))
        if s.state == RUNNING and s.share_url:
            link = _md_url(s.share_url)
            out += ["", f"**Public Gradio app:** [{s.share_url}]({link})",
                    f"*URL check from this PC:* {s.url_check}",
                    "*Model loaded on the runtime: yes.*"]
            if s.selftest == "ok":
                out.append(f"*Self-test (the app's own first example): passed* - output began: `{(s.selftest_detail or '')[:80]}`")
            elif s.selftest == "fail":
                out.append(f"*Self-test (the app's own first example): FAILED* - `{(s.selftest_detail or '')[:160]}`. The app is up, "
                           "but inference raised an error on its example; check the log.")
            elif s.selftest == "none":
                out.append("*Self-test: not available for this app (no built-in example). Try it at the link.*")
            out += ["", f"**Warning:** {PUBLIC_LINK_WARNING}" if not s.auth_protected else
                    "**Note:** the link is password-protected (the password you set). It is still temporary.",
                    "Images, audio and every other output type are handled by the Gradio app at that link itself; "
                    "nothing is serialised through this local app."]
        elif s.state in (FAILED, CANCELLED):
            out.append("No public URL is available for this run.")
            if s.state == FAILED:
                out += ColabPanel._failure_markdown(s)
        elif s.state == STOPPED:
            out.append("Any earlier link is dead.")
        return "\n".join(out)

    @staticmethod
    def _failure_markdown(s: LaunchSnapshot) -> List[str]:
        """Phase 3: the classified cause and next step in plain words, then the ORIGINAL technical diagnostics (tracebacks, pip and
        CLI output, every automatic retry) in a collapsed code block. Runtime-supplied text only ever goes inside the code block."""
        lines: List[str] = []
        if s.error_category:
            lines.append(f"**Cause:** `{s.error_category}/{s.error_code}`" + (f" - failed at stage `{s.failed_stage}`" if s.failed_stage else "")
                         + (" - transient: a new launch may succeed." if s.retryable
                            else " - permanent: launching again will not help until the cause is fixed."))
        if s.recovery:
            lines.append(f"**What to do:** {s.recovery}")
        body = []
        if s.recovery_log:
            body += ["Automatic retries made (transient errors only):", *s.recovery_log, ""]
        if (s.diagnostics or "").strip():
            body.append(s.diagnostics)
        if body:
            text = "\n".join(body).replace("```", "'''")[-6000:]
            lines += ["", "<details><summary>Technical diagnostics (original errors and tracebacks)</summary>", "", "```text", text,
                      "```", "", "</details>"]
        return lines

    # ------------------------------------------------------------------ compact card (what the page shows by default)
    def card_view(self) -> CardView:
        """The concise card: connection badge, progress, verified link. Technical detail stays in ``status_markdown`` and the log."""
        snap = self.launcher.snapshot()
        busy = snap.state in ACTIVE_STATES or bool(self.launcher.busy)
        if not busy:
            self._cancel_requested = False  # the cancel has finished
        if self._notice == CANCELLING_NOTICE and not busy:
            self._notice = ""  # the transient notice must not outlive the cancel
        ident_ok = self.cli_identity[0]
        state = self.google[0]
        running = snap.state == RUNNING and bool(snap.share_url)  # RUNNING is only set after the URL answered a real request
        connected = (state == "ok" and ident_ok is not False) or running  # a verified run also proves the sign-in worked
        has_runtime = running or (bool(snap.session) and not busy)  # a runtime THIS app created and still tracks
        can_launch = connected or not (ident_ok is False or state == "needs_auth")
        signing_in = self._signin is not None and self._prompt is not None and bool(self._signin.waiting)
        checking = self._checking
        stopping = self._stopping or self._cancel_requested
        url = verified_url(snap)
        parts = [self._connection_html(connected)]
        sign_in = self._signin_html()
        if sign_in:
            parts.append(sign_in)
        elif self._notice:
            tone = " m2g-note-bad" if (not connected or self._notice_bad) else ""
            parts.append(f'<div class="m2g-note{tone}">{_e(_short(self._notice, 240))}</div>')
        parts.append(self._runtime_html(snap, busy))
        return CardView(
            html='<div class="m2g-colab-card">' + "".join(parts) + "</div>", connected=connected, busy=busy,
            running=running, has_runtime=has_runtime, can_launch=can_launch, signing_in=signing_in, checking=checking,
            stopping=stopping,
            signin_enabled=not connected and not signing_in and not checking and not busy,
            signin_label="Signed in" if connected else ("Signing in…" if signing_in else "Sign in with Google"),
            check_enabled=not busy and not checking and not stopping and not signing_in,
            check_label="Checking…" if checking else "Check status",
            # Stop acts on the app's own tracked runtime; during a launch it cancels that launch (the launcher releases the
            # runtime it created). It is off while a stop/check runs so it cannot be triggered twice.
            stop_enabled=(has_runtime or busy) and not stopping and not checking,
            stop_label="Stopping…" if stopping else "Stop Colab runtime",
            launch_enabled=can_launch and not busy and not has_runtime and not stopping and not checking and not signing_in,
            launch_label="Launching…" if busy else "Launch on Colab",
            url=url, url_html=url_box_html(snap, url))

    def _connection_html(self, connected: bool) -> str:
        ident_ok, ident_msg = self.cli_identity
        state, message, when = self.google
        if connected:
            stamp = time.strftime("%H:%M", time.localtime(when)) if when else ""
            tone, label = "ok", "Connected"
            sub = "Google sign-in verified" + (f" at {stamp}." if stamp else ".")
        elif ident_ok is False:
            tone, label = "bad", "Not connected"
            sub = f"The Colab CLI is not available. {_e(_short(ident_msg, 120))} See docs/COLAB_SETUP.md."
        elif state == "needs_auth":
            tone, label, sub = "idle", "Not connected", "Sign in with Google to continue."
        elif ident_ok and state != "unknown":
            tone, label = "bad", "Not connected"
            sub = f"Could not verify the connection. {_e(_short(message, 120))} See View logs."
        else:
            tone, label, sub = "idle", "Not connected", "Not checked yet. Click Check status."
        return (f'<div class="m2g-conn"><span class="m2g-dot m2g-dot-{tone}"></span><div>'
                f'<div class="m2g-conn-title">{label}</div><div class="m2g-sub">{sub}</div></div></div>')

    def _signin_html(self) -> str:
        if not (self._signin is not None and self._prompt is not None and self._signin.waiting):
            return ""
        url = self._prompt.url or ""
        if not url.startswith("https://"):
            return ""
        return ('<div class="m2g-note"><b>1.</b> <a class="m2g-url" href="' + _e(url) + '" target="_blank" '
                'rel="noopener noreferrer">Open Google sign-in</a> and approve access. <b>2.</b> Paste the code Google '
                'shows into the box below. No password is entered here.</div>')

    @staticmethod
    def _runtime_html(s: LaunchSnapshot, busy: bool) -> str:
        if busy:
            idx = _STEP_OF.get(s.state, 0)
            pct = int((idx + 1) / len(_STEPS) * 100)
            pills = "".join(f'<li class="{"done" if i < idx else ("current" if i == idx else "todo")}">{label}</li>'
                            for i, label in enumerate(_STEPS))
            return (f'<div class="m2g-run"><div class="m2g-run-title">{_STEPS[idx]}… '
                    f'<span class="m2g-sub">Step {idx + 1} of {len(_STEPS)}</span></div>'
                    f'<div class="m2g-bar"><div style="width:{pct}%"></div></div><ol class="m2g-steps">{pills}</ol></div>')
        if s.state == RUNNING and s.share_url:
            accel = f' <span class="m2g-sub">· {_e(s.gpu_requested)}</span>' if s.gpu_requested else ""
            return ('<div class="m2g-run"><div class="m2g-run-title"><span class="m2g-dot m2g-dot-ok"></span>'
                    f'Runtime running{accel}</div></div>')  # the link lives in its own Live Gradio URL box
        if s.state == FAILED:
            extra = ""
            if s.error_category:  # Phase 3: the cause, where it failed and what was already retried, in plain words
                cause = f"Cause: {s.error_category}/{s.error_code}" + (f" at stage '{s.failed_stage}'" if s.failed_stage else "")
                if s.recovery_log:
                    n = len(s.recovery_log)
                    cause += f" - {n} automatic {'retry' if n == 1 else 'retries'} made before giving up"
                extra += f'<div class="m2g-sub">{_e(_short(cause, 200))}</div>'
            if s.recovery:
                extra += f'<div class="m2g-sub"><b>What to do:</b> {_e(_short(s.recovery, 240))}</div>'
            return ('<div class="m2g-run"><div class="m2g-run-title"><span class="m2g-dot m2g-dot-bad"></span>Launch failed</div>'
                    f'<div class="m2g-sub">{_e(_short(s.message, 200))}</div>{extra}'
                    '<div class="m2g-sub">Details are in View logs.</div></div>')
        if s.state == CANCELLED:
            return '<div class="m2g-run"><div class="m2g-run-title">Launch cancelled</div></div>'
        if s.state == STOPPED:
            return '<div class="m2g-run"><div class="m2g-run-title">Runtime stopped</div></div>'
        return ""

    @staticmethod
    def active(snapshot: LaunchSnapshot) -> bool:
        return snapshot.state in ACTIVE_STATES
