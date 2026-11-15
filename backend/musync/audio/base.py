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
    QueueSettings,
    QueueSettingsPut,
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
        """List bluez + Cast (Nest Mini) + always-available SPP defaults."""
        ...

    @abstractmethod
    def apply_devices_config(self, entries: list[DeviceConfigEntry]) -> list[Device]:
        """Persist UI sink selection (BT/SPP/Cast; MAC/UUID filled from discovery)."""
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

    def get_queue_settings(
        self, *, assignments: dict[str, str] | None = None
    ) -> QueueSettings:
        """Queue loop + turn-taking status (override in backends)."""
        raise NotImplementedError

    def set_queue_settings(
        self,
        body: QueueSettingsPut,
        *,
        assignments: dict[str, str] | None = None,
    ) -> QueueSettings:
        """Update loop / turn-taking (override in backends)."""
        raise NotImplementedError

    def notify_playback_ended(self) -> Transport:
        """Player/process EOF → advance queue (override when session supports it)."""
        raise NotImplementedError

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

    def shutdown(self) -> None:
        """Release session resources on SIGINT/SIGTERM / FastAPI lifespan.

        PipeWire backend tears down MuSync-owned loopbacks, feeder null-sinks,
        and child processes. Does not unpair BlueZ devices.
        """
        return

    def get_avrcp_status(self):
        """BT Absolute Volume + transport relay status (soft-fail when unsupported)."""
        from ..models import AvrcpStatus

        return AvrcpStatus(detail="AVRCP not available on this backend")

    def relay_avrcp_command(self, action: str, *, device_id: Optional[str] = None):
        """Relay headset play/pause/skip to hub transport (any now-playing source).

        Optional ``device_id`` promotes that BT sink on Play when turn-taking.
        """
        raise NotImplementedError("AVRCP transport relay not available")

    def inject_avrcp_volume(self, device_id: str, volume: float):
        """Simulate Absolute Volume → slider sync (mock/tests)."""
        raise NotImplementedError("AVRCP volume inject not available")

    @abstractmethod
    def health(self) -> Health:
        ...
