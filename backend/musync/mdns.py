"""Advertise the hub UI as ``musync.local`` via mDNS (zeroconf).

Spotify OAuth **cannot** use ``musync.local`` or ``localhost`` as redirect
hosts — keep redirects on ``127.0.0.1`` (see ``docs/spotify-apple.md``).
This module is **UI discovery only**: phones/laptops on the LAN open
``http://musync.local:8080``. Soft-fails when zeroconf is missing or mDNS
sockets fail so the API still boots.
"""

from __future__ import annotations

import logging
import os
import socket
from dataclasses import dataclass, field
from typing import Any, Optional

log = logging.getLogger(__name__)

# Hostname label only — FQDN is musync.local (never "Mu.Sync" / "MuSync").
DEFAULT_HOSTNAME = "musync"
# Zeroconf service *instance* display name (not the .local host).
DEFAULT_INSTANCE = "MuSync"
HTTP_SERVICE = "_http._tcp.local."


def mdns_hostname_label() -> str:
    """Return the mDNS host label (default ``musync`` → ``musync.local``)."""
    raw = (os.environ.get("MUSYNC_MDNS_HOSTNAME") or DEFAULT_HOSTNAME).strip().lower()
    # Strip accidental .local suffix / dots — Spotify-forbidden Mu.Sync style.
    raw = raw.replace(".local", "").replace(".", "-").strip("-")
    if not raw or not raw.replace("-", "").isalnum():
        return DEFAULT_HOSTNAME
    return raw


def mdns_fqdn() -> str:
    """Fully-qualified mDNS name, e.g. ``musync.local``."""
    return f"{mdns_hostname_label()}.local"


def mdns_wanted() -> bool:
    """Whether the hub should try to advertise (env override).

    ``MUSYNC_MDNS=0|off|false|no`` disables. Default **on**.
    """
    raw = (os.environ.get("MUSYNC_MDNS") or "1").strip().lower()
    return raw not in {"0", "off", "false", "no", "disabled"}


def _local_ipv4s() -> list[bytes]:
    """Non-loopback IPv4 addresses as packed bytes for ServiceInfo."""
    found: list[bytes] = []
    seen: set[str] = set()
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = info[4][0]
            if ip.startswith("127.") or ip in seen:
                continue
            seen.add(ip)
            found.append(socket.inet_aton(ip))
    except OSError as exc:
        log.debug("getaddrinfo for mDNS: %s", exc)

    # Also probe the outbound interface (works when hostname has no A record).
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(("8.8.8.8", 80))
            ip = probe.getsockname()[0]
            if not ip.startswith("127.") and ip not in seen:
                seen.add(ip)
                found.append(socket.inet_aton(ip))
        finally:
            probe.close()
    except OSError as exc:
        log.debug("outbound IP probe for mDNS: %s", exc)

    if not found:
        # Last resort — still advertise so local mDNS clients can resolve.
        found.append(socket.inet_aton("127.0.0.1"))
    return found


def _instance_name() -> str:
    raw = (os.environ.get("MUSYNC_MDNS_NAME") or DEFAULT_INSTANCE).strip() or DEFAULT_INSTANCE
    # Service instance must end with the type.
    if raw.endswith(HTTP_SERVICE):
        return raw
    return f"{raw}.{HTTP_SERVICE}"


@dataclass
class MdnsStatus:
    """Public snapshot for ``GET /api/mdns``."""

    hostname: str = ""
    advertising: bool = False
    port: int = 8080
    addresses: list[str] = field(default_factory=list)
    detail: str = ""
    soft_warn: str = ""
    ui_only: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "hostname": self.hostname,
            "advertising": self.advertising,
            "port": self.port,
            "addresses": list(self.addresses),
            "detail": self.detail,
            "soft_warn": self.soft_warn,
            "ui_only": self.ui_only,
            "spotify_note": (
                "mDNS is for UI only — Spotify redirect must use 127.0.0.1 "
                "(not musync.local / localhost)"
            ),
        }


class MdnsAdvertiser:
    """Long-lived zeroconf registration for the hub HTTP UI."""

    def __init__(self) -> None:
        self._zc: Any = None
        self._info: Any = None
        self._status = MdnsStatus(hostname=mdns_fqdn())

    @property
    def status(self) -> MdnsStatus:
        return self._status

    def start(self, *, port: int) -> MdnsStatus:
        """Register ``musync.local`` → LAN IPs for ``_http._tcp``. Soft-fail OK."""
        self._status = MdnsStatus(hostname=mdns_fqdn(), port=int(port), ui_only=True)

        if not mdns_wanted():
            self._status.detail = "mDNS advertise disabled (MUSYNC_MDNS=0)"
            self._status.soft_warn = "mDNS off"
            return self._status

        try:
            from zeroconf import ServiceInfo, Zeroconf
        except ImportError:
            self._status.detail = "zeroconf missing"
            self._status.soft_warn = (
                "zeroconf missing — UI mDNS soft-fail (pip install zeroconf)"
            )
            log.warning(self._status.soft_warn)
            return self._status

        addrs = _local_ipv4s()
        self._status.addresses = [socket.inet_ntoa(a) for a in addrs]
        # Always musync.local (or sanitized MUSYNC_MDNS_HOSTNAME) — never Mu.Sync.
        server = f"{mdns_hostname_label()}.local."
        self._status.hostname = server.rstrip(".")

        try:
            info = ServiceInfo(
                HTTP_SERVICE,
                _instance_name(),
                addresses=addrs,
                port=int(port),
                properties={b"path": b"/", b"ui": b"1"},
                server=server,
            )
            zc = Zeroconf()
            zc.register_service(info)
            self._zc = zc
            self._info = info
            self._status.advertising = True
            self._status.detail = (
                f"Advertising {self._status.hostname}:{port} "
                f"({', '.join(self._status.addresses)}) — UI only"
            )
            log.info("mDNS: %s", self._status.detail)
        except Exception as exc:  # noqa: BLE001 — soft-fail advertise
            self._status.advertising = False
            self._status.detail = f"mDNS advertise error: {exc}"
            self._status.soft_warn = "mDNS soft-fail — check Wi‑Fi / avahi"
            log.warning("mDNS advertise soft-fail: %s", exc)
            self.stop()
        return self._status

    def stop(self) -> None:
        info, zc = self._info, self._zc
        self._info = None
        self._zc = None
        if zc is None:
            return
        try:
            if info is not None:
                zc.unregister_service(info)
        except Exception as exc:  # noqa: BLE001
            log.debug("mDNS unregister: %s", exc)
        try:
            zc.close()
        except Exception as exc:  # noqa: BLE001
            log.debug("mDNS close: %s", exc)
        if self._status.advertising:
            self._status.advertising = False
            self._status.detail = (self._status.detail or "mDNS") + " (stopped)"


_advertiser: Optional[MdnsAdvertiser] = None


def get_mdns_status() -> MdnsStatus:
    if _advertiser is None:
        return MdnsStatus(
            hostname=mdns_fqdn(),
            detail="mDNS not started",
            soft_warn="" if mdns_wanted() else "mDNS off",
        )
    return _advertiser.status


def start_hub_mdns(*, port: int) -> MdnsStatus:
    """Start (or restart) hub UI mDNS advertise. Soft-fail safe."""
    global _advertiser
    if _advertiser is not None:
        _advertiser.stop()
    adv = MdnsAdvertiser()
    status = adv.start(port=port)
    _advertiser = adv
    return status


def stop_hub_mdns() -> None:
    global _advertiser
    if _advertiser is not None:
        _advertiser.stop()
        _advertiser = None
