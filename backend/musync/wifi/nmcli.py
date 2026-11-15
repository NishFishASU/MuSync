"""NetworkManager (nmcli) Wi-Fi backend for Raspberry Pi."""

from __future__ import annotations

import logging
import shutil
import subprocess
from typing import Sequence

from ..models import WifiNetwork, WifiStatus
from .base import WifiBackend

log = logging.getLogger(__name__)

# Fields we never want in logs or exception text after a failed nmcli call.
_REDACT_PLACEHOLDER = "***"


class NmcliWifiBackend(WifiBackend):
    """Join / pre-save SSIDs via ``nmcli`` (NetworkManager)."""

    def __init__(self, *, interface: str | None = None) -> None:
        if not self.available():
            raise RuntimeError("nmcli not found — install NetworkManager or use mock Wi-Fi")
        self._iface_override = interface

    @staticmethod
    def available() -> bool:
        return shutil.which("nmcli") is not None

    def status(self) -> WifiStatus:
        iface = self._wifi_interface()
        ssid = self._active_ssid(iface)
        ips = self._ipv4_addrs(iface) if iface else []
        connected = bool(ssid) or bool(ips)
        detail = ""
        if not iface:
            detail = "No Wi-Fi interface found"
        elif not connected:
            detail = f"Interface {iface} not associated"
        else:
            detail = f"Associated on {iface}"
        return WifiStatus(
            backend="nmcli",
            connected=connected,
            ssid=ssid,
            ips=ips,
            interface=iface,
            detail=detail,
        )

    def list_networks(self) -> list[WifiNetwork]:
        active = self._active_ssid(self._wifi_interface())
        rows = self._run(
            ["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show"],
            secrets=[],
        )
        networks: list[WifiNetwork] = []
        seen: set[str] = set()
        for line in rows.splitlines():
            if not line.strip():
                continue
            # NAME may contain ':' — TYPE is last field for 802-11-wireless.
            parts = line.split(":")
            if len(parts) < 2:
                continue
            conn_type = parts[-1]
            name = ":".join(parts[:-1])
            if conn_type != "802-11-wireless":
                continue
            if name in seen:
                continue
            seen.add(name)
            networks.append(
                WifiNetwork(
                    ssid=name,
                    saved=True,
                    active=name == active,
                    has_password=True,  # NM profiles typically store a secret
                )
            )
        return sorted(networks, key=lambda n: n.ssid.lower())

    def save_network(self, ssid: str, password: str | None = None) -> WifiNetwork:
        ssid = _normalize_ssid(ssid)
        secrets = _secret_list(password)
        existing = {n.ssid for n in self.list_networks()}
        if ssid in existing:
            self._modify_secret(ssid, password, secrets)
        else:
            self._add_profile(ssid, password, secrets)
        active = self._active_ssid(self._wifi_interface())
        return WifiNetwork(
            ssid=ssid,
            saved=True,
            active=ssid == active,
            has_password=bool(password),
        )

    def connect(self, ssid: str, password: str | None = None) -> WifiStatus:
        ssid = _normalize_ssid(ssid)
        secrets = _secret_list(password)
        saved = {n.ssid for n in self.list_networks()}
        if ssid in saved:
            if password is not None and password != "":
                self._modify_secret(ssid, password, secrets)
            self._run(
                ["nmcli", "connection", "up", ssid],
                secrets=secrets,
                timeout=60,
            )
        else:
            if password is None or password == "":
                # Open / already-known AP via device wifi connect without psk
                self._run(
                    ["nmcli", "device", "wifi", "connect", ssid],
                    secrets=secrets,
                    timeout=60,
                )
            else:
                self._run(
                    [
                        "nmcli",
                        "device",
                        "wifi",
                        "connect",
                        ssid,
                        "password",
                        password,
                    ],
                    secrets=secrets,
                    timeout=60,
                )
        return self.status()

    def forget_network(self, ssid: str) -> None:
        ssid = _normalize_ssid(ssid)
        saved = {n.ssid for n in self.list_networks()}
        if ssid not in saved:
            raise KeyError(ssid)
        self._run(["nmcli", "connection", "delete", ssid], secrets=[])

    # --- helpers ---------------------------------------------------------

    def _add_profile(
        self, ssid: str, password: str | None, secrets: Sequence[str]
    ) -> None:
        iface = self._wifi_interface() or "*"
        cmd = [
            "nmcli",
            "connection",
            "add",
            "type",
            "wifi",
            "con-name",
            ssid,
            "ifname",
            iface,
            "ssid",
            ssid,
        ]
        if password:
            cmd.extend(
                [
                    "wifi-sec.key-mgmt",
                    "wpa-psk",
                    "wifi-sec.psk",
                    password,
                ]
            )
        self._run(cmd, secrets=secrets)

    def _modify_secret(
        self, ssid: str, password: str | None, secrets: Sequence[str]
    ) -> None:
        if password is None or password == "":
            return
        self._run(
            [
                "nmcli",
                "connection",
                "modify",
                ssid,
                "wifi-sec.key-mgmt",
                "wpa-psk",
                "wifi-sec.psk",
                password,
            ],
            secrets=secrets,
        )

    def _wifi_interface(self) -> str | None:
        if self._iface_override:
            return self._iface_override
        out = self._run(
            ["nmcli", "-t", "-f", "DEVICE,TYPE,STATE", "device", "status"],
            secrets=[],
        )
        wifi_candidates: list[tuple[str, str]] = []
        for line in out.splitlines():
            parts = line.split(":")
            if len(parts) < 3:
                continue
            device, dev_type, state = parts[0], parts[1], parts[2]
            if dev_type != "wifi":
                continue
            wifi_candidates.append((device, state))
        if not wifi_candidates:
            return None
        for device, state in wifi_candidates:
            if state == "connected":
                return device
        return wifi_candidates[0][0]

    def _active_ssid(self, iface: str | None) -> str | None:
        if not iface:
            return None
        try:
            out = self._run(
                ["nmcli", "-t", "-f", "GENERAL.CONNECTION", "device", "show", iface],
                secrets=[],
            )
        except RuntimeError:
            return None
        for line in out.splitlines():
            if line.startswith("GENERAL.CONNECTION:"):
                name = line.split(":", 1)[1].strip()
                if name and name != "--":
                    return name
        # Fallback: active wifi from device wifi list
        try:
            listed = self._run(
                ["nmcli", "-t", "-f", "ACTIVE,SSID", "device", "wifi", "list"],
                secrets=[],
            )
        except RuntimeError:
            return None
        for line in listed.splitlines():
            if not line.startswith("yes:"):
                continue
            ssid = line.split(":", 1)[1]
            return ssid or None
        return None

    def _ipv4_addrs(self, iface: str | None) -> list[str]:
        if not iface:
            return []
        try:
            out = self._run(
                ["nmcli", "-t", "-f", "IP4.ADDRESS", "device", "show", iface],
                secrets=[],
            )
        except RuntimeError:
            return []
        addrs: list[str] = []
        for line in out.splitlines():
            if "IP4.ADDRESS" not in line or ":" not in line:
                continue
            raw = line.split(":", 1)[1].strip()
            # nmcli returns CIDR e.g. 192.168.43.10/24
            ip = raw.split("/", 1)[0].strip()
            if ip:
                addrs.append(ip)
        return addrs

    def _run(
        self,
        cmd: list[str],
        *,
        secrets: Sequence[str],
        timeout: int = 30,
    ) -> str:
        """Run nmcli; never log argv/stderr containing Wi-Fi passwords."""
        safe_cmd = [_redact_text(part, secrets) for part in cmd]
        log.debug("nmcli: %s", " ".join(safe_cmd))
        try:
            proc = subprocess.run(
                cmd,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"nmcli timed out: {' '.join(safe_cmd)}") from exc
        if proc.returncode != 0:
            err = _redact_text((proc.stderr or proc.stdout or "").strip(), secrets)
            raise RuntimeError(err or f"nmcli failed ({proc.returncode})")
        return proc.stdout or ""


def _secret_list(password: str | None) -> list[str]:
    if password:
        return [password]
    return []


def _redact_text(text: str, secrets: Sequence[str]) -> str:
    out = text
    for secret in secrets:
        if secret:
            out = out.replace(secret, _REDACT_PLACEHOLDER)
    return out


def _normalize_ssid(ssid: str) -> str:
    cleaned = (ssid or "").strip()
    if not cleaned:
        raise ValueError("SSID must be non-empty")
    if len(cleaned) > 32:
        raise ValueError("SSID must be at most 32 characters")
    return cleaned
