"""Load / save YAML device profiles and discover local audio/video sources."""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import wave
from pathlib import Path
from typing import Any, Optional

import yaml

from .models import (
    MAX_BT_SINKS,
    MAX_SPP_SINKS,
    Device,
    DeviceConfigEntry,
    DeviceKind,
    MediaKind,
    Source,
    SourceType,
)

log = logging.getLogger(__name__)

# Virtual / missing-file demo tracks only — never invent this for real media.
DEMO_DURATION_MS = 180_000

AUDIO_EXTENSIONS = {".wav", ".flac", ".mp3", ".ogg", ".m4a", ".aac"}
VIDEO_EXTENSIONS = {".mp4", ".mkv", ".webm", ".mov", ".m4v", ".avi"}
LOCAL_MEDIA_EXTENSIONS = AUDIO_EXTENSIONS | VIDEO_EXTENSIONS

# Always-on SPP template — UI can enable/configure without copying YAML.
DEFAULT_SPP_DEVICE: dict[str, Any] = {
    "id": "spp-1",
    "name": "Phone SPP",
    "kind": "network_spp",
    "mac": "",
    "hci": "",
    "connected": False,
    "delay_ms": 0,
    "volume": 0.9,
    "mute": False,
    "enabled": True,
    "listen_host": "0.0.0.0",
    "listen_port": 1234,
    "sample_rate": 48000,
    "channels": 1,
}

# Default mock sinks (≤6 BT + SPP) for cloud/dev without hardware.
DEFAULT_MOCK_DEVICES: list[dict[str, Any]] = [
    {
        "id": "sink-1",
        "name": "Living Room",
        "mac": "AA:BB:CC:DD:EE:01",
        "hci": "hci0",
        "connected": True,
        "delay_ms": 0,
        "volume": 0.85,
        "mute": False,
        "enabled": True,
    },
    {
        "id": "sink-2",
        "name": "Kitchen",
        "mac": "AA:BB:CC:DD:EE:02",
        "hci": "hci1",
        "connected": True,
        "delay_ms": 40,
        "volume": 0.75,
        "mute": False,
        "enabled": True,
    },
    {
        "id": "sink-3",
        "name": "Bedroom",
        "mac": "AA:BB:CC:DD:EE:03",
        "hci": "hci2",
        "connected": False,
        "delay_ms": 80,
        "volume": 0.7,
        "mute": False,
        "enabled": False,
    },
    {
        "id": "sink-4",
        "name": "Patio",
        "mac": "AA:BB:CC:DD:EE:04",
        "hci": "hci3",
        "connected": True,
        "delay_ms": 120,
        "volume": 0.9,
        "mute": False,
        "enabled": True,
    },
    {
        "id": "sink-5",
        "name": "Office",
        "mac": "AA:BB:CC:DD:EE:05",
        "hci": "hci4",
        "connected": False,
        "delay_ms": 0,
        "volume": 0.8,
        "mute": False,
        "enabled": False,
    },
    {**DEFAULT_SPP_DEVICE, "connected": True},
]

# Pi / PipeWire boot when devices.yaml is missing or broken: SPP only until
# discovery + UI save fills in bluez sinks by name (no placeholder MACs).
DEFAULT_PI_DEVICES: list[dict[str, Any]] = [
    {**DEFAULT_SPP_DEVICE},
]

# Hub-side stream catalog rows. Availability flips when StreamingService is ready.
PLACEHOLDER_SOURCES: list[Source] = [
    Source(
        id="spotify:hub",
        title="Spotify (hub Connect)",
        type=SourceType.SPOTIFY,
        locator="spotify:hub",
        available=False,
        note="Connect Spotify in Streaming — librespot → musync_feeder",
    ),
    Source(
        id="apple:hub",
        title="Apple Music (hub — limited on Linux)",
        type=SourceType.APPLE,
        locator="apple:hub",
        available=False,
        note="MusicKit search + optional AirPlay/shairport → feeder; see docs/spotify-apple.md",
    ),
]


def repo_root() -> Path:
    """Resolve repository root (…/MuSync) from this package location."""
    return Path(__file__).resolve().parents[2]


