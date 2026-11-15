"""Legacy SourceType.APPLE helpers — MusicKit search removed.

Hub PCM for Apple devices is **AirPlay receive** only (shairport-sync →
PipeWire → musync_feeder). See ``airplay.py`` and docs/airplay-receive.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from .airplay import AirplayReceiver


@dataclass
class AppleTrack:
    """Unused catalog row shape kept for older call sites / tests."""

    id: str
    uri: str
    title: str
    artist: str
    duration_ms: Optional[int] = None
    album: Optional[str] = None
    cover_url: Optional[str] = None


class AppleMusicClient:
    """AirPlay-backed stand-in; no MusicKit developer token or catalog search."""

    def __init__(self) -> None:
        self._airplay = AirplayReceiver()

    def status(self) -> dict[str, Any]:
        """Map AirPlay status into the old apple provider shape (tests / health)."""
        raw = self._airplay.status()
        return {
            "provider": "apple",
            "configured": bool(raw.get("configured")),
            "authenticated": bool(raw.get("configured")),
            "user_token_set": False,
            "airplay_source": raw.get("source"),
            "playback_mode": "airplay_capture" if raw.get("source") else "unavailable",
            "limits": list(raw.get("setup") or []),
            "note": raw.get("note") or "",
        }

    def configured(self) -> bool:
        return bool(self._airplay.configured_source()) or self._airplay.status()[
            "shairport_available"
        ]

    def authenticated(self) -> bool:
        # No MusicKit auth — "ready" means AirPlay capture can be attempted.
        return self.configured()

    def airplay_source(self) -> Optional[str]:
        return self._airplay.configured_source()

    def search_tracks(self, query: str, *, limit: int = 15) -> list[AppleTrack]:
        raise ValueError(
            "Apple Music search removed — use AirPlay receive "
            "(Inputs → Refresh → live AirPlay host) or Spotify. "
            "See docs/airplay-receive.md"
        )

    def track_metadata(self, uri: str) -> Optional[AppleTrack]:
        return None

    def play_limits_message(self) -> str:
        return self._airplay.play_note(self.airplay_source())
