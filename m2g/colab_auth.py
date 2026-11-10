"""Connect / disconnect a Google account for the official Colab CLI.

How authorization works (the CLI's own flow; this app only drives it):
  * The CLI runs a copy-paste OAuth flow with ITS OWN bundled Google client. It prints a Google consent URL and then
    reads the one-time code Google displays. Another client id cannot be used with that flow, so this app does not
    run its own Colab OAuth; it relays the CLI's official flow.
  * No Google password is ever requested here, and there is no email field. The user approves on accounts.google.com.
  * "Connected" is declared ONLY after a real authenticated Colab call (`colab sessions`) succeeds.
  * The CLI stores its own token. This module never reads or parses it, never logs the pasted code, and redacts
    token-looking strings from anything it shows.
  * The consent screen lists the scopes the CLI requests (shown to the user before they approve, see SignInPrompt.scopes).
"""
from __future__ import annotations

import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from .colab_cli import ColabCli, child_environment, kill_process_tree, truncate

_URL_RE = re.compile(r"https://accounts\.google\.com/o/oauth2/\S+")
# One-time codes are base64url-ish and can contain '/', '-', '_' and '.'. Nothing else is ever sent to the CLI.
_CODE_RE = re.compile(r"^[A-Za-z0-9/_.~-]{8,2048}$")
_TOKEN_LIKE = re.compile(r"(ya29\.[\w.-]+|1//[\w.-]+|4/[\w.-]{20,})")
REVOKE_URL = "https://myaccount.google.com/permissions"


class SignInError(Exception):
    """Safe-to-show failure while starting or finishing sign-in."""


@dataclass
class SignInPrompt:
    url: str
    scopes: List[str] = field(default_factory=list)


def redact(text: str, *secrets: str) -> str:
    """Remove explicit secrets (e.g. the pasted code) and anything shaped like a Google access/refresh token."""
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[redacted]")
    return _TOKEN_LIKE.sub("[redacted]", text)


def validate_auth_url(url: str) -> SignInPrompt:
    """The CLI prints the consent URL. Only an https accounts.google.com URL whose redirect also stays on Google is shown."""
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != "accounts.google.com" or parsed.username or parsed.password:
        raise SignInError("The Colab CLI printed a sign-in link that is not on accounts.google.com; not shown.")
    query = parse_qs(parsed.query)
    raw_redirect = (query.get("redirect_uri") or [""])[0]
    if not raw_redirect.startswith("urn:ietf:wg:oauth:2.0:oob"):  # classic copy-paste flow: Google shows the code itself
        redirect = urlparse(raw_redirect)
        host = redirect.hostname or ""
        if redirect.scheme not in ("https",) or not (host == "google.com" or host.endswith(".google.com")):
            raise SignInError("The sign-in link would send the code to a non-Google page; not shown.")
    scopes = (query.get("scope") or [""])[0].split()
    return SignInPrompt(url=url, scopes=scopes)