def env_path(name: str, default: Path) -> Path:
    raw = os.environ.get(name)
    return Path(raw).expanduser().resolve() if raw else default.resolve()


def devices_config_path() -> Path:
    return env_path("MUSYNC_DEVICES_CONFIG", repo_root() / "config" / "devices.yaml")


def media_library_path() -> Path:
    return env_path("MUSYNC_MEDIA_DIR", repo_root() / "media")


def web_root() -> Path:
    """Static UI root. Prefer SvelteKit `web/build` (adapter-static output)."""
    default = repo_root() / "web" / "build"
    return env_path("MUSYNC_WEB_DIR", default)


def validate_devices(devices: list[Device], *, require_min: int | None = None) -> list[Device]:
    """Harden BT600 (≤6) + SPP network sinks (≤4): unique id/mac/hci/port."""
    bt = [d for d in devices if d.kind == DeviceKind.BLUETOOTH]
    spp = [d for d in devices if d.kind == DeviceKind.NETWORK_SPP]
    if len(bt) > MAX_BT_SINKS:
        raise ValueError(
            f"MuSync supports at most {MAX_BT_SINKS} Bluetooth sinks (one A2DP per BT600)"
        )
    if len(spp) > MAX_SPP_SINKS:
        raise ValueError(f"MuSync supports at most {MAX_SPP_SINKS} SPP network sinks")
    if require_min is not None and len(devices) < require_min:
        raise ValueError(f"Expected at least {require_min} devices, got {len(devices)}")

    seen_ids: set[str] = set()
    seen_macs: set[str] = set()
    seen_hcis: set[str] = set()
    seen_ports: set[int] = set()
    for d in devices:
        if not d.id.strip():
            raise ValueError("Device id must be non-empty")
        if d.id in seen_ids:
            raise ValueError(f"Duplicate device id: {d.id}")
        seen_ids.add(d.id)

        if d.kind == DeviceKind.NETWORK_SPP:
            if d.listen_port in seen_ports:
                raise ValueError(f"Duplicate SPP listen_port: {d.listen_port}")
            seen_ports.add(d.listen_port)
            if not d.listen_host.strip():
                raise ValueError(f"SPP sink {d.id} needs listen_host")
            continue

        mac = d.mac.strip().upper()
        if mac:
            if mac in seen_macs:
                raise ValueError(f"Duplicate speaker MAC: {mac}")
            seen_macs.add(mac)

        hci = d.hci.strip().lower()
        if hci:
            if not hci.startswith("hci"):
                raise ValueError(f"HCI adapter must look like hci0..hci5, got {d.hci!r}")
            if hci in seen_hcis:
                raise ValueError(f"Duplicate HCI adapter: {hci}")
            seen_hcis.add(hci)

    return devices


def default_spp_device() -> Device:
    return Device.model_validate(DEFAULT_SPP_DEVICE)


def default_devices(*, backend: str = "mock") -> list[Device]:
    """Boot defaults when YAML is missing/broken. Mock keeps demo sinks; Pi = SPP."""
    raw = DEFAULT_MOCK_DEVICES if backend == "mock" else DEFAULT_PI_DEVICES
    return validate_devices([Device.model_validate(d) for d in raw])


def ensure_spp_present(devices: list[Device]) -> list[Device]:
    """Guarantee at least one network_spp sink (UI always offers SPP)."""
    if any(d.kind == DeviceKind.NETWORK_SPP for d in devices):
        return devices
    return validate_devices([*devices, default_spp_device()])


