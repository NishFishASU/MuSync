"""In-memory MockAudioBackend for cloud/dev — no PipeWire or BT hardware."""

from __future__ import annotations

import threading
from typing import Optional

from ..config import (
    clamp_delay_ms,
    clamp_volume,
    default_spp_device,
    devices_config_path,
    load_devices,
    resolve_devices_from_config,
    save_devices,
    validate_devices,
)
from ..models import (
    BluetoothConnectRequest,
    BluetoothConnectResponse,
    BluetoothScanStatus,
    CaptureDiscoverResponse,
    CapturePath,
    Device,
    DeviceConfigEntry,
    DeviceDiscoverResponse,
    DeviceKind,
    DevicePatch,
    DiscoveredBluetoothSink,
    DiscoveredCaptureSource,
    Health,
    HostKind,
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

# Laptop/dev discovery fixtures — friendly names, no real bluez.
_MOCK_DISCOVERED = [
    DiscoveredBluetoothSink(
        name="Qudelix-5K",
        mac="AA:BB:CC:DD:EE:01",
        pw_node="48",
        pw_name="bluez_output.AA_BB_CC_DD_EE_01.1",
        hci="hci0",
        paired=True,
        connected=True,
    ),
    DiscoveredBluetoothSink(
        name="Kitchen Speaker",
        mac="AA:BB:CC:DD:EE:02",
        pw_node="49",
        pw_name="bluez_output.AA_BB_CC_DD_EE_02.1",
        hci="hci1",
        paired=True,
        connected=True,
    ),
]

# Appear only while scanning (or after connect) — unpaired until connected via UI.
_MOCK_SCAN_ONLY = [
    DiscoveredBluetoothSink(
        name="WH-1000XM5",
        mac="AA:BB:CC:DD:EE:55",
        paired=False,
        connected=False,
    ),
    DiscoveredBluetoothSink(
        name="Soundcore Motion",
        mac="AA:BB:CC:DD:EE:66",
        paired=False,
        connected=False,
    ),
]
from .base import AudioBackend
from .input_registry import InputRegistry, CAPTURE_NOTES
from .source_session import SourceSession

# Laptop/dev: S21 FE appears as a live bluez_input so UI can pick playable live input.
_MOCK_CAPTURE_SOURCES = [
    DiscoveredCaptureSource(
        name="Galaxy S21 FE",
        pw_node="61",
        pw_name="bluez_input.AA_BB_CC_DD_EE_21",
        mac="AA:BB:CC:DD:EE:21",
        capture_path=CapturePath.BT_A2DP,
        connected=True,
    ),
    DiscoveredCaptureSource(
        name="USB Audio CODEC",
        pw_node="62",
        pw_name="alsa_input.usb-Generic_USB_Audio-00",
        mac="",
        capture_path=CapturePath.USB,
        connected=True,
    ),
]


def _mock_live_hosts() -> list[InputHost]:
    return [
        InputHost(
            id="input-1",
            name="Galaxy S21 FE",
            kind=HostKind.PHONE,
            capture_path=CapturePath.BT_A2DP,
            armed=True,
            connected=True,
            endpoint="bluez_input.AA_BB_CC_DD_EE_21",
            note=CAPTURE_NOTES[CapturePath.BT_A2DP],
        ),
        InputHost(
            id="input-2",
            name="Laptop (USB)",
            kind=HostKind.PC,
            capture_path=CapturePath.USB,
            armed=True,
            connected=True,
            endpoint="alsa_input.usb-Generic_USB_Audio-00",
            note=CAPTURE_NOTES[CapturePath.USB],
        ),
    ]


class MockAudioBackend(AudioBackend):
    """Fake ≤6 sinks with delay/volume/mute/enabled + song-level source switching."""

    name = "mock"

    def __init__(self, devices: Optional[list[Device]] = None) -> None:
        self._lock = threading.RLock()
        loaded = devices if devices is not None else load_devices(backend="mock")
        validate_devices(loaded)
        self._devices: dict[str, Device] = {
            d.id: d.model_copy(deep=True) for d in loaded
        }
        self._config_ok = True
        self._config_detail = "mock"
        self._bt_scanning = False
        # MAC → DiscoveredBluetoothSink accumulated during scan / connect.
        self._bt_scan_cache: dict[str, DiscoveredBluetoothSink] = {
            d.mac.upper(): d.model_copy(deep=True) for d in _MOCK_DISCOVERED if d.mac
        }
        self._bt_scan_cleared_macs: set[str] = set()

        # Feeder callbacks are no-ops: mock has no PipeWire graph to preserve.
        # Seed S21 FE as live BT A2DP so Play works for live input on laptop.
        self._inputs = InputRegistry(hosts=_mock_live_hosts(), lock=self._lock)
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
            # SPP mock: "connected" means armed/listening when enabled.
            if updated.kind == DeviceKind.NETWORK_SPP and "connected" not in updates:
                updated.connected = bool(updated.enabled)
            trial = [updated if d.id == device_id else d for d in self._devices.values()]
            validate_devices(trial)
            self._devices[device_id] = updated
            self._persist()
            return updated.model_copy(deep=True)

    def _annotate_bt_rows(self, rows: list[DiscoveredBluetoothSink]) -> list[DiscoveredBluetoothSink]:
        configured = {
            d.name.strip().lower(): d.id
            for d in self._devices.values()
            if d.kind == DeviceKind.BLUETOOTH and d.name.strip()
        }
        also_mac = {
            d.mac.strip().upper(): d.id
            for d in self._devices.values()
            if d.kind == DeviceKind.BLUETOOTH and d.mac.strip()
        }
        out: list[DiscoveredBluetoothSink] = []
        for item in rows:
            cfg = configured.get(item.name.lower()) or also_mac.get(
                (item.mac or "").upper()
            )
            out.append(item.model_copy(update={"configured_id": cfg}))
        return out

    def _bt_discovery_rows(self) -> list[DiscoveredBluetoothSink]:
        """Merge base fixtures + scan cache (scan-only remotes while scanning)."""
        by_mac: dict[str, DiscoveredBluetoothSink] = {
            d.mac.upper(): d.model_copy(deep=True)
            for d in _MOCK_DISCOVERED
            if d.mac and d.mac.upper() not in self._bt_scan_cleared_macs
        }
        for mac, row in self._bt_scan_cache.items():
            key = mac.upper()
            if key in self._bt_scan_cleared_macs and not row.connected:
                continue
            by_mac[key] = row.model_copy(deep=True)
        if self._bt_scanning:
            for item in _MOCK_SCAN_ONLY:
                mac = item.mac.upper()
                if mac in self._bt_scan_cleared_macs:
                    continue
                if mac not in by_mac:
                    by_mac[mac] = item.model_copy(deep=True)
                    self._bt_scan_cache[mac] = item.model_copy(deep=True)
        return self._annotate_bt_rows(list(by_mac.values()))

    def discover_device_setup(self) -> DeviceDiscoverResponse:
        with self._lock:
            bluetooth = self._bt_discovery_rows()
            spp = next(
                (d for d in self._devices.values() if d.kind == DeviceKind.NETWORK_SPP),
                default_spp_device(),
            )
            path = devices_config_path()
            scan_note = " · scanning" if self._bt_scanning else ""
            return DeviceDiscoverResponse(
                bluetooth=bluetooth,
                spp_default=spp.model_copy(deep=True),
                config_path=str(path),
                config_ok=self._config_ok,
                config_detail=self._config_detail,
                detail=(
                    "mock discovery — Qudelix-5K + Kitchen Speaker fixtures"
                    f"{scan_note}"
                ),
            )

    def bluetooth_scan_status(self) -> BluetoothScanStatus:
        with self._lock:
            rows = self._bt_discovery_rows()
            return BluetoothScanStatus(
                scanning=self._bt_scanning,
                bluetooth=rows,
                detail=(
                    f"mock scan {'on' if self._bt_scanning else 'off'} · "
                    f"{len(rows)} device{'s' if len(rows) != 1 else ''}"
                ),
            )

    def bluetooth_scan_start(self) -> BluetoothScanStatus:
        with self._lock:
            self._bt_scanning = True
            # Seed scan-only fixtures into the cache immediately (unless cleared).
            for item in _MOCK_SCAN_ONLY:
                mac = item.mac.upper()
                if mac in self._bt_scan_cleared_macs:
                    continue
                if mac not in self._bt_scan_cache:
                    self._bt_scan_cache[mac] = item.model_copy(deep=True)
            return self.bluetooth_scan_status()

    def bluetooth_scan_stop(self) -> BluetoothScanStatus:
        with self._lock:
            self._bt_scanning = False
            return self.bluetooth_scan_status()

    def bluetooth_clear_discovery(self) -> BluetoothScanStatus:
        with self._lock:
            kept: dict[str, DiscoveredBluetoothSink] = {}
            for mac, row in self._bt_scan_cache.items():
                if row.connected:
                    kept[mac] = row
                    self._bt_scan_cleared_macs.discard(mac)
                else:
                    self._bt_scan_cleared_macs.add(mac)
            # Always keep currently connected configured BT sinks in the list.
            for d in self._devices.values():
                if d.kind != DeviceKind.BLUETOOTH or not d.mac:
                    continue
                mac = d.mac.upper()
                if d.connected:
                    kept[mac] = DiscoveredBluetoothSink(
                        name=d.name,
                        mac=d.mac,
                        pw_node=d.pw_node,
                        hci=d.hci,
                        paired=True,
                        connected=True,
                        configured_id=d.id,
                    )
                    self._bt_scan_cleared_macs.discard(mac)
            self._bt_scan_cache = kept
            return BluetoothScanStatus(
                scanning=self._bt_scanning,
                bluetooth=self._bt_discovery_rows(),
                detail=f"cleared · kept {len(kept)} connected",
            )

    def bluetooth_connect(self, body: BluetoothConnectRequest) -> BluetoothConnectResponse:
        with self._lock:
            mac = body.mac.upper()
            name = (body.name or "").strip()
            existing = self._bt_scan_cache.get(mac)
            if existing is None:
                for item in list(_MOCK_DISCOVERED) + list(_MOCK_SCAN_ONLY):
                    if item.mac.upper() == mac:
                        existing = item.model_copy(deep=True)
                        break
            if existing is None:
                existing = DiscoveredBluetoothSink(
                    name=name or f"BT {mac}",
                    mac=mac,
                    paired=False,
                    connected=False,
                )
            if name:
                existing = existing.model_copy(update={"name": name})
            connected = existing.model_copy(
                update={
                    "paired": True,
                    "connected": True,
                    "pw_node": existing.pw_node or "99",
                    "pw_name": existing.pw_name
                    or f"bluez_output.{mac.replace(':', '_')}.1",
                }
            )
            self._bt_scan_cache[mac] = connected
            self._bt_scan_cleared_macs.discard(mac)

            device: Device | None = None
            if body.enable_as_output:
                # Reuse configured sink with same MAC/name, else create one.
                for d in self._devices.values():
                    if d.kind == DeviceKind.BLUETOOTH and (
                        d.mac.upper() == mac or d.name.lower() == connected.name.lower()
                    ):
                        data = d.model_dump()
                        data.update(
                            {
                                "mac": mac,
                                "name": connected.name,
                                "connected": True,
                                "enabled": True,
                                "pw_node": connected.pw_node,
                            }
                        )
                        device = Device.model_validate(data)
                        self._devices[d.id] = device
                        break
                if device is None:
                    bt_count = sum(
                        1 for d in self._devices.values() if d.kind == DeviceKind.BLUETOOTH
                    )
                    new_id = f"bt-{bt_count + 1}"
                    if new_id in self._devices:
                        new_id = f"bt-{mac.replace(':', '').lower()[-6:]}"
                    device = Device(
                        id=new_id,
                        name=connected.name,
                        kind=DeviceKind.BLUETOOTH,
                        mac=mac,
                        connected=True,
                        enabled=True,
                        pw_node=connected.pw_node,
                        delay_ms=0,
                        volume=0.85,
                    )
                    trial = list(self._devices.values()) + [device]
                    validate_devices(trial)
                    self._devices[device.id] = device
                self._persist()
                connected = connected.model_copy(update={"configured_id": device.id})
                self._bt_scan_cache[mac] = connected

            rows = self._bt_discovery_rows()
            return BluetoothConnectResponse(
                ok=True,
                mac=mac,
                detail=f"mock connected {connected.name}",
                device=device.model_copy(deep=True) if device else None,
                bluetooth=rows,
            )

    def apply_devices_config(self, entries: list[DeviceConfigEntry]) -> list[Device]:
        with self._lock:
            by_name = {
                d.name.lower(): {
                    "mac": d.mac,
                    "pw_node": d.pw_node,
                    "hci": d.hci,
                    "connected": d.connected,
                }
                for d in _MOCK_DISCOVERED
            }
            by_mac = {
                d.mac.upper(): by_name[d.name.lower()]
                for d in _MOCK_DISCOVERED
                if d.mac
            }
            resolved = resolve_devices_from_config(
                entries,
                existing=list(self._devices.values()),
                discovery_by_name=by_name,
                discovery_by_mac=by_mac,
            )
            self._devices = {d.id: d.model_copy(deep=True) for d in resolved}
            self._persist()
            return [d.model_copy(deep=True) for d in self._devices.values()]

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
        host = self._inputs.add_host(body)
        self._session.refresh_live_availability()
        return host

    def patch_input(self, host_id: str, patch: InputHostPatch) -> InputHost:
        host = self._inputs.patch_host(host_id, patch)
        self._session.refresh_live_availability()
        return host

    def remove_input(self, host_id: str) -> None:
        self._inputs.remove_host(host_id)
        self._session.clear_input_bindings(host_id)
        self._session.refresh_live_availability()

    def discover_capture_sources(self) -> CaptureDiscoverResponse:
        with self._lock:
            # Prefer auto-detect: bind USB fixture onto a waiting USB/PC host.
            usb_fix = next(
                (s for s in _MOCK_CAPTURE_SOURCES if s.capture_path == CapturePath.USB),
                None,
            )
            auto_bound: list[str] = []
            if usb_fix:
                waiting = next(
                    (
                        h
                        for h in self._inputs.list_hosts()
                        if h.capture_path == CapturePath.USB
                        and not (h.connected and h.endpoint)
                    ),
                    None,
                )
                already = any(
                    (h.endpoint or "") == usb_fix.pw_name
                    for h in self._inputs.list_hosts()
                )
                if waiting and not already:
                    keep_laptop = "laptop" in waiting.name.lower()
                    self._inputs.patch_host(
                        waiting.id,
                        InputHostPatch(
                            name=waiting.name if keep_laptop else usb_fix.name,
                            kind=HostKind.PC,
                            capture_path=CapturePath.USB,
                            connected=True,
                            endpoint=usb_fix.pw_name,
                            armed=True,
                            note=CAPTURE_NOTES[CapturePath.USB],
                        ),
                    )
                    auto_bound.append(waiting.id)
                    self._session.refresh_live_availability()

            configured = {h.name.strip().lower(): h.id for h in self._inputs.list_hosts()}
            also_ep = {
                (h.endpoint or "").strip(): h.id
                for h in self._inputs.list_hosts()
                if h.endpoint
            }
            sources: list[DiscoveredCaptureSource] = []
            for item in _MOCK_CAPTURE_SOURCES:
                cfg = configured.get(item.name.lower()) or also_ep.get(item.pw_name)
                sources.append(item.model_copy(update={"configured_id": cfg}))
            auto_note = (
                f"; auto-bound USB hosts={','.join(auto_bound)}" if auto_bound else ""
            )
            return CaptureDiscoverResponse(
                sources=sources,
                detail=(
                    "mock capture — Galaxy S21 FE bluez_input + USB Audio fixtures"
                    f"{auto_note}"
                ),
            )

    def get_now_playing(self):
        from ..nowplaying import apply_mock_sample_metadata, build_now_playing

        # Laptop mock always returns title / artist / album / cover for the header.
        return apply_mock_sample_metadata(build_now_playing(self._session), self._session)

    def health(self) -> Health:
        with self._lock:
            from ..streaming import get_streaming_service

            devices = list(self._devices.values())
            hci_map = {d.hci: d.mac for d in devices if d.hci}
            stream = get_streaming_service().status()
            return Health(
                ok=True,
                backend="mock",
                pipewire_up=False,
                sink_count=len(devices),
                enabled_count=sum(1 for d in devices if d.enabled),
                hci_map=hci_map,
                mapped_sinks=0,
                graph_dry_run=True,
                spotify_ready=stream.spotify.authenticated,
                apple_ready=stream.apple.authenticated,
                detail="MockAudioBackend — simulated sinks; source switch keeps sink graph (noop)",
            )
