"""BT AVRCP Absolute Volume + transport relay (soft-fail).

When the Pi is A2DP **source** to a headset/speaker:

* Headset volume buttons (Absolute Volume) update BlueZ
  ``org.bluez.MediaTransport1.Volume`` (0–127) and often the PipeWire
  bluez sink via ``wpctl``. MuSync mirrors that into per-sink sliders.
* Headset play / pause / skip are relayed to the active hub source
  (local / Spotify / YouTube / …) via the shared transport + queue.

Everything here soft-fails when ``busctl`` / BlueZ / AVRCP Absolute Volume
are missing (laptop mock, dry-run, unsupported headset) — API stays up.
"""

from __future__ import annotations

import logging
import re
import subprocess
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional

from .pw_tools import normalize_mac, run_cmd, which

log = logging.getLogger(__name__)

# BlueZ Absolute Volume is a byte 0–127.
AVRCP_VOLUME_MAX = 127
# Ignore tiny jitter / Absolute Volume ↔ wpctl rounding.
VOLUME_EPSILON = 0.025
# After UI → wpctl set-volume, ignore Absolute Volume echoes briefly.
ECHO_GUARD_S = 1.75
# Throttle volume polls so UI list_devices cannot spam busctl.
VOLUME_POLL_MIN_INTERVAL_S = 0.85

_DEV_PATH_RE = re.compile(
    r"/org/bluez/[^/]+/dev_([0-9A-Fa-f]{2}(?:_[0-9A-Fa-f]{2}){5})",
    re.IGNORECASE,
)
_VOLUME_PROP_RE = re.compile(
    r"""['\"]?Volume['\"]?\s*[:=]\s*(?:byte\s+|y\s+|<.*>\s*)?(\d{1,3})""",
    re.IGNORECASE,
)
_WPCTL_VOL_RE = re.compile(r"Volume:\s*([0-9.]+)", re.IGNORECASE)
_ACTION_TOKEN_RE = re.compile(
    r"\b(PlayPause|Play|Pause|Stop|Next|Previous|Seek)\b",
    re.IGNORECASE,
)


class AvrcpAction(str, Enum):
    PLAY = "play"
    PAUSE = "pause"
    STOP = "stop"
    TOGGLE = "toggle"
    NEXT = "next"
    PREVIOUS = "previous"


def absolute_volume_to_float(raw: int) -> float:
    """Map BlueZ Absolute Volume byte (0–127) → MuSync 0.0–1.0."""
    v = max(0, min(int(raw), AVRCP_VOLUME_MAX))
    return round(v / float(AVRCP_VOLUME_MAX), 3)


def float_to_absolute_volume(level: float) -> int:
    level = max(0.0, min(1.0, float(level)))
    return int(round(level * AVRCP_VOLUME_MAX))


def parse_wpctl_get_volume(text: str) -> Optional[float]:
    """Parse ``wpctl get-volume <id>`` → 0.0–1.0 (None if unparseable)."""
    m = _WPCTL_VOL_RE.search(text or "")
    if not m:
        return None
    try:
        return round(max(0.0, min(1.0, float(m.group(1)))), 3)
    except ValueError:
        return None


def parse_busctl_volume_property(text: str) -> Optional[int]:
    """Parse ``busctl get-property … Volume`` stdout (``y 64`` / ``byte 64``)."""
    raw = (text or "").strip()
    if not raw:
        return None
    m = re.search(r"\b(?:y|byte)\s+(\d{1,3})\b", raw, re.IGNORECASE)
    if m:
        return max(0, min(int(m.group(1)), AVRCP_VOLUME_MAX))
    m2 = re.search(r"\b(\d{1,3})\b", raw)
    if m2:
        return max(0, min(int(m2.group(1)), AVRCP_VOLUME_MAX))
    return None


def mac_from_bluez_path(path: str) -> Optional[str]:
    m = _DEV_PATH_RE.search(path or "")
    if not m:
        return None
    return normalize_mac(m.group(1).replace("_", ":"))