def load_devices_result(
    *,
    require_min: int | None = None,
    backend: str = "mock",
) -> tuple[list[Device], bool, str]:
    """Load devices.yaml; never raise on missing/broken file.

    Returns ``(devices, ok, detail)``. On failure, returns backend defaults
    (mock demo sinks or Pi SPP-only) so the API still boots.
    """
    path = devices_config_path()
    if not path.is_file():
        devices = default_devices(backend=backend)
        return devices, True, f"no config at {path} — using {backend} defaults"

    try:
        with path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except (OSError, yaml.YAMLError) as exc:
        devices = default_devices(backend=backend)
        return (
            devices,
            False,
            f"broken config at {path} ({exc}) — using {backend} defaults",
        )

    if data is None:
        devices = default_devices(backend=backend)
        return devices, False, f"empty config at {path} — using {backend} defaults"

    raw_devices = data.get("devices") if isinstance(data, dict) else data
    if not isinstance(raw_devices, list):
        devices = default_devices(backend=backend)
        return (
            devices,
            False,
            f"invalid devices list at {path} — using {backend} defaults",
        )

    try:
        devices = [Device.model_validate(item) for item in raw_devices]
        devices = validate_devices(devices, require_min=require_min)
        devices = ensure_spp_present(devices)
        return devices, True, f"loaded {len(devices)} sinks from {path}"
    except (ValueError, TypeError) as exc:
        devices = default_devices(backend=backend)
        return (
            devices,
            False,
            f"invalid device entries at {path} ({exc}) — using {backend} defaults",
        )


def load_devices(*, require_min: int | None = None, backend: str = "mock") -> list[Device]:
    """Load devices; tolerate missing/broken YAML (log + defaults, no crash)."""
    devices, ok, detail = load_devices_result(require_min=require_min, backend=backend)
    if not ok:
        log.warning("devices.yaml: %s", detail)
    else:
        log.info("devices.yaml: %s", detail)
    return devices


def save_devices(devices: list[Device]) -> Path:
    """Atomically write devices.yaml (temp file + replace)."""
    devices = validate_devices(devices)
    path = devices_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "max_sinks": MAX_BT_SINKS,
        "max_spp_sinks": MAX_SPP_SINKS,
        "devices": [d.model_dump(mode="json") for d in devices],
    }
    text = yaml.safe_dump(payload, sort_keys=False)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    return path


def slug_device_id(prefix: str, name: str, used: set[str]) -> str:
    """Build a stable-ish id from a friendly name (``bt-qudelix``, ``spp-1``)."""
    base = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-") or "sink"
    base = base[:24]
    candidate = f"{prefix}-{base}"
    if candidate not in used:
        return candidate
    for i in range(2, 100):
        alt = f"{candidate}-{i}"
        if alt not in used:
            return alt
    return f"{candidate}-{len(used)+1}"


def resolve_devices_from_config(
    entries: list[DeviceConfigEntry],
    *,
    existing: list[Device] | None = None,
    discovery_by_name: dict[str, dict[str, Any]] | None = None,
    discovery_by_mac: dict[str, dict[str, Any]] | None = None,
) -> list[Device]:
    """Turn UI config entries into Device rows; fill MAC/pw_node from discovery by name.

    ``discovery_by_name`` / ``discovery_by_mac`` map lower-name / upper-mac →
    ``{mac, pw_node, pw_name, hci, connected}``. Callers build these from
    PipeWire/bluetoothctl (or mock fixtures). No manual MAC typing required.
    """
    by_name = {k.strip().lower(): v for k, v in (discovery_by_name or {}).items()}
    by_mac = {k.strip().upper(): v for k, v in (discovery_by_mac or {}).items()}
    existing_by_id = {d.id: d for d in (existing or [])}
    used_ids: set[str] = set()
    out: list[Device] = []

    for entry in entries:
        kind = entry.kind
        name = entry.name.strip() or (
            "Phone SPP" if kind == DeviceKind.NETWORK_SPP else "Speaker"
        )
        prefix = "spp" if kind == DeviceKind.NETWORK_SPP else "bt"
        device_id = (entry.id or "").strip() or slug_device_id(prefix, name, used_ids)
        if device_id in used_ids:
            device_id = slug_device_id(prefix, name, used_ids)
        used_ids.add(device_id)

        prev = existing_by_id.get(device_id)
        mac = (entry.mac or (prev.mac if prev else "") or "").strip()
        hci = (entry.hci or (prev.hci if prev else "") or "").strip()
        pw_node = (entry.pw_node or (prev.pw_node if prev else "") or "").strip()
        connected = bool(prev.connected) if prev else False

        if kind == DeviceKind.BLUETOOTH:
            hint = by_name.get(name.lower())
            if not hint and mac:
                hint = by_mac.get(mac.upper())
            if hint:
                mac = hint.get("mac") or mac
                pw_node = hint.get("pw_node") or pw_node
                hci = hint.get("hci") or hci
                connected = bool(hint.get("connected", connected))

        spp = prev if prev and prev.kind == DeviceKind.NETWORK_SPP else default_spp_device()
        device = Device(
            id=device_id,
            name=name,
            kind=kind,
            mac=mac,
            hci=hci,
            pw_node=pw_node,
            connected=connected if kind == DeviceKind.BLUETOOTH else bool(entry.enabled),
            enabled=entry.enabled,
            delay_ms=entry.delay_ms if entry.delay_ms is not None else (prev.delay_ms if prev else 0),
            volume=entry.volume if entry.volume is not None else (prev.volume if prev else 0.85),
            gain=entry.gain if entry.gain is not None else (prev.gain if prev else 1.0),
            mute=entry.mute if entry.mute is not None else (prev.mute if prev else False),
            listen_host=(
                entry.listen_host
                if entry.listen_host is not None
                else (prev.listen_host if prev else spp.listen_host)
            ),
            listen_port=(
                entry.listen_port
                if entry.listen_port is not None
                else (prev.listen_port if prev else spp.listen_port)
            ),
            sample_rate=(
                entry.sample_rate
                if entry.sample_rate is not None
                else (prev.sample_rate if prev else spp.sample_rate)
            ),
            channels=(
                entry.channels
                if entry.channels is not None
                else (prev.channels if prev else spp.channels)
            ),
        )
        out.append(device)

    return validate_devices(ensure_spp_present(out))


