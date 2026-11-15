"""Unified streaming facade used by API + audio backends."""

from __future__ import annotations

import logging
import os
import threading
from typing import Any, Optional

from ..models import (
    StreamingProviderStatus,
    StreamingSearchHit,
    StreamingSearchResponse,
    StreamingStatus,
    SourceType,
)
from .apple import AppleMusicClient
from .spotify import SpotifyClient

log = logging.getLogger(__name__)

_service: Optional["StreamingService"] = None
_service_lock = threading.Lock()


def streaming_mock_enabled() -> bool:
    """When set, mock backend treats authenticated streaming tracks as playable (software clock)."""
    return (os.environ.get("MUSYNC_STREAMING_MOCK") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


class StreamingService:
    def __init__(self) -> None:
        self.spotify = SpotifyClient()
        self.apple = AppleMusicClient()

    def status(self) -> StreamingStatus:
        sp = self.spotify.status()
        ap = self.apple.status()
        return StreamingStatus(
            spotify=StreamingProviderStatus.model_validate(sp),
            apple=StreamingProviderStatus.model_validate(ap),
            mock_playback=streaming_mock_enabled(),
        )

    def spotify_ready(self) -> bool:
        return self.spotify.authenticated()

    def apple_ready(self) -> bool:
        return self.apple.authenticated()

    def spotify_playable(self) -> bool:
        """Hub can attempt Spotify play (auth). PipeWire still needs librespot for PCM."""
        return self.spotify.authenticated()

    def apple_playable(self) -> bool:
        """Playable when AirPlay capture source is configured (best-effort)."""
        return bool(self.apple.authenticated() and self.apple.airplay_source())

    def is_hub_stream_playable(self, source_type: SourceType) -> bool:
        if source_type == SourceType.SPOTIFY:
            return self.spotify_playable()
        if source_type == SourceType.APPLE:
            return self.apple_playable() or (
                streaming_mock_enabled() and self.apple_ready()
            )
        return False

    def search(self, provider: str, query: str, *, limit: int = 15) -> StreamingSearchResponse:
        p = (provider or "").strip().lower()
        if p == "spotify":
            tracks = self.spotify.search_tracks(query, limit=limit)
            hits = [
                StreamingSearchHit(
                    provider="spotify",
                    id=t.id,
                    uri=t.uri,
                    title=t.title,
                    artist=t.artist,
                    duration_ms=t.duration_ms,
                    album=t.album,
                    cover_url=t.cover_url,
                )
                for t in tracks
            ]
            return StreamingSearchResponse(provider="spotify", query=query, hits=hits)
        if p in {"apple", "apple_music", "music"}:
            tracks = self.apple.search_tracks(query, limit=limit)
            hits = [
                StreamingSearchHit(
                    provider="apple",
                    id=t.id,
                    uri=t.uri,
                    title=t.title,
                    artist=t.artist,
                    duration_ms=t.duration_ms,
                    album=t.album,
                    cover_url=t.cover_url,
                )
                for t in tracks
            ]
            return StreamingSearchResponse(provider="apple", query=query, hits=hits)
        raise ValueError(f"Unknown streaming provider: {provider!r}")

    def play_spotify(self, uri: str, *, feeder: str) -> dict[str, Any]:
        return self.spotify.play_uri(uri, feeder=feeder)

    def stop_spotify(self) -> None:
        try:
            self.spotify.pause()
        except Exception as exc:  # noqa: BLE001
            log.debug("spotify pause: %s", exc)
        self.spotify.stop_librespot()

    def ensure_spotify_player(self, *, feeder: str) -> Optional[str]:
        return self.spotify.ensure_librespot(feeder=feeder)

    def apple_airplay_source(self) -> Optional[str]:
        return self.apple.airplay_source()

    def apple_play_note(self) -> str:
        return self.apple.play_limits_message()


def get_streaming_service() -> StreamingService:
    global _service
    with _service_lock:
        if _service is None:
            _service = StreamingService()
        return _service


def reset_streaming_service() -> None:
    """Test helper — drop singleton."""
    global _service
    with _service_lock:
        if _service is not None:
            try:
                _service.spotify.stop_librespot()
            except Exception:  # noqa: BLE001
                pass
        _service = None
