"""Phone/PC input hosts (≤4) — capture paths are stubs until Pi wiring."""

from __future__ import annotations

import itertools
import threading
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
    CapturePath.BT_A2DP: "Stub — BT A2DP source→Pi not wired (dongle contention risk)",
    CapturePath.USB: "Stub — USB audio interface capture not wired",
    CapturePath.NETWORK: "Stub — network / AirPlay / upload path not wired",
}


def default_demo_hosts() -> list[InputHost]:
    """Two example hosts so the UI is usable without registration ceremony."""
    return [
        InputHost(
            id="input-1",
            name="Nishanth's iPhone",
            kind=HostKind.PHONE,
            capture_path=CapturePath.NETWORK,
            armed=True,
            connected=False,
            endpoint="airplay:stub",
            note=CAPTURE_NOTES[CapturePath.NETWORK],
        ),
        InputHost(
            id="input-2",
            name="Laptop (USB)",
            kind=HostKind.PC,
            capture_path=CapturePath.USB,
            armed=False,
            connected=False,
            endpoint="usb:audio:stub",
            note=CAPTURE_NOTES[CapturePath.USB],
        ),
    ]


class InputRegistry:
    """In-memory ≤4 phone/PC hosts. Persistence can come later."""

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
                note=body.note or CAPTURE_NOTES[body.capture_path],
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
            if "capture_path" in updates and "note" not in updates:
                path = CapturePath(data["capture_path"])
                data["note"] = CAPTURE_NOTES[path]
            updated = InputHost.model_validate(data)
            self._hosts[host_id] = updated
            return updated.model_copy(deep=True)

    def remove_host(self, host_id: str) -> None:
        with self._lock:
            if host_id not in self._hosts:
                raise KeyError(f"Unknown input host: {host_id}")
            del self._hosts[host_id]

    def catalog_sources(self) -> list[Source]:
        """Per-host contribution stubs: local / Spotify / Apple / live input."""
        with self._lock:
            out: list[Source] = []
            for host in self._hosts.values():
                path_note = CAPTURE_NOTES[host.capture_path]
                if host.offers_local:
                    out.append(
                        Source(
                            id=f"host:{host.id}:local",
                            title=f"{host.name} · local files",
                            type=SourceType.LOCAL,
                            locator=f"host:{host.id}:local",
                            available=False,
                            input_id=host.id,
                            note=f"Stub — upload/share from {host.kind.value}; {path_note}",
                        )
                    )
                if host.offers_spotify:
                    out.append(
                        Source(
                            id=f"host:{host.id}:spotify",
                            title=f"{host.name} · Spotify",
                            type=SourceType.SPOTIFY,
                            locator=f"spotify:host:{host.id}",
                            available=False,
                            input_id=host.id,
                            note=f"Stub — that device's Spotify session; {path_note}",
                        )
                    )
                if host.offers_apple:
                    out.append(
                        Source(
                            id=f"host:{host.id}:apple",
                            title=f"{host.name} · Apple Music",
                            type=SourceType.APPLE,
                            locator=f"apple:host:{host.id}",
                            available=False,
                            input_id=host.id,
                            note=f"Stub — that device's Apple Music session; {path_note}",
                        )
                    )
                if host.offers_live_input:
                    out.append(
                        Source(
                            id=f"host:{host.id}:input",
                            title=f"{host.name} · live ({host.capture_path.value})",
                            type=SourceType.INPUT,
                            locator=f"input:{host.id}:{host.capture_path.value}",
                            available=False,
                            input_id=host.id,
                            note=path_note,
                        )
                    )
            return out
