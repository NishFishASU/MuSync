"""BT AVRCP Absolute Volume + transport relay (soft-fail).

When the Pi is A2DP **source** to a headset/speaker (e.g. Qudelix):

* Headset volume buttons (Absolute Volume) update BlueZ
  ``org.bluez.MediaTransport1.Volume`` (0–127) and often the PipeWire
  bluez sink via ``wpctl``. MuSync mirrors that into per-sink sliders.
* Headset play / pause / skip require MuSync to **RegisterPlayer** with
  BlueZ ``org.bluez.Media1`` as an MPRIS ``org.mpris.MediaPlayer2.Player``.
  BlueZ then delivers AVRCP passthrough as Play/Pause/Next/Previous method
  calls on that object — relayed to the active hub source (local mpv/ffmpeg,
  Spotify, YouTube, …) via the shared transport + queue.

A ``busctl monitor`` of BlueZ *signals* alone cannot see those buttons
(volume is PropertyChanged; transport is method_call on our player).

Everything here soft-fails when ``busctl`` / ``python3-dbus`` / BlueZ /
Absolute Volume are missing (laptop mock, dry-run) — API stays up.
"""

from __future__ import annotations

import logging
import re
import subprocess
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional

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
# Debounce headset double-fires (PlayPause often emits twice). Keep short so
# buttons feel snappy; 0.1s still collapses typical double-press chatter.
ACTION_DEBOUNCE_S = 0.1

PLAYER_PATH = "/org/musync/player"
PLAYER_IFACE = "org.mpris.MediaPlayer2.Player"
MPRIS_ROOT_IFACE = "org.mpris.MediaPlayer2"
DBUS_PROP_IFACE = "org.freedesktop.DBus.Properties"
BLUEZ_MEDIA_IFACE = "org.bluez.Media1"

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


def transport_state_to_mpris(state: str) -> str:
    """Map MuSync transport state → MPRIS PlaybackStatus."""
    key = (state or "").strip().lower()
    if key == "playing":
        return "Playing"
    if key == "paused":
        return "Paused"
    return "Stopped"


# Hint when the running interpreter cannot import apt python3-dbus / python3-gi
# (common when `.venv` was created without `--system-site-packages`).
_DBUS_IMPORT_HINT = (
    "python3-dbus / python3-gi not importable in this Python — "
    "sudo apt install -y python3-dbus python3-gi; "
    "recreate venv with: python3 -m venv --system-site-packages .venv "
    "(or run ./scripts/run_pi.sh which enables system-site-packages)"
)


def _try_import_dbus():
    """Soft-import system dbus + GLib (Pi: python3-dbus + python3-gi)."""
    try:
        import dbus
        import dbus.mainloop.glib
        import dbus.service
        from gi.repository import GLib

        return dbus, dbus.service, dbus.mainloop.glib, GLib
    except Exception as exc:  # noqa: BLE001
        log.info("AVRCP MPRIS soft-fail (no dbus/gi): %s", exc)
        return None


def _build_mpris_props(dbus_mod, *, status: str = "Stopped") -> Any:
    track = dbus_mod.Dictionary(
        {
            "mpris:trackid": dbus_mod.ObjectPath("/org/musync/track/0"),
            "xesam:title": "",
            "xesam:artist": dbus_mod.Array([], signature="s"),
            "xesam:album": "",
            "mpris:length": dbus_mod.Int64(0),
        },
        signature="sv",
    )
    return dbus_mod.Dictionary(
        {
            "PlaybackStatus": status,
            "LoopStatus": "None",
            "Rate": dbus_mod.Double(1.0),
            "Shuffle": dbus_mod.Boolean(False),
            "Metadata": track,
            "Volume": dbus_mod.Double(1.0),
            "Position": dbus_mod.Int64(0),
            "MinimumRate": dbus_mod.Double(1.0),
            "MaximumRate": dbus_mod.Double(1.0),
            "CanGoNext": dbus_mod.Boolean(True),
            "CanGoPrevious": dbus_mod.Boolean(True),
            "CanPlay": dbus_mod.Boolean(True),
            "CanPause": dbus_mod.Boolean(True),
            "CanSeek": dbus_mod.Boolean(False),
            "CanControl": dbus_mod.Boolean(True),
        },
        signature="sv",
    )


