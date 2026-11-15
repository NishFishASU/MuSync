"""Hub streaming providers (Spotify, Apple Music, YouTube)."""

from __future__ import annotations

from .service import StreamingService, get_streaming_service

__all__ = ["StreamingService", "get_streaming_service"]
