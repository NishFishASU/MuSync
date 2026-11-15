"""In-memory MockAudioBackend for cloud/dev — no PipeWire or BT hardware."""

from __future__ import annotations

import threading
import time
from typing import Optional

from ..config import (
    PLACEHOLDER_SOURCES,
    clamp_delay_ms,
    clamp_volume,
    discover_local_sources,
    load_devices,
    save_devices,
)
from ..models import (
    Device,
    DevicePatch,
    Health,
    Source,
    SourceType,
    Transport,
    TransportPut,
    TransportState,
)
from .base import AudioBackend


class MockAudioBackend(AudioBackend):
    """Fake ≤6 sinks with delay/volume/mute/enabled + local playback clock."""

    name = "mock"

    def __init__(self, devices: Optional[list[Device]] = None) -> None:
        self._lock = threading.RLock()
        self._devices: dict[str, Device] = {
            d.id: d.model_copy(deep=True)
            for d in (devices if devices is not None else load_devices())
        }
        if len(self._devices) > 6:
            raise ValueError("Mock backend supports at most 6 sinks")

        self._sources = self._build_sources()
        active = next((s for s in self._sources if s.available), None)
        self._transport = Transport(
            state=TransportState.STOPPED,
            position_ms=0,
            duration_ms=(active.duration_ms or 0) if active else 0,
            source_id=active.id if active else None,
            title=active.title if active else None,
        )
        self._play_started_wall: Optional[float] = None
        self._play_started_pos: int = 0

    def _build_sources(self) -> list[Source]:
        local = discover_local_sources()
        if not local:
            local = [
                Source(
                    id="local:demo/sample-tone",
                    title="Sample Tone (demo)",
                    type=SourceType.LOCAL,
                    path=None,
                    available=True,
                    duration_ms=180_000,
                    note="Virtual demo track — drop files under media/ for real paths",
                )
            ]
        return local + list(PLACEHOLDER_SOURCES)

    def _source_by_id(self, source_id: str) -> Source:
        for src in self._sources:
            if src.id == source_id:
                return src
        raise KeyError(f"Unknown source: {source_id}")

    def _tick_position(self) -> None:
        """Advance position while playing (monotonic wall clock)."""
        if self._transport.state != TransportState.PLAYING:
            return
        if self._play_started_wall is None:
            return
        elapsed = int((time.monotonic() - self._play_started_wall) * 1000)
        pos = self._play_started_pos + elapsed
        duration = self._transport.duration_ms or 0
        if duration and pos >= duration:
            self._transport.position_ms = duration
            self._transport.state = TransportState.STOPPED
            self._play_started_wall = None
            self._play_started_pos = 0
        else:
            self._transport.position_ms = pos

    def _persist(self) -> None:
        try:
            save_devices(list(self._devices.values()))
        except OSError:
            # Cloud/dev may not have a writable config path; ignore.
            pass

    def list_devices(self) -> list[Device]:
        with self._lock:
            return [d.model_copy(deep=True) for d in self._devices.values()]

    def get_device(self, device_id: str) -> Device:
        with self._lock:
            if device_id not in self._devices:
                raise KeyError(f"Unknown device: {device_id}")
            return self._devices[device_id].model_copy(deep=True)

    def patch_device(self, device_id: str, patch: DevicePatch) -> Device:
        with self._lock:
            if device_id not in self._devices:
                raise KeyError(f"Unknown device: {device_id}")
            device = self._devices[device_id]
            data = device.model_dump()
            updates = patch.model_dump(exclude_unset=True)
            if "delay_ms" in updates and updates["delay_ms"] is not None:
                updates["delay_ms"] = clamp_delay_ms(updates["delay_ms"])
            if "volume" in updates and updates["volume"] is not None:
                updates["volume"] = clamp_volume(updates["volume"])
            data.update(updates)
            updated = Device.model_validate(data)
            self._devices[device_id] = updated
            self._persist()
            return updated.model_copy(deep=True)

    def get_transport(self) -> Transport:
        with self._lock:
            self._tick_position()
            return self._transport.model_copy(deep=True)

    def set_transport(self, body: TransportPut) -> Transport:
        with self._lock:
            self._tick_position()
            if body.source_id is not None:
                self._apply_source(body.source_id, reset_position=False)

            if body.position_ms is not None:
                duration = self._transport.duration_ms or 0
                pos = max(0, body.position_ms)
                if duration:
                    pos = min(pos, duration)
                self._transport.position_ms = pos
                if self._transport.state == TransportState.PLAYING:
                    self._play_started_wall = time.monotonic()
                    self._play_started_pos = pos

            if body.state is not None:
                self._apply_state(body.state)

            return self._transport.model_copy(deep=True)

    def _apply_state(self, state: TransportState) -> None:
        current = self._transport.state
        if state == TransportState.PLAYING:
            if current == TransportState.PLAYING:
                return
            self._play_started_wall = time.monotonic()
            self._play_started_pos = self._transport.position_ms
            self._transport.state = TransportState.PLAYING
        elif state == TransportState.PAUSED:
            self._tick_position()
            self._play_started_wall = None
            self._transport.state = TransportState.PAUSED
        elif state == TransportState.STOPPED:
            self._play_started_wall = None
            self._play_started_pos = 0
            self._transport.position_ms = 0
            self._transport.state = TransportState.STOPPED

    def _apply_source(self, source_id: str, *, reset_position: bool) -> None:
        src = self._source_by_id(source_id)
        if not src.available:
            raise ValueError(f"Source not available in MVP: {source_id}")
        self._transport.source_id = src.id
        self._transport.title = src.title
        self._transport.duration_ms = src.duration_ms or 0
        if reset_position:
            self._transport.position_ms = 0
            self._play_started_wall = None
            self._play_started_pos = 0
            if self._transport.state == TransportState.PLAYING:
                self._play_started_wall = time.monotonic()

    def list_sources(self) -> list[Source]:
        with self._lock:
            # Refresh local discovery each call so dropped files appear.
            placeholders = [s for s in self._sources if s.type != SourceType.LOCAL]
            local = discover_local_sources()
            if not local:
                local = [s for s in self._sources if s.type == SourceType.LOCAL]
            self._sources = local + placeholders
            return [s.model_copy(deep=True) for s in self._sources]

    def set_active_source(self, source_id: str) -> Transport:
        with self._lock:
            was_playing = self._transport.state == TransportState.PLAYING
            self._apply_source(source_id, reset_position=True)
            self._transport.state = (
                TransportState.PLAYING if was_playing else TransportState.STOPPED
            )
            if was_playing:
                self._play_started_wall = time.monotonic()
                self._play_started_pos = 0
            return self._transport.model_copy(deep=True)

    def health(self) -> Health:
        with self._lock:
            devices = list(self._devices.values())
            hci_map = {d.hci: d.mac for d in devices if d.hci}
            return Health(
                ok=True,
                backend="mock",
                pipewire_up=False,
                sink_count=len(devices),
                enabled_count=sum(1 for d in devices if d.enabled),
                hci_map=hci_map,
                detail="MockAudioBackend — simulated sinks; no PipeWire",
            )