def parse_managed_objects_volumes(text: str) -> dict[str, float]:
    """Best-effort extract MAC → volume from GetManagedObjects / monitor dumps."""
    out: dict[str, float] = {}
    if not text:
        return out
    # Split roughly by object path lines that mention a device.
    chunks = re.split(r"(?=/org/bluez/)", text)
    for chunk in chunks:
        mac = mac_from_bluez_path(chunk)
        if not mac:
            continue
        if "MediaTransport1" not in chunk and "Volume" not in chunk:
            continue
        m = _VOLUME_PROP_RE.search(chunk)
        if not m:
            continue
        try:
            raw = int(m.group(1))
        except ValueError:
            continue
        if 0 <= raw <= AVRCP_VOLUME_MAX:
            out[mac] = absolute_volume_to_float(raw)
    return out


def parse_monitor_action(line: str) -> Optional[AvrcpAction]:
    """Map a dbus-monitor / busctl-monitor line to a transport action (or None)."""
    if not line:
        return None
    # Prefer PlayPause before Play/Pause so "PlayPause" does not become Play.
    m = _ACTION_TOKEN_RE.search(line)
    if not m:
        return None
    token = m.group(1).lower()
    mapping = {
        "playpause": AvrcpAction.TOGGLE,
        "play": AvrcpAction.PLAY,
        "pause": AvrcpAction.PAUSE,
        "stop": AvrcpAction.STOP,
        "next": AvrcpAction.NEXT,
        "previous": AvrcpAction.PREVIOUS,
        "seek": None,  # ignored — hub uses position scrub via UI
    }
    return mapping.get(token)


