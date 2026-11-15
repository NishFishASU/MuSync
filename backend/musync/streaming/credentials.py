"""Load Spotify / Apple developer credentials from env files (no password UI).

OAuth login is link-style: ``/api/streaming/spotify/login`` → Spotify authorize
→ callback → tokens. Client ID/secret stay in ``~/musync.env`` (Windows:
``%USERPROFILE%\\musync.env``) or ``config/streaming.local.env`` (gitignored) —
never typed as a MuSync password form.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from ..config import repo_root

log = logging.getLogger(__name__)

_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# bash `export KEY=`, cmd `set KEY=`, PowerShell-ish noise
_PREFIX_RE = re.compile(r"^(?:export|set)\s+", re.IGNORECASE)


def local_env_path() -> Path:
    return (repo_root() / "config" / "streaming.local.env").resolve()


def _parse_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    try:
        # utf-8-sig strips Windows BOM; CRLF handled by splitlines()
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        log.warning("Could not read %s: %s", path, exc)
        return out
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        line = _PREFIX_RE.sub("", line, count=1)
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if not _KEY_RE.match(key):
            continue
        # Skip blank assignments so an empty example copy does not clobber
        # a real value already present in the process environment.
        if not val:
            continue
        out[key] = val
    return out


def load_streaming_env_files() -> list[str]:
    """Load ~/musync.env + config/streaming.local.env into os.environ.

    Does not override variables already set (non-empty) in the process environment.
    Returns paths that were loaded. Works on Windows and Unix.
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
        applied = 0
        for key, val in values.items():
            if key not in os.environ or not (os.environ.get(key) or "").strip():
                os.environ[key] = val
                applied += 1
        loaded_paths.append(str(path))
        log.info(
            "Loaded streaming env from %s (%d keys, %d applied)",
            path,
            len(values),
            applied,
        )
    return loaded_paths