def _make_player_class(dbus_mod, service_mod):
    """Build MPRIS Player dbus.service.Object subclass (needs live dbus import)."""

    class InvalidArgsException(dbus_mod.exceptions.DBusException):
        _dbus_error_name = "org.freedesktop.DBus.Error.InvalidArgs"

    class MusyncMprisPlayer(service_mod.Object):
        """BlueZ AVRCP target — headset buttons land here as MPRIS methods."""

        def __init__(self, bus, path: str, on_action: Callable[[AvrcpAction], None]) -> None:
            self.path = path
            self._on_action = on_action
            self.properties = _build_mpris_props(dbus_mod, status="Stopped")
            service_mod.Object.__init__(self, bus, path)

        def get_path(self):
            return dbus_mod.ObjectPath(self.path)

        def get_properties(self):
            return self.properties

        def _emit_action(self, action: AvrcpAction) -> None:
            try:
                self._on_action(action)
            except Exception as exc:  # noqa: BLE001
                log.debug("MPRIS %s callback: %s", action, exc)

        @service_mod.method(DBUS_PROP_IFACE, in_signature="ss", out_signature="v")
        def Get(self, interface, prop):
            if interface not in (PLAYER_IFACE, MPRIS_ROOT_IFACE):
                raise InvalidArgsException()
            if interface == MPRIS_ROOT_IFACE:
                root = {
                    "Identity": "MuSync",
                    "DesktopEntry": "musync",
                    "CanQuit": False,
                    "CanRaise": False,
                    "HasTrackList": False,
                    "SupportedUriSchemes": dbus_mod.Array([], signature="s"),
                    "SupportedMimeTypes": dbus_mod.Array([], signature="s"),
                }
                if prop not in root:
                    raise InvalidArgsException()
                return root[prop]
            if prop not in self.properties:
                raise InvalidArgsException()
            return self.properties[prop]

        @service_mod.method(DBUS_PROP_IFACE, in_signature="s", out_signature="a{sv}")
        def GetAll(self, interface):
            if interface == MPRIS_ROOT_IFACE:
                return dbus_mod.Dictionary(
                    {
                        "Identity": "MuSync",
                        "DesktopEntry": "musync",
                        "CanQuit": dbus_mod.Boolean(False),
                        "CanRaise": dbus_mod.Boolean(False),
                        "HasTrackList": dbus_mod.Boolean(False),
                        "SupportedUriSchemes": dbus_mod.Array([], signature="s"),
                        "SupportedMimeTypes": dbus_mod.Array([], signature="s"),
                    },
                    signature="sv",
                )
            if interface != PLAYER_IFACE:
                raise InvalidArgsException()
            return self.get_properties()

        @service_mod.method(DBUS_PROP_IFACE, in_signature="ssv", out_signature="")
        def Set(self, interface, prop, value):
            # Volume from Absolute Volume goes via MediaTransport — ignore MPRIS Set.
            if interface != PLAYER_IFACE:
                raise InvalidArgsException()
            if prop == "Volume":
                return
            # LoopStatus / Shuffle unused for hub queue.
            return

        @service_mod.signal(DBUS_PROP_IFACE, signature="sa{sv}as")
        def PropertiesChanged(self, interface, changed, invalidated=None):
            pass

        def update_playback(
            self,
            *,
            status: str,
            title: str = "",
            artist: str = "",
            album: str = "",
            position_ms: int = 0,
            duration_ms: int = 0,
        ) -> None:
            """Push Now Playing into MPRIS so the headset display stays in sync."""
            changed: dict[str, Any] = {}
            mpris_status = transport_state_to_mpris(status)
            if self.properties.get("PlaybackStatus") != mpris_status:
                self.properties["PlaybackStatus"] = mpris_status
                changed["PlaybackStatus"] = mpris_status
            pos_us = dbus_mod.Int64(max(0, int(position_ms)) * 1000)
            if int(self.properties.get("Position", 0)) != int(pos_us):
                self.properties["Position"] = pos_us
                changed["Position"] = pos_us
            artists = [artist] if artist else []
            # Stable track id so BlueZ/headsets treat Metadata updates as one track.
            safe = "".join(
                ch if ch.isalnum() else "_"
                for ch in (title or "track")[:48]
            ) or "track"
            track = dbus_mod.Dictionary(
                {
                    "mpris:trackid": dbus_mod.ObjectPath(f"/org/musync/track/{safe}"),
                    "xesam:title": title or "",
                    "xesam:artist": dbus_mod.Array(artists, signature="s"),
                    "xesam:album": album or "",
                    "mpris:length": dbus_mod.Int64(max(0, int(duration_ms)) * 1000),
                },
                signature="sv",
            )
            self.properties["Metadata"] = track
            changed["Metadata"] = track
            # Keep capability flags true so BlueZ keeps advertising control.
            for key in (
                "CanGoNext",
                "CanGoPrevious",
                "CanPlay",
                "CanPause",
                "CanControl",
            ):
                self.properties[key] = dbus_mod.Boolean(True)
            if changed:
                try:
                    self.PropertiesChanged(
                        PLAYER_IFACE,
                        dbus_mod.Dictionary(changed, signature="sv"),
                        dbus_mod.Array([], signature="s"),
                    )
                except Exception as exc:  # noqa: BLE001
                    log.debug("MPRIS PropertiesChanged: %s", exc)

        # --- org.mpris.MediaPlayer2.Player (AVRCP target) ---

        @service_mod.method(PLAYER_IFACE, in_signature="", out_signature="")
        def Play(self):
            self._emit_action(AvrcpAction.PLAY)

        @service_mod.method(PLAYER_IFACE, in_signature="", out_signature="")
        def Pause(self):
            self._emit_action(AvrcpAction.PAUSE)

        @service_mod.method(PLAYER_IFACE, in_signature="", out_signature="")
        def PlayPause(self):
            self._emit_action(AvrcpAction.TOGGLE)

        @service_mod.method(PLAYER_IFACE, in_signature="", out_signature="")
        def Stop(self):
            self._emit_action(AvrcpAction.STOP)

        @service_mod.method(PLAYER_IFACE, in_signature="", out_signature="")
        def Next(self):
            self._emit_action(AvrcpAction.NEXT)

        @service_mod.method(PLAYER_IFACE, in_signature="", out_signature="")
        def Previous(self):
            self._emit_action(AvrcpAction.PREVIOUS)

        @service_mod.method(PLAYER_IFACE, in_signature="x", out_signature="")
        def Seek(self, offset):
            return  # hub scrub is UI-only

        @service_mod.method(PLAYER_IFACE, in_signature="ox", out_signature="")
        def SetPosition(self, track_id, position):
            return

        @service_mod.method(PLAYER_IFACE, in_signature="s", out_signature="")
        def OpenUri(self, uri):
            return

        # --- org.mpris.MediaPlayer2 root (minimal) ---

        @service_mod.method(MPRIS_ROOT_IFACE, in_signature="", out_signature="")
        def Raise(self):
            return

        @service_mod.method(MPRIS_ROOT_IFACE, in_signature="", out_signature="")
        def Quit(self):
            return

    return MusyncMprisPlayer


