"""Testable controller for the local deployment card."""
from __future__ import annotations

import html
from pathlib import Path
from typing import List, Optional

from .local_workflow import LocalLauncher, LocalSnapshot


class LocalPanel:
    def __init__(self, launcher: Optional[LocalLauncher] = None) -> None:
        self.launcher = launcher or LocalLauncher()

    @property
    def snapshot(self) -> LocalSnapshot:
        return self.launcher.snapshot

    def launch(self, app_code: str, requirements: str, files: List[str]) -> str:
        if not files:
            raise RuntimeError("Analyze and generate an app before launching locally.")
        output_dir = Path(files[0]).resolve().parent
        if (output_dir / "loader.py").is_file():
            # a generated package runs through its own run_<folder>.bat and its own .venv (Windows); local_runner re-validates it first
            from .local_runner import LocalRunError, start_local_app
            try:
                start_local_app(self.launcher, output_dir.parent, output_dir.name)
            except LocalRunError as exc:
                raise RuntimeError(exc.message)
            return self.status_html()
        self.launcher.launch(app_code, requirements, output_dir)
        return self.status_html()

    def stop(self) -> str:
        self.launcher.stop()
        return self.status_html()

    def status_html(self) -> str:
        s = self.snapshot
        details = s.error or (s.hardware + (f"; environment: {s.environment}" if s.environment else ""))
        link = (f"<br><a href=\"{html.escape(s.url, quote=True)}\" target=\"_blank\">"
                "Open local Gradio app</a>" if s.url else "")
        return (
            f"<b>Local deployment: {html.escape(s.state)}</b><br>"
            f"{html.escape(details)}{link}"
        )

    def logs_text(self) -> str:
        return "\n".join(self.snapshot.logs)
