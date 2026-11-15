"""Load / persist Spotify + YouTube developer credentials from env files.

OAuth login is link-style: ``/api/streaming/spotify/login`` → Spotify authorize
→ callback → tokens. Client ID/secret and the YouTube API key stay in
``~/musync.env`` (Windows: ``%USERPROFILE%\\musync.env``) or
``config/streaming.local.env`` (gitignored) — never logged or returned raw
from the API (masked only).
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any, Optional

from ..config import repo_root

log = logging.getLogger(__name__)

_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# bash `export KEY=`, cmd `set KEY=`, PowerShell-ish noise
_PREFIX_RE = re.compile(r"^(?:export|set)\s+", re.IGNORECASE)

# Keys the Streaming credentials UI may read/write.
MANAGED_KEYS: tuple[str, ...] = (
    "MUSYNC_SPOTIFY_CLIENT_ID",
    "MUSYNC_SPOTIFY_CLIENT_SECRET",
    "MUSYNC_SPOTIFY_REDIRECT_URI",
    "MUSYNC_YOUTUBE_API_KEY",
)

# Never echo these in API JSON — only ``set`` + opaque mask.
SECRET_KEYS: frozenset[str] = frozenset(
    {
        "MUSYNC_SPOTIFY_CLIENT_SECRET",
        "MUSYNC_YOUTUBE_API_KEY",
    }
)

# Semi-public (appears in OAuth URLs) — still mask; show last 4 only.
MASK_TAIL_KEYS: frozenset[str] = frozenset(
    {
        "MUSYNC_SPOTIFY_CLIENT_ID",
    }
)

_MASK = "••••••••"


def local_env_path() -> Path:
    return (repo_root() / "config" / "streaming.local.env").resolve()


def musync_env_path() -> Path:
    """Primary writable credentials file (outside the git tree)."""
    return Path.home() / "musync.env"


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
    Never logs secret values — only path + key counts.
    """
    candidates = [
        musync_env_path(),
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


def _env_get(key: str) -> str:
    return (os.environ.get(key) or "").strip()


def mask_secret(value: str, *, key: str = "") -> str:
    """Return an opaque mask for API responses — never the raw secret.

    Empty → ``\"\"``. Secrets → fixed bullets. Client ID → bullets + last 4.
    Redirect URI is not masked (caller should return it plaintext).
    """
    raw = (value or "").strip()
    if not raw:
        return ""
    if key in SECRET_KEYS or not key:
        return _MASK
    if key in MASK_TAIL_KEYS:
        tail = raw[-4:] if len(raw) >= 4 else raw
        return f"{_MASK}{tail}"
    return _MASK


def _field_status(key: str, *, plaintext: bool = False) -> dict[str, Any]:
    val = _env_get(key)
    set_ = bool(val)
    out: dict[str, Any] = {"set": set_, "masked": mask_secret(val, key=key) if set_ else ""}
    if plaintext:
        out["value"] = val
        # Redirect URI is not a secret; still keep ``masked`` empty when unset.
        if set_:
            out["masked"] = ""
    return out


def credentials_status() -> dict[str, Any]:
    """Masked credential snapshot for the Streaming UI (no raw secrets)."""
    from .spotify import default_redirect_uri

    path = musync_env_path()
    spotify_id = _env_get("MUSYNC_SPOTIFY_CLIENT_ID")
    spotify_secret = _env_get("MUSYNC_SPOTIFY_CLIENT_SECRET")
    yt_key = _env_get("MUSYNC_YOUTUBE_API_KEY")
    redirect = _field_status("MUSYNC_SPOTIFY_REDIRECT_URI", plaintext=True)
    # Prefill Pi/laptop default (127.0.0.1:8080 via MUSYNC_PORT) when unset.
    if not redirect.get("value"):
        redirect = {
            "set": False,
            "masked": "",
            "value": default_redirect_uri(),
        }
    return {
        "env_path": str(path),
        "env_exists": path.is_file(),
        "spotify_client_id": _field_status("MUSYNC_SPOTIFY_CLIENT_ID"),
        "spotify_client_secret": _field_status("MUSYNC_SPOTIFY_CLIENT_SECRET"),
        "spotify_redirect_uri": redirect,
        "youtube_api_key": _field_status("MUSYNC_YOUTUBE_API_KEY"),
        "spotify_configured": bool(spotify_id and spotify_secret),
        "youtube_configured": bool(yt_key),
    }


def _quote_env_value(val: str) -> str:
    """Write KEY=value; quote when whitespace/specials present."""
    if not val:
        return ""
    if re.search(r'[\s#"\'\\]', val):
        escaped = val.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return val


def _upsert_env_lines(text: str, updates: dict[str, Optional[str]]) -> str:
    """Rewrite or append managed KEY= lines; preserve comments/other keys.

    ``updates`` values: non-empty string → set; ``\"\"`` → remove assignment
    (soft-clear); ``None`` keys should not be passed.
    """
    lines = text.splitlines(keepends=True)
    if lines and not lines[-1].endswith(("\n", "\r")):
        # Normalize so appends get a newline separator.
        pass
    seen: set[str] = set()
    out: list[str] = []
    for line in lines:
        raw = line.rstrip("\r\n")
        stripped = raw.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            out.append(line if line.endswith(("\n", "\r")) else line + "\n")
            continue
        body = _PREFIX_RE.sub("", stripped, count=1)
        key, _, _rest = body.partition("=")
        key = key.strip()
        if key not in updates:
            out.append(line if line.endswith(("\n", "\r")) else line + "\n")
            continue
        seen.add(key)
        new_val = updates[key]
        if new_val is None:
            out.append(line if line.endswith(("\n", "\r")) else line + "\n")
            continue
        if not new_val:
            # Soft-clear: drop the assignment line (empty = unset).
            continue
        out.append(f"{key}={_quote_env_value(new_val)}\n")
    for key, new_val in updates.items():
        if key in seen or new_val is None or not new_val:
            continue
        out.append(f"{key}={_quote_env_value(new_val)}\n")
    return "".join(out)


def apply_credentials_to_environ(updates: dict[str, Optional[str]]) -> None:
    """Push saved values into the live process env (empty clears)."""
    for key, val in updates.items():
        if val is None:
            continue
        if not val:
            os.environ.pop(key, None)
        else:
            os.environ[key] = val


def save_streaming_credentials(
    *,
    spotify_client_id: Optional[str] = None,
    spotify_client_secret: Optional[str] = None,
    spotify_redirect_uri: Optional[str] = None,
    youtube_api_key: Optional[str] = None,
) -> dict[str, Any]:
    """Persist provided fields to ``~/musync.env`` and update ``os.environ``.

    ``None`` = leave unchanged. Non-empty = set. Empty string = soft-clear
    that key. Empty/partial credentials soft-fail at use sites (status shows
    not configured) — this never raises for blank input.

    Never logs secret values — only which keys changed and the target path.
    """
    mapping: dict[str, Optional[str]] = {
        "MUSYNC_SPOTIFY_CLIENT_ID": spotify_client_id,
        "MUSYNC_SPOTIFY_CLIENT_SECRET": spotify_client_secret,
        "MUSYNC_SPOTIFY_REDIRECT_URI": spotify_redirect_uri,
        "MUSYNC_YOUTUBE_API_KEY": youtube_api_key,
    }
    updates: dict[str, Optional[str]] = {}
    for key, raw in mapping.items():
        if raw is None:
            continue
        updates[key] = raw.strip()

    if not updates:
        # Soft-fail empty POST — no file write, return current masked status.
        status = credentials_status()
        status["updated_keys"] = []
        status["saved"] = False
        return status

    path = musync_env_path()
    try:
        existing = ""
        if path.is_file():
            existing = path.read_text(encoding="utf-8-sig")
        new_text = _upsert_env_lines(existing, updates)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Restrictive perms on Unix; Windows ignores mode bits.
        path.write_text(new_text, encoding="utf-8")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    except OSError as exc:
        log.warning("Could not write streaming env %s: %s", path, exc)
        raise ValueError(
            f"Could not write {path} — check permissions (never commit this file)"
        ) from exc

    apply_credentials_to_environ(updates)
    changed = sorted(updates.keys())
    # Count only — never interpolate secret values into logs.
    log.info(
        "Saved streaming credentials to %s (%d keys: %s)",
        path,
        len(changed),
        ", ".join(changed),
    )
    status = credentials_status()
    status["updated_keys"] = changed
    status["saved"] = True
    return status
