"""Runtime configuration. Secrets come only from environment variables (or a local .env file)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Mapping, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _parse_env_file(path: Path) -> Dict[str, str]:
    """Parse a minimal KEY=VALUE .env file. No interpolation, no export keyword tricks."""
    values: Dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            values[key] = value
    return values


@dataclass(frozen=True)
class Settings:
    host: str = "127.0.0.1"
    port: int = 8765
    workspace_root: Path = PROJECT_ROOT / "workspaces"
    log_dir: Path = PROJECT_ROOT / "logs"

    # Credentials: never rendered in repr, never written to generated files.
    hf_token: Optional[str] = field(default=None, repr=False)
    github_token: Optional[str] = field(default=None, repr=False)

    # Network and resource limits.
    http_timeout: float = 20.0
    max_text_bytes: int = 256 * 1024
    max_json_bytes: int = 8 * 1024 * 1024
    max_files_to_read: int = 14
    max_body_bytes: int = 8 * 1024
    max_workspaces: int = 100
    max_concurrent_jobs: int = 3

    # Typical Colab free-tier hardware. These are NOT guarantees; the generated
    # notebook measures the real runtime before loading anything.
    colab_gpu_vram_gib: float = 15.0
    colab_ram_gib: float = 12.0


def _as_int(value: Optional[str], default: int, low: int, high: int) -> int:
    if value is None or value == "":
        return default
    try:
        number = int(value)
    except ValueError:
        return default
    return max(low, min(high, number))


def load_settings(
    env: Optional[Mapping[str, str]] = None,
    project_root: Optional[Path] = None,
) -> Settings:
    """Build Settings from the environment, falling back to <project_root>/.env."""
    root = project_root or PROJECT_ROOT
    merged: Dict[str, str] = dict(_parse_env_file(root / ".env"))
    merged.update(env if env is not None else os.environ)

    def pick(*names: str) -> Optional[str]:
        for name in names:
            value = merged.get(name)
            if value:
                return value
        return None

    workspace = pick("M2G_WORKSPACE_ROOT")
    log_dir = pick("M2G_LOG_DIR")
    return Settings(
        host=pick("M2G_HOST") or "127.0.0.1",
        port=_as_int(pick("M2G_PORT"), 8765, 0, 65535),
        workspace_root=Path(workspace) if workspace else root / "workspaces",
        log_dir=Path(log_dir) if log_dir else root / "logs",
        hf_token=pick("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN"),
        github_token=pick("GITHUB_TOKEN", "GH_TOKEN"),
        max_workspaces=_as_int(pick("M2G_MAX_WORKSPACES"), 100, 1, 10000),
        max_concurrent_jobs=_as_int(pick("M2G_MAX_CONCURRENT_JOBS"), 3, 1, 32),
    )
