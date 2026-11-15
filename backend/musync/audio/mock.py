"""In-memory MockAudioBackend for cloud/dev — no PipeWire or BT hardware."""

from __future__ import annotations

import threading
from typing import Optional

from ..config import clamp_delay_ms, clamp_volume, load_devices, save_devices
from ..models import (
    Device,
    DevicePatch,
    Health,
    InputHost,
    InputHostCreate,
    InputHostPatch,
    QueueItem,
    QueueItemCreate,
    QueueItemPatch,
    Source,
    Transport,
    TransportPut,
)
from .base import AudioBackend
from .input_registry import InputRegistry
from .source_session import SourceSession


class MockAudioBackend(AudioBackend):
    """Fake ≤6 sinks with delay/volume/mute/enabled + song-level source switching."""

    name = "mock"

    def __init__(self, devices: Optional[list[Device]] = None) -> None:
        self._lock = threading.RLock()
        self._devices: dict[str, Device] = {
            d.id: d.model_copy(deep=True)
            for d in (devices if devices is not None else load_devices())
        }
        if len(self._devices) > 6:
            raise ValueError("Mock backend supports at most 6 sinks")

        # Feeder callbacks are no-ops: mock has no PipeWire graph to preserve.
        self._inputs = InputRegistry(lock=self._lock)
        self._session = SourceSession(inputs=self._inputs, lock=self._lock)

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
        return self._session.get_transport()

    def set_transport(self, body: TransportPut) -> Transport:
        return self._session.set_transport(body)

    def list_sources(self) -> list[Source]:
        return self._session.list_sources()

    def set_active_source(self, source_id: str) -> Transport:
        return self._session.set_active_source(source_id)

    def list_queue(self) -> list[QueueItem]:
        return self._session.list_queue()

    def add_queue_item(self, body: QueueItemCreate) -> QueueItem:
        return self._session.add_queue_item(body)

    def patch_queue_item(self, item_id: str, patch: QueueItemPatch) -> QueueItem:
        return self._session.patch_queue_item(item_id, patch)

    def remove_queue_item(self, item_id: str) -> None:
        self._session.remove_queue_item(item_id)

    def set_active_queue_item(self, item_id: str) -> Transport:
        return self._session.set_active_queue_item(item_id)

    def list_inputs(self) -> list[InputHost]:
        return self._inputs.list_hosts()

    def add_input(self, body: InputHostCreate) -> InputHost:
        return self._inputs.add_host(body)

    def patch_input(self, host_id: str, patch: InputHostPatch) -> InputHost:
        return self._inputs.patch_host(host_id, patch)

    def remove_input(self, host_id: str) -> None:
        self._inputs.remove_host(host_id)
        self._session.clear_input_bindings(host_id)

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
                mapped_sinks=0,
                graph_dry_run=True,
                detail="MockAudioBackend — simulated sinks; source switch keeps sink graph (noop)",
            )
