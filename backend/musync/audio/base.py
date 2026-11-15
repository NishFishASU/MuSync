"""Abstract audio backend — same contract for Mock and PipeWire."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import (
    Device,
    DeviceConfigEntry,
    DeviceDiscoverResponse,
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


class AudioBackend(ABC):
    """Fan-out control plane: devices + song queue + source switching + transport.

    Source / queue switches must only swap the feeder — never tear down sinks.
    """

    name: str = "base"

    @abstractmethod
    def list_devices(self) -> list[Device]:
        ...

    @abstractmethod
    def get_device(self, device_id: str) -> Device:
        ...

    @abstractmethod
    def patch_device(self, device_id: str, patch: DevicePatch) -> Device:
        ...

    @abstractmethod
    def discover_device_setup(self) -> DeviceDiscoverResponse:
        """List bluez sinks by name + always-available SPP defaults (no MAC typing)."""
        ...

    @abstractmethod
    def apply_devices_config(self, entries: list[DeviceConfigEntry]) -> list[Device]:
        """Persist UI sink selection (type + SPP details; MAC filled from discovery)."""
        ...

    @abstractmethod
    def get_transport(self) -> Transport:
        ...

    @abstractmethod
    def set_transport(self, body: TransportPut) -> Transport:
        ...

    @abstractmethod
    def list_sources(self) -> list[Source]:
        ...

    @abstractmethod
    def set_active_source(self, source_id: str) -> Transport:
        ...

    @abstractmethod
    def list_queue(self) -> list[QueueItem]:
        ...

    @abstractmethod
    def add_queue_item(self, body: QueueItemCreate) -> QueueItem:
        ...

    @abstractmethod
    def patch_queue_item(self, item_id: str, patch: QueueItemPatch) -> QueueItem:
        ...

    @abstractmethod
    def remove_queue_item(self, item_id: str) -> None:
        ...

    @abstractmethod
    def set_active_queue_item(self, item_id: str) -> Transport:
        ...

    @abstractmethod
    def list_inputs(self) -> list[InputHost]:
        ...

    @abstractmethod
    def add_input(self, body: InputHostCreate) -> InputHost:
        ...

    @abstractmethod
    def patch_input(self, host_id: str, patch: InputHostPatch) -> InputHost:
        ...

    @abstractmethod
    def remove_input(self, host_id: str) -> None:
        ...

    @abstractmethod
    def health(self) -> Health:
        ...
