"""Load Spotify / Apple developer credentials from env files (no password UI).

OAuth login is link-style: ``/api/streaming/spotify/login`` → Spotify authorize
→ callback → tokens. Client ID/secret stay in ``~/musync.env`` or
``config/streaming.local.env`` (gitignored) — never typed as a MuSync password form.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from ..config import repo_root

log = logging.getLogger(__name__)

_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def local_env_path() -> Path:
    return (repo_root() / "config" / "streaming.local.env").resolve()


def _parse_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        log.warning("Could not read %s: %s", path, exc)
        return out
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if _KEY_RE.match(key):
            out[key] = val
    return out


def load_streaming_env_files() -> list[str]:
    """Load ~/musync.env + config/streaming.local.env into os.environ.

    Does not override variables already set in the process environment.
    Returns paths that were loaded.
    """
    candidates = [
        Path.home() / "musync.env",
        local_env_path(),
    ]
    loaded_paths: list[str] = []
    for path in candidates:
        values = _parse_env_file(path)
        if not values:
            continue
        for key, val in values.items():
            if key not in os.environ or not (os.environ.get(key) or "").strip():
                os.environ[key] = val
        loaded_paths.append(str(path))
        log.info("Loaded streaming env from %s (%d keys)", path, len(values))
    return loaded_paths