def _probe_wav_duration_ms(path: Path) -> Optional[int]:
    try:
        with wave.open(str(path), "rb") as wf:
            frames = wf.getnframes()
            rate = wf.getframerate()
            if rate <= 0 or frames < 0:
                return None
            return int(round(frames * 1000 / rate))
    except (wave.Error, OSError, EOFError):
        return None


def _probe_ffprobe_duration_ms(path: Path) -> Optional[int]:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    try:
        proc = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=5.0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.debug("ffprobe failed for %s: %s", path, exc)
        return None
    if proc.returncode != 0:
        return None
    raw = (proc.stdout or "").strip()
    if not raw:
        return None
    try:
        seconds = float(raw)
    except ValueError:
        return None
    if seconds < 0 or seconds != seconds:  # NaN
        return None
    return int(round(seconds * 1000))


def probe_audio_duration_ms(path: Path | str) -> Optional[int]:
    """Return media duration in ms, or None if probing fails.

    Prefers stdlib ``wave`` for ``.wav``, then ``ffprobe`` when installed
    (common on Pi with ffmpeg). Works for audio and video containers.
    Does not invent a stub length for real files.
    """
    p = Path(path)
    if not p.is_file():
        return None
    if p.suffix.lower() == ".wav":
        wav_ms = _probe_wav_duration_ms(p)
        if wav_ms is not None:
            return wav_ms
    return _probe_ffprobe_duration_ms(p)


