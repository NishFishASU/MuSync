"""In-memory Wi-Fi backend for laptop / cloud (no NetworkManager)."""

from __future__ import annotations

from ..models import WifiNetwork, WifiStatus
from .base import WifiBackend

# Fake LAN address so SPP docs / UI can show something useful in mock mode.
_MOCK_IP = "192.168.43.10"


class MockWifiBackend(WifiBackend):
    """Dev stand-in: stores SSIDs in memory; never talks to real radios."""

    def __init__(self) -> None:
        # password kept only in process memory; never returned by the API.
        self._secrets: dict[str, str | None] = {
            "MuSync-Dev": "dev-only-secret",
            "PhoneHotspot": None,
        }
        self._active: str | None = "MuSync-Dev"

    def status(self) -> WifiStatus:
        if self._active:
            return WifiStatus(
                backend="mock",
                connected=True,
                ssid=self._active,
                ips=[_MOCK_IP],
                interface="wlan0",
                detail="Mock Wi-Fi — laptop/dev; no real radio",
            )
        return WifiStatus(
            backend="mock",
            connected=False,
            ssid=None,
            ips=[],
            interface="wlan0",
            detail="Mock Wi-Fi — not associated",
        )

    def list_networks(self) -> list[WifiNetwork]:
        return [
            WifiNetwork(
                ssid=ssid,
                saved=True,
                active=ssid == self._active,
                has_password=bool(secret),
            )
            for ssid, secret in sorted(self._secrets.items())
        ]

    def save_network(self, ssid: str, password: str | None = None) -> WifiNetwork:
        ssid = _normalize_ssid(ssid)
        if password is not None and password == "":
            password = None
        self._secrets[ssid] = password
        return WifiNetwork(
            ssid=ssid,
            saved=True,
            active=ssid == self._active,
            has_password=bool(password),
        )

    def connect(self, ssid: str, password: str | None = None) -> WifiStatus:
        ssid = _normalize_ssid(ssid)
        if password is not None and password == "":
            password = None
        if ssid not in self._secrets and password is None:
            raise ValueError(f"No saved profile for {ssid!r}; provide a password")
        if password is not None:
            self._secrets[ssid] = password
        elif ssid not in self._secrets:
            self._secrets[ssid] = None
        self._active = ssid
        return self.status()

    def forget_network(self, ssid: str) -> None:
        ssid = _normalize_ssid(ssid)
        if ssid not in self._secrets:
            raise KeyError(ssid)
        del self._secrets[ssid]
        if self._active == ssid:
            self._active = None


def _normalize_ssid(ssid: str) -> str:
    cleaned = (ssid or "").strip()
    if not cleaned:
        raise ValueError("SSID must be non-empty")
    if len(cleaned) > 32:
        raise ValueError("SSID must be at most 32 characters")
    return cleaned
