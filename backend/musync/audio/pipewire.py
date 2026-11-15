r"""PipeWireBackend — Pi-ready control plane via wpctl / pw-cli / pw-play.

Without hardware this backend still boots: it loads the same device profile,
tracks transport state in software, and exposes health that reports whether
PipeWire tools are present. Real graph mutations are implemented as documented
subprocess hooks so the Pi path can be filled in without changing the HTTP API.

## How this will talk to PipeWire on a Pi 5

Target graph (one feeder → N delayed/volumed sinks):

    [mpv/ffplay/pw-play file] → PipeWire source/filter
         → loop / link → [delay node N] → [volume] → bluez_output.… (hci0)
                       → [delay node N] → [volume] → bluez_output.… (hci1)
                       → … up to 6 BT600 A2DP sinks

Control tools (WirePlumber / PipeWire CLI):

| Concern | Tooling (Phase 0 plan) |
|---------|-------------------------|
| List sinks / default | `wpctl status`, `pw-cli ls Node` |
| Per-sink volume / mute | `wpctl set-volume <id> <0–1>`, `wpctl set-mute <id> 0|1` |
| Per-sink latency / delay | Prefer a PipeWire `delay` / `echo-cancel`-style filter node, or module-loopback with latency; alternatively `pw-cli set-param` on a custom filter. Exact node IDs come from WirePlumber after A2DP connect. |
| Connect graph | `pw-link` (or WirePlumber policy) from feeder → delay → bluez sink |
| Playback | `mpv --ao=pipewire` or `pw-play` / `ffplay` feeding the shared source |
| BT adapter map | `bluetoothctl`, `hciconfig` / `bluetoothctl list` → persist MAC ↔ hciN in devices.yaml |

HCI ↔ speaker binding: each ASUS BT600 enumerates as its own adapter (`hci0`…`hci5`).
Pair one speaker per adapter; store `mac` + `hci` in config. On boot, reconnect
and map each `bluez_output.*` PipeWire sink to the configured device id.

This stub implements device/transport/source state + CLI probes. Methods
`_apply_volume`, `_apply_mute`, `_apply_delay`, and `_ensure_graph` are the
hooks that will shell out on real hardware.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import threading
import time
from typing import Optional

from ..config import (
    PLACEHOLDER_SOURCES,
    clamp_delay_ms,
    clamp_volume,
    discover_local_sources,
    load_devices,
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

log = logging.getLogger(__name__)


def _which(cmd: str) -> Optional[str]:
    return shutil.which(cmd)


def _run(
    args: list[str],
    *,
    timeout: float = 5.0,
    check: bool = False,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=check,
    )


class PipeWireBackend(AudioBackend):
    """PipeWire-oriented backend: full state machine + CLI hooks for Pi."""

    name = "pipewire"

    def __init__(self, devices: Optional[list[Device]] = None) -> None:
        self._lock = threading.RLock()
        self._devices: dict[str, Device] = {
            d.id: d.model_copy(deep=True)
            for d in (devices if devices is not None else load_devices())
        }
        if len(self._devices) > 6:
            raise ValueError("MuSync supports at most 6 sinks")

        # Optional mapping: musync device id → PipeWire / WirePlumber node id.
        self._pw_node_ids: dict[str, str] = {}

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
            "wpctl": _which("wpctl"),
            "pw-cli": _which("pw-cli"),
            "pw-link": _which("pw-link"),
            "pw-play": _which("pw-play"),
            "mpv": _which("mpv"),
            "bluetoothctl": _which("bluetoothctl"),
        }

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

    # --- PipeWire / WirePlumber hooks (safe no-ops without hardware) ---

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
                proc = _run([wpctl, "status"], timeout=3.0)
                if proc.returncode == 0:
                    return True, "wpctl status ok"
                return False, (proc.stderr or proc.stdout or "wpctl failed").strip()
            proc = _run([pw_cli, "info", "0"], timeout=3.0)
            if proc.returncode == 0:
                return True, "pw-cli info ok"
            return False, (proc.stderr or proc.stdout or "pw-cli failed").strip()
        except (OSError, subprocess.TimeoutExpired) as exc:
            return False, f"PipeWire probe failed: {exc}"

    def _apply_volume(self, device: Device) -> None:
        """Map device.volume to WirePlumber when a node id is known."""
        node = self._pw_node_ids.get(device.id)
        wpctl = self._tools["wpctl"]
        if not node or not wpctl:
            log.debug(
                "volume deferred (no pw node): %s → %.3f", device.id, device.volume
            )
            return
        # Effective volume respects mute at the API layer; still push level.
        _run([wpctl, "set-volume", node, f"{device.volume:.3f}"])

    def _apply_mute(self, device: Device) -> None:
        node = self._pw_node_ids.get(device.id)
        wpctl = self._tools["wpctl"]
        if not node or not wpctl:
            log.debug("mute deferred (no pw node): %s → %s", device.id, device.mute)
            return
        _run([wpctl, "set-mute", node, "1" if device.mute else "0"])

    def _apply_delay(self, device: Device) -> None:
        """Apply per-sink delay_ms to the device's delay filter node.

        On Pi: create/update a PipeWire delay/filter node linked ahead of the
        bluez sink, then set latency from device.delay_ms. Without a mapped
        node id this is a documented no-op so cloud/dev stays safe.
        """
        node = self._pw_node_ids.get(device.id)
        if not node:
            log.debug(
                "delay deferred (no pw node): %s → %sms", device.id, device.delay_ms
            )
            return
        # Placeholder: real implementation will pw-cli set-param / custom module.
        log.info(
            "TODO apply delay %sms on PipeWire node %s for %s",
            device.delay_ms,
            node,
            device.id,
        )

    def _ensure_graph(self) -> None:
        """Ensure feeder → delay/volume → enabled sinks links exist.

        Full implementation on Pi will:
        1. Resolve bluez_output nodes per MAC/hci.
        2. Create delay+volume filter chain per enabled sink.
        3. pw-link feeder to each chain; unlink disabled sinks without
           tearing down the whole session (single-sink loss tolerance).
        """
        enabled = [d for d in self._devices.values() if d.enabled]
        log.debug("ensure_graph: %d enabled sinks (stub)", len(enabled))

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
        """Best-effort local file playback into PipeWire when tools exist."""
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

        mpv = self._tools["mpv"]
        pw_play = self._tools["pw-play"]
        try:
            if mpv:
                self._player_proc = subprocess.Popen(
                    [
                        mpv,
                        "--no-video",
                        "--ao=pipewire",
                        f"--start={self._transport.position_ms / 1000:.3f}",
                        src.path,
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    text=True,
                )
            elif pw_play:
                # pw-play has limited seek; used when mpv is unavailable.
                self._player_proc = subprocess.Popen(
                    [pw_play, src.path],
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
            self._persist()

            if "volume" in updates:
                self._apply_volume(updated)
            if "mute" in updates:
                self._apply_mute(updated)
            if "delay_ms" in updates:
                self._apply_delay(updated)
            if "enabled" in updates:
                self._ensure_graph()

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
                detail=f"{detail}; tools: {tools}",
            )
