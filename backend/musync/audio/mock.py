"""In-memory MockAudioBackend for cloud/dev — no PipeWire or BT hardware."""

from __future__ import annotations

import threading
from typing import Optional

from ..config import (
    clamp_delay_ms,
    clamp_gain,
    clamp_volume,
    default_spp_device,
    devices_config_path,
    load_devices,
    resolve_devices_from_config,
    save_devices,
    validate_devices,
)
from ..models import (
    MAX_BT_SINKS,
    BluetoothAdapter,
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
    DiscoveredCastSink,
    DiscoveredCaptureSource,
    Health,
    HostKind,
    InputHost,
    InputHostCreate,
    InputHostPatch,
    QueueItem,
    QueueItemCreate,
    QueueItemPatch,
    QueueSettings,
    QueueSettingsPut,
    Source,
    SourceType,
    Transport,
    TransportPut,
    TransportState,
)
from ..nowplaying import DEMO_SOURCE_ID
from .avrcp import AvrcpAction, AvrcpBridge, parse_action, relay_action_to_session
from .cast import DEFAULT_CAST_DELAY_MS, mock_nest_mini
from .host_player import HostSpeakerPlayer
from .turn_taking import (
    ROTATION_NOTE,
    eligible_turn_devices,
    mute_targets_for_turn,
    next_turn_device_id,
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

# Wi‑Fi Cast fixture — Nest Mini (higher latency than BT A2DP).
_MOCK_CAST = [mock_nest_mini()]

# Six healthy HCIs for ≤6 A2DP sinks; plus one dead USB stick that must not block.
_MOCK_ADAPTERS = [
    BluetoothAdapter(
        hci="hci0",
        mac="00:11:22:33:44:00",
        name="Pi onboard",
        powered=True,
        healthy=True,
        onboard=True,
        bus="uart",
        detail="powered",
    ),
    *[
        BluetoothAdapter(
            hci=f"hci{i}",
            mac=f"00:11:22:33:44:{i:02X}",
            name=f"USB HCI hci{i}",
            powered=True,
            healthy=True,
            onboard=False,
            bus="usb",
            usb_id="0b05:1d70" if i == 1 else "",
            detail="powered",
        )
        for i in range(1, 6)
    ],
    BluetoothAdapter(
        hci="hci6",
        mac="00:11:22:33:44:66",
        name="USB HCI dead",
        powered=False,
        healthy=False,
        onboard=False,
        bus="usb",
        detail="probe timeout — skip (unhealthy USB HCI?)",
    ),
]
from .base import AudioBackend
from .input_registry import InputRegistry, CAPTURE_NOTES
from .source_session import SourceSession

# Laptop/dev: S21 FE + USB + HDMI + AirPlay fixtures for live input UI.
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
    DiscoveredCaptureSource(
        name="HDMI Capture Card",
        pw_node="63",
        pw_name="alsa_input.usb-MACROSILICON_HDMI-00",
        mac="",
        capture_path=CapturePath.HDMI,
        connected=True,
    ),
    DiscoveredCaptureSource(
        name="AirPlay / shairport-sync",
        pw_node="64",
        pw_name="shairport-sync",
        mac="",
        capture_path=CapturePath.AIRPLAY,
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
        InputHost(
            id="input-3",
            name="HDMI Capture",
            kind=HostKind.OTHER,
            capture_path=CapturePath.HDMI,
            armed=True,
            connected=True,
            endpoint="alsa_input.usb-MACROSILICON_HDMI-00",
            note=CAPTURE_NOTES[CapturePath.HDMI],
        ),
        InputHost(
            id="input-4",
            name="AirPlay Receive",
            kind=HostKind.OTHER,
            capture_path=CapturePath.AIRPLAY,
            armed=True,
            connected=True,
            endpoint="shairport-sync",
            note=CAPTURE_NOTES[CapturePath.AIRPLAY],
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
        # Tests may shrink this list to exercise single-adapter soft-fail.
        self._bt_adapters: list[BluetoothAdapter] = [
            a.model_copy(deep=True) for a in _MOCK_ADAPTERS
        ]

        # Seed S21 FE as live BT A2DP so Play works for live input on laptop.
        # Local files route to host default speakers (ffplay/sounddevice/…); soft-fail OK.
        self._inputs = InputRegistry(hosts=_mock_live_hosts(), lock=self._lock)
        self._host_player = HostSpeakerPlayer()
        self._turn_taking: bool = True
        self._turn_device_id: Optional[str] = None
        self._turn_assignments: dict[str, str] = {}
        self._session = SourceSession(
            inputs=self._inputs,
            lock=self._lock,
            on_feeder_stop=self._stop_host_player,
            on_feeder_start=self._start_host_player,
            on_auto_advance=self._on_queue_auto_advance,
        )
        # Laptop: Absolute Volume / headset buttons via inject API (no BlueZ).
        self._avrcp = AvrcpBridge(
            on_volume=self._on_avrcp_volume,
            on_action=self._on_avrcp_action,
        )
        # Solo the first eligible sink when turn-taking starts (fan-out until then).
        self._apply_turn_policy(rotate=False)
        self._avrcp.update_targets(
            mac_by_device={
                d.id: d.mac
                for d in self._devices.values()
                if d.kind == DeviceKind.BLUETOOTH and d.mac
            }
        )

    def _adapter_snapshot(self) -> tuple[list[BluetoothAdapter], int, str]:
        adapters = [a.model_copy(deep=True) for a in self._bt_adapters]
        healthy = [a for a in adapters if a.healthy]
        capacity = min(len(healthy), MAX_BT_SINKS)
        if capacity == 0:
            warn = (
                "No healthy Bluetooth adapters — BT outputs stay MISSING; "
                "unhealthy USB HCI soft-fail (API stays up)"
            )
        elif capacity == 1:
            label = "onboard" if healthy[0].onboard else healthy[0].hci
            warn = (
                f"Only 1 healthy adapter ({label}) — one A2DP sink max; "
                "extra USB HCI dongles soft-fail until plugged / healthy"
            )
        else:
            warn = f"{capacity} healthy adapters · up to {capacity} A2DP sinks"
        return adapters, capacity, warn

    def _used_hcis(self, *, exclude_id: str | None = None) -> set[str]:
        used: set[str] = set()
        for d in self._devices.values():
            if d.kind != DeviceKind.BLUETOOTH:
                continue
            if exclude_id and d.id == exclude_id:
                continue
            hci = (d.hci or "").strip().lower()
            if hci:
                used.add(hci)
        return used

    def _pick_free_hci(
        self, *, preferred: str = "", exclude_id: str | None = None
    ) -> BluetoothAdapter | None:
        used = self._used_hcis(exclude_id=exclude_id)
        prefer = (preferred or "").strip().lower()
        healthy = [a for a in self._bt_adapters if a.healthy]
        if prefer:
            for a in healthy:
                if a.hci == prefer and a.hci not in used:
                    return a
        ordered = sorted(
            healthy,
            key=lambda a: (0 if a.onboard else 1, int(a.hci.replace("hci", "") or "99")),
        )
        for a in ordered:
            if a.hci not in used:
                return a
        return None

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
            if "gain" in updates and updates["gain"] is not None:
                updates["gain"] = clamp_gain(updates["gain"])
            data.update(updates)
            updated = Device.model_validate(data)
            # SPP / Cast mock: "connected" means armed when enabled.
            if (
                updated.kind in {DeviceKind.NETWORK_SPP, DeviceKind.CAST}
                and "connected" not in updates
            ):
                updated.connected = bool(updated.enabled)
            trial = [updated if d.id == device_id else d for d in self._devices.values()]
            validate_devices(trial)
            self._devices[device_id] = updated
            if "volume" in updates and updates["volume"] is not None:
                try:
                    self._avrcp.note_ui_volume(device_id, updated.volume)
                except Exception:  # noqa: BLE001
                    pass
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

    def _cast_discovery_rows(self) -> list[DiscoveredCastSink]:
        configured = {
            d.name.strip().lower(): d.id
            for d in self._devices.values()
            if d.kind == DeviceKind.CAST and d.name.strip()
        }
        also_uuid = {
            d.cast_uuid.strip().lower(): d.id
            for d in self._devices.values()
            if d.kind == DeviceKind.CAST and d.cast_uuid.strip()
        }
        out: list[DiscoveredCastSink] = []
        for item in _MOCK_CAST:
            cfg = configured.get(item.name.lower()) or also_uuid.get(
                (item.uuid or "").lower()
            )
            out.append(item.model_copy(update={"configured_id": cfg}))
        return out

    def discover_device_setup(self) -> DeviceDiscoverResponse:
        with self._lock:
            bluetooth = self._bt_discovery_rows()
            cast = self._cast_discovery_rows()
            spp = next(
                (d for d in self._devices.values() if d.kind == DeviceKind.NETWORK_SPP),
                default_spp_device(),
            )
            adapters, capacity, soft_warn = self._adapter_snapshot()
            path = devices_config_path()
            scan_note = " · scanning" if self._bt_scanning else ""
            return DeviceDiscoverResponse(
                bluetooth=bluetooth,
                cast=cast,
                spp_default=spp.model_copy(deep=True),
                adapters=adapters,
                adapter_capacity=capacity,
                soft_warn=soft_warn,
                config_path=str(path),
                config_ok=self._config_ok,
                config_detail=self._config_detail,
                detail=(
                    "mock discovery — Qudelix-5K + Kitchen Speaker + Nest Mini Cast"
                    f"{scan_note}"
                ),
            )

    def _scan_status_payload(
        self, rows: list[DiscoveredBluetoothSink], detail: str
    ) -> BluetoothScanStatus:
        adapters, capacity, soft_warn = self._adapter_snapshot()
        return BluetoothScanStatus(
            scanning=self._bt_scanning,
            bluetooth=rows,
            adapters=adapters,
            adapter_capacity=capacity,
            soft_warn=soft_warn,
            detail=detail,
        )

    def bluetooth_scan_status(self) -> BluetoothScanStatus:
        with self._lock:
            rows = self._bt_discovery_rows()
            return self._scan_status_payload(
                rows,
                (
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
            return self._scan_status_payload(
                self._bt_discovery_rows(),
                f"cleared · kept {len(kept)} connected",
            )

    def bluetooth_connect(self, body: BluetoothConnectRequest) -> BluetoothConnectResponse:
        with self._lock:
            mac = body.mac.upper()
            name = (body.name or "").strip()
            existing_row = self._bt_scan_cache.get(mac)
            if existing_row is None:
                for item in list(_MOCK_DISCOVERED) + list(_MOCK_SCAN_ONLY):
                    if item.mac.upper() == mac:
                        existing_row = item.model_copy(deep=True)
                        break
            if existing_row is None:
                existing_row = DiscoveredBluetoothSink(
                    name=name or f"BT {mac}",
                    mac=mac,
                    paired=False,
                    connected=False,
                )
            if name:
                existing_row = existing_row.model_copy(update={"name": name})

            existing_dev: Device | None = None
            for d in self._devices.values():
                if d.kind == DeviceKind.BLUETOOTH and (
                    d.mac.upper() == mac
                    or d.name.lower() == existing_row.name.lower()
                ):
                    existing_dev = d
                    break

            adapters, _capacity, soft_warn = self._adapter_snapshot()
            picked = self._pick_free_hci(
                preferred=(body.hci or "")
                or (existing_dev.hci if existing_dev else "")
                or existing_row.hci,
                exclude_id=existing_dev.id if existing_dev else None,
            )
            assigned_hci = picked.hci if picked else (
                existing_dev.hci if existing_dev else ""
            )

            connected = existing_row.model_copy(
                update={
                    "paired": True,
                    "connected": True,
                    "hci": assigned_hci or existing_row.hci,
                    "pw_node": existing_row.pw_node or "99",
                    "pw_name": existing_row.pw_name
                    or f"bluez_output.{mac.replace(':', '_')}.1",
                }
            )
            self._bt_scan_cache[mac] = connected
            self._bt_scan_cleared_macs.discard(mac)

            device: Device | None = None
            enable_output = bool(body.enable_as_output)
            if enable_output and picked is None and existing_dev is None:
                enable_output = False
                soft_warn = soft_warn or "no free healthy Bluetooth adapter"

            if enable_output:
                # Reuse configured sink with same MAC/name, else create one.
                if existing_dev is not None:
                    data = existing_dev.model_dump()
                    data.update(
                        {
                            "mac": mac,
                            "name": connected.name,
                            "connected": True,
                            "enabled": True,
                            "pw_node": connected.pw_node,
                        }
                    )
                    if assigned_hci:
                        data["hci"] = assigned_hci
                    device = Device.model_validate(data)
                    self._devices[existing_dev.id] = device
                else:
                    bt_count = sum(
                        1 for d in self._devices.values() if d.kind == DeviceKind.BLUETOOTH
                    )
                    if bt_count >= MAX_BT_SINKS:
                        soft_warn = f"At most {MAX_BT_SINKS} Bluetooth sinks"
                    else:
                        new_id = f"bt-{bt_count + 1}"
                        if new_id in self._devices:
                            new_id = f"bt-{mac.replace(':', '').lower()[-6:]}"
                        device = Device(
                            id=new_id,
                            name=connected.name,
                            kind=DeviceKind.BLUETOOTH,
                            mac=mac,
                            hci=assigned_hci,
                            connected=True,
                            enabled=True,
                            pw_node=connected.pw_node,
                            delay_ms=0,
                            volume=0.85,
                        )
                        trial = list(self._devices.values()) + [device]
                        validate_devices(trial)
                        self._devices[device.id] = device
                if device is not None:
                    self._persist()
                    connected = connected.model_copy(
                        update={"configured_id": device.id, "hci": device.hci}
                    )
                    self._bt_scan_cache[mac] = connected

            rows = self._bt_discovery_rows()
            adapters, _capacity, soft_warn = self._adapter_snapshot()
            detail = f"mock connected {connected.name}"
            if device is None and body.enable_as_output:
                detail = f"{detail}; enable skipped — {soft_warn}"
            return BluetoothConnectResponse(
                ok=True,
                mac=mac,
                detail=detail,
                device=device.model_copy(deep=True) if device else None,
                bluetooth=rows,
                adapters=adapters,
                soft_warn=soft_warn,
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
            cast_by_name = {
                d.name.lower(): {
                    "uuid": d.uuid,
                    "host": d.host,
                    "port": d.port,
                    "model": d.model,
                    "connected": d.connected,
                }
                for d in _MOCK_CAST
            }
            cast_by_uuid = {
                d.uuid.lower(): cast_by_name[d.name.lower()]
                for d in _MOCK_CAST
                if d.uuid
            }
            resolved = resolve_devices_from_config(
                entries,
                existing=list(self._devices.values()),
                discovery_by_name=by_name,
                discovery_by_mac=by_mac,
                cast_by_name=cast_by_name,
                cast_by_uuid=cast_by_uuid,
            )
            # Cast sinks keep the higher default delay when newly enabled.
            for d in resolved:
                if (
                    d.kind == DeviceKind.CAST
                    and d.enabled
                    and d.delay_ms == 0
                    and not any(
                        x.id == d.id and x.kind == DeviceKind.CAST
                        for x in self._devices.values()
                    )
                ):
                    d.delay_ms = DEFAULT_CAST_DELAY_MS
            self._devices = {d.id: d.model_copy(deep=True) for d in resolved}
            self._persist()
            return [d.model_copy(deep=True) for d in self._devices.values()]

    def get_transport(self) -> Transport:
        return self._session.get_transport()

    def set_transport(self, body: TransportPut) -> Transport:
        transport = self._session.set_transport(body)
        # Ensure solo sink is applied when Play starts (no rotate).
        if body.state is TransportState.PLAYING:
            self._apply_turn_policy(rotate=False)
        return transport

    def _on_queue_auto_advance(self) -> None:
        """Track end → next eligible output device (mute solo)."""
        self._apply_turn_policy(rotate=True)

    def _apply_turn_policy(self, *, rotate: bool) -> None:
        with self._lock:
            devices = list(self._devices.values())
            eligible = eligible_turn_devices(
                devices, assignments=self._turn_assignments
            )
            eligible_ids = [d.id for d in eligible]
            self._turn_device_id = next_turn_device_id(
                eligible_ids, self._turn_device_id, rotate=rotate
            )
            for device_id, want_mute in mute_targets_for_turn(
                devices,
                turn_device_id=self._turn_device_id,
                turn_taking=self._turn_taking,
            ):
                device = self._devices.get(device_id)
                if device is None or device.mute == want_mute:
                    continue
                self._devices[device_id] = device.model_copy(update={"mute": want_mute})
            try:
                self._persist()
            except Exception:  # noqa: BLE001
                pass

    def get_queue_settings(
        self, *, assignments: Optional[dict[str, str]] = None
    ) -> QueueSettings:
        if assignments is not None:
            self._turn_assignments = {
                k: v for k, v in assignments.items() if v
            }
        with self._lock:
            devices = list(self._devices.values())
            eligible = eligible_turn_devices(
                devices, assignments=self._turn_assignments
            )
            order = [d.id for d in eligible]
            if self._turn_taking and self._turn_device_id not in order and order:
                self._turn_device_id = order[0]
            return QueueSettings(
                loop=self._session.get_loop(),
                turn_taking=self._turn_taking,
                turn_device_id=self._turn_device_id if self._turn_taking else None,
                turn_order=order,
                rotation_note=ROTATION_NOTE,
            )

    def set_queue_settings(
        self,
        body: QueueSettingsPut,
        *,
        assignments: Optional[dict[str, str]] = None,
    ) -> QueueSettings:
        if assignments is not None:
            self._turn_assignments = {
                k: v for k, v in assignments.items() if v
            }
        if body.loop is not None:
            self._session.set_loop(body.loop)
        if body.turn_taking is not None:
            self._turn_taking = bool(body.turn_taking)
            self._apply_turn_policy(rotate=False)
        return self.get_queue_settings(assignments=assignments)

    def notify_playback_ended(self) -> Transport:
        return self._session.notify_playback_ended()

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
        transport = self._session.set_active_queue_item(item_id)
        self._apply_turn_policy(rotate=False)
        return transport

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
            auto_bound: list[str] = []

            def _auto_bind_fixture(
                path: CapturePath,
                *,
                kind: HostKind,
                keep_name_tokens: tuple[str, ...],
            ) -> None:
                fixture = next(
                    (s for s in _MOCK_CAPTURE_SOURCES if s.capture_path == path),
                    None,
                )
                if not fixture:
                    return
                waiting = next(
                    (
                        h
                        for h in self._inputs.list_hosts()
                        if h.capture_path == path
                        and not (h.connected and h.endpoint)
                    ),
                    None,
                )
                already = any(
                    (h.endpoint or "") == fixture.pw_name
                    for h in self._inputs.list_hosts()
                )
                if waiting and not already:
                    keep = any(t in waiting.name.lower() for t in keep_name_tokens)
                    self._inputs.patch_host(
                        waiting.id,
                        InputHostPatch(
                            name=waiting.name if keep else fixture.name,
                            kind=kind,
                            capture_path=path,
                            connected=True,
                            endpoint=fixture.pw_name,
                            armed=True,
                            note=CAPTURE_NOTES[path],
                        ),
                    )
                    auto_bound.append(waiting.id)

            # Prefer auto-detect: bind USB / HDMI / AirPlay fixtures onto waiting hosts.
            _auto_bind_fixture(
                CapturePath.USB, kind=HostKind.PC, keep_name_tokens=("laptop", "usb")
            )
            _auto_bind_fixture(
                CapturePath.HDMI,
                kind=HostKind.OTHER,
                keep_name_tokens=("hdmi", "capture"),
            )
            _auto_bind_fixture(
                CapturePath.AIRPLAY,
                kind=HostKind.OTHER,
                keep_name_tokens=("airplay", "shairport"),
            )
            if auto_bound:
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
                f"; auto-bound hosts={','.join(auto_bound)}" if auto_bound else ""
            )
            return CaptureDiscoverResponse(
                sources=sources,
                detail=(
                    "mock capture — Galaxy S21 FE + USB + HDMI + AirPlay fixtures"
                    f"{auto_note}"
                ),
            )

    def get_now_playing(self):
        from ..nowplaying import apply_mock_sample_metadata, build_now_playing

        # Laptop mock always returns title / artist / album / cover for the header.
        return apply_mock_sample_metadata(build_now_playing(self._session), self._session)

    def _on_avrcp_volume(self, device_id: str, volume: float) -> None:
        with self._lock:
            device = self._devices.get(device_id)
            if device is None:
                return
            vol = clamp_volume(volume)
            if abs(device.volume - vol) < 0.001:
                return
            self._devices[device_id] = device.model_copy(update={"volume": vol})
            self._persist()

    def _on_avrcp_action(self, action: AvrcpAction) -> None:
        try:
            relay_action_to_session(self._session, action)
        except ValueError:
            # Soft-fail unavailable play — mock stays up.
            pass

    def get_avrcp_status(self):
        from ..models import AvrcpStatus

        snap = self._avrcp.status
        # Mock always "supports" inject path even before first event.
        return AvrcpStatus(
            supported=True,
            watching=False,
            detail=snap.detail or "Mock AVRCP — inject volume/command (no BlueZ)",
            last_volume_sync=dict(snap.last_volume_sync),
            last_action=snap.last_action,
            last_error=snap.last_error,
            player_registered=False,
            volume_supported=True,
            dbus_import_ok=False,
            player_path="/org/musync/player",
            registered_adapters=[],
            mpris_playback_status="Stopped",
        )

    def relay_avrcp_command(self, action: str):
        parsed = parse_action(action)
        # Status only — apply once below (toggle/next must not fire twice).
        self._avrcp.inject_action(parsed, deliver=False)
        return relay_action_to_session(self._session, parsed)

    def inject_avrcp_volume(self, device_id: str, volume: float):
        with self._lock:
            if device_id not in self._devices:
                raise KeyError(f"Unknown device: {device_id}")
        self._avrcp.inject_volume(device_id, clamp_volume(volume))
        return self.get_device(device_id)

    def _stop_host_player(self) -> None:
        self._host_player.stop()

    def _start_host_player(self) -> None:
        """Route local mock playback to host speakers; soft-fail if no audio.

        Also best-effort pause phone-side Spotify (OAuth) so mock local play
        does not fight the phone speakers when the same account is playing.
        """
        transport = self._session.get_transport()
        # Live / streaming cues stay software-clock on laptop mock.
        if transport.source_type in {
            SourceType.SPOTIFY,
            SourceType.APPLE,
            SourceType.YOUTUBE,
            SourceType.INPUT,
        }:
            self._host_player.stop()
            return
        try:
            from ..streaming import get_streaming_service

            get_streaming_service().pause_competing_phone_playback()
        except Exception:  # noqa: BLE001
            pass
        path = self._session.active_file_path()
        if path:
            self._host_player.play(path, position_ms=transport.position_ms)
            return
        # Pathless demo cue — short generated tone (still soft-fails without a player).
        if (transport.source_id or "") == DEMO_SOURCE_ID or (
            transport.source_id or ""
        ).startswith("local:demo"):
            self._host_player.play_demo_tone(position_ms=0)
            return
        self._host_player.play(None, position_ms=transport.position_ms)

    def shutdown(self) -> None:
        """Stop host speakers + streaming children on Ctrl+C / lifespan."""
        try:
            self._stop_host_player()
        except Exception:  # noqa: BLE001
            pass
        try:
            self._host_player.cleanup()
        except Exception:  # noqa: BLE001
            pass
        try:
            from ..streaming import get_streaming_service

            svc = get_streaming_service()
            svc.stop_spotify(keep_librespot=False)
            svc.stop_youtube()
        except Exception:  # noqa: BLE001
            pass

    def health(self) -> Health:
        with self._lock:
            from ..streaming import get_streaming_service

            devices = list(self._devices.values())
            hci_map = {d.hci: d.mac for d in devices if d.hci}
            adapters, _capacity, soft_warn = self._adapter_snapshot()
            healthy_n = sum(1 for a in adapters if a.healthy)
            stream = get_streaming_service().status()
            detail = (
                "MockAudioBackend — simulated sinks; local play → host speakers "
                f"({self._host_player.detail})"
            )
            if soft_warn:
                detail = f"{detail}; {soft_warn}"
            detail = f"{detail}; AVRCP inject ready (mock)"
            return Health(
                ok=True,
                backend="mock",
                pipewire_up=False,
                sink_count=len(devices),
                enabled_count=sum(1 for d in devices if d.enabled),
                hci_map=hci_map,
                adapters=adapters,
                adapter_count=len(adapters),
                healthy_adapter_count=healthy_n,
                mapped_sinks=0,
                graph_dry_run=True,
                spotify_ready=stream.spotify.authenticated,
                apple_ready=stream.airplay.configured,
                youtube_ready=stream.youtube.configured,
                avrcp_supported=True,
                avrcp_player_registered=False,
                host_speakers=self._host_player.backend,
                detail=detail,
            )
