"""Phone/PC/HDMI/AirPlay input hosts (≤4) — BT A2DP / USB / HDMI / AirPlay / network → feeder."""

from __future__ import annotations

import itertools
import threading
from pathlib import Path
from typing import Optional

from ..models import (
    MAX_INPUT_HOSTS,
    CapturePath,
    HostKind,
    InputHost,
    InputHostCreate,
    InputHostPatch,
    Source,
    SourceType,
)

CAPTURE_NOTES: dict[CapturePath, str] = {
    CapturePath.BT_A2DP: "BT A2DP source → PipeWire bluez_input → musync_feeder",
    CapturePath.USB: "USB-A sound card / interface → ALSA → musync_feeder "
    "(Pi 5 USB-C PD is power-only — not audio)",
    CapturePath.HDMI: "HDMI / capture card → ALSA → PipeWire → musync_feeder "
    "(audio only; picture stays on the capture display path)",
    CapturePath.AIRPLAY: "AirPlay receive (shairport-sync) → PipeWire → musync_feeder",
    CapturePath.NETWORK: "Network / uploaded file → musync_feeder",
}

WAITING_CAPTURE_NOTES: dict[CapturePath, str] = {
    CapturePath.BT_A2DP: "Unavailable — waiting for bluez_input; pair phone as A2DP source",
    CapturePath.USB: "Unavailable — waiting for USB-A sound card (not USB-C PD). "
    "Plug an interface, then Refresh inputs",
    CapturePath.HDMI: "Unavailable — waiting for HDMI / capture card. "
    "Plug a capture dongle, then Refresh inputs",
    CapturePath.AIRPLAY: "Unavailable — waiting for shairport-sync AirPlay source. "
    "Install receiver, AirPlay to the Pi, then Refresh inputs "
    "(docs/airplay-receive.md)",
    CapturePath.NETWORK: "Unavailable — upload a file or set endpoint to play into the feeder",
}

# Back-compat alias
STUB_CAPTURE_NOTES = WAITING_CAPTURE_NOTES


def host_live_note(host: InputHost) -> str:
    if host.connected and host.endpoint:
        return f"Live · {CAPTURE_NOTES[host.capture_path]} · {host.endpoint}"
    return host.note or WAITING_CAPTURE_NOTES[host.capture_path]


def host_is_live(host: InputHost) -> bool:
    """True when this host can feed PCM into the fan-out right now."""
    if not host.offers_live_input:
        return False
    if not host.connected:
        return False
    if not host.endpoint:
        return False
    if host.capture_path == CapturePath.NETWORK:
        # Network path is live when endpoint is an existing file (upload).
        return Path(host.endpoint).is_file()
    # BT A2DP / USB / HDMI / AirPlay: connected + PipeWire source endpoint.
    return True


def default_demo_hosts() -> list[InputHost]:
    """Demo hosts — S21 FE BT + USB PC + HDMI + AirPlay; mock marks them live."""
    return [
        InputHost(
            id="input-1",
            name="Galaxy S21 FE",
            kind=HostKind.PHONE,
            capture_path=CapturePath.BT_A2DP,
            armed=True,
            connected=False,
            endpoint=None,
            note=WAITING_CAPTURE_NOTES[CapturePath.BT_A2DP],
        ),
        InputHost(
            id="input-2",
            name="Laptop (USB)",
            kind=HostKind.PC,
            capture_path=CapturePath.USB,
            armed=False,
            connected=False,
            endpoint=None,
            note=WAITING_CAPTURE_NOTES[CapturePath.USB],
        ),
        InputHost(
            id="input-3",
            name="HDMI Capture",
            kind=HostKind.OTHER,
            capture_path=CapturePath.HDMI,
            armed=False,
            connected=False,
            endpoint=None,
            note=WAITING_CAPTURE_NOTES[CapturePath.HDMI],
        ),
        InputHost(
            id="input-4",
            name="AirPlay Receive",
            kind=HostKind.OTHER,
            capture_path=CapturePath.AIRPLAY,
            armed=False,
            connected=False,
            endpoint=None,
            note=WAITING_CAPTURE_NOTES[CapturePath.AIRPLAY],
        ),
    ]


