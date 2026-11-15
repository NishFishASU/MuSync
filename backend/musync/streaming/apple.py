"""Best-effort Apple Music on Linux.

Real hub PCM for Apple Music is limited on Linux (no official server player).
This module supports:
  - Developer token (MusicKit) for catalog search when keys are set
  - Optional AirPlay/shairport PipeWire source → musync_feeder capture
  - Clear documented limits when playback cannot be real

Env (never commit secrets / .p8 keys):
  MUSYNC_APPLE_TEAM_ID
  MUSYNC_APPLE_KEY_ID
  MUSYNC_APPLE_PRIVATE_KEY_PATH   (.p8 path)
  MUSYNC_APPLE_MUSIC_USER_TOKEN   (optional; needed for personalized library)
  MUSYNC_APPLE_AIRPLAY_SOURCE     (PipeWire source name from shairport-sync)
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import httpx

log = logging.getLogger(__name__)

APPLE_CATALOG = "https://api.music.apple.com/v1"


def _team_id() -> str:
    return (os.environ.get("MUSYNC_APPLE_TEAM_ID") or "").strip()


def _key_id() -> str:
    return (os.environ.get("MUSYNC_APPLE_KEY_ID") or "").strip()


def _private_key_path() -> Optional[Path]:
    raw = (os.environ.get("MUSYNC_APPLE_PRIVATE_KEY_PATH") or "").strip()
    return Path(raw).expanduser().resolve() if raw else None


def _user_token() -> str:
    return (os.environ.get("MUSYNC_APPLE_MUSIC_USER_TOKEN") or "").strip()


def _airplay_source() -> str:
    return (os.environ.get("MUSYNC_APPLE_AIRPLAY_SOURCE") or "").strip()


def _storefront() -> str:
    return (os.environ.get("MUSYNC_APPLE_STOREFRONT") or "us").strip() or "us"


def credentials_configured() -> bool:
    path = _private_key_path()
    return bool(_team_id() and _key_id() and path and path.is_file())


@dataclass
class AppleTrack:
    id: str
    uri: str
    title: str
    artist: str
    duration_ms: Optional[int] = None
    album: Optional[str] = None


class AppleMusicClient:
    """MusicKit developer-token catalog + AirPlay capture hook."""

    def __init__(self) -> None:
        self._dev_token: Optional[str] = None
        self._dev_exp: float = 0.0

    def status(self) -> dict[str, Any]:
        configured = credentials_configured()
        airplay = _airplay_source()
        limits = [
            "Linux has no official Apple Music server player",
            "Catalog search needs MusicKit keys (team/key/.p8)",
            "Full playback: AirPlay to shairport-sync → PipeWire source → feeder, "
            "or MusicKit JS in a browser (not hub PCM)",
        ]
        if not configured:
            limits.insert(0, "Apple MusicKit keys not set — Apple stays stub")
        if not airplay:
            limits.append(
                "Set MUSYNC_APPLE_AIRPLAY_SOURCE to a shairport PipeWire source "
                "for best-effort hub capture"
            )
        return {
            "provider": "apple",
            "configured": configured,
            "authenticated": configured,  # developer token only; user token optional
            "user_token_set": bool(_user_token()),
            "airplay_source": airplay or None,
            "playback_mode": "airplay_capture" if airplay else "limited_stub",
            "limits": limits,
            "note": (
                "Search OK; play uses AirPlay capture into musync_feeder"
                if configured and airplay
                else (
                    "Search OK; play remains limited without AirPlay source"
                    if configured
                    else "Credentials missing — mock/local still work; Apple stays stub"
                )
            ),
        }

    def configured(self) -> bool:
        return credentials_configured()

    def authenticated(self) -> bool:
        return credentials_configured()

    def airplay_source(self) -> Optional[str]:
        src = _airplay_source()
        return src or None

    def developer_token(self) -> str:
        if not credentials_configured():
            raise ValueError(
                "Apple Music not configured — set MUSYNC_APPLE_TEAM_ID, "
                "MUSYNC_APPLE_KEY_ID, MUSYNC_APPLE_PRIVATE_KEY_PATH (see docs/spotify-apple.md)"
            )
        now = time.time()
        if self._dev_token and now < self._dev_exp - 60:
            return self._dev_token
        try:
            import jwt  # PyJWT
        except ImportError as exc:
            raise ValueError(
                "PyJWT required for Apple MusicKit tokens — pip install PyJWT cryptography"
            ) from exc

        path = _private_key_path()
        assert path is not None
        private_key = path.read_text(encoding="utf-8")
        headers = {"alg": "ES256", "kid": _key_id()}
        payload = {
            "iss": _team_id(),
            "iat": int(now),
            "exp": int(now) + 12 * 3600,
        }
        token = jwt.encode(payload, private_key, algorithm="ES256", headers=headers)
        self._dev_token = token if isinstance(token, str) else token.decode("utf-8")
        self._dev_exp = now + 12 * 3600
        return self._dev_token

    def search_tracks(self, query: str, *, limit: int = 15) -> list[AppleTrack]:
        q = (query or "").strip()
        if not q:
            return []
        if not self.authenticated():
            raise ValueError("Configure Apple MusicKit keys first")
        store = _storefront()
        headers = {
            "Authorization": f"Bearer {self.developer_token()}",
            "Music-User-Token": _user_token(),
        }
        # User token optional for catalog search.
        if not _user_token():
            headers.pop("Music-User-Token", None)
        with httpx.Client(timeout=20.0) as client:
            resp = client.get(
                f"{APPLE_CATALOG}/catalog/{store}/search",
                headers=headers,
                params={
                    "term": q,
                    "types": "songs",
                    "limit": max(1, min(limit, 25)),
                },
            )
            if resp.status_code >= 400:
                raise ValueError(f"Apple Music search failed: {resp.status_code} {resp.text[:200]}")
            data = resp.json()
        songs = (((data.get("results") or {}).get("songs") or {}).get("data")) or []
        out: list[AppleTrack] = []
        for raw in songs:
            attrs = raw.get("attributes") or {}
            sid = str(raw.get("id") or "")
            out.append(
                AppleTrack(
                    id=sid,
                    uri=f"apple:song:{sid}",
                    title=attrs.get("name") or "Song",
                    artist=attrs.get("artistName") or "Unknown",
                    duration_ms=attrs.get("durationInMillis"),
                    album=attrs.get("albumName"),
                )
            )
        return out

    def play_limits_message(self) -> str:
        if self.airplay_source():
            return (
                "Apple play: capturing AirPlay/shairport source "
                f"{self.airplay_source()!r} into musync_feeder. "
                "Start playback on an Apple device targeting this receiver."
            )
        return (
            "Apple Music cannot fully play on Linux hub PCM yet. "
            "Options: (1) AirPlay to shairport-sync and set MUSYNC_APPLE_AIRPLAY_SOURCE, "
            "(2) use Spotify for real hub playback, (3) keep Local files. "
            "See docs/spotify-apple.md."
        )
