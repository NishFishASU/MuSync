"""Audio backend implementations (mock for cloud; PipeWire fan-out for Pi)."""

from __future__ import annotations

import os

from .base import AudioBackend
from .mock import MockAudioBackend
from .pipewire import PipeWireBackend


def create_backend() -> AudioBackend:
    """Select backend via MUSYNC_BACKEND=mock|pipewire (default: mock)."""
    name = os.environ.get("MUSYNC_BACKEND", "mock").strip().lower()
    if name in {"pipewire", "pw", "pi"}:
        return PipeWireBackend()
    if name in {"mock", "dev", "cloud"}:
        return MockAudioBackend()
    raise ValueError(
        f"Unknown MUSYNC_BACKEND={name!r}; use 'mock' or 'pipewire'"
    )


__all__ = [
    "AudioBackend",
    "MockAudioBackend",
    "PipeWireBackend",
    "create_backend",
]