class ColabSignIn:
    """One sign-in attempt: begin() -> user approves on Google -> submit(code) -> real verification."""

    def __init__(self, cli: ColabCli, *, popen: Callable = subprocess.Popen, url_timeout: float = 60,
                 finish_timeout: float = 120):
        self.cli, self.popen = cli, popen
        self.url_timeout, self.finish_timeout = url_timeout, finish_timeout
        self.proc = None
        self._lines: List[str] = []
        self._lock = threading.Lock()

    def _reader(self, stream) -> None:
        for line in iter(stream.readline, ""):
            with self._lock:
                self._lines.append(line)

    def _output(self) -> str:
        with self._lock:
            return "".join(self._lines)

    def begin(self) -> SignInPrompt:
        if not self.cli.available:
            raise SignInError("The Colab CLI was not found. Install it first (see docs/COLAB_SETUP.md).")
        if self.cli.auth != "oauth2":
            raise SignInError("COLAB_AUTH is 'adc', so Google credentials come from gcloud, not from this button. "
                              "Run: gcloud auth application-default login --scopes=openid,"
                              "https://www.googleapis.com/auth/cloud-platform,"
                              "https://www.googleapis.com/auth/userinfo.email,"
                              "https://www.googleapis.com/auth/colaboratory")
        self.cancel()
        with self._lock:
            self._lines = []
        cmd = [*self.cli.command, "--auth", "oauth2", "sessions"]
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        try:
            self.proc = self.popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, encoding="utf-8", errors="replace", bufsize=1,
                                   env=child_environment(), shell=False, creationflags=flags)
        except OSError as exc:
            raise SignInError(f"Could not start the Colab CLI ({type(exc).__name__}).") from exc
        threading.Thread(target=self._reader, args=(self.proc.stdout,), daemon=True).start()
        deadline = time.monotonic() + self.url_timeout
        while time.monotonic() < deadline:
            match = _URL_RE.search(self._output())
            if match:
                try:
                    return validate_auth_url(match.group(0))
                except SignInError:
                    self.cancel()
                    raise
            if self.proc.poll() is not None:
                time.sleep(0.1)  # let the reader drain
                match = _URL_RE.search(self._output())
                if match:
                    continue
                if self.proc.returncode == 0:
                    raise SignInError("Already connected: the Colab CLI did not ask for sign-in.")
                raise SignInError("The Colab CLI exited before showing a sign-in link: "
                                  + truncate(redact(self._output()), 400))
            time.sleep(0.05)
        self.cancel()
        raise SignInError("Timed out waiting for the Colab CLI to print the Google sign-in link.")

    def submit(self, code: str) -> Tuple[bool, str]:
        """Send the pasted code to the CLI, then verify with a real authenticated call. The code is never logged."""
        code = (code or "").strip()
        if self.proc is None or self.proc.poll() is not None:
            return False, "No sign-in is waiting for a code. Click 'Sign in with Google' again."
        if not _CODE_RE.match(code):
            return False, "That does not look like the code Google shows after approval. Copy it again."
        try:
            self.proc.stdin.write(code + "\n")
            self.proc.stdin.flush()
            self.proc.stdin.close()
        except OSError:
            self.cancel()
            return False, "The sign-in process closed before the code was sent. Start again."
        try:
            self.proc.wait(timeout=self.finish_timeout)
        except subprocess.TimeoutExpired:
            self.cancel()
            return False, "Google did not finish the sign-in in time. Start again."
        time.sleep(0.1)
        detail = redact(self._output().split("Enter the authorization code:")[-1], code)
        returncode = self.proc.returncode
        self.proc = None
        state, message = self.cli.check()  # the only thing that proves we are connected
        if state == "ok":
            return True, "Connected. A real authenticated Colab call succeeded."
        reason = truncate(detail.strip(), 300) if returncode else message
        return False, f"Sign-in did not complete ({state}). {reason}"

    @property
    def waiting(self) -> bool:
        """True while the CLI process is alive and still waiting for the pasted code."""
        return self.proc is not None and self.proc.poll() is None

    def cancel(self) -> None:
        proc, self.proc = self.proc, None
        if proc is not None and proc.poll() is None:
            kill_process_tree(proc)


def token_path(home: Optional[object] = None) -> Path:
    """Where the CLI itself caches its sign-in (~/.config/colab-cli/token.json). Used ONLY to delete it on disconnect."""
    base = Path(home) if home is not None else Path.home()
    return base / ".config" / "colab-cli" / "token.json"


def remove_saved_sign_in(home: Optional[object] = None) -> Tuple[bool, str]:
    """Delete the CLI's cached sign-in on this machine (never reads it). Google-side access is revoked on REVOKE_URL."""
    path = token_path(home)
    try:
        if not path.exists():
            return True, "No saved Google sign-in was found at the CLI's usual location."
        path.unlink()
    except OSError as exc:
        return False, f"Could not remove the saved sign-in ({type(exc).__name__})."
    return True, (f"Removed the saved sign-in from this computer. To revoke access on Google's side, open {REVOKE_URL} "
                  "and remove the app.")