def probe_wpctl_volume(wpctl: Optional[str], node_id: str) -> Optional[float]:
    """Read live PipeWire node volume (Absolute Volume often mirrors here)."""
    if not wpctl or not node_id:
        return None
    try:
        proc = run_cmd([wpctl, "get-volume", str(node_id)], timeout=1.5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return parse_wpctl_get_volume(proc.stdout or "")


def probe_bluez_transport_volumes(
    busctl: Optional[str],
    macs: list[str],
) -> tuple[dict[str, float], str]:
    """Soft-fail Absolute Volume probe via ``busctl`` ObjectManager.

    Returns ``({mac: 0..1}, detail)``. Empty dict when unsupported.
    """
    if not busctl:
        return {}, "busctl missing — AVRCP Absolute Volume unsupported"
    want = {normalize_mac(m) for m in macs if m}
    if not want:
        return {}, "no BT MACs to probe"
    try:
        proc = run_cmd(
            [
                busctl,
                "call",
                "--system",
                "org.bluez",
                "/",
                "org.freedesktop.DBus.ObjectManager",
                "GetManagedObjects",
                "--verbose",
            ],
            timeout=2.5,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {}, f"busctl GetManagedObjects: {exc}"
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "busctl failed").strip()
        # Common on laptop / no bluetoothd: soft-fail.
        return {}, f"AVRCP probe soft-fail: {err[:160]}"
    text = (proc.stdout or "") + "\n" + (proc.stderr or "")
    found = parse_managed_objects_volumes(text)
    matched = {mac: vol for mac, vol in found.items() if mac in want}
    if not matched:
        # Fallback: try per-device tree walk for …/fdN Volume properties.
        matched = _probe_per_device_volume(busctl, sorted(want))
    if not matched:
        return {}, "no MediaTransport Volume (headset may lack Absolute Volume)"
    return matched, f"Absolute Volume for {len(matched)} transport(s)"


def _probe_per_device_volume(busctl: str, macs: list[str]) -> dict[str, float]:
    """Walk ``busctl tree`` under each ``dev_XX_…`` and get-property Volume."""
    out: dict[str, float] = {}
    try:
        tree = run_cmd([busctl, "tree", "org.bluez"], timeout=2.0)
    except (OSError, subprocess.TimeoutExpired):
        return out
    if tree.returncode != 0:
        return out
    lines = (tree.stdout or "").splitlines()
    for mac in macs:
        suffix = mac.replace(":", "_")
        needle = f"dev_{suffix}"
        paths = [ln.strip() for ln in lines if needle in ln and "/sep" in ln]
        # Prefer leaf paths that look like file descriptors (…/fd0).
        paths = sorted(paths, key=lambda p: (0 if "/fd" in p else 1, len(p)))
        for path in paths[:6]:
            # busctl tree prints hierarchy with glyphs — extract absolute path.
            m = re.search(r"(/org/bluez/\S+)", path)
            obj = m.group(1) if m else None
            if not obj:
                continue
            try:
                prop = run_cmd(
                    [
                        busctl,
                        "get-property",
                        "org.bluez",
                        obj,
                        "org.bluez.MediaTransport1",
                        "Volume",
                    ],
                    timeout=1.2,
                )
            except (OSError, subprocess.TimeoutExpired):
                continue
            if prop.returncode != 0:
                continue
            raw = parse_busctl_volume_property(prop.stdout or "")
            if raw is None:
                continue
            out[mac] = absolute_volume_to_float(raw)
            break
    return out


def parse_action(value: str) -> AvrcpAction:
    """Parse API / inject action strings (raises ValueError if unknown)."""
    key = (value or "").strip().lower()
    aliases = {
        "play": AvrcpAction.PLAY,
        "pause": AvrcpAction.PAUSE,
        "stop": AvrcpAction.STOP,
        "toggle": AvrcpAction.TOGGLE,
        "playpause": AvrcpAction.TOGGLE,
        "next": AvrcpAction.NEXT,
        "skip": AvrcpAction.NEXT,
        "previous": AvrcpAction.PREVIOUS,
        "prev": AvrcpAction.PREVIOUS,
    }
    if key not in aliases:
        raise ValueError(f"Unknown AVRCP action: {value!r}")
    return aliases[key]


def relay_action_to_session(session, action: AvrcpAction):
    """Apply play/pause/skip to SourceSession for the active hub source.

    Works for local / Spotify / YouTube / Apple / live input — same transport
    clock the web UI uses. Raises ValueError when Play is rejected (unavailable).
    """
    from ..models import TransportPut, TransportState

    if action == AvrcpAction.PLAY:
        return session.set_transport(TransportPut(state=TransportState.PLAYING))
    if action == AvrcpAction.PAUSE:
        return session.set_transport(TransportPut(state=TransportState.PAUSED))
    if action == AvrcpAction.STOP:
        return session.set_transport(TransportPut(state=TransportState.STOPPED))
    if action == AvrcpAction.TOGGLE:
        current = session.get_transport()
        if current.state == TransportState.PLAYING:
            return session.set_transport(TransportPut(state=TransportState.PAUSED))
        return session.set_transport(TransportPut(state=TransportState.PLAYING))
    if action == AvrcpAction.NEXT:
        return session.skip_relative(+1)
    if action == AvrcpAction.PREVIOUS:
        return session.skip_relative(-1)
    raise ValueError(f"Unhandled AVRCP action: {action}")


@dataclass
class AvrcpStatusSnapshot:
    supported: bool = False
    watching: bool = False
    detail: str = "AVRCP not started"
    last_volume_sync: dict[str, float] = field(default_factory=dict)
    last_action: Optional[str] = None
    last_error: str = ""


class AvrcpBridge:
    """Optional Absolute Volume poller + soft-fail transport command watcher."""

    def __init__(
        self,
        *,
        on_volume: Optional[Callable[[str, float], None]] = None,
        on_action: Optional[Callable[[AvrcpAction], None]] = None,
    ) -> None:
        self._on_volume = on_volume
        self._on_action = on_action
        self._lock = threading.RLock()
        self._status = AvrcpStatusSnapshot()
        self._busctl = which("busctl")
        self._wpctl = which("wpctl")
        self._echo_guard: dict[str, tuple[float, float]] = {}
        self._last_seen_vol: dict[str, float] = {}
        self._last_poll_at = 0.0
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._mac_by_device: dict[str, str] = {}
        self._node_by_device: dict[str, str] = {}

    @property
    def status(self) -> AvrcpStatusSnapshot:
        with self._lock:
            return AvrcpStatusSnapshot(
                supported=self._status.supported,
                watching=self._status.watching,
                detail=self._status.detail,
                last_volume_sync=dict(self._status.last_volume_sync),
                last_action=self._status.last_action,
                last_error=self._status.last_error,
            )

    def note_ui_volume(self, device_id: str, volume: float) -> None:
        """Mark a UI-driven volume change so Absolute Volume echoes are ignored."""
        with self._lock:
            self._echo_guard[device_id] = (
                round(float(volume), 3),
                time.monotonic() + ECHO_GUARD_S,
            )
            self._last_seen_vol[device_id] = round(float(volume), 3)

    def update_targets(
        self,
        *,
        mac_by_device: dict[str, str],
        node_by_device: Optional[dict[str, str]] = None,
    ) -> None:
        with self._lock:
            self._mac_by_device = {
                did: normalize_mac(mac)
                for did, mac in mac_by_device.items()
                if mac
            }
            if node_by_device is not None:
                self._node_by_device = {
                    did: nid for did, nid in node_by_device.items() if nid
                }

    def start_watcher(self) -> None:
        """Best-effort ``busctl monitor`` for Volume / PlayPause / Next / Prev."""
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            if not self._busctl:
                self._status.supported = False
                self._status.watching = False
                self._status.detail = (
                    "AVRCP soft-fail — busctl missing (volume/transport relay idle)"
                )
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._watch_loop,
                name="musync-avrcp",
                daemon=True,
            )
            self._thread.start()
            self._status.watching = True
            self._status.supported = True
            self._status.detail = "AVRCP watcher started (busctl monitor)"

    def stop_watcher(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=1.5)
        with self._lock:
            self._status.watching = False

    def poll_volumes(self, *, force: bool = False) -> dict[str, float]:
        """Probe Absolute Volume / wpctl and invoke ``on_volume`` for changes.

        Returns ``{device_id: volume}`` for devices whose headset volume moved.
        """
        now = time.monotonic()
        with self._lock:
            if (
                not force
                and (now - self._last_poll_at) < VOLUME_POLL_MIN_INTERVAL_S
            ):
                return {}
            self._last_poll_at = now
            mac_by_device = dict(self._mac_by_device)
            node_by_device = dict(self._node_by_device)
            busctl = self._busctl
            wpctl = self._wpctl

        if not mac_by_device:
            return {}

        by_mac, detail = probe_bluez_transport_volumes(
            busctl, list(mac_by_device.values())
        )
        # Prefer wpctl when Absolute Volume already mirrored into PipeWire.
        for device_id, node in node_by_device.items():
            mac = mac_by_device.get(device_id)
            if not mac:
                continue
            wp_vol = probe_wpctl_volume(wpctl, node)
            if wp_vol is None:
                continue
            # wpctl wins when present — more accurate for PipeWire Absolute Volume.
            by_mac[mac] = wp_vol

        with self._lock:
            if by_mac:
                self._status.supported = True
                if "soft-fail" not in detail.lower() and "missing" not in detail.lower():
                    self._status.detail = detail
            elif detail and not self._status.watching:
                self._status.detail = detail
            elif not by_mac and detail:
                self._status.last_error = detail

        changed: dict[str, float] = {}
        for device_id, mac in mac_by_device.items():
            vol = by_mac.get(mac)
            if vol is None:
                continue
            if not self._should_apply_headset_volume(device_id, vol):
                continue
            changed[device_id] = vol
            with self._lock:
                self._last_seen_vol[device_id] = vol
                self._status.last_volume_sync[device_id] = vol
            if self._on_volume:
                try:
                    self._on_volume(device_id, vol)
                except Exception as exc:  # noqa: BLE001
                    log.debug("AVRCP volume callback %s: %s", device_id, exc)
        return changed

    def inject_action(self, action: AvrcpAction, *, deliver: bool = True) -> None:
        """Record (and optionally deliver) a transport action.

        ``deliver=False`` updates status only — callers that apply the action
        themselves use this to avoid double toggle/next.
        """
        with self._lock:
            self._status.last_action = action.value
            self._status.supported = True
            if not self._status.detail or "soft-fail" in self._status.detail.lower():
                self._status.detail = "AVRCP transport relay active"
        if not deliver:
            return
        if self._on_action:
            try:
                self._on_action(action)
            except Exception as exc:  # noqa: BLE001
                log.debug("AVRCP action callback %s: %s", action, exc)
                with self._lock:
                    self._status.last_error = str(exc)

    def inject_volume(self, device_id: str, volume: float) -> None:
        """Simulate Absolute Volume from a headset (mock / tests)."""
        vol = round(max(0.0, min(1.0, float(volume))), 3)
        with self._lock:
            self._last_seen_vol[device_id] = vol
            self._status.last_volume_sync[device_id] = vol
            self._status.supported = True
            self._status.detail = "AVRCP volume inject (mock/test)"
        if self._on_volume:
            self._on_volume(device_id, vol)

    def _should_apply_headset_volume(self, device_id: str, volume: float) -> bool:
        with self._lock:
            guard = self._echo_guard.get(device_id)
            if guard is not None:
                gvol, until = guard
                if time.monotonic() < until and abs(gvol - volume) <= VOLUME_EPSILON + 0.02:
                    return False
                if time.monotonic() >= until:
                    self._echo_guard.pop(device_id, None)
            prev = self._last_seen_vol.get(device_id)
            if prev is not None and abs(prev - volume) < VOLUME_EPSILON:
                return False
            return True

    def _watch_loop(self) -> None:
        assert self._busctl
        # Match BlueZ signals; parse Volume + Play/Pause/Next/Previous tokens.
        cmd = [
            self._busctl,
            "monitor",
            "--system",
            "--match=type='signal',sender='org.bluez'",
        ]
        proc: Optional[subprocess.Popen[str]] = None
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except OSError as exc:
            with self._lock:
                self._status.watching = False
                self._status.supported = False
                self._status.detail = f"AVRCP watcher soft-fail: {exc}"
                self._status.last_error = str(exc)
            return

        assert proc.stdout is not None
        try:
            while not self._stop.is_set():
                line = proc.stdout.readline()
                if line == "" and proc.poll() is not None:
                    break
                if not line:
                    time.sleep(0.05)
                    continue
                self._handle_monitor_line(line)
        finally:
            try:
                proc.terminate()
                proc.wait(timeout=1.0)
            except Exception:  # noqa: BLE001
                try:
                    proc.kill()
                except Exception:  # noqa: BLE001
                    pass
            with self._lock:
                self._status.watching = False
                if self._stop.is_set():
                    self._status.detail = "AVRCP watcher stopped"
                else:
                    self._status.detail = (
                        "AVRCP watcher exited — volume poll + API relay still soft-ok"
                    )

    def _handle_monitor_line(self, line: str) -> None:
        action = parse_monitor_action(line)
        if action is not None:
            self.inject_action(action)
            return
        if "Volume" not in line and "MediaTransport" not in line:
            return
        # Volume signal — refresh polls soon (debounce via poll throttle).
        try:
            self.poll_volumes(force=True)
        except Exception as exc:  # noqa: BLE001
            log.debug("AVRCP monitor volume poll: %s", exc)
