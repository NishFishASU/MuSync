r"""PipeWireBackend — Pi demo control plane via wpctl / pactl / pw-link / mpv.

## Fan-out graph (Phase 1)

```
[mpv --ao=pipewire --audio-device=musync_feeder]
        │
        ▼
  musync_feeder  (null/virtual sink)
        │  module-loopback latency_msec=<delay_ms>   (per enabled sink)
        ├──────────────► bluez_output.<MAC>   (hci0 / speaker 1)
        ├──────────────► bluez_output.<MAC>   (hci1 / speaker 2)
        └──────────────► … up to 6 BT600 A2DP sinks
```

Volume/mute: ``wpctl set-volume|set-mute <node-id>``.
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

wpctl set-volume <NODE_ID> 0.75
wpctl set-mute <NODE_ID> 0

# Play into the feeder
mpv --no-video --ao=pipewire --audio-device=musync_feeder /path/to/track.flac
```

HCI ↔ speaker: one ASUS BT600 per A2DP sink; persist MAC + hci in devices.yaml.
"""

from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

from ..config import (
    clamp_delay_ms,
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
    Device,
    DeviceConfigEntry,
    DeviceDiscoverResponse,
    DeviceKind,
    DevicePatch,
    DiscoveredBluetoothSink,
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
    TransportState,
)
from .base import AudioBackend
from .input_registry import InputRegistry
from .pw_tools import (
    GraphLink,
    GraphPlan,
    PwNode,
    build_sink_commands,
    discover_bluetooth_remotes,
    discover_nodes,
    find_musync_loopback_ids,
    friendly_bluez_label,
    match_device_node,
    normalize_mac,
    run_cmd,
    which,
)
from .source_session import SourceSession
from .spp import (
    build_spp_ffmpeg,
    spp_anullsrc,
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
        self._last_plan: Optional[GraphPlan] = None
        self._discovered: list[PwNode] = []
        self._bt_remotes_by_mac: dict = {}
        self._player_proc: Optional[subprocess.Popen[str]] = None
        self._spp_procs: dict[str, subprocess.Popen[str]] = {}
        # Track SPP input mode so we can switch silence → file on Play without
        # bouncing a healthy listener that already matches the desired source.
        self._spp_source_keys: dict[str, str] = {}

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

        # Refresh map / plan once at startup (no-op / dry-run without hardware).
        try:
            self.refresh_sink_map()
            self._ensure_graph()
        except Exception as exc:  # noqa: BLE001 — never block API boot
            log.warning("Startup graph plan failed: %s", exc)

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
            rows.append(
                DiscoveredBluetoothSink(
                    name=remote.name or f"BT {mac}",
                    mac=mac,
                    paired=remote.paired,
                    connected=remote.connected,
                    configured_id=cfg,
                )
            )

        return by_name, by_mac, rows, f"{detail}; {bt_detail}"

    def _ensure_feeder_commands(self) -> list[list[str]]:
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
        if device.kind == DeviceKind.NETWORK_SPP:
            # SPP listen matches manual ffmpeg (no -af volume) — avoid bounce.
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
        self._exec_soft([wpctl, "set-volume", node, f"{device.volume:.3f}"])

    def _apply_mute(self, device: Device) -> None:
        if device.kind == DeviceKind.NETWORK_SPP:
            # SPP listen matches manual ffmpeg (no -af) — avoid bounce.
            return
        node = self._pw_node_ids.get(device.id)
        wpctl = self._tools["wpctl"]
        if not node or not wpctl or self._env_dry_run():
            log.debug("mute deferred: %s → %s", device.id, device.mute)
            return
        self._exec_soft([wpctl, "set-mute", node, "1" if device.mute else "0"])

    def _apply_delay(self, device: Device) -> None:
        """Re-wire this sink's loopback (BT). SPP uses manual argv without adelay."""
        if device.kind == DeviceKind.NETWORK_SPP:
            # Delay is stored for UI but not applied via -af (breaks SPP handshake).
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

    def _spp_desired_source(self, device: Device) -> tuple[str, str, str]:
        """Return ``(input_kind, input_spec, source_key)`` for the SPP listen.

        Prefer the proven manual shape (``-re -i <file>``) while a local track
        is playing. Otherwise feed continuous ``anullsrc`` silence so the phone
        can complete its handshake before Play — pulse ``feeder.monitor`` often
        emits no frames until something plays into the feeder.
        """
        path = ""
        try:
            path = self._session.active_file_path() or ""
        except Exception:  # noqa: BLE001 — session optional during early init
            path = ""
        transport = self._session.get_transport()
        if path and transport.state == TransportState.PLAYING:
            return "file", path, f"file:{path}"
        spec = spp_anullsrc(device)
        return "lavfi", spec, f"lavfi:{spec}"

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

        ff = self._tools.get("ffmpeg")
        if not ff:
            log.warning("ffmpeg missing — cannot start SPP listener for %s", device.id)
            device.connected = False
            return

        cmd = build_spp_ffmpeg(
            device, input_kind=kind, input_spec=spec, ffmpeg_bin=ff
        )
        # Ensure feeder exists for BT fan-out; SPP itself uses file/anullsrc.
        for feeder_cmd in self._ensure_feeder_commands():
            self._exec_soft(feeder_cmd)

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

        # Live path — create feeder (idempotent soft-fail if exists).
        for cmd in self._ensure_feeder_commands():
            self._exec_soft(cmd)

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

    def _stop_player_process(self) -> None:
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

    def _start_player_process(self) -> None:
        """Play local file into the MuSync feeder. Does not touch sink loopbacks."""
        self._stop_player_process()
        path = self._session.active_file_path()
        transport = self._session.get_transport()
        if not path:
            log.info(
                "No local file for feeder (%s / %s) — software clock only",
                transport.source_type,
                transport.queue_item_id or transport.source_id,
            )
            return

        if self._env_dry_run():
            log.info(
                "Dry-run playback: mpv --ao=pipewire --audio-device=%s %s",
                self._feeder,
                path,
            )
            return

        mpv = self._tools["mpv"]
        pw_play = self._tools["pw-play"]
        try:
            if mpv:
                self._player_proc = subprocess.Popen(
                    [
                        mpv,
                        "--no-video",
                        "--ao=pipewire",
                        f"--audio-device={self._feeder}",
                        f"--start={transport.position_ms / 1000:.3f}",
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

    # --- AudioBackend API ---

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
            trial = [updated if d.id == device_id else d for d in self._devices.values()]
            validate_devices(trial)
            self._devices[device_id] = updated
            if updated.kind == DeviceKind.BLUETOOTH and updated.pw_node:
                self._pw_node_ids[device_id] = updated.pw_node
            self._persist()

            # SPP uses file/anullsrc without -af — only restart on bind-shape changes.
            spp_bind_fields = {
                "listen_host",
                "listen_port",
                "sample_rate",
                "channels",
                "enabled",
            }
            if updated.kind == DeviceKind.NETWORK_SPP and spp_bind_fields & set(updates):
                self._restart_spp_listener(updated)
            elif updated.kind != DeviceKind.NETWORK_SPP:
                if "volume" in updates:
                    self._apply_volume(updated)
                if "mute" in updates:
                    self._apply_mute(updated)
                if "delay_ms" in updates or "enabled" in updates or "pw_node" in updates:
                    self._ensure_graph(only_device=device_id)

            return updated.model_copy(deep=True)

    def discover_device_setup(self) -> DeviceDiscoverResponse:
        with self._lock:
            _by_name, _by_mac, rows, detail = self._discovery_maps()
            spp = next(
                (d for d in self._devices.values() if d.kind == DeviceKind.NETWORK_SPP),
                default_spp_device(),
            )
            path = devices_config_path()
            return DeviceDiscoverResponse(
                bluetooth=rows,
                spp_default=spp.model_copy(deep=True),
                config_path=str(path),
                config_ok=self._config_ok,
                config_detail=self._config_detail,
                detail=detail,
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
            hint = ""
            if placeholders:
                hint += (
                    f"; PLACEHOLDER_MACS={','.join(placeholders)} "
                    "(edit config/devices.yaml — see devices.pi-2sink.example.yaml)"
                )
            if spp_n == 0:
                hint += "; NO_SPP_SINK (add kind: network_spp spp-1)"
            return Health(
                ok=True,
                backend="pipewire",
                pipewire_up=up,
                sink_count=len(devices),
                enabled_count=sum(1 for d in devices if d.enabled),
                hci_map=hci_map,
                mapped_sinks=mapped,
                graph_dry_run=dry,
                detail=(
                    f"{detail}; mapped={mapped}/{len(devices)}; "
                    f"planned_links={links}; dry_run={dry}; tools: {tools}{hint}"
                ),
            )
