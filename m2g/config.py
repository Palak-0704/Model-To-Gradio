"""Central configuration.

Secrets are read from the environment (or a local .env file) only. They are excluded from
repr() and are never serialized into generated artifacts.
"""
from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Hosts the inspector may contact directly (exact match, https only).
TRUSTED_HOSTS: Tuple[str, ...] = (
    "huggingface.co",
    "www.huggingface.co",
    "github.com",
    "www.github.com",
    "api.github.com",
    "raw.githubusercontent.com",
)
# Hosts we may be redirected to (CDN). Never receive credentials.
REDIRECT_HOST_SUFFIXES: Tuple[str, ...] = (".hf.co", ".huggingface.co", ".githubusercontent.com")
# Only these hosts ever receive the matching token.
HF_TOKEN_HOSTS: Tuple[str, ...] = ("huggingface.co", "www.huggingface.co")
GITHUB_TOKEN_HOSTS: Tuple[str, ...] = ("api.github.com",)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def load_dotenv_file(path: Optional[Path] = None) -> None:
    """Minimal .env loader (KEY=VALUE). Never overrides variables that are already set."""
    path = path or (PROJECT_ROOT / ".env")
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


@dataclass(frozen=True)
class Settings:
    output_dir: Path = PROJECT_ROOT / "output"
    http_timeout: float = 15.0
    max_response_bytes: int = 1_500_000
    max_redirects: int = 3
    max_readme_chars: int = 20_000
    max_tree_entries: int = 5_000
    # Static GitHub inspection budgets. These are cost bounds, not a fixed list
    # of files: candidates are prioritized and imports can add related modules.
    github_max_source_files: int = 60
    github_max_source_bytes: int = 4_000_000
    github_max_source_file_bytes: int = 1_000_000
    # Security defaults: both are OFF unless explicitly enabled.
    allow_remote_code: bool = False
    allow_pickle_weights: bool = False
    # REA adaptive-investigation budget: resource ceilings for the dynamic, evidence-gap-driven REA investigation (NOT a round
    # limit - the loop stops when the open questions are exhausted). Tune with M2G_REA_MAX_* if a larger repository needs more.
    rea_max_investigation_calls: int = 40
    rea_max_investigation_seconds: float = 120.0
    rea_max_investigation_failures: int = 5
    # Deep static source analysis: how many of the repository's own Python modules are parsed per run (bounded, never executed).
    max_source_modules: int = 60
    hf_token: Optional[str] = field(default=None, repr=False)
    github_token: Optional[str] = field(default=None, repr=False)

    def __post_init__(self) -> None:
        # Resolve relative output paths against the project, not the process cwd.
        # Gradio, the CLI, and API launchers may each use a different cwd.
        output = Path(self.output_dir).expanduser()
        if not output.is_absolute():
            output = PROJECT_ROOT / output
        object.__setattr__(self, "output_dir", output.resolve())

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv_file()
        out = os.environ.get("M2G_OUTPUT_DIR")
        return cls(
            output_dir=Path(out) if out else PROJECT_ROOT / "output",
            http_timeout=float(os.environ.get("M2G_HTTP_TIMEOUT", "15")),
            allow_remote_code=_env_bool("M2G_ALLOW_REMOTE_CODE", False),
            allow_pickle_weights=_env_bool("M2G_ALLOW_PICKLE_WEIGHTS", False),
            github_max_source_files=int(os.environ.get("M2G_GITHUB_MAX_SOURCE_FILES", "60")),
            github_max_source_bytes=int(os.environ.get("M2G_GITHUB_MAX_SOURCE_BYTES", "4000000")),
            github_max_source_file_bytes=int(os.environ.get("M2G_GITHUB_MAX_SOURCE_FILE_BYTES", "1000000")),
            rea_max_investigation_calls=int(os.environ.get("M2G_REA_MAX_CALLS", "40")),
            rea_max_investigation_seconds=float(os.environ.get("M2G_REA_MAX_SECONDS", "120")),
            rea_max_investigation_failures=int(os.environ.get("M2G_REA_MAX_FAILURES", "5")),
            max_source_modules=int(os.environ.get("M2G_MAX_SOURCE_MODULES", "60")),
            hf_token=os.environ.get("HF_TOKEN") or None,
            github_token=os.environ.get("GITHUB_TOKEN") or None,
        )


def check_write_access(directory: Path) -> Tuple[bool, str]:
    """Create the directory if needed, then write, read back and delete a probe file."""
    try:
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        probe = directory / f".write_probe_{uuid.uuid4().hex}"
        probe.write_text("ok", encoding="utf-8")
        ok = probe.read_text(encoding="utf-8") == "ok"
        probe.unlink()
        return (ok, "write/read/delete OK" if ok else "read-back mismatch")
    except OSError as exc:  # permission, read-only drive, invalid path
        return (False, f"{type(exc).__name__}: {exc}")