def cover_cache_dir() -> Path:
    """Writable cache for extracted embedded album art (gitignored via config/)."""
    d = repo_root() / "config" / "cover-cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def probe_media_tags(path: Path | str) -> dict[str, Optional[str]]:
    """Best-effort title / artist / album via ffprobe tags (empty when unavailable)."""
    out: dict[str, Optional[str]] = {"title": None, "artist": None, "album": None}
    p = Path(path)
    if not p.is_file():
        return out
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return out
    try:
        proc = subprocess.run(
            [
                ffprobe,
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_entries",
                "format_tags=title,artist,album,TITLE,ARTIST,ALBUM",
                str(p),
            ],
            capture_output=True,
            text=True,
            timeout=5.0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.debug("ffprobe tags failed for %s: %s", p, exc)
        return out
    if proc.returncode != 0 or not (proc.stdout or "").strip():
        return out
    try:
        import json

        data = json.loads(proc.stdout)
    except (json.JSONDecodeError, TypeError):
        return out
    tags = (data.get("format") or {}).get("tags") or {}
    # ffprobe may use any casing.
    lowered = {str(k).lower(): str(v) for k, v in tags.items() if v}
    for key in ("title", "artist", "album"):
        val = lowered.get(key)
        if val and val.strip():
            out[key] = val.strip()
    return out


def extract_embedded_cover(path: Path | str) -> Optional[Path]:
    """Extract embedded cover art to cover-cache; return path or None."""
    p = Path(path)
    if not p.is_file():
        return None
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    # Stable cache key from absolute path + mtime.
    import hashlib

    key = hashlib.sha1(f"{p.resolve()}:{p.stat().st_mtime_ns}".encode()).hexdigest()[:20]
    dest = cover_cache_dir() / f"{key}.jpg"
    if dest.is_file() and dest.stat().st_size > 0:
        return dest
    try:
        proc = subprocess.run(
            [
                ffmpeg,
                "-y",
                "-i",
                str(p),
                "-an",
                "-vcodec",
                "mjpeg",
                "-frames:v",
                "1",
                str(dest),
            ],
            capture_output=True,
            text=True,
            timeout=8.0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.debug("cover extract failed for %s: %s", p, exc)
        return None
    if proc.returncode != 0 or not dest.is_file() or dest.stat().st_size == 0:
        if dest.exists():
            try:
                dest.unlink()
            except OSError:
                pass
        return None
    return dest


def media_kind_for_path(path: Path | str) -> MediaKind:
    suffix = Path(path).suffix.lower()
    if suffix in VIDEO_EXTENSIONS:
        return MediaKind.VIDEO
    return MediaKind.AUDIO


def resolve_media_file(rel_path: str, *, media_dir: Path | None = None) -> Path:
    """Resolve a relative path under the media library (no path traversal)."""
    root = (media_dir or media_library_path()).resolve()
    cleaned = rel_path.strip().lstrip("/")
    if not cleaned or cleaned.startswith("..") or "/../" in f"/{cleaned}/":
        raise ValueError("Invalid media path")
    candidate = (root / cleaned).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("Media path escapes library root") from exc
    if not candidate.is_file():
        raise FileNotFoundError(f"Media not found: {cleaned}")
    if candidate.suffix.lower() not in LOCAL_MEDIA_EXTENSIONS:
        raise ValueError(f"Unsupported media type: {candidate.suffix}")
    return candidate


def discover_local_sources(media_dir: Path | None = None) -> list[Source]:
    """Discover hub-local audio and video files under ``media/``.

    Video rows stay ``type=local``; audio is extracted by mpv ``--no-video``
    into ``musync_feeder`` (same fan-out as audio tracks).
    """
    root = media_dir or media_library_path()
    sources: list[Source] = []
    if not root.is_dir():
        return sources

    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in LOCAL_MEDIA_EXTENSIONS:
            continue
        rel = path.relative_to(root)
        source_id = f"local:{rel.as_posix()}"
        resolved = str(path.resolve())
        kind = media_kind_for_path(path)
        duration_ms = probe_audio_duration_ms(path)
        tags = probe_media_tags(path)
        cover = extract_embedded_cover(path)
        stem_title = path.stem.replace("_", " ").replace("-", " ")
        note = (
            "Local video — audio → musync_feeder; optional muted preview in UI"
            if kind == MediaKind.VIDEO
            else None
        )
        sources.append(
            Source(
                id=source_id,
                title=tags.get("title") or stem_title,
                type=SourceType.LOCAL,
                path=resolved,
                locator=resolved,
                available=True,
                duration_ms=duration_ms,
                media_kind=kind,
                artist=tags.get("artist"),
                album=tags.get("album"),
                cover_url=f"/api/now-playing/cover?source={source_id}" if cover else None,
                note=note,
            )
        )
    return sources


def clamp_delay_ms(value: int) -> int:
    return max(0, min(2000, int(value)))


def clamp_volume(value: float) -> float:
    return round(max(0.0, min(1.0, float(value))), 3)


def clamp_gain(value: float) -> float:
    """Headphone/IEM sensitivity factor (default 1.0; typical 0.1–4.0)."""
    return round(max(0.1, min(4.0, float(value))), 3)
