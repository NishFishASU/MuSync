"""Wi-Fi join backends — nmcli on Pi, mock on laptop."""

from __future__ import annotations

import os

from .base import WifiBackend
from .mock import MockWifiBackend
from .nmcli import NmcliWifiBackend


def create_wifi_backend() -> WifiBackend:
    """
    Select Wi-Fi backend via MUSYNC_WIFI_BACKEND=mock|nmcli|auto (default auto).

    Auto: mock when MUSYNC_BACKEND is mock/dev/cloud, else nmcli if available,
    else mock (so laptop pipewire dry-runs still work without NetworkManager).
    """
    name = os.environ.get("MUSYNC_WIFI_BACKEND", "auto").strip().lower()
    if name in {"mock", "dev", "cloud"}:
        return MockWifiBackend()
    if name == "nmcli":
        return NmcliWifiBackend()
    if name in {"", "auto"}:
        audio = os.environ.get("MUSYNC_BACKEND", "mock").strip().lower()
        if audio in {"mock", "dev", "cloud"}:
            return MockWifiBackend()
        if NmcliWifiBackend.available():
            return NmcliWifiBackend()
        return MockWifiBackend()
    raise ValueError(
        f"Unknown MUSYNC_WIFI_BACKEND={name!r}; use 'mock', 'nmcli', or 'auto'"
    )


__all__ = [
    "WifiBackend",
    "MockWifiBackend",
    "NmcliWifiBackend",
    "create_wifi_backend",
]