@dataclass
class AvrcpStatusSnapshot:
    supported: bool = False
    watching: bool = False
    detail: str = "AVRCP not started"
    last_volume_sync: dict[str, float] = field(default_factory=dict)
    last_action: Optional[str] = None
    last_error: str = ""
    player_registered: bool = False
    volume_supported: bool = False
    dbus_import_ok: bool = False
    player_path: str = PLAYER_PATH
    registered_adapters: list[str] = field(default_factory=list)
    mpris_playback_status: str = "Stopped"


class AvrcpBridge:
    """Absolute Volume poller + BlueZ MPRIS RegisterPlayer transport relay."""

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
        self._last_action_at = 0.0
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._mac_by_device: dict[str, str] = {}
        self._node_by_device: dict[str, str] = {}
        # MPRIS / BlueZ RegisterPlayer (Pi AVRCP target).
        self._dbus_mods = None
        self._bus = None
        self._player = None
        self._glib_loop = None
        self._glib_thread: Optional[threading.Thread] = None
        self._registered_adapters: set[str] = set()
        self._bluez_owner: str = ""
        self._dbus_import_ok = _try_import_dbus() is not None
        self._status.dbus_import_ok = self._dbus_import_ok

    @property
    def status(self) -> AvrcpStatusSnapshot:
        with self._lock:
            mpris_status = "Stopped"
            player = self._player
            if player is not None:
                try:
                    mpris_status = str(
                        player.properties.get("PlaybackStatus") or "Stopped"
                    )
                except Exception:  # noqa: BLE001
                    mpris_status = "Stopped"
            return AvrcpStatusSnapshot(
                supported=self._status.supported,
                watching=self._status.watching,
                detail=self._status.detail,
                last_volume_sync=dict(self._status.last_volume_sync),
                last_action=self._status.last_action,
                last_error=self._status.last_error,
                player_registered=self._status.player_registered,
                volume_supported=self._status.volume_supported,
                dbus_import_ok=self._dbus_import_ok,
                player_path=PLAYER_PATH,
                registered_adapters=sorted(self._registered_adapters),
                mpris_playback_status=mpris_status,
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
        # Hot-plug HCI / bluetoothd restart: re-RegisterPlayer when needed.
        self._schedule_on_glib(self._register_player_on_adapters)

    def force_reregister(self) -> None:
        """Clear adapter cache and retry Media1.RegisterPlayer (BT connect / churn)."""
        with self._lock:
            self._registered_adapters.clear()
            self._bluez_owner = ""
        self._schedule_on_glib(self._register_player_on_adapters)

    def start_watcher(self) -> None:
        """Register MPRIS player with BlueZ + best-effort volume signal monitor."""
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()

        player_ok, player_detail = self._start_mpris_player()
        with self._lock:
            self._status.player_registered = player_ok
            self._status.dbus_import_ok = self._dbus_import_ok
            if player_ok:
                self._status.supported = True
                self._status.detail = player_detail
                self._status.last_error = ""
            elif not self._busctl and not self._dbus_import_ok:
                self._status.supported = False
                self._status.watching = False
                self._status.detail = (
                    "AVRCP soft-fail — busctl/dbus missing "
                    "(volume/transport relay idle)"
                )
                self._status.last_error = player_detail
                return
            else:
                self._status.last_error = player_detail
                # Absolute Volume may still work via busctl — do not claim buttons OK.
                self._status.detail = (
                    f"AVRCP Absolute Volume may work; headset buttons soft-fail — "
                    f"{player_detail[:160]}"
                )

        # Volume still uses busctl monitor (PropertyChanged); optional action tokens.
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            if not self._busctl:
                self._status.watching = player_ok
                return
            self._thread = threading.Thread(
                target=self._watch_loop,
                name="musync-avrcp",
                daemon=True,
            )
            self._thread.start()
            self._status.watching = True
            if player_ok:
                self._status.supported = True
                self._status.detail = (
                    "AVRCP MPRIS player registered + Absolute Volume watcher"
                )

    def stop_watcher(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=1.5)
        self._stop_mpris_player()
        with self._lock:
            self._status.watching = False
            self._status.player_registered = False

    def publish_transport(
        self,
        *,
        state: str,
        title: str = "",
        artist: str = "",
        album: str = "",
        position_ms: int = 0,
        duration_ms: int = 0,
    ) -> None:
        """Mirror hub Now Playing into the registered MPRIS player (soft-fail)."""

        def _do() -> None:
            player = self._player
            if player is None:
                return
            try:
                player.update_playback(
                    status=state,
                    title=title or "",
                    artist=artist or "",
                    album=album or "",
                    position_ms=int(position_ms or 0),
                    duration_ms=int(duration_ms or 0),
                )
            except Exception as exc:  # noqa: BLE001
                log.debug("AVRCP publish_transport: %s", exc)

        self._schedule_on_glib(_do)

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
                self._status.volume_supported = True
                self._status.supported = True
                if not self._status.player_registered:
                    # Volume OK ≠ buttons OK — keep RegisterPlayer failure visible.
                    if self._status.last_error and (
                        "RegisterPlayer" in (self._status.last_error or "")
                        or "dbus" in (self._status.last_error or "").lower()
                        or "python3-dbus" in (self._status.last_error or "")
                    ):
                        self._status.detail = (
                            f"Absolute Volume OK; headset buttons soft-fail — "
                            f"{self._status.last_error[:120]}"
                        )
                    elif (
                        "soft-fail" not in detail.lower()
                        and "missing" not in detail.lower()
                        and "RegisterPlayer" not in (self._status.detail or "")
                        and "buttons soft-fail" not in (self._status.detail or "")
                    ):
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
        now = time.monotonic()
        with self._lock:
            # Debounce only delivered headset events (API inject skips debounce
            # when deliver=False from relay_avrcp_command).
            if deliver and (now - self._last_action_at) < ACTION_DEBOUNCE_S:
                return
            if deliver:
                self._last_action_at = now
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

    def _start_mpris_player(self) -> tuple[bool, str]:
        """Export MPRIS player + RegisterPlayer on each BlueZ Media1 adapter.

        D-Bus object + GLib mainloop live on one daemon thread (dbus-python
        requirement). Ready/error is signaled back to the caller.
        """
        mods = _try_import_dbus()
        self._dbus_import_ok = mods is not None
        if mods is None:
            return False, _DBUS_IMPORT_HINT
        dbus_mod, service_mod, mainloop_mod, GLib = mods
        ready = threading.Event()
        result: dict[str, Any] = {"ok": False, "detail": "MPRIS start pending"}

        def _glib_main() -> None:
            try:
                mainloop_mod.DBusGMainLoop(set_as_default=True)
                bus = dbus_mod.SystemBus()
                PlayerCls = _make_player_class(dbus_mod, service_mod)

                def _on_player_action(action: AvrcpAction) -> None:
                    # Relay off the GLib thread so session/mpv locks cannot stall BlueZ.
                    threading.Thread(
                        target=lambda: self.inject_action(action, deliver=True),
                        name="musync-avrcp-action",
                        daemon=True,
                    ).start()

                player = PlayerCls(bus, PLAYER_PATH, _on_player_action)
                loop = GLib.MainLoop()
                self._dbus_mods = mods
                self._bus = bus
                self._player = player
                self._glib_loop = loop
                registered = self._register_player_on_adapters()
                if not registered:
                    result["ok"] = False
                    result["detail"] = (
                        "BlueZ Media1.RegisterPlayer found no adapters "
                        "(bluetoothd down? After=bluetooth.service; retry on connect)"
                    )
                else:
                    result["ok"] = True
                    result["detail"] = (
                        f"RegisterPlayer on {registered} adapter(s) "
                        f"at {PLAYER_PATH}"
                    )
                ready.set()
                # Keep serving Play/Pause/Next even if no adapter registered yet
                # (update_targets may RegisterPlayer later).
                loop.run()
            except Exception as exc:  # noqa: BLE001
                result["ok"] = False
                result["detail"] = str(exc)[:200]
                log.info("AVRCP MPRIS RegisterPlayer soft-fail: %s", exc)
                ready.set()

        glib_thread = threading.Thread(
            target=_glib_main,
            name="musync-avrcp-glib",
            daemon=True,
        )
        self._glib_thread = glib_thread
        glib_thread.start()
        if not ready.wait(timeout=4.0):
            self._stop_mpris_player()
            return False, "MPRIS GLib thread timed out"
        if not result["ok"]:
            # Leave thread running only when player object exported — adapters
            # may appear later via update_targets → _register_player_on_adapters.
            if self._player is None:
                self._stop_mpris_player()
            return False, str(result["detail"])
        return True, str(result["detail"])

    def _schedule_on_glib(self, fn: Callable[[], Any]) -> None:
        """Run ``fn`` on the AVRCP GLib thread when available (else inline)."""
        mods = self._dbus_mods
        loop = self._glib_loop
        if mods is None or loop is None:
            try:
                fn()
            except Exception as exc:  # noqa: BLE001
                log.debug("AVRCP schedule inline soft-fail: %s", exc)
            return
        GLib = mods[3]

        def _idle() -> bool:
            try:
                fn()
            except Exception as exc:  # noqa: BLE001
                log.debug("AVRCP GLib idle soft-fail: %s", exc)
            return False  # one-shot

        try:
            GLib.idle_add(_idle)
        except Exception as exc:  # noqa: BLE001
            log.debug("AVRCP idle_add soft-fail: %s", exc)
            try:
                fn()
            except Exception as exc2:  # noqa: BLE001
                log.debug("AVRCP schedule fallback soft-fail: %s", exc2)

    def _register_player_on_adapters(self) -> int:
        """Call Media1.RegisterPlayer on each HCI adapter not yet registered.

        If ``org.bluez`` name owner changes (bluetoothd restart under dual A2DP
        churn), clear the adapter cache and re-register — otherwise status can
        stay ``player_registered=true`` while BlueZ has forgotten our player.
        """
        mods = self._dbus_mods
        bus = self._bus
        player = self._player
        if not mods or bus is None or player is None:
            return 0
        dbus_mod = mods[0]
        try:
            owner = str(bus.get_name_owner("org.bluez"))
        except Exception as exc:  # noqa: BLE001
            log.debug("AVRCP get_name_owner(org.bluez): %s", exc)
            owner = ""
        if owner and owner != self._bluez_owner:
            if self._bluez_owner:
                log.info(
                    "AVRCP: org.bluez owner changed (%s → %s); re-RegisterPlayer",
                    self._bluez_owner,
                    owner,
                )
            self._registered_adapters.clear()
            self._bluez_owner = owner
        try:
            om = dbus_mod.Interface(
                bus.get_object("org.bluez", "/"),
                "org.freedesktop.DBus.ObjectManager",
            )
            objects = om.GetManagedObjects()
        except Exception as exc:  # noqa: BLE001
            log.debug("AVRCP GetManagedObjects: %s", exc)
            return 0
        count = 0
        errors: list[str] = []
        for path, ifaces in objects.items():
            path_s = str(path)
            if BLUEZ_MEDIA_IFACE not in ifaces:
                continue
            if path_s in self._registered_adapters:
                count += 1
                continue
            try:
                media = dbus_mod.Interface(
                    bus.get_object("org.bluez", path_s),
                    BLUEZ_MEDIA_IFACE,
                )
                media.RegisterPlayer(
                    dbus_mod.ObjectPath(PLAYER_PATH),
                    player.get_properties(),
                )
                self._registered_adapters.add(path_s)
                count += 1
                log.info("AVRCP RegisterPlayer on %s → %s", path_s, PLAYER_PATH)
            except Exception as exc:  # noqa: BLE001
                # AlreadyExists / NotSupported — soft-continue.
                err = str(exc)
                if "AlreadyExists" in err or "already" in err.lower():
                    self._registered_adapters.add(path_s)
                    count += 1
                else:
                    errors.append(f"{path_s}: {err[:80]}")
                    log.info("AVRCP RegisterPlayer %s soft-fail: %s", path_s, exc)
        with self._lock:
            if count:
                self._status.player_registered = True
                self._status.supported = True
                self._status.last_error = ""
                self._status.detail = (
                    f"RegisterPlayer on {count} adapter(s) at {PLAYER_PATH}"
                )
            elif errors:
                self._status.last_error = "; ".join(errors)[:200]
                if "buttons soft-fail" not in (self._status.detail or ""):
                    self._status.detail = (
                        f"AVRCP RegisterPlayer soft-fail — {self._status.last_error}"
                    )
        return count

    def _stop_mpris_player(self) -> None:
        mods = self._dbus_mods
        bus = self._bus
        if mods and bus is not None and self._registered_adapters:
            dbus_mod = mods[0]
            for path_s in list(self._registered_adapters):
                try:
                    media = dbus_mod.Interface(
                        bus.get_object("org.bluez", path_s),
                        BLUEZ_MEDIA_IFACE,
                    )
                    media.UnregisterPlayer(dbus_mod.ObjectPath(PLAYER_PATH))
                except Exception:  # noqa: BLE001
                    pass
            self._registered_adapters.clear()
        loop = self._glib_loop
        if loop is not None:
            try:
                loop.quit()
            except Exception:  # noqa: BLE001
                pass
        thread = self._glib_thread
        if thread and thread.is_alive():
            thread.join(timeout=1.0)
        self._glib_loop = None
        self._glib_thread = None
        self._player = None
        self._bus = None
        self._dbus_mods = None

    def _watch_loop(self) -> None:
        assert self._busctl
        # Absolute Volume PropertyChanged (+ rare action tokens if present).
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
                if not self._status.player_registered:
                    self._status.supported = False
                self._status.detail = (
                    self._status.detail
                    if self._status.player_registered
                    else f"AVRCP watcher soft-fail: {exc}"
                )
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
                self._status.watching = bool(self._status.player_registered)
                if self._stop.is_set():
                    if not self._status.player_registered:
                        self._status.detail = "AVRCP watcher stopped"
                else:
                    if self._status.player_registered:
                        self._status.detail = (
                            "AVRCP MPRIS player still registered "
                            "(volume monitor exited)"
                        )
                    else:
                        self._status.detail = (
                            "AVRCP watcher exited — volume poll + API relay "
                            "still soft-ok"
                        )

    def _handle_monitor_line(self, line: str) -> None:
        action = parse_monitor_action(line)
        if action is not None:
            # Prefer MPRIS RegisterPlayer path; monitor tokens are a fallback.
            if not self._status.player_registered:
                self.inject_action(action)
            return
        if "Volume" not in line and "MediaTransport" not in line:
            return
        # Volume signal — refresh polls soon (debounce via poll throttle).
        try:
            self.poll_volumes(force=True)
        except Exception as exc:  # noqa: BLE001
            log.debug("AVRCP monitor volume poll: %s", exc)
