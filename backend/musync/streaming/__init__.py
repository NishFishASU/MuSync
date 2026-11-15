"""Hub streaming providers (Spotify Connect + best-effort Apple Music)."""

from __future__ import annotations

from .service import StreamingService, get_streaming_service

__all__ = ["StreamingService", "get_streaming_service"]
