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
    PLACEHOLDER_SOURCES,
    clamp_delay_ms,
    clamp_volume,
    discover_local_sources,
    load_devices,
    repo_root,
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
from .pw_tools import (
    GraphLink,
    GraphPlan,
    PwNode,
    build_sink_commands,
    discover_nodes,
    find_musync_loopback_ids,
    match_device_node,
    normalize_mac,
    run_cmd,
    which,
)

log = logging.getLogger(__name__)

DEFAULT_FEEDER = "musync_feeder"


class PipeWireBackend(AudioBackend):
    """PipeWire-oriented backend: graph planner + CLI execution for Pi demos."""

    name = "pipewire"

    def __init__(self, devices: Optional[list[Device]] = None) -> None:
        self._lock = threading.RLock()
        self._devices: dict[str, Device] = {
            d.id: d.model_copy(deep=True)
            for d in (devices if devices is not None else load_devices())
        }
        if len(self._devices) > 6:
            raise ValueError("MuSync supports at most 6 sinks")
        if len(self._devices) < 1:
            raise ValueError("At least one device profile is required")

        self._feeder = os.environ.get("MUSYNC_PW_FEEDER", DEFAULT_FEEDER).strip() or DEFAULT_FEEDER
        self._pw_node_ids: dict[str, str] = {
            d.id: d.pw_node for d in self._devices.values() if d.pw_node
        }
        self._pw_node_names: dict[str, str] = {}
        self._loopback_module_ids: dict[str, str] = {}
        self._last_plan: Optional[GraphPlan] = None
        self._discovered: list[PwNode] = []

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
        self._player_proc: Optional[subprocess.Popen[str]] = None

        self._tools = {
            "wpctl": which("wpctl"),
            "pw-cli": which("pw-cli"),
            "pw-link": which("pw-link"),
            "pw-play": which("pw-play"),
            "mpv": which("mpv"),
            "pactl": which("pactl"),
            "bluetoothctl": which("bluetoothctl"),
        }

        # Refresh map / plan once at startup (no-op / dry-run without hardware).
        try:
            self.refresh_sink_map()
            self._ensure_graph()
        except Exception as exc:  # noqa: BLE001 — never block API boot
            log.warning("Startup graph plan failed: %s", exc)

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
                    note="Add WAV/FLAC/MP3 under media/ for real playback on Pi",
                )
            ]
        return local + list(PLACEHOLDER_SOURCES)

    def _source_by_id(self, source_id: str) -> Source:
        for src in self._sources:
            if src.id == source_id:
                return src
        raise KeyError(f"Unknown source: {source_id}")

    def _tick_position(self) -> None:
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
            self._stop_player_process()
        else:
            self._transport.position_ms = pos

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
        self._discovered = nodes
        log.info("Sink discovery: %s", detail)

        mapped: dict[str, str] = {}
        for device in self._devices.values():
            node = match_device_node(
                mac=device.mac,
                hci=device.hci,
                pw_node_override=device.pw_node or self._pw_node_ids.get(device.id, ""),
                nodes=nodes,
            )
            if node:
                self._pw_node_ids[device.id] = node.id
                self._pw_node_names[device.id] = node.name
                mapped[device.id] = node.id
                # Reflect link state when we can see the node.
                if device.mac or device.pw_node:
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
        node = self._pw_node_ids.get(device.id)
        wpctl = self._tools["wpctl"]
        if not node or not wpctl or self._env_dry_run():
            log.debug("mute deferred: %s → %s", device.id, device.mute)
            return
        self._exec_soft([wpctl, "set-mute", node, "1" if device.mute else "0"])

    def _apply_delay(self, device: Device) -> None:
        """Re-wire this sink's loopback with updated latency_msec."""
        if not device.enabled:
            return
        # Rebuild just this sink by running ensure_graph for all — keeps feeder shared.
        self._ensure_graph(only_device=device.id)

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
            return plan

        # Live path — create feeder (idempotent soft-fail if exists).
        for cmd in self._ensure_feeder_commands():
            self._exec_soft(cmd)

        for link in plan.links:
            if only_device and link.device_id != only_device:
                continue
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
            if not device.enabled:
                self._unload_loopback(device.id)

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
        """Play local file into the MuSync feeder (or default PipeWire)."""
        self._stop_player_process()
        src_id = self._transport.source_id
        if not src_id:
            return
        try:
            src = self._source_by_id(src_id)
        except KeyError:
            return
        if not src.path:
            log.info("No file path for %s — software clock only", src_id)
            return

        if self._env_dry_run():
            log.info(
                "Dry-run playback: mpv --ao=pipewire --audio-device=%s %s",
                self._feeder,
                src.path,
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
                        f"--start={self._transport.position_ms / 1000:.3f}",
                        src.path,
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    text=True,
                )
            elif pw_play:
                self._player_proc = subprocess.Popen(
                    [pw_play, f"--target={self._feeder}", src.path],
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
            self._devices[device_id] = updated
            if updated.pw_node:
                self._pw_node_ids[device_id] = updated.pw_node
            self._persist()

            if "volume" in updates:
                self._apply_volume(updated)
            if "mute" in updates:
                self._apply_mute(updated)
            if "delay_ms" in updates or "enabled" in updates or "pw_node" in updates:
                self._ensure_graph(only_device=device_id)

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
                    self._start_player_process()

            if body.state is not None:
                self._apply_state(body.state)

            return self._transport.model_copy(deep=True)

    def _apply_state(self, state: TransportState) -> None:
        current = self._transport.state
        if state == TransportState.PLAYING:
            if current == TransportState.PLAYING:
                return
            self._ensure_graph()
            self._play_started_wall = time.monotonic()
            self._play_started_pos = self._transport.position_ms
            self._transport.state = TransportState.PLAYING
            self._start_player_process()
        elif state == TransportState.PAUSED:
            self._tick_position()
            self._play_started_wall = None
            self._transport.state = TransportState.PAUSED
            self._stop_player_process()
        elif state == TransportState.STOPPED:
            self._play_started_wall = None
            self._play_started_pos = 0
            self._transport.position_ms = 0
            self._transport.state = TransportState.STOPPED
            self._stop_player_process()

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

    def list_sources(self) -> list[Source]:
        with self._lock:
            placeholders = [s for s in self._sources if s.type != SourceType.LOCAL]
            local = discover_local_sources()
            if not local:
                local = [s for s in self._sources if s.type == SourceType.LOCAL]
            self._sources = local + placeholders
            return [s.model_copy(deep=True) for s in self._sources]

    def set_active_source(self, source_id: str) -> Transport:
        with self._lock:
            was_playing = self._transport.state == TransportState.PLAYING
            if was_playing:
                self._stop_player_process()
            self._apply_source(source_id, reset_position=True)
            self._transport.state = (
                TransportState.PLAYING if was_playing else TransportState.STOPPED
            )
            if was_playing:
                self._play_started_wall = time.monotonic()
                self._play_started_pos = 0
                self._start_player_process()
            return self._transport.model_copy(deep=True)

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
                    f"planned_links={links}; dry_run={dry}; tools: {tools}"
                ),
            )