class InputRegistry:
    """In-memory ≤4 phone/PC/HDMI hosts. Persistence can come later."""

    def __init__(
        self,
        hosts: Optional[list[InputHost]] = None,
        *,
        lock: Optional[threading.RLock] = None,
    ) -> None:
        self._lock = lock or threading.RLock()
        self._seq = itertools.count(1)
        seed = hosts if hosts is not None else default_demo_hosts()
        if len(seed) > MAX_INPUT_HOSTS:
            raise ValueError(f"At most {MAX_INPUT_HOSTS} input hosts")
        self._hosts: dict[str, InputHost] = {h.id: h.model_copy(deep=True) for h in seed}
        max_n = 0
        for host_id in self._hosts:
            if host_id.startswith("input-"):
                try:
                    max_n = max(max_n, int(host_id.split("-", 1)[1]))
                except ValueError:
                    pass
        self._seq = itertools.count(max_n + 1)

    def _next_id(self) -> str:
        while True:
            candidate = f"input-{next(self._seq)}"
            if candidate not in self._hosts:
                return candidate

    def list_hosts(self) -> list[InputHost]:
        with self._lock:
            return [h.model_copy(deep=True) for h in self._hosts.values()]

    def get_host(self, host_id: str) -> InputHost:
        with self._lock:
            if host_id not in self._hosts:
                raise KeyError(f"Unknown input host: {host_id}")
            return self._hosts[host_id].model_copy(deep=True)

    def add_host(self, body: InputHostCreate) -> InputHost:
        with self._lock:
            if len(self._hosts) >= MAX_INPUT_HOSTS:
                raise ValueError(f"At most {MAX_INPUT_HOSTS} phone/PC input hosts")
            host = InputHost(
                id=self._next_id(),
                name=body.name.strip() or "Phone / PC",
                kind=body.kind,
                capture_path=body.capture_path,
                armed=body.armed,
                connected=body.connected,
                endpoint=body.endpoint,
                offers_local=body.offers_local,
                offers_spotify=body.offers_spotify,
                offers_apple=body.offers_apple,
                offers_live_input=body.offers_live_input,
                note=body.note
                or (
                    CAPTURE_NOTES[body.capture_path]
                    if body.connected and body.endpoint
                    else WAITING_CAPTURE_NOTES[body.capture_path]
                ),
            )
            self._hosts[host.id] = host
            return host.model_copy(deep=True)

    def patch_host(self, host_id: str, patch: InputHostPatch) -> InputHost:
        with self._lock:
            if host_id not in self._hosts:
                raise KeyError(f"Unknown input host: {host_id}")
            data = self._hosts[host_id].model_dump()
            updates = patch.model_dump(exclude_unset=True)
            data.update(updates)
            if "note" not in updates:
                path = CapturePath(data["capture_path"])
                connected = bool(data.get("connected"))
                endpoint = data.get("endpoint")
                data["note"] = (
                    CAPTURE_NOTES[path]
                    if connected and endpoint
                    else WAITING_CAPTURE_NOTES[path]
                )
            updated = InputHost.model_validate(data)
            self._hosts[host_id] = updated
            return updated.model_copy(deep=True)

    def remove_host(self, host_id: str) -> None:
        with self._lock:
            if host_id not in self._hosts:
                raise KeyError(f"Unknown input host: {host_id}")
            del self._hosts[host_id]

    def catalog_sources(self) -> list[Source]:
        """Per-host hub paths: live BT A2DP / USB / HDMI / upload only (no fake stubs).

        On-device Spotify/Apple/local sessions are not hub PCM — use Streaming
        (OAuth + librespot) or +Device from a live input instead.
        """
        with self._lock:
            out: list[Source] = []
            for host in self._hosts.values():
                path_note = host_live_note(host)
                if not host.offers_live_input:
                    continue
                live = host_is_live(host)
                out.append(
                    Source(
                        id=f"host:{host.id}:input",
                        title=f"{host.name} · live ({host.capture_path.value})",
                        type=SourceType.INPUT,
                        locator=f"input:{host.id}:{host.capture_path.value}",
                        available=live,
                        input_id=host.id,
                        note=None if live else path_note,
                        artist=host.name if live else None,
                        album=CAPTURE_NOTES[host.capture_path] if live else None,
                    )
                )
            return out
