"""Abstract audio backend — same contract for Mock and PipeWire."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import (
    BluetoothConnectRequest,
    BluetoothConnectResponse,
    BluetoothScanStatus,
    CaptureDiscoverResponse,
    Device,
    DeviceConfigEntry,
    DeviceDiscoverResponse,
    DevicePatch,
    Health,
    InputHost,
    InputHostCreate,
    InputHostPatch,
    NowPlaying,
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
    def discover_capture_sources(self) -> CaptureDiscoverResponse:
        """List PipeWire Audio/Source nodes (bluez_input / USB / HDMI) for binding."""
        ...

    def bluetooth_scan_status(self) -> BluetoothScanStatus:
        """Return scan flag + accumulated BT discovery rows (override in backends)."""
        raise NotImplementedError

    def bluetooth_scan_start(self) -> BluetoothScanStatus:
        """Start BlueZ discovery (``bluetoothctl scan on``)."""
        raise NotImplementedError

    def bluetooth_scan_stop(self) -> BluetoothScanStatus:
        """Stop BlueZ discovery (``bluetoothctl scan off``)."""
        raise NotImplementedError

    def bluetooth_clear_discovery(self) -> BluetoothScanStatus:
        """Drop non-connected rows from the discovery list (keep connected)."""
        raise NotImplementedError

    def bluetooth_connect(self, body: BluetoothConnectRequest) -> BluetoothConnectResponse:
        """Pair/connect a remote by MAC and optionally enable it as an output."""
        raise NotImplementedError

    def bind_input_upload(self, host_id: str, file_path: str) -> InputHost:
        """Network capture stub: point host at an uploaded file that plays into feeder."""
        from ..models import CapturePath

        return self.patch_input(
            host_id,
            InputHostPatch(
                capture_path=CapturePath.NETWORK,
                connected=True,
                endpoint=file_path,
                note=f"Upload → feeder · {file_path}",
            ),
        )

    def get_now_playing(self) -> NowPlaying:
        """Unified title/artist/album/cover for the active cue (override in backends)."""
        raise NotImplementedError

    @abstractmethod
    def health(self) -> Health:
        ...
