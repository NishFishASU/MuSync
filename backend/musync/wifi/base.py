"""Wi-Fi join backend interface (nmcli on Pi, mock on laptop)."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import WifiNetwork, WifiStatus


class WifiBackend(ABC):
    """Pre-save SSIDs and join phone hotspots without SSH."""

    @abstractmethod
    def status(self) -> WifiStatus:
        """Current association + IPv4 addresses (for SPP / UI)."""

    @abstractmethod
    def list_networks(self) -> list[WifiNetwork]:
        """Saved Wi-Fi profiles (never includes passwords)."""

    @abstractmethod
    def save_network(self, ssid: str, password: str | None = None) -> WifiNetwork:
        """Create or update a saved profile without necessarily activating it."""

    @abstractmethod
    def connect(self, ssid: str, password: str | None = None) -> WifiStatus:
        """Join SSID (uses saved secret when password omitted)."""

    @abstractmethod
    def forget_network(self, ssid: str) -> None:
        """Remove a saved profile."""
