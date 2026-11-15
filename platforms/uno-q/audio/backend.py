"""Stub AudioBackend for Arduino Uno Q — not wired into the FastAPI app yet.

Port target: mirror ``backend.musync.audio.pipewire.PipeWireBackend`` against
Uno Q Debian PipeWire/BlueZ, respecting limits in ``docs/uno-q-hub.md``:

- A2DP speaker outs first; no HSP/HFP mic on onboard radio
- Headless WirePlumber seat-monitoring + linger required
- Soft-fail USB HCI beyond what the board proves
- Host packages only (not App Lab sandbox)

When ready, register behind ``MUSYNC_BACKEND=uno_q`` (or reuse ``pipewire``
with ``MUSYNC_PLATFORM=uno-q`` deploy defaults).
"""

from __future__ import annotations

from typing import Any


class UnoQAudioBackend:
    """Skeleton only — raises until the PipeWire port lands on hardware."""

    name = "uno_q"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._args = args
        self._kwargs = kwargs

    def _nyi(self, op: str) -> None:
        raise NotImplementedError(
            f"UnoQAudioBackend.{op} is a scaffold stub. "
            "Use MUSYNC_BACKEND=mock|pipewire until the Uno Q port is implemented. "
            "See platforms/uno-q/audio/README.md and docs/uno-q-hub.md."
        )

    def list_devices(self) -> list[Any]:
        self._nyi("list_devices")

    def get_device(self, device_id: str) -> Any:
        self._nyi("get_device")

    def patch_device(self, device_id: str, patch: Any) -> Any:
        self._nyi("patch_device")

    def get_transport(self) -> Any:
        self._nyi("get_transport")

    def set_transport(self, body: Any) -> Any:
        self._nyi("set_transport")

    def health(self) -> Any:
        self._nyi("health")
