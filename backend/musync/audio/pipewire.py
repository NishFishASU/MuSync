r"""PipeWireBackend — Pi demo control plane via wpctl / pactl / pw-link / mpv.

## Fan-out graph (Phase 1)

```
[mpv local file]  OR  [bluez_input / USB / HDMI capture via module-loopback]
        │
        ▼
  musync_feeder  (null/virtual sink)
        │  module-loopback latency_msec=<delay_ms>   (per enabled sink)
        ├──────────────► bluez_output.<MAC>   (hci0 / speaker 1)
        ├──────────────► bluez_output.<MAC>   (hci1 / speaker 2)
        └──────────────► … up to 6 BlueZ HCI A2DP sinks (+ optional SPP TCP)
```

Live phone input (priority): phone pairs as A2DP **source** → ``bluez_input.*``
→ ``module-loopback`` into ``musync_feeder`` → existing sink fan-out.

Volume/mute: ``wpctl set-volume|set-mute <node-id>``.
AVRCP: Absolute Volume (BlueZ MediaTransport / wpctl) → MuSync sliders;
headset play/pause/skip → active source (soft-fail if unsupported).
Delay: PipeWire pulse-compat ``pactl load-module module-loopback latency_msec=N``.

Without Bluetooth hardware this backend still boots: it plans the graph, can
write a shell script of Pi commands, and dry-runs safely when tools are missing
(``MUSYNC_PW_DRY_RUN=1`` or auto when PipeWire is down).

## Documented Pi commands (after pairing 2 speakers)

```bash
# Create feeder (once per boot / session)
pactl load-module module-null-sink sink_name=musync_feeder \
  sink_properties=device.description=MuSync_Feeder

# Per sink (example: 40 ms delay to kitchen bluez node)
pactl load-module module-loopback latency_msec=40 \
  source=musync_feeder.monitor sink=bluez_output.AA_BB_CC_DD_EE_02 \
  sink_dont_move=true source_dont_move=true

# Phone as A2DP source (S21 FE → bluez_input) into the same feeder
pactl load-module module-loopback latency_msec=20 \
  source=bluez_input.XX_XX_XX_XX_XX_XX sink=musync_feeder \
  sink_dont_move=true source_dont_move=true

# HDMI / capture card (ALSA → PipeWire source) into the same feeder
pactl load-module module-loopback latency_msec=20 \
  source=alsa_input.usb-MACROSILICON_HDMI-00 sink=musync_feeder \
  sink_dont_move=true source_dont_move=true

wpctl set-volume <NODE_ID> 0.75
wpctl set-mute <NODE_ID> 0

# Play a local file into the feeder
mpv --no-video --ao=pipewire --audio-device=musync_feeder /path/to/track.flac
```

HCI ↔ speaker: one BlueZ HCI adapter per A2DP sink; persist MAC + hci in devices.yaml.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

from ..config import (
    clamp_delay_ms,
    clamp_gain,
    clamp_volume,
    default_spp_device,
    devices_config_path,
    load_devices_result,
    repo_root,
    resolve_devices_from_config,
    save_devices,
    validate_devices,
)
from ..models import (
    MAX_BT_SINKS,
    MAX_INPUT_HOSTS,
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
    SourceType,
    Transport,
    TransportPut,
    TransportState,
)
from .avrcp import AvrcpAction, AvrcpBridge, parse_action, relay_action_to_session
from .base import AudioBackend
from .input_registry import CAPTURE_NOTES, STUB_CAPTURE_NOTES, InputRegistry
from .pw_tools import (
    BT_CONNECT_SOFT_TIMEOUT_S,
    BT_PAIR_SOFT_TIMEOUT_S,
    GraphLink,
    GraphPlan,
    PwNode,
    adapter_capacity_note,
    bluetoothctl_connect,
    bluetoothctl_pair,
    bluetoothctl_power_on,
    bluetoothctl_scan_off,
    bluetoothctl_scan_on,
    bluetoothctl_select,
    build_capture_loopback_commands,
    build_sink_commands,
    classify_capture_source,
    discover_alsa_hdmi_cards,
    discover_alsa_usb_cards,
    discover_bluetooth_adapters,
    discover_bluetooth_remotes,
    discover_nodes,
    discover_sources,
    feeder_listed_in_sinks,
    find_feeder_null_sink_ids,
    find_musync_capture_ids,
    find_musync_loopback_ids,
    friendly_bluez_label,
    is_capture_candidate,
    match_capture_source,
    merge_alsa_hdmi_into_sources,
    merge_alsa_usb_into_sources,
    match_device_node,
    normalize_mac,
    pick_free_adapter,
    run_cmd,
    which,
)
from .source_session import SourceSession
from .spp import (
    build_spp_ffmpeg,
    free_spp_listen_port,
    spp_anullsrc,
    spp_controls_key,
    spp_plan_notes,
    spp_tcp_url,
)

log = logging.getLogger(__name__)

DEFAULT_FEEDER = "musync_feeder"


class PipeWireBackend(AudioBackend):
    """PipeWire-oriented backend: graph planner + CLI execution for Pi demos."""

    name = "pipewire"

    def __init__(self, devices: Optional[list[Device]] = None) -> None:
        self._lock = threading.RLock()
        if devices is not None:
            loaded = devices
            self._config_ok = True
            self._config_detail = "injected devices"
        else:
            loaded, self._config_ok, self._config_detail = load_devices_result(
                backend="pipewire"
            )
            if not self._config_ok:
                log.warning("devices.yaml: %s", self._config_detail)
        validate_devices(loaded)
        self._devices: dict[str, Device] = {
            d.id: d.model_copy(deep=True) for d in loaded
        }
        if len(self._devices) < 1:
            raise ValueError("At least one device profile is required")

        self._feeder = os.environ.get("MUSYNC_PW_FEEDER", DEFAULT_FEEDER).strip() or DEFAULT_FEEDER
        self._pw_node_ids: dict[str, str] = {
            d.id: d.pw_node
            for d in self._devices.values()
            if d.pw_node and d.kind == DeviceKind.BLUETOOTH
        }
        self._pw_node_names: dict[str, str] = {}
        self._loopback_module_ids: dict[str, str] = {}
        self._capture_module_ids: dict[str, str] = {}
        self._active_capture_host_id: Optional[str] = None
        self._last_plan: Optional[GraphPlan] = None
        self._discovered: list[PwNode] = []
        self._discovered_sources: list[PwNode] = []
        self._bt_remotes_by_mac: dict = {}
        self._player_proc: Optional[subprocess.Popen[str]] = None
        self._spp_procs: dict[str, subprocess.Popen[str]] = {}
        # Track SPP input mode so we can switch silence → file on Play without
        # bouncing a healthy listener that already matches the desired source.
        self._spp_source_keys: dict[str, str] = {}
        # Soft BT reconnect throttle (demo reboot: paired but A2DP not up yet).
        self._last_bt_reconnect_at: float = 0.0
        self._bt_reconnect_min_interval_s: float = 8.0
        # UI BlueZ scan session — accumulate remotes; clear keeps connected only.
        self._bt_scanning: bool = False
        self._bt_scan_cache: dict[str, DiscoveredBluetoothSink] = {}
        self._bt_scan_cleared_macs: set[str] = set()
        # Onboard + USB HCI inventory (unhealthy dongles marked dead, never block).
        self._bt_adapters: list[BluetoothAdapter] = []
        self._bt_adapters_detail: str = ""
        self._last_adapter_probe_at: float = 0.0
        self._adapter_probe_min_interval_s: float = 12.0

        self._tools = {
            "wpctl": which("wpctl"),
            "pw-cli": which("pw-cli"),
            "pw-link": which("pw-link"),
            "pw-play": which("pw-play"),
            "mpv": which("mpv"),
            "pactl": which("pactl"),
            "bluetoothctl": which("bluetoothctl"),
            "ffmpeg": which("ffmpeg"),
        }

        # Song/source switches only stop/start the feeder player — sink graph stays.
        self._inputs = InputRegistry(lock=self._lock)
        self._session = SourceSession(
            inputs=self._inputs,
            lock=self._lock,
            on_feeder_stop=self._stop_player_process,
            on_feeder_start=self._start_player_process,
        )
        # BT AVRCP Absolute Volume → sliders; headset buttons → active source.
        # Soft-fail when BlueZ/busctl/Absolute Volume unavailable (API stays up).
        self._avrcp = AvrcpBridge(
            on_volume=self._on_avrcp_volume,
            on_action=self._on_avrcp_action,
        )

        # Refresh map / plan once at startup (no-op / dry-run without hardware).
        # Purge orphan MuSync_Feeder null-sinks from prior soft-load restarts.
        # Free SPP ports + soft-reconnect paired BT so UI shows READY after reboot.
        # Adapter probe is soft — unhealthy USB HCI timeouts must not block API boot.
        try:
            self._refresh_adapters(force=True)
            if not self._env_dry_run():
                self._ensure_feeder(reset=True)
                self._free_configured_spp_ports()
            self.refresh_sink_map()
            self._reconnect_bt_sinks(force=True)
            self.refresh_sink_map()
            self._ensure_graph()
            # Bind Galaxy S21 / Laptop demo hosts to live bluez_input / USB when present.
            self._refresh_input_host_links()
        except Exception as exc:  # noqa: BLE001 — never block API boot
            log.warning("Startup graph plan failed: %s", exc)

        try:
            self._sync_avrcp_targets()
            if not self._env_dry_run():
                self._avrcp.start_watcher()
        except Exception as exc:  # noqa: BLE001
            log.debug("AVRCP watcher soft-fail at boot: %s", exc)

    def _persist(self) -> None:
        try:
            save_devices(list(self._devices.values()))
        except OSError as exc:
            log.warning("Could not persist devices: %s", exc)

    def _env_dry_run(self) -> bool:
        raw = os.environ.get("MUSYNC_PW_DRY_RUN", "").strip().lower()
        if raw in {"1", "true", "yes"}:
            return True
        if raw in {"0", "false", "no"}:
            return False
        # Auto: dry-run when PipeWire tools are missing or daemon is down.
        up, _ = self.probe_pipewire()
        return not up

    def pipewire_available(self) -> bool:
        return bool(self._tools["wpctl"] or self._tools["pw-cli"])

    def probe_pipewire(self) -> tuple[bool, str]:
        """Return (up, detail). Does not raise if tools are missing."""
        wpctl = self._tools["wpctl"]
        pw_cli = self._tools["pw-cli"]
        if not wpctl and not pw_cli:
            return False, "wpctl/pw-cli not found — install pipewire + wireplumber"

        try:
            if wpctl:
                proc = run_cmd([wpctl, "status"], timeout=3.0)
                if proc.returncode == 0:
                    return True, "wpctl status ok"
                return False, (proc.stderr or proc.stdout or "wpctl failed").strip()
            proc = run_cmd([pw_cli, "info", "0"], timeout=3.0)
            if proc.returncode == 0:
                return True, "pw-cli info ok"
            return False, (proc.stderr or proc.stdout or "pw-cli failed").strip()
        except (OSError, subprocess.TimeoutExpired) as exc:
            return False, f"PipeWire probe failed: {exc}"

    def refresh_sink_map(self) -> dict[str, str]:
        """Discover bluez sinks and map configured devices → PipeWire node ids."""
        nodes, detail = discover_nodes(
            wpctl=self._tools["wpctl"],
            pw_cli=self._tools["pw-cli"],
        )
        remotes, bt_detail = discover_bluetooth_remotes(
            bluetoothctl=self._tools["bluetoothctl"],
        )
        self._discovered = nodes
        self._bt_remotes_by_mac = {normalize_mac(r.mac): r for r in remotes if r.mac}
        log.info("Sink discovery: %s; %s", detail, bt_detail)

        # Fill friendly names onto bluez nodes from bluetoothctl when possible.
        for node in nodes:
            if node.mac and not node.description:
                remote = self._bt_remotes_by_mac.get(normalize_mac(node.mac))
                if remote and remote.name:
                    node.description = remote.name

        mapped: dict[str, str] = {}
        for device in self._devices.values():
            if device.kind == DeviceKind.NETWORK_SPP:
                # SPP has no bluez node; connected reflects ffmpeg listen state.
                continue
            node = match_device_node(
                mac=device.mac,
                hci=device.hci,
                pw_node_override=device.pw_node or self._pw_node_ids.get(device.id, ""),
                nodes=nodes,
                name=device.name,
            )
            if node:
                self._pw_node_ids[device.id] = node.id
                self._pw_node_names[device.id] = node.name
                mapped[device.id] = node.id
                # Auto-fill MAC from discovery when UI saved by name only.
                if node.mac and not device.mac:
                    device.mac = normalize_mac(node.mac)
                if device.mac or device.pw_node or device.name:
                    device.connected = True
            else:
                # Keep manual override ids even if not currently visible.
                if device.pw_node:
                    self._pw_node_ids[device.id] = device.pw_node
                    mapped[device.id] = device.pw_node
                elif device.id in self._pw_node_ids and not nodes:
                    mapped[device.id] = self._pw_node_ids[device.id]
                else:
                    self._pw_node_ids.pop(device.id, None)
                    self._pw_node_names.pop(device.id, None)
                    if not device.pw_node:
                        device.connected = False
        return mapped

    def _discovery_maps(self) -> tuple[dict, dict, list[DiscoveredBluetoothSink], str]:
        """Build name/mac lookup maps + UI discovery rows from bluez + remotes."""
        nodes, detail = discover_nodes(
            wpctl=self._tools["wpctl"],
            pw_cli=self._tools["pw-cli"],
        )
        remotes, bt_detail = discover_bluetooth_remotes(
            bluetoothctl=self._tools["bluetoothctl"],
        )
        remotes_by_mac = {normalize_mac(r.mac): r for r in remotes if r.mac}
        self._discovered = nodes
        self._bt_remotes_by_mac = remotes_by_mac

        by_name: dict = {}
        by_mac: dict = {}
        rows: list[DiscoveredBluetoothSink] = []
        seen_macs: set[str] = set()

        configured_by_name = {
            d.name.strip().lower(): d.id
            for d in self._devices.values()
            if d.kind == DeviceKind.BLUETOOTH and d.name.strip()
        }
        configured_by_mac = {
            normalize_mac(d.mac): d.id
            for d in self._devices.values()
            if d.kind == DeviceKind.BLUETOOTH and d.mac.strip()
        }

        bluez_nodes = [n for n in nodes if n.mac or "bluez" in n.name.lower()]
        for node in bluez_nodes:
            label = friendly_bluez_label(node, remotes_by_mac)
            mac = normalize_mac(node.mac) if node.mac else ""
            remote = remotes_by_mac.get(mac) if mac else None
            hint = {
                "mac": mac,
                "pw_node": node.id,
                "pw_name": node.name,
                "hci": "",
                "connected": True,
            }
            by_name[label.lower()] = hint
            if mac:
                by_mac[mac] = hint
                seen_macs.add(mac)
            cfg = configured_by_name.get(label.lower()) or (
                configured_by_mac.get(mac) if mac else None
            )
            rows.append(
                DiscoveredBluetoothSink(
                    name=label,
                    mac=mac,
                    pw_node=node.id,
                    pw_name=node.name,
                    paired=bool(remote.paired) if remote else True,
                    connected=True,
                    configured_id=cfg,
                )
            )

        # Paired remotes not yet visible as PipeWire sinks (offer by name anyway).
        for remote in remotes:
            mac = normalize_mac(remote.mac)
            if mac in seen_macs:
                continue
            if mac in self._bt_scan_cleared_macs and not remote.connected:
                continue
            hint = {
                "mac": mac,
                "pw_node": "",
                "pw_name": "",
                "hci": "",
                "connected": remote.connected,
            }
            by_name[remote.name.lower()] = hint
            by_mac[mac] = hint
            cfg = configured_by_name.get(remote.name.lower()) or configured_by_mac.get(mac)
            row = DiscoveredBluetoothSink(
                name=remote.name or f"BT {mac}",
                mac=mac,
                paired=remote.paired,
                connected=remote.connected,
                configured_id=cfg,
            )
            rows.append(row)
            self._bt_scan_cache[mac] = row
            self._bt_scan_cleared_macs.discard(mac)

        # Merge scan-cache remotes not present in live listing (still scanning / cleared filter).
        for mac, cached in list(self._bt_scan_cache.items()):
            if mac in seen_macs:
                continue
            if any(normalize_mac(r.mac) == mac for r in rows if r.mac):
                continue
            if mac in self._bt_scan_cleared_macs and not cached.connected:
                continue
            cfg = configured_by_name.get(cached.name.lower()) or configured_by_mac.get(mac)
            rows.append(cached.model_copy(update={"configured_id": cfg}))
            if cached.name:
                by_name.setdefault(
                    cached.name.lower(),
                    {
                        "mac": mac,
                        "pw_node": cached.pw_node,
                        "pw_name": cached.pw_name,
                        "hci": cached.hci,
                        "connected": cached.connected,
                    },
                )
            by_mac.setdefault(
                mac,
                {
                    "mac": mac,
                    "pw_node": cached.pw_node,
                    "pw_name": cached.pw_name,
                    "hci": cached.hci,
                    "connected": cached.connected,
                },
            )

        scan_note = "; scanning" if self._bt_scanning else ""
        return by_name, by_mac, rows, f"{detail}; {bt_detail}{scan_note}"

    def _ensure_feeder_commands(self) -> list[list[str]]:
        """Shell plan helper — prefer ``_ensure_feeder`` at runtime."""
        pactl = self._tools["pactl"]
        if not pactl:
            return []
        return [
            [
                pactl,
                "load-module",
                "module-null-sink",
                f"sink_name={self._feeder}",
                "sink_properties=device.description=MuSync_Feeder",
            ]
        ]

    def _list_feeder_module_ids(self) -> list[str]:
        pactl = self._tools["pactl"]
        if not pactl:
            return []
        proc = self._exec_soft([pactl, "list", "modules", "short"])
        if not proc or not proc.stdout:
            return []
        return find_feeder_null_sink_ids(proc.stdout, self._feeder)

    def _feeder_sink_present(self) -> bool:
        pactl = self._tools["pactl"]
        if not pactl:
            return False
        proc = self._exec_soft([pactl, "list", "sinks", "short"])
        if not proc or not proc.stdout:
            return False
        return feeder_listed_in_sinks(proc.stdout, self._feeder)

    def _purge_feeder_modules(self) -> int:
        """Unload every MuSync null-sink feeder module (orphans from soft-loads)."""
        pactl = self._tools["pactl"]
        ids = self._list_feeder_module_ids()
        for mid in ids:
            if pactl:
                self._exec_soft([pactl, "unload-module", mid])
        if ids:
            log.warning(
                "Purged %d orphan MuSync feeder null-sink module(s): %s",
                len(ids),
                ", ".join(ids),
            )
        return len(ids)

    def _load_feeder_module(self) -> None:
        pactl = self._tools["pactl"]
        if not pactl:
            return
        cmd = [
            pactl,
            "load-module",
            "module-null-sink",
            f"sink_name={self._feeder}",
            "sink_properties=device.description=MuSync_Feeder",
        ]
        proc = self._exec_soft(cmd)
        if proc and proc.returncode == 0:
            log.info(
                "Created feeder sink %s (module %s)",
                self._feeder,
                (proc.stdout or "").strip() or "?",
            )
        else:
            log.warning(
                "Failed to create feeder %s: %s",
                self._feeder,
                (proc.stderr or proc.stdout or "").strip() if proc else "no pactl",
            )

    def _ensure_feeder(self, *, reset: bool = False) -> None:
        """Idempotent feeder: one ``musync_feeder`` null-sink, purge orphans.

        ``reset=True`` (startup): unload all MuSync feeder modules, then create one.
        Otherwise: create only when missing; if duplicates exist, purge + recreate.
        """
        if self._env_dry_run():
            return
        pactl = self._tools["pactl"]
        if not pactl:
            return

        ids = self._list_feeder_module_ids()
        present = self._feeder_sink_present()

        if reset or len(ids) > 1 or (present and len(ids) == 0):
            # Orphans / restart: wipe MuSync feeders then create exactly one.
            if ids:
                self._purge_feeder_modules()
            self._load_feeder_module()
            return

        if len(ids) == 1 and present:
            return

        if len(ids) == 0 and not present:
            self._load_feeder_module()
            return

        if len(ids) == 1 and not present:
            # Stale module index — reload cleanly.
            self._purge_feeder_modules()
            self._load_feeder_module()

    def plan_graph(self) -> GraphPlan:
        """Build feeder → delay/volume → bluez plan for enabled devices."""
        dry_run = self._env_dry_run()
        plan = GraphPlan(feeder=self._feeder, dry_run=dry_run)
        plan.notes.append(
            "Create feeder once: pactl load-module module-null-sink "
            f"sink_name={self._feeder}"
        )
        if not self._tools["pactl"]:
            plan.notes.append(
                "pactl missing — delay loopbacks unavailable; install pulseaudio-utils"
            )

        for device in self._devices.values():
            if not device.enabled:
                plan.skipped.append(f"{device.id} disabled")
                continue

            # SPP TCP network sink — long-lived ffmpeg listen (managed separately).
            if device.kind == DeviceKind.NETWORK_SPP:
                plan.notes.extend(spp_plan_notes(device, feeder=self._feeder))
                if not self._tools.get("ffmpeg"):
                    plan.notes.append(
                        f"{device.id}: ffmpeg missing — install ffmpeg for SPP TCP listen"
                    )
                plan.links.append(
                    GraphLink(
                        device_id=device.id,
                        source=f"{self._feeder}.monitor",
                        target=spp_tcp_url(device),
                        delay_ms=device.delay_ms,
                        volume=device.volume,
                        mute=device.mute,
                        commands=[],  # started via _sync_spp_listeners (not one-shot)
                    )
                )
                continue

            node_id = self._pw_node_ids.get(device.id, "")
            node_name = self._pw_node_names.get(device.id, "")
            if not node_id and not node_name:
                # Still emit a plan row using expected bluez name from MAC.
                if device.mac:
                    suffix = normalize_mac(device.mac).replace(":", "_")
                    node_name = f"bluez_output.{suffix}"
                    plan.notes.append(
                        f"{device.id}: no live PipeWire node yet — planned target {node_name}"
                    )
                else:
                    plan.skipped.append(f"{device.id} missing mac/pw_node mapping")
                    continue

            cmds, notes = build_sink_commands(
                device_id=device.id,
                feeder=self._feeder,
                target_node_id=node_id or node_name,
                target_name=node_name or node_id,
                delay_ms=device.delay_ms,
                volume=device.volume,
                mute=device.mute,
                tools=self._tools,
            )
            plan.notes.extend(notes)
            plan.links.append(
                GraphLink(
                    device_id=device.id,
                    source=f"{self._feeder}.monitor",
                    target=node_name or node_id,
                    delay_ms=device.delay_ms,
                    volume=device.volume,
                    mute=device.mute,
                    commands=cmds,
                )
            )
        self._last_plan = plan
        return plan

    def write_graph_plan(self, path: Path | None = None) -> Path:
        plan = self._last_plan or self.plan_graph()
        out = path or (repo_root() / "config" / "pipewire-graph.plan.sh")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(plan.as_shell_script(), encoding="utf-8")
        return out

    def _exec_soft(self, args: list[str]) -> subprocess.CompletedProcess[str] | None:
        if not args:
            return None
        try:
            proc = run_cmd(args, timeout=8.0)
            if proc.returncode != 0:
                log.debug(
                    "cmd soft-fail %s → %s",
                    args,
                    (proc.stderr or proc.stdout or "").strip(),
                )
            return proc
        except (OSError, subprocess.TimeoutExpired) as exc:
            log.debug("cmd error %s: %s", args, exc)
            return None

    def _unload_loopback(self, device_id: str) -> None:
        pactl = self._tools["pactl"]
        if not pactl:
            return
        mid = self._loopback_module_ids.pop(device_id, None)
        if mid:
            self._exec_soft([pactl, "unload-module", mid])
            return
        # Best-effort scan for leftover MuSync loopbacks.
        proc = self._exec_soft([pactl, "list", "modules", "short"])
        if not proc or not proc.stdout:
            return
        for found in find_musync_loopback_ids(proc.stdout, device_id):
            self._exec_soft([pactl, "unload-module", found])

    def _apply_volume(self, device: Device) -> None:
        """Apply per-sink volume on this BT/PipeWire output (not global)."""
        if device.kind == DeviceKind.NETWORK_SPP:
            # SPP volume lives in ffmpeg -af; restart is handled by patch_device.
            return
        node = self._pw_node_ids.get(device.id)
        wpctl = self._tools["wpctl"]
        if not node or not wpctl or self._env_dry_run():
            log.debug(
                "volume deferred: %s → %.3f (node=%s dry=%s)",
                device.id,
                device.volume,
                node,
                self._env_dry_run(),
            )
            return
        # Guard Absolute Volume echo so headset→slider sync does not fight UI.
        try:
            self._avrcp.note_ui_volume(device.id, device.volume)
        except Exception as exc:  # noqa: BLE001
            log.debug("AVRCP echo guard: %s", exc)
        self._exec_soft([wpctl, "set-volume", node, f"{device.volume:.3f}"])

    def _sync_avrcp_targets(self) -> None:
        """Point the AVRCP bridge at connected BT sinks (MAC + PipeWire node)."""
        macs: dict[str, str] = {}
        nodes: dict[str, str] = {}
        for d in self._devices.values():
            if d.kind != DeviceKind.BLUETOOTH:
                continue
            if not d.mac or d.mac.upper().startswith("AA:BB:CC:DD:EE:"):
                continue
            if "REPLACE" in d.mac.upper():
                continue
            macs[d.id] = d.mac
            node = self._pw_node_ids.get(d.id) or d.pw_node
            if node:
                nodes[d.id] = node
        self._avrcp.update_targets(mac_by_device=macs, node_by_device=nodes)

    def _on_avrcp_volume(self, device_id: str, volume: float) -> None:
        """Headset Absolute Volume → MuSync slider (do not re-apply wpctl)."""
        with self._lock:
            device = self._devices.get(device_id)
            if device is None or device.kind != DeviceKind.BLUETOOTH:
                return
            vol = clamp_volume(volume)
            if abs(device.volume - vol) < 0.001:
                return
            updated = device.model_copy(update={"volume": vol})
            self._devices[device_id] = updated
            try:
                self._persist()
            except Exception as exc:  # noqa: BLE001
                log.debug("AVRCP volume persist: %s", exc)
            log.info("AVRCP Absolute Volume → %s slider %.0f%%", device_id, vol * 100)

    def _on_avrcp_action(self, action: AvrcpAction) -> None:
        """Headset play/pause/skip → active hub source (Spotify/YouTube/local/…)."""
        try:
            relay_action_to_session(self._session, action)
            log.info("AVRCP %s → active source", action.value)
        except ValueError as exc:
            # Soft-fail: unavailable cue / empty queue — do not crash watcher.
            log.info("AVRCP %s soft-fail: %s", action.value, exc)
        except Exception as exc:  # noqa: BLE001
            log.debug("AVRCP action %s: %s", action, exc)

    def _poll_avrcp_volumes(self) -> None:
        try:
            self._sync_avrcp_targets()
            self._avrcp.poll_volumes()
        except Exception as exc:  # noqa: BLE001
            log.debug("AVRCP volume poll soft-fail: %s", exc)

    def get_avrcp_status(self):
        from ..models import AvrcpStatus

        snap = self._avrcp.status
        return AvrcpStatus(
            supported=snap.supported,
            watching=snap.watching,
            detail=snap.detail,
            last_volume_sync=dict(snap.last_volume_sync),
            last_action=snap.last_action,
            last_error=snap.last_error,
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

    def _apply_mute(self, device: Device) -> None:
        """Apply per-sink mute on this BT/PipeWire output (not global)."""
        if device.kind == DeviceKind.NETWORK_SPP:
            # SPP mute lives in ffmpeg -af volume=0; restart via patch_device.
            return
        node = self._pw_node_ids.get(device.id)
        wpctl = self._tools["wpctl"]
        if not node or not wpctl or self._env_dry_run():
            log.debug("mute deferred: %s → %s", device.id, device.mute)
            return
        self._exec_soft([wpctl, "set-mute", node, "1" if device.mute else "0"])

    def _apply_delay(self, device: Device) -> None:
        """Re-wire this sink's loopback latency (BT). SPP delay is ffmpeg adelay."""
        if device.kind == DeviceKind.NETWORK_SPP:
            # SPP delay lives in ffmpeg -af; restart via patch_device.
            return
        if not device.enabled:
            return
        # Rebuild just this sink by running ensure_graph — keeps feeder shared.
        self._ensure_graph(only_device=device.id)

    def _stop_spp_listener(self, device_id: str) -> None:
        proc = self._spp_procs.pop(device_id, None)
        self._spp_source_keys.pop(device_id, None)
        if proc is None:
            return
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                proc.kill()

    def _spp_listener_alive(self, device_id: str) -> bool:
        proc = self._spp_procs.get(device_id)
        return proc is not None and proc.poll() is None

    def _free_configured_spp_ports(self) -> None:
        """Clear orphan ffmpeg listens for every configured SPP port."""
        ports = {
            int(d.listen_port)
            for d in self._devices.values()
            if d.kind == DeviceKind.NETWORK_SPP and d.listen_port
        }
        if not ports:
            ports = {1234}
        for port in sorted(ports):
            detail = free_spp_listen_port(port)
            log.info("SPP preflight :%s — %s", port, detail)

    def _refresh_adapters(self, *, force: bool = False) -> list[BluetoothAdapter]:
        """Probe onboard + USB HCI adapters; unhealthy dongles soft-fail (short timeout)."""
        now = time.monotonic()
        if (
            not force
            and self._bt_adapters
            and (now - self._last_adapter_probe_at) < self._adapter_probe_min_interval_s
        ):
            return list(self._bt_adapters)
        btctl = self._tools.get("bluetoothctl")
        raw, detail = discover_bluetooth_adapters(bluetoothctl=btctl, probe=True)
        models = [
            BluetoothAdapter(
                hci=a.hci,
                mac=a.mac,
                name=a.name,
                powered=a.powered,
                healthy=a.healthy,
                onboard=a.onboard,
                bus=a.bus,
                usb_id=a.usb_id,
                detail=a.detail,
            )
            for a in raw
        ]
        self._bt_adapters = models
        self._bt_adapters_detail = detail
        self._last_adapter_probe_at = now
        if models:
            log.info("BT adapters: %s", detail)
        return list(models)

    def _adapter_snapshot(self) -> tuple[list[BluetoothAdapter], int, str]:
        """Return ``(adapters, healthy_capacity, soft_warn)`` for API payloads."""
        adapters = self._refresh_adapters()
        # Convert to BtAdapter-shaped objects for capacity note helper.
        from .pw_tools import BtAdapter

        raw = [
            BtAdapter(
                hci=a.hci,
                mac=a.mac,
                name=a.name,
                powered=a.powered,
                healthy=a.healthy,
                onboard=a.onboard,
                bus=a.bus,
                usb_id=a.usb_id,
                detail=a.detail,
            )
            for a in adapters
        ]
        capacity = min(sum(1 for a in adapters if a.healthy), MAX_BT_SINKS)
        warn = adapter_capacity_note(raw)
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

    def _adapter_by_hci(self, hci: str) -> BluetoothAdapter | None:
        want = (hci or "").strip().lower()
        if not want:
            return None
        for a in self._bt_adapters:
            if a.hci == want:
                return a
        return None

    def _prepare_adapter_for_connect(
        self,
        *,
        preferred_hci: str = "",
        existing_hci: str = "",
        exclude_device_id: str | None = None,
    ) -> tuple[BluetoothAdapter | None, str]:
        """Pick + select a healthy free HCI. Soft-fail when only onboard / none free."""
        adapters, _cap, warn = self._adapter_snapshot()
        from .pw_tools import BtAdapter

        raw = [
            BtAdapter(
                hci=a.hci,
                mac=a.mac,
                name=a.name,
                powered=a.powered,
                healthy=a.healthy,
                onboard=a.onboard,
                bus=a.bus,
                usb_id=a.usb_id,
                detail=a.detail,
            )
            for a in adapters
        ]
        used = self._used_hcis(exclude_id=exclude_device_id)
        # Reuse existing HCI when still healthy.
        prefer = (preferred_hci or existing_hci or "").strip().lower()
        if prefer:
            current = self._adapter_by_hci(prefer)
            if current and current.healthy and (
                prefer not in used or prefer == (existing_hci or "").strip().lower()
            ):
                picked = current
            else:
                picked_raw = pick_free_adapter(
                    raw, used_hcis=used, preferred_hci=prefer
                )
                picked = self._adapter_by_hci(picked_raw.hci) if picked_raw else None
        else:
            picked_raw = pick_free_adapter(raw, used_hcis=used)
            picked = self._adapter_by_hci(picked_raw.hci) if picked_raw else None

        if picked is None:
            return None, warn

        btctl = self._tools.get("bluetoothctl")
        if btctl and not self._env_dry_run():
            target = picked.mac or picked.hci
            sel_ok, sel_detail = bluetoothctl_select(btctl, target)
            if not sel_ok:
                # Soft-fail this adapter — try without select (default controller).
                log.warning("BT select %s failed: %s", target, sel_detail)
            else:
                bluetoothctl_power_on(btctl)
        return picked, warn

    def _reconnect_bt_sinks(self, *, force: bool = False) -> list[str]:
        """Soft-connect enabled BT sinks that are paired but not mapped yet.

        Demo reboot path: Qudelix is paired but A2DP may be down until
        ``bluetoothctl connect``. Throttled so UI polls cannot spam BlueZ.
        Unhealthy USB HCI adapters are skipped (short connect timeout).
        """
        now = time.monotonic()
        if (
            not force
            and (now - self._last_bt_reconnect_at) < self._bt_reconnect_min_interval_s
        ):
            return []
        if self._env_dry_run():
            return []
        btctl = self._tools.get("bluetoothctl")
        if not btctl:
            return []

        # Probe adapters first — never block long on a dead dongle.
        try:
            self._refresh_adapters(force=force)
        except Exception as exc:  # noqa: BLE001
            log.debug("adapter probe on reconnect: %s", exc)

        # Prefer fresh remotes; fall back to last discovery cache.
        remotes, _detail = discover_bluetooth_remotes(bluetoothctl=btctl)
        if remotes:
            self._bt_remotes_by_mac = {
                normalize_mac(r.mac): r for r in remotes if r.mac
            }
        attempted: list[str] = []
        for device in self._devices.values():
            if device.kind != DeviceKind.BLUETOOTH or not device.enabled:
                continue
            if device.connected and device.id in self._pw_node_ids:
                continue
            mac = normalize_mac(device.mac) if device.mac else ""
            if not mac or mac.upper().startswith("AA:BB:CC:DD:EE:"):
                continue
            remote = self._bt_remotes_by_mac.get(mac)
            # Skip unknown remotes that were never paired — connect would hang.
            if remote is not None and not remote.paired and not remote.connected:
                continue

            # Skip sinks whose assigned HCI is known-dead (don't burn the timeout).
            if device.hci:
                adapter = self._adapter_by_hci(device.hci)
                if adapter is not None and not adapter.healthy:
                    log.info(
                        "BT reconnect skip %s — adapter %s unhealthy (%s)",
                        device.id,
                        device.hci,
                        adapter.detail or "dead",
                    )
                    attempted.append(device.id)
                    continue
                if adapter is not None and (adapter.mac or adapter.hci):
                    bluetoothctl_select(btctl, adapter.mac or adapter.hci)

            ok, detail = bluetoothctl_connect(
                btctl, mac, timeout=BT_CONNECT_SOFT_TIMEOUT_S
            )
            attempted.append(device.id)
            log.info(
                "BT reconnect %s (%s): %s — %s",
                device.id,
                mac,
                "ok" if ok else "fail",
                detail,
            )
        if attempted or force:
            self._last_bt_reconnect_at = now
        return attempted

    def _refresh_live_sink_state(self, *, reconnect: bool = True) -> None:
        """Refresh BT map + SPP listeners; optionally soft-reconnect MISSING BT."""
        if reconnect:
            self._reconnect_bt_sinks()
        self.refresh_sink_map()
        self._sync_spp_listeners()

    def _sink_readiness_summary(self) -> tuple[list[str], list[str]]:
        """Return ``(ready_ids, missing_ids)`` for enabled sinks (demo UI / health)."""
        ready: list[str] = []
        missing: list[str] = []
        for device in self._devices.values():
            if not device.enabled:
                continue
            if device.kind == DeviceKind.NETWORK_SPP:
                ok = bool(device.connected) and (
                    self._env_dry_run() or self._spp_listener_alive(device.id)
                )
            else:
                ok = bool(device.connected)
            (ready if ok else missing).append(device.id)
        return ready, missing

    def _spp_desired_source(self, device: Device) -> tuple[str, str, str]:
        """Return ``(input_kind, input_spec, source_key)`` for the SPP listen.

        Prefer the proven manual shape (``-re -i <file>``) while a local track
        is playing. Otherwise feed continuous ``anullsrc`` silence so the phone
        can complete its handshake before Play — pulse ``feeder.monitor`` often
        emits no frames until something plays into the feeder.

        ``source_key`` includes per-sink delay/volume/mute so a control PATCH
        forces a listener rebuild with the new ``-af`` chain.
        """
        controls = spp_controls_key(device)
        path = ""
        try:
            path = self._session.active_file_path() or ""
        except Exception:  # noqa: BLE001 — session optional during early init
            path = ""
        transport = self._session.get_transport()
        if path and transport.state == TransportState.PLAYING:
            return "file", path, f"file:{path}|{controls}"
        spec = spp_anullsrc(device)
        return "lavfi", spec, f"lavfi:{spec}|{controls}"

    def _restart_spp_listener(self, device: Device) -> None:
        """(Re)start TCP listen for an enabled SPP sink.

        Forced restart when listen_port / rate / channels change, or when the
        desired input switches (silence ↔ file). Do **not** bounce a healthy
        listener that already matches the desired source — that drops the phone.
        """
        self._stop_spp_listener(device.id)
        if device.kind != DeviceKind.NETWORK_SPP or not device.enabled:
            device.connected = False
            return

        kind, spec, source_key = self._spp_desired_source(device)
        if self._env_dry_run():
            dry_cmd = build_spp_ffmpeg(
                device, input_kind=kind, input_spec=spec, ffmpeg_bin="ffmpeg"
            )
            log.info("Dry-run SPP (%s): %s", source_key, " ".join(dry_cmd))
            device.connected = True
            self._spp_source_keys[device.id] = source_key
            return

        # Orphan manual ffmpeg often still owns the port after a crash/restart.
        free_detail = free_spp_listen_port(int(device.listen_port))
        log.info("SPP free :%s — %s", device.listen_port, free_detail)

        ff = self._tools.get("ffmpeg")
        if not ff:
            log.warning("ffmpeg missing — cannot start SPP listener for %s", device.id)
            device.connected = False
            return

        cmd = build_spp_ffmpeg(
            device, input_kind=kind, input_spec=spec, ffmpeg_bin=ff
        )
        # Ensure feeder exists for BT fan-out; SPP itself uses file/anullsrc.
        self._ensure_feeder()

        try:
            # Never use stderr=PIPE without a reader — a full pipe blocks ffmpeg
            # and the phone sees "unable to stream" even though the port is open.
            log_path = repo_root() / "config" / f"spp-{device.id}.ffmpeg.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_fh = log_path.open("w", encoding="utf-8")
            self._spp_procs[device.id] = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=log_fh,
                text=True,
            )
            try:
                log_fh.close()
            except OSError:
                pass
            # Brief settle — ffmpeg exits immediately if bind/format fails.
            time.sleep(0.2)
            proc = self._spp_procs.get(device.id)
            if proc is None:
                device.connected = False
                return
            if proc.poll() is not None:
                err = ""
                try:
                    err = log_path.read_text(encoding="utf-8")[-2000:]
                except OSError:
                    err = ""
                self._spp_procs.pop(device.id, None)
                self._spp_source_keys.pop(device.id, None)
                device.connected = False
                log.warning(
                    "SPP listener %s exited immediately (code %s): %s — cmd=%s",
                    device.id,
                    proc.returncode,
                    (err or "").strip() or "(no stderr)",
                    " ".join(cmd),
                )
                return
            device.connected = True
            self._spp_source_keys[device.id] = source_key
            log.info(
                "SPP listening %s → %s (pid %s, source=%s)",
                device.id,
                spp_tcp_url(device),
                proc.pid,
                source_key,
            )
        except OSError as exc:
            log.warning("Failed to start SPP listener %s: %s", device.id, exc)
            device.connected = False

    def _ensure_spp_listener(self, device: Device) -> None:
        """Start/upgrade SPP listen; keep process if source already matches."""
        if device.kind != DeviceKind.NETWORK_SPP:
            return
        if not device.enabled:
            self._stop_spp_listener(device.id)
            device.connected = False
            return
        _kind, _spec, source_key = self._spp_desired_source(device)
        if (
            self._spp_listener_alive(device.id)
            and self._spp_source_keys.get(device.id) == source_key
        ):
            device.connected = True
            return
        # Dead, never started, or need silence↔file switch.
        self._restart_spp_listener(device)

    def _sync_spp_listeners(self, only_device: str | None = None) -> None:
        """Keep enabled SPP sinks listening without bouncing healthy processes.

        Called from ``_ensure_graph`` (including on Play). Restarting here used
        to drop Simple Protocol Player the moment transport started.
        """
        for device in self._devices.values():
            if device.kind != DeviceKind.NETWORK_SPP:
                continue
            if only_device and device.id != only_device:
                continue
            self._ensure_spp_listener(device)

    def _ensure_graph(self, only_device: str | None = None) -> GraphPlan:
        """Ensure feeder → delay/volume → enabled sinks.

        Cloud/dev: plan + write ``config/pipewire-graph.plan.sh`` (dry-run).
        Pi with tools: create feeder, (re)load loopbacks, apply wpctl volume/mute.
        Losing one sink does not tear down others — we only touch changed/enabled ids.
        """
        self.refresh_sink_map()
        plan = self.plan_graph()

        try:
            self.write_graph_plan()
        except OSError as exc:
            log.debug("Could not write graph plan: %s", exc)

        if plan.dry_run:
            log.info(
                "PipeWire graph dry-run: %d links, %d skipped (see config/pipewire-graph.plan.sh)",
                len(plan.links),
                len(plan.skipped),
            )
            # Still "arm" SPP in dry-run so UI shows connected when enabled.
            self._sync_spp_listeners(only_device=only_device)
            return plan

        # Live path — one feeder null-sink (idempotent; no duplicate soft-loads).
        self._ensure_feeder()

        for link in plan.links:
            if only_device and link.device_id != only_device:
                continue
            device = self._devices.get(link.device_id)
            if device and device.kind == DeviceKind.NETWORK_SPP:
                continue  # SPP ffmpeg is long-lived; see _sync_spp_listeners
            self._unload_loopback(link.device_id)
            for cmd in link.commands:
                proc = self._exec_soft(cmd)
                # Capture pactl load-module id (stdout is module index).
                if (
                    proc
                    and proc.returncode == 0
                    and len(cmd) >= 3
                    and cmd[1] == "load-module"
                    and cmd[2] == "module-loopback"
                    and proc.stdout.strip().isdigit()
                ):
                    self._loopback_module_ids[link.device_id] = proc.stdout.strip()

        # Unload loopbacks for disabled devices so a single mute/disable doesn't
        # kill the rest of the session.
        for device in self._devices.values():
            if only_device and device.id != only_device:
                continue
            if device.kind == DeviceKind.NETWORK_SPP:
                continue
            if not device.enabled:
                self._unload_loopback(device.id)

        # SPP TCP listeners share the feeder; independent of bluez loopbacks.
        self._sync_spp_listeners(only_device=only_device)
        return plan

    def _unload_capture(self, host_id: Optional[str] = None) -> None:
        """Tear down capture→feeder loopback(s)."""
        pactl = self._tools["pactl"]
        targets = (
            [host_id]
            if host_id
            else list(self._capture_module_ids.keys())
            or ([self._active_capture_host_id] if self._active_capture_host_id else [])
        )
        for hid in list(targets):
            if not hid:
                continue
            mid = self._capture_module_ids.pop(hid, None)
            if mid and pactl and not self._env_dry_run():
                self._exec_soft([pactl, "unload-module", mid])
            elif pactl and not self._env_dry_run():
                proc = self._exec_soft([pactl, "list", "modules", "short"])
                if proc and proc.stdout:
                    for found in find_musync_capture_ids(proc.stdout, hid):
                        self._exec_soft([pactl, "unload-module", found])
        if host_id is None or host_id == self._active_capture_host_id:
            self._active_capture_host_id = None

    def _stop_player_process(self) -> None:
        self._unload_capture()
        try:
            from ..streaming import get_streaming_service

            svc = get_streaming_service()
            svc.stop_spotify()
            svc.stop_youtube()
        except Exception as exc:  # noqa: BLE001
            log.debug("Streaming stop: %s", exc)
        if self._player_proc is None:
            return
        proc = self._player_proc
        self._player_proc = None
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                proc.kill()

    def _start_mpv_into_feeder(self, path: str, *, position_ms: int = 0) -> None:
        """Play local audio or video into the feeder (video: audio-only via --no-video)."""
        if self._env_dry_run():
            log.info(
                "Dry-run playback: mpv --no-video --ao=pipewire --audio-device=%s %s",
                self._feeder,
                path,
            )
            return
        mpv = self._tools["mpv"]
        pw_play = self._tools["pw-play"]
        try:
            if mpv:
                # --no-video: extract audio from video containers into musync_feeder.
                self._player_proc = subprocess.Popen(
                    [
                        mpv,
                        "--no-video",
                        "--ao=pipewire",
                        f"--audio-device={self._feeder}",
                        f"--start={position_ms / 1000:.3f}",
                        path,
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    text=True,
                )
            elif pw_play:
                self._player_proc = subprocess.Popen(
                    [pw_play, f"--target={self._feeder}", path],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    text=True,
                )
            else:
                log.info("mpv/pw-play missing — transport is software-only")
        except OSError as exc:
            log.warning("Failed to start player: %s", exc)
            self._player_proc = None

    def _start_capture_into_feeder(self, host: InputHost) -> None:
        """Route bluez_input / USB / HDMI Audio/Source into musync_feeder."""
        self._refresh_input_host_links()
        host = self._inputs.get_host(host.id)
        source_ref = (host.endpoint or "").strip()
        if not source_ref:
            log.warning("No capture endpoint for %s — cannot feed live input", host.id)
            return

        self._ensure_feeder()

        cmds, notes = build_capture_loopback_commands(
            host_id=host.id,
            feeder=self._feeder,
            source_ref=source_ref,
            tools=self._tools,
        )
        for note in notes:
            log.info("%s", note)

        if self._env_dry_run():
            for cmd in cmds:
                log.info("Dry-run capture: %s", " ".join(cmd))
            self._active_capture_host_id = host.id
            return

        self._unload_capture(host.id)
        for cmd in cmds:
            proc = self._exec_soft(cmd)
            if (
                proc
                and proc.returncode == 0
                and len(cmd) >= 3
                and cmd[1] == "load-module"
                and cmd[2] == "module-loopback"
                and proc.stdout.strip().isdigit()
            ):
                self._capture_module_ids[host.id] = proc.stdout.strip()
        self._active_capture_host_id = host.id
        log.info(
            "Capture live: %s (%s) %s → %s",
            host.name,
            host.capture_path.value,
            source_ref,
            self._feeder,
        )

    def _start_spotify_into_feeder(self, locator: str) -> None:
        """librespot Connect → PULSE_SINK=musync_feeder; Web API transfers the track."""
        from ..streaming import get_streaming_service

        self._ensure_feeder()
        svc = get_streaming_service()
        if self._env_dry_run():
            log.info("Dry-run Spotify play %s → %s", locator, self._feeder)
            return
        try:
            result = svc.play_spotify(locator, feeder=self._feeder)
            log.info("Spotify Connect play: %s", result)
        except ValueError as exc:
            # Degraded: 30s preview via mpv when Connect device missing.
            preview = svc.spotify.preview_url_for(locator)
            if preview:
                log.warning("Spotify Connect failed (%s) — preview via mpv", exc)
                self._start_mpv_into_feeder(preview, position_ms=0)
                return
            raise

    def _resolve_airplay_source_ref(self) -> Optional[str]:
        """Prefer a live AirPlay input host endpoint, else MUSYNC_AIRPLAY_SOURCE."""
        from ..streaming import get_streaming_service

        for host in self._inputs.list_hosts():
            if (
                host.capture_path == CapturePath.AIRPLAY
                and host.connected
                and host.endpoint
            ):
                return host.endpoint
        env_src = get_streaming_service().apple_airplay_source()
        if env_src:
            return env_src
        # Last resort: classify any discovered shairport source.
        for node in self._discovered_sources:
            if classify_capture_source(node) == CapturePath.AIRPLAY.value:
                return node.name or node.id
        return None

    def _start_apple_into_feeder(self) -> None:
        """Loop AirPlay/shairport PipeWire source into musync_feeder (no MusicKit)."""
        from ..streaming import get_streaming_service

        svc = get_streaming_service()
        source_ref = self._resolve_airplay_source_ref()
        if not source_ref:
            raise ValueError(svc.apple_play_note())
        self._ensure_feeder()
        host_id = "airplay-receive"
        cmds, notes = build_capture_loopback_commands(
            host_id=host_id,
            feeder=self._feeder,
            source_ref=source_ref,
            tools=self._tools,
        )
        for note in notes:
            log.info("%s", note)
        if self._env_dry_run():
            for cmd in cmds:
                log.info("Dry-run AirPlay capture: %s", " ".join(cmd))
            self._active_capture_host_id = host_id
            return
        self._unload_capture(host_id)
        for cmd in cmds:
            proc = self._exec_soft(cmd)
            if (
                proc
                and proc.returncode == 0
                and len(cmd) >= 3
                and cmd[1] == "load-module"
                and cmd[2] == "module-loopback"
                and proc.stdout.strip().isdigit()
            ):
                self._capture_module_ids[host_id] = proc.stdout.strip()
        self._active_capture_host_id = host_id
        log.info("AirPlay capture %s → %s", source_ref, self._feeder)

    def _start_youtube_into_feeder(self, locator: str) -> None:
        """yt-dlp audio → ffmpeg → Pulse sink musync_feeder."""
        from ..streaming import get_streaming_service

        self._ensure_feeder()
        svc = get_streaming_service()
        if self._env_dry_run():
            log.info("Dry-run YouTube play %s → %s", locator, self._feeder)
            return
        result = svc.play_youtube(locator, feeder=self._feeder)
        log.info("YouTube play: %s", result)

    def _start_player_process(self) -> None:
        """Start feeder: local mpv, streaming, network upload, or capture.

        Does not tear down sink loopbacks / SPP listens.
        """
        self._stop_player_process()
        transport = self._session.get_transport()

        if transport.source_type == SourceType.SPOTIFY:
            locator = self._session.active_locator() or ""
            # Surface Premium / librespot / Connect failures to the API/UI.
            self._start_spotify_into_feeder(locator)
            return

        if transport.source_type == SourceType.APPLE:
            # AirPlay receive only — MusicKit catalog search removed.
            self._start_apple_into_feeder()
            return

        if transport.source_type == SourceType.YOUTUBE:
            locator = self._session.active_locator() or ""
            self._start_youtube_into_feeder(locator)
            return

        if transport.source_type == SourceType.INPUT and transport.input_id:
            try:
                host = self._inputs.get_host(transport.input_id)
            except KeyError:
                log.warning("Active input host missing: %s", transport.input_id)
                return
            if host.capture_path in {
                CapturePath.BT_A2DP,
                CapturePath.USB,
                CapturePath.HDMI,
                CapturePath.AIRPLAY,
            }:
                self._start_capture_into_feeder(host)
                return
            if host.capture_path == CapturePath.NETWORK and host.endpoint:
                path = Path(host.endpoint)
                if path.is_file():
                    self._start_mpv_into_feeder(
                        str(path), position_ms=transport.position_ms
                    )
                    return
                log.info(
                    "Network capture endpoint not a file yet: %s", host.endpoint
                )
                return

        path = self._session.active_file_path()
        if not path:
            log.info(
                "No local file for feeder (%s / %s) — software clock only",
                transport.source_type,
                transport.queue_item_id or transport.source_id,
            )
            return
        self._start_mpv_into_feeder(path, position_ms=transport.position_ms)

    def _discover_capture_candidates(self) -> tuple[list[PwNode], str]:
        """PipeWire Audio/Source + ALSA USB + HDMI/capture cards."""
        nodes, detail = discover_sources(
            wpctl=self._tools["wpctl"],
            pw_cli=self._tools["pw-cli"],
        )
        arecord = which("arecord")
        alsa_cards, alsa_detail = discover_alsa_usb_cards(arecord=arecord)
        hdmi_cards, hdmi_detail = discover_alsa_hdmi_cards(arecord=arecord)
        merged = merge_alsa_usb_into_sources(nodes, alsa_cards)
        merged = merge_alsa_hdmi_into_sources(merged, hdmi_cards)
        candidates = [n for n in merged if is_capture_candidate(n)]
        # Synthesized ALSA-only nodes are always capture candidates.
        for n in merged:
            if n.id.startswith("alsa:") and n not in candidates:
                candidates.append(n)
        detail_full = f"{detail}; {alsa_detail}; {hdmi_detail}"
        return candidates, detail_full

    def _auto_bind_usb_hosts(self, candidates: list[PwNode]) -> list[str]:
        """Bind unbound USB PipeWire/ALSA sources onto PC hosts (prefer auto-detect).

        Returns host ids that were newly bound or created.
        Pi 5 USB-C PD is power-only — these are USB-A sound cards / interfaces.
        """
        usb_nodes = [
            n
            for n in candidates
            if classify_capture_source(n) == CapturePath.USB.value
        ]
        if not usb_nodes:
            return []

        bound: list[str] = []
        hosts = self._inputs.list_hosts()
        used_endpoints = {
            (h.endpoint or "").strip()
            for h in hosts
            if h.capture_path == CapturePath.USB and h.endpoint
        }

        def _claim(node: PwNode, host_id: str, *, name: str | None = None) -> None:
            source_ref = node.name or node.id
            host = self._inputs.get_host(host_id)
            # Keep friendly demo names (Laptop) when binding a generic codec.
            keep_name = host.name if "laptop" in host.name.lower() else None
            patch_kwargs: dict = {
                "capture_path": CapturePath.USB,
                "connected": True,
                "endpoint": source_ref,
                "armed": True,
                "kind": HostKind.PC,
                "note": CAPTURE_NOTES[CapturePath.USB],
            }
            if name and not keep_name:
                patch_kwargs["name"] = name
            self._inputs.patch_host(host_id, InputHostPatch(**patch_kwargs))
            used_endpoints.add(source_ref)
            bound.append(host_id)

        for node in usb_nodes:
            source_ref = node.name or node.id
            if source_ref in used_endpoints:
                continue
            # Prefer an existing USB host that is not live yet.
            waiting = next(
                (
                    h
                    for h in self._inputs.list_hosts()
                    if h.capture_path == CapturePath.USB
                    and not (h.connected and h.endpoint)
                ),
                None,
            )
            label = (node.description or "").strip() or node.name
            if waiting:
                _claim(node, waiting.id, name=label if label else waiting.name)
                continue
            # Already have a live USB host with this endpoint? skip.
            if any(
                (h.endpoint or "") == source_ref
                for h in self._inputs.list_hosts()
            ):
                continue
            if len(self._inputs.list_hosts()) >= MAX_INPUT_HOSTS:
                break
            created = self._inputs.add_host(
                InputHostCreate(
                    name=label or "Laptop (USB)",
                    kind=HostKind.PC,
                    capture_path=CapturePath.USB,
                    armed=True,
                    connected=True,
                    endpoint=source_ref,
                    note=CAPTURE_NOTES[CapturePath.USB],
                )
            )
            used_endpoints.add(source_ref)
            bound.append(created.id)
        return bound

    def _auto_bind_hdmi_hosts(self, candidates: list[PwNode]) -> list[str]:
        """Bind unbound HDMI / capture-card sources onto HDMI hosts."""
        hdmi_nodes = [
            n
            for n in candidates
            if classify_capture_source(n) == CapturePath.HDMI.value
        ]
        if not hdmi_nodes:
            return []

        bound: list[str] = []
        used_endpoints = {
            (h.endpoint or "").strip()
            for h in self._inputs.list_hosts()
            if h.capture_path == CapturePath.HDMI and h.endpoint
        }

        def _claim(node: PwNode, host_id: str, *, name: str | None = None) -> None:
            source_ref = node.name or node.id
            host = self._inputs.get_host(host_id)
            keep_name = "hdmi" in host.name.lower() or "capture" in host.name.lower()
            patch_kwargs: dict = {
                "capture_path": CapturePath.HDMI,
                "connected": True,
                "endpoint": source_ref,
                "armed": True,
                "kind": HostKind.OTHER,
                "note": CAPTURE_NOTES[CapturePath.HDMI],
            }
            if name and not keep_name:
                patch_kwargs["name"] = name
            self._inputs.patch_host(host_id, InputHostPatch(**patch_kwargs))
            used_endpoints.add(source_ref)
            bound.append(host_id)

        for node in hdmi_nodes:
            source_ref = node.name or node.id
            if source_ref in used_endpoints:
                continue
            waiting = next(
                (
                    h
                    for h in self._inputs.list_hosts()
                    if h.capture_path == CapturePath.HDMI
                    and not (h.connected and h.endpoint)
                ),
                None,
            )
            label = (node.description or "").strip() or node.name
            if waiting:
                _claim(node, waiting.id, name=label if label else waiting.name)
                continue
            if any(
                (h.endpoint or "") == source_ref for h in self._inputs.list_hosts()
            ):
                continue
            if len(self._inputs.list_hosts()) >= MAX_INPUT_HOSTS:
                break
            created = self._inputs.add_host(
                InputHostCreate(
                    name=label or "HDMI Capture",
                    kind=HostKind.OTHER,
                    capture_path=CapturePath.HDMI,
                    armed=True,
                    connected=True,
                    endpoint=source_ref,
                    note=CAPTURE_NOTES[CapturePath.HDMI],
                )
            )
            used_endpoints.add(source_ref)
            bound.append(created.id)
        return bound

    def _auto_bind_airplay_hosts(self, candidates: list[PwNode]) -> list[str]:
        """Bind unbound shairport-sync / AirPlay sources onto AirPlay hosts."""
        from ..streaming import get_streaming_service

        airplay_nodes = [
            n
            for n in candidates
            if classify_capture_source(n) == CapturePath.AIRPLAY.value
        ]
        env_src = get_streaming_service().apple_airplay_source()
        if not airplay_nodes and env_src:
            # Synthetic node from env when PipeWire discovery hasn't listed it yet.
            airplay_nodes = [
                PwNode(
                    id=f"airplay:{env_src}",
                    name=env_src,
                    description="AirPlay / shairport-sync",
                    mac="",
                )
            ]
        if not airplay_nodes:
            return []

        bound: list[str] = []
        used_endpoints = {
            (h.endpoint or "").strip()
            for h in self._inputs.list_hosts()
            if h.capture_path == CapturePath.AIRPLAY and h.endpoint
        }

        for node in airplay_nodes:
            source_ref = node.name or node.id
            if source_ref in used_endpoints:
                continue
            waiting = next(
                (
                    h
                    for h in self._inputs.list_hosts()
                    if h.capture_path == CapturePath.AIRPLAY
                    and not (h.connected and h.endpoint)
                ),
                None,
            )
            label = (node.description or "").strip() or node.name
            if waiting:
                keep = "airplay" in waiting.name.lower() or "shairport" in waiting.name.lower()
                patch_kwargs: dict = {
                    "capture_path": CapturePath.AIRPLAY,
                    "connected": True,
                    "endpoint": source_ref,
                    "armed": True,
                    "kind": HostKind.OTHER,
                    "note": CAPTURE_NOTES[CapturePath.AIRPLAY],
                }
                if label and not keep:
                    patch_kwargs["name"] = label
                self._inputs.patch_host(waiting.id, InputHostPatch(**patch_kwargs))
                used_endpoints.add(source_ref)
                bound.append(waiting.id)
                continue
            if any(
                (h.endpoint or "") == source_ref for h in self._inputs.list_hosts()
            ):
                continue
            if len(self._inputs.list_hosts()) >= MAX_INPUT_HOSTS:
                break
            created = self._inputs.add_host(
                InputHostCreate(
                    name=label or "AirPlay Receive",
                    kind=HostKind.OTHER,
                    capture_path=CapturePath.AIRPLAY,
                    armed=True,
                    connected=True,
                    endpoint=source_ref,
                    note=CAPTURE_NOTES[CapturePath.AIRPLAY],
                )
            )
            used_endpoints.add(source_ref)
            bound.append(created.id)
        return bound

    def _auto_bind_bt_hosts(self, candidates: list[PwNode]) -> list[str]:
        """Bind unbound bluez_input sources onto phone BT A2DP hosts (e.g. Galaxy S21)."""
        bt_nodes = [
            n
            for n in candidates
            if classify_capture_source(n) == CapturePath.BT_A2DP.value
        ]
        if not bt_nodes:
            return []

        bound: list[str] = []
        used_endpoints = {
            (h.endpoint or "").strip()
            for h in self._inputs.list_hosts()
            if h.capture_path == CapturePath.BT_A2DP and h.endpoint
        }

        for node in bt_nodes:
            source_ref = node.name or node.id
            if source_ref in used_endpoints:
                continue
            waiting = next(
                (
                    h
                    for h in self._inputs.list_hosts()
                    if h.capture_path == CapturePath.BT_A2DP
                    and not (h.connected and h.endpoint)
                ),
                None,
            )
            label = (node.description or "").strip() or node.name
            if waiting:
                keep = any(
                    k in waiting.name.lower() for k in ("s21", "galaxy", "phone")
                )
                patch = InputHostPatch(
                    capture_path=CapturePath.BT_A2DP,
                    connected=True,
                    endpoint=source_ref,
                    armed=True,
                    kind=HostKind.PHONE,
                    note=CAPTURE_NOTES[CapturePath.BT_A2DP],
                )
                if label and not keep:
                    patch = patch.model_copy(update={"name": label})
                self._inputs.patch_host(waiting.id, patch)
                used_endpoints.add(source_ref)
                bound.append(waiting.id)
                continue
            if any(
                (h.endpoint or "") == source_ref for h in self._inputs.list_hosts()
            ):
                continue
            if len(self._inputs.list_hosts()) >= MAX_INPUT_HOSTS:
                break
            created = self._inputs.add_host(
                InputHostCreate(
                    name=label or "Phone (BT A2DP)",
                    kind=HostKind.PHONE,
                    capture_path=CapturePath.BT_A2DP,
                    armed=True,
                    connected=True,
                    endpoint=source_ref,
                    note=CAPTURE_NOTES[CapturePath.BT_A2DP],
                )
            )
            used_endpoints.add(source_ref)
            bound.append(created.id)
        return bound

    def _refresh_input_host_links(self) -> None:
        """Match registered hosts to live PipeWire/ALSA sources; update connected/endpoint."""
        candidates, detail = self._discover_capture_candidates()
        self._discovered_sources = candidates
        log.debug("Capture discovery: %s (%d candidates)", detail, len(candidates))

        for host in self._inputs.list_hosts():
            if host.capture_path == CapturePath.NETWORK:
                # File upload path — connected when endpoint file exists.
                ep = host.endpoint or ""
                live = bool(ep and Path(ep).is_file())
                if host.connected != live:
                    self._inputs.patch_host(
                        host.id,
                        InputHostPatch(
                            connected=live,
                            note=(
                                CAPTURE_NOTES[CapturePath.NETWORK]
                                if live
                                else STUB_CAPTURE_NOTES[CapturePath.NETWORK]
                            ),
                        ),
                    )
                continue

            prefer = host.capture_path.value
            match = match_capture_source(
                endpoint=host.endpoint or "",
                name=host.name,
                nodes=candidates,
                prefer_path=prefer,
            )
            if match:
                source_ref = match.name or match.id
                self._inputs.patch_host(
                    host.id,
                    InputHostPatch(
                        connected=True,
                        endpoint=source_ref,
                        note=CAPTURE_NOTES[host.capture_path],
                    ),
                )
            elif host.endpoint and any(
                host.endpoint in n.name or n.id == host.endpoint for n in candidates
            ):
                self._inputs.patch_host(
                    host.id,
                    InputHostPatch(
                        connected=True,
                        note=CAPTURE_NOTES[host.capture_path],
                    ),
                )
            else:
                # Keep explicit endpoint but mark disconnected if not visible.
                if host.connected:
                    self._inputs.patch_host(
                        host.id,
                        InputHostPatch(
                            connected=False,
                            note=STUB_CAPTURE_NOTES[host.capture_path],
                        ),
                    )

        # Prefer auto-detect: claim USB / HDMI / AirPlay / BT A2DP without manual bind.
        try:
            self._auto_bind_hdmi_hosts(candidates)
        except Exception as exc:  # noqa: BLE001
            log.debug("HDMI auto-bind skipped: %s", exc)
        try:
            self._auto_bind_usb_hosts(candidates)
        except Exception as exc:  # noqa: BLE001
            log.debug("USB auto-bind skipped: %s", exc)
        try:
            self._auto_bind_airplay_hosts(candidates)
        except Exception as exc:  # noqa: BLE001
            log.debug("AirPlay auto-bind skipped: %s", exc)
        try:
            self._auto_bind_bt_hosts(candidates)
        except Exception as exc:  # noqa: BLE001
            log.debug("BT auto-bind skipped: %s", exc)

        self._session.refresh_live_availability()

    # --- AudioBackend API ---

    def list_devices(self) -> list[Device]:
        with self._lock:
            # Soft refresh so UI READY/MISSING tracks BT/SPP after reboot.
            try:
                self._refresh_live_sink_state(reconnect=True)
            except Exception as exc:  # noqa: BLE001 — never fail listing
                log.debug("live sink refresh: %s", exc)
            # Headset Absolute Volume → sliders (throttled; soft-fail).
            try:
                self._poll_avrcp_volumes()
            except Exception as exc:  # noqa: BLE001
                log.debug("AVRCP list_devices poll: %s", exc)
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
            trial = [updated if d.id == device_id else d for d in self._devices.values()]
            validate_devices(trial)
            self._devices[device_id] = updated
            if updated.kind == DeviceKind.BLUETOOTH and updated.pw_node:
                self._pw_node_ids[device_id] = updated.pw_node
            self._persist()

            # Per-sink controls only — never treat delay/volume as global transport.
            spp_restart_fields = {
                "listen_host",
                "listen_port",
                "sample_rate",
                "channels",
                "enabled",
                "delay_ms",
                "volume",
                "mute",
            }
            if updated.kind == DeviceKind.NETWORK_SPP and spp_restart_fields & set(
                updates
            ):
                # Rebuild ffmpeg listen with this sink's -af adelay/volume.
                self._restart_spp_listener(updated)
            elif updated.kind != DeviceKind.NETWORK_SPP:
                # BT / PipeWire: loopback latency_msec + wpctl on this node only.
                if "delay_ms" in updates or "enabled" in updates or "pw_node" in updates:
                    self._ensure_graph(only_device=device_id)
                if "volume" in updates or "mute" in updates:
                    # Refresh map so wpctl hits the live bluez node id.
                    try:
                        self.refresh_sink_map()
                    except Exception as exc:  # noqa: BLE001
                        log.debug("sink map before volume: %s", exc)
                    if "volume" in updates:
                        self._apply_volume(updated)
                    if "mute" in updates:
                        self._apply_mute(updated)

            return updated.model_copy(deep=True)

    def discover_device_setup(self) -> DeviceDiscoverResponse:
        with self._lock:
            # Refresh discovery forces a BT reconnect pass (UI "Refresh").
            try:
                self._reconnect_bt_sinks(force=True)
            except Exception as exc:  # noqa: BLE001
                log.debug("BT reconnect on discover: %s", exc)
            adapters, capacity, soft_warn = self._adapter_snapshot()
            _by_name, _by_mac, rows, detail = self._discovery_maps()
            try:
                self.refresh_sink_map()
                self._sync_spp_listeners()
            except Exception as exc:  # noqa: BLE001
                log.debug("post-discover sync: %s", exc)
            spp = next(
                (d for d in self._devices.values() if d.kind == DeviceKind.NETWORK_SPP),
                default_spp_device(),
            )
            ready, missing = self._sink_readiness_summary()
            readiness = ""
            if ready or missing:
                readiness = (
                    f"; READY={','.join(ready) or '—'} "
                    f"MISSING={','.join(missing) or '—'}"
                )
            path = devices_config_path()
            adapter_note = f"; {self._bt_adapters_detail}" if self._bt_adapters_detail else ""
            return DeviceDiscoverResponse(
                bluetooth=rows,
                spp_default=spp.model_copy(deep=True),
                adapters=adapters,
                adapter_capacity=capacity,
                soft_warn=soft_warn,
                config_path=str(path),
                config_ok=self._config_ok,
                config_detail=self._config_detail,
                detail=f"{detail}{adapter_note}{readiness}",
            )

    def apply_devices_config(self, entries: list[DeviceConfigEntry]) -> list[Device]:
        with self._lock:
            by_name, by_mac, _rows, _detail = self._discovery_maps()
            # Stop SPP listeners that may be removed.
            old_spp = {
                d.id for d in self._devices.values() if d.kind == DeviceKind.NETWORK_SPP
            }
            resolved = resolve_devices_from_config(
                entries,
                existing=list(self._devices.values()),
                discovery_by_name=by_name,
                discovery_by_mac=by_mac,
            )
            new_ids = {d.id for d in resolved}
            for spp_id in old_spp - new_ids:
                self._stop_spp_listener(spp_id)
            self._devices = {d.id: d.model_copy(deep=True) for d in resolved}
            self._pw_node_ids = {
                d.id: d.pw_node
                for d in self._devices.values()
                if d.pw_node and d.kind == DeviceKind.BLUETOOTH
            }
            self._persist()
            self._config_ok = True
            self._config_detail = f"saved {len(resolved)} sinks via UI"
            try:
                self.refresh_sink_map()
                self._ensure_graph()
            except Exception as exc:  # noqa: BLE001
                log.warning("Graph refresh after config apply failed: %s", exc)
            return [d.model_copy(deep=True) for d in self._devices.values()]

    def _scan_status_payload(
        self,
        rows: list[DiscoveredBluetoothSink],
        detail: str,
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
            _by_name, _by_mac, rows, detail = self._discovery_maps()
            return self._scan_status_payload(rows, detail)

    def bluetooth_scan_start(self) -> BluetoothScanStatus:
        with self._lock:
            btctl = self._tools.get("bluetoothctl")
            # Prefer a healthy adapter (onboard or first free USB) before scan on.
            try:
                adapters, _cap, _warn = self._adapter_snapshot()
                healthy = next((a for a in adapters if a.healthy), None)
                if healthy and btctl:
                    bluetoothctl_select(btctl, healthy.mac or healthy.hci)
                    bluetoothctl_power_on(btctl)
            except Exception as exc:  # noqa: BLE001
                log.debug("adapter select before scan: %s", exc)
            ok, msg = bluetoothctl_scan_on(btctl)
            self._bt_scanning = bool(ok) or self._bt_scanning
            if ok:
                self._bt_scanning = True
            # Refresh remotes so newly advertised devices enter the cache.
            _by_name, _by_mac, rows, detail = self._discovery_maps()
            return self._scan_status_payload(
                rows, f"{msg}; {detail}" if msg else detail
            )

    def bluetooth_scan_stop(self) -> BluetoothScanStatus:
        with self._lock:
            btctl = self._tools.get("bluetoothctl")
            ok, msg = bluetoothctl_scan_off(btctl)
            if ok or not btctl:
                self._bt_scanning = False
            _by_name, _by_mac, rows, detail = self._discovery_maps()
            return self._scan_status_payload(
                rows, f"{msg}; {detail}" if msg else detail
            )

    def bluetooth_clear_discovery(self) -> BluetoothScanStatus:
        with self._lock:
            kept: dict[str, DiscoveredBluetoothSink] = {}
            cleared = 0
            for mac, row in list(self._bt_scan_cache.items()):
                if row.connected:
                    kept[mac] = row
                    self._bt_scan_cleared_macs.discard(mac)
                else:
                    cleared += 1
                    self._bt_scan_cleared_macs.add(mac)
            # Connected configured sinks always stay visible.
            for d in self._devices.values():
                if d.kind != DeviceKind.BLUETOOTH or not d.mac:
                    continue
                mac = normalize_mac(d.mac)
                if not mac:
                    continue
                if d.connected or (d.enabled and d.connected):
                    kept[mac] = DiscoveredBluetoothSink(
                        name=d.name,
                        mac=mac,
                        pw_node=d.pw_node,
                        hci=d.hci,
                        paired=True,
                        connected=True,
                        configured_id=d.id,
                    )
                    self._bt_scan_cleared_macs.discard(mac)
            self._bt_scan_cache = kept
            _by_name, _by_mac, rows, detail = self._discovery_maps()
            # Filter live rows through cleared set (except connected).
            filtered = [
                r
                for r in rows
                if r.connected
                or not r.mac
                or normalize_mac(r.mac) not in self._bt_scan_cleared_macs
            ]
            return self._scan_status_payload(
                filtered, f"cleared {cleared} · kept {len(filtered)} · {detail}"
            )

    def bluetooth_connect(self, body: BluetoothConnectRequest) -> BluetoothConnectResponse:
        with self._lock:
            mac = normalize_mac(body.mac)
            btctl = self._tools.get("bluetoothctl")
            notes: list[str] = []
            soft_warn = ""

            # Resolve existing configured sink (if any) before HCI pick.
            name = (body.name or "").strip()
            existing: Device | None = None
            for d in self._devices.values():
                if d.kind != DeviceKind.BLUETOOTH:
                    continue
                if normalize_mac(d.mac) == mac or (
                    name and d.name.strip().lower() == name.lower()
                ):
                    existing = d
                    break

            preferred = (body.hci or "").strip()
            adapter, capacity_warn = self._prepare_adapter_for_connect(
                preferred_hci=preferred,
                existing_hci=existing.hci if existing else "",
                exclude_device_id=existing.id if existing else None,
            )
            soft_warn = capacity_warn
            assigned_hci = ""
            if adapter is not None:
                assigned_hci = adapter.hci
                notes.append(f"adapter {adapter.hci}")
            elif body.enable_as_output and existing is None:
                # Soft-fail: BlueZ pair/connect may still work on default HCI,
                # but do not enable an extra A2DP output beyond adapter capacity.
                notes.append(capacity_warn or "no free healthy Bluetooth adapter")

            pair_ok, pair_detail = bluetoothctl_pair(
                btctl, mac, timeout=BT_PAIR_SOFT_TIMEOUT_S
            )
            notes.append(pair_detail)
            ok, connect_detail = bluetoothctl_connect(
                btctl, mac, timeout=BT_CONNECT_SOFT_TIMEOUT_S
            )
            notes.append(connect_detail)
            adapters, _capacity, soft_warn = self._adapter_snapshot()
            if not ok and not self._env_dry_run():
                # Still refresh discovery so UI shows current state.
                _bn, _bm, rows, _d = self._discovery_maps()
                return BluetoothConnectResponse(
                    ok=False,
                    mac=mac,
                    detail="; ".join(n for n in notes if n),
                    bluetooth=rows,
                    adapters=adapters,
                    soft_warn=soft_warn,
                )

            # Update / create scan-cache row.
            cached = self._bt_scan_cache.get(mac)
            label = name or (cached.name if cached else f"BT {mac}")
            row_hci = assigned_hci or (cached.hci if cached else "") or (
                existing.hci if existing else ""
            )
            row = DiscoveredBluetoothSink(
                name=label,
                mac=mac,
                pw_node=cached.pw_node if cached else "",
                pw_name=cached.pw_name if cached else "",
                hci=row_hci,
                paired=True,
                connected=True if ok or self._env_dry_run() else False,
            )
            self._bt_scan_cache[mac] = row
            self._bt_scan_cleared_macs.discard(mac)

            device: Device | None = None
            enable_output = bool(body.enable_as_output)
            if enable_output and adapter is None and existing is None:
                # Capacity soft-fail — keep API up; sink stays unconfigured.
                enable_output = False
                soft_warn = capacity_warn or soft_warn
                notes.append("enable_as_output skipped — no free healthy adapter")

            if enable_output:
                if existing is not None:
                    data = existing.model_dump()
                    data.update(
                        {
                            "mac": mac,
                            "name": label,
                            "connected": True,
                            "enabled": True,
                        }
                    )
                    if assigned_hci:
                        data["hci"] = assigned_hci
                    device = Device.model_validate(data)
                    trial = [
                        device if d.id == existing.id else d
                        for d in self._devices.values()
                    ]
                    validate_devices(trial)
                    self._devices[existing.id] = device
                else:
                    bt_count = sum(
                        1 for d in self._devices.values() if d.kind == DeviceKind.BLUETOOTH
                    )
                    if bt_count >= MAX_BT_SINKS:
                        soft_warn = (
                            f"At most {MAX_BT_SINKS} Bluetooth sinks — "
                            "connect skipped for enable"
                        )
                        notes.append(soft_warn)
                    else:
                        new_id = f"bt-{bt_count + 1}"
                        if new_id in self._devices:
                            new_id = f"bt-{mac.replace(':', '').lower()[-6:]}"
                        device = Device(
                            id=new_id,
                            name=label,
                            kind=DeviceKind.BLUETOOTH,
                            mac=mac,
                            hci=assigned_hci,
                            connected=True,
                            enabled=True,
                            delay_ms=0,
                            volume=0.85,
                        )
                        trial = list(self._devices.values()) + [device]
                        validate_devices(trial)
                        self._devices[device.id] = device
                if device is not None:
                    self._persist()
                    row = row.model_copy(
                        update={"configured_id": device.id, "hci": device.hci}
                    )
                    self._bt_scan_cache[mac] = row
                    try:
                        self.refresh_sink_map()
                        self._ensure_graph()
                    except Exception as exc:  # noqa: BLE001
                        notes.append(f"graph refresh: {exc}")

            _bn, _bm, rows, _d = self._discovery_maps()
            adapters, _capacity, soft_warn = self._adapter_snapshot()
            return BluetoothConnectResponse(
                ok=True if ok or self._env_dry_run() else False,
                mac=mac,
                detail="; ".join(n for n in notes if n),
                device=device.model_copy(deep=True) if device else None,
                bluetooth=rows,
                adapters=adapters,
                soft_warn=soft_warn,
            )

    def get_transport(self) -> Transport:
        return self._session.get_transport()

    def set_transport(self, body: TransportPut) -> Transport:
        # Ensure BT sink graph before Play; swap feeder; then align SPP source
        # (anullsrc ↔ ffmpeg -re -i file) without bouncing a matching listener.
        if body.state is TransportState.PLAYING:
            self._ensure_graph()
        transport = self._session.set_transport(body)
        self._sync_spp_listeners()
        return transport

    def list_sources(self) -> list[Source]:
        return self._session.list_sources()

    def set_active_source(self, source_id: str) -> Transport:
        # Feeder swap only — sink loopbacks remain. Revive dead SPP listens.
        transport = self._session.set_active_source(source_id)
        self._sync_spp_listeners()
        return transport

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
        self._sync_spp_listeners()
        return transport

    def list_inputs(self) -> list[InputHost]:
        with self._lock:
            try:
                self._refresh_input_host_links()
            except Exception as exc:  # noqa: BLE001
                log.debug("Capture refresh failed: %s", exc)
            return self._inputs.list_hosts()

    def add_input(self, body: InputHostCreate) -> InputHost:
        with self._lock:
            host = self._inputs.add_host(body)
            try:
                self._refresh_input_host_links()
                host = self._inputs.get_host(host.id)
            except Exception as exc:  # noqa: BLE001
                log.debug("Capture refresh after add failed: %s", exc)
            return host

    def patch_input(self, host_id: str, patch: InputHostPatch) -> InputHost:
        with self._lock:
            host = self._inputs.patch_host(host_id, patch)
            self._session.refresh_live_availability()
            # If this host is actively capturing and endpoint/path changed, restart.
            if (
                self._active_capture_host_id == host_id
                and self._session.get_transport().state == TransportState.PLAYING
            ):
                self._start_player_process()
            return host

    def remove_input(self, host_id: str) -> None:
        with self._lock:
            if self._active_capture_host_id == host_id:
                self._unload_capture(host_id)
            self._inputs.remove_host(host_id)
            self._session.clear_input_bindings(host_id)
            self._session.refresh_live_availability()

    def discover_capture_sources(self) -> CaptureDiscoverResponse:
        with self._lock:
            candidates, detail = self._discover_capture_candidates()
            self._discovered_sources = candidates
            remotes, bt_detail = discover_bluetooth_remotes(
                bluetoothctl=self._tools["bluetoothctl"],
            )
            remotes_by_mac = {normalize_mac(r.mac): r for r in remotes if r.mac}

            # Auto-bind HDMI / USB / AirPlay before building the response rows.
            auto_ids: list[str] = []
            try:
                auto_ids.extend(self._auto_bind_hdmi_hosts(candidates))
            except Exception as exc:  # noqa: BLE001
                log.debug("HDMI auto-bind on discover failed: %s", exc)
            try:
                auto_ids.extend(self._auto_bind_usb_hosts(candidates))
            except Exception as exc:  # noqa: BLE001
                log.debug("USB auto-bind on discover failed: %s", exc)
            try:
                auto_ids.extend(self._auto_bind_airplay_hosts(candidates))
            except Exception as exc:  # noqa: BLE001
                log.debug("AirPlay auto-bind on discover failed: %s", exc)

            configured_by_name = {
                h.name.strip().lower(): h.id for h in self._inputs.list_hosts()
            }
            configured_by_ep = {
                (h.endpoint or "").strip(): h.id
                for h in self._inputs.list_hosts()
                if h.endpoint
            }

            rows: list[DiscoveredCaptureSource] = []
            for node in candidates:
                path_val = classify_capture_source(node)
                if node.id.startswith("alsa:hdmi:") and path_val == "network":
                    path_val = CapturePath.HDMI.value
                elif node.id.startswith("alsa:") and path_val == "network":
                    path_val = CapturePath.USB.value
                path = CapturePath(path_val)
                label = node.description.strip() if node.description else ""
                label = re.sub(r"\[vol:[^\]]*\]", "", label).strip() if label else ""
                if not label or label.lower().startswith("bluez"):
                    if node.mac and remotes_by_mac.get(normalize_mac(node.mac)):
                        label = remotes_by_mac[normalize_mac(node.mac)].name
                    elif node.mac:
                        label = f"BT {normalize_mac(node.mac)}"
                    else:
                        label = node.name
                cfg = configured_by_name.get(label.lower()) or configured_by_ep.get(
                    node.name
                )
                rows.append(
                    DiscoveredCaptureSource(
                        name=label,
                        pw_node=node.id,
                        pw_name=node.name,
                        mac=normalize_mac(node.mac) if node.mac else "",
                        capture_path=path,
                        connected=True,
                        configured_id=cfg,
                    )
                )

            # Refresh BT / network host links after USB auto-bind.
            try:
                self._refresh_input_host_links()
            except Exception as exc:  # noqa: BLE001
                log.debug("Capture auto-link failed: %s", exc)

            # Rebuild configured_id after refresh (hosts may have new endpoints).
            configured_by_ep = {
                (h.endpoint or "").strip(): h.id
                for h in self._inputs.list_hosts()
                if h.endpoint
            }
            configured_by_name = {
                h.name.strip().lower(): h.id for h in self._inputs.list_hosts()
            }
            refreshed: list[DiscoveredCaptureSource] = []
            for row in rows:
                cfg = (
                    configured_by_name.get(row.name.lower())
                    or configured_by_ep.get(row.pw_name)
                    or row.configured_id
                )
                refreshed.append(row.model_copy(update={"configured_id": cfg}))

            auto_note = (
                f"; auto-bound hosts={','.join(auto_ids)}" if auto_ids else ""
            )
            return CaptureDiscoverResponse(
                sources=refreshed,
                detail=f"{detail}; {bt_detail}{auto_note}",
            )

    def get_now_playing(self):
        from ..nowplaying import build_now_playing

        return build_now_playing(self._session)

    def health(self) -> Health:
        with self._lock:
            # Keep connected flags fresh without spamming BlueZ (throttled).
            try:
                self._refresh_live_sink_state(reconnect=True)
            except Exception as exc:  # noqa: BLE001
                log.debug("health refresh: %s", exc)
            up, detail = self.probe_pipewire()
            devices = list(self._devices.values())
            hci_map = {d.hci: d.mac for d in devices if d.hci}
            mapped = len(self._pw_node_ids)
            dry = self._env_dry_run()
            plan = self._last_plan
            links = len(plan.links) if plan else 0
            tools = ", ".join(
                f"{k}={'yes' if v else 'no'}" for k, v in self._tools.items()
            )
            placeholders = [
                d.id
                for d in devices
                if d.kind == DeviceKind.BLUETOOTH
                and (
                    d.mac.upper().startswith("AA:BB:CC:DD:EE:")
                    or "REPLACE" in d.mac.upper()
                )
            ]
            spp_n = sum(1 for d in devices if d.kind == DeviceKind.NETWORK_SPP)
            ready, missing = self._sink_readiness_summary()
            hint = ""
            if placeholders:
                hint += (
                    f"; PLACEHOLDER_MACS={','.join(placeholders)} "
                    "(use Device setup — discover by name, Save sinks)"
                )
            if spp_n == 0:
                hint += "; NO_SPP_SINK (enable Phone SPP in Device setup)"
            hint += (
                f"; READY={','.join(ready) or '—'} "
                f"MISSING={','.join(missing) or '—'}"
            )
            spp_ports = [
                f"{d.id}:{d.listen_port}={'READY' if d.connected else 'MISSING'}"
                for d in devices
                if d.kind == DeviceKind.NETWORK_SPP and d.enabled
            ]
            if spp_ports:
                hint += f"; SPP={','.join(spp_ports)}"
            from ..streaming import get_streaming_service

            stream = get_streaming_service().status()
            all_ready = not missing
            adapters, capacity, soft_warn = self._adapter_snapshot()
            healthy_n = sum(1 for a in adapters if a.healthy)
            if soft_warn:
                hint += f"; {soft_warn}"
            if self._bt_adapters_detail:
                hint += f"; {self._bt_adapters_detail}"
            avrcp = self._avrcp.status
            if avrcp.supported:
                hint += f"; AVRCP={avrcp.detail or 'ok'}"
            elif avrcp.detail:
                hint += f"; AVRCP soft-fail ({avrcp.detail[:80]})"
            return Health(
                ok=up or dry or all_ready,
                backend="pipewire",
                pipewire_up=up,
                sink_count=len(devices),
                enabled_count=sum(1 for d in devices if d.enabled),
                hci_map=hci_map,
                adapters=adapters,
                adapter_count=len(adapters),
                healthy_adapter_count=healthy_n,
                mapped_sinks=mapped,
                graph_dry_run=dry,
                spotify_ready=stream.spotify.authenticated,
                apple_ready=stream.airplay.configured,
                youtube_ready=stream.youtube.configured,
                avrcp_supported=bool(avrcp.supported),
                detail=(
                    f"{detail}; mapped={mapped}/{len(devices)}; "
                    f"planned_links={links}; dry_run={dry}; "
                    f"adapters={healthy_n}/{len(adapters)} (cap {capacity}); "
                    f"tools: {tools}{hint}"
                ),
            )
