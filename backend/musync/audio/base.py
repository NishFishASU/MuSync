"""Abstract audio backend — same contract for Mock and PipeWire."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import Device, DevicePatch, Health, Source, Transport, TransportPut


class AudioBackend(ABC):
    """Fan-out control plane: devices + local-file transport + sources."""

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
    def health(self) -> Health:
        ...
