"""Load / save YAML device profiles and discover local audio sources."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from .models import Device, Source, SourceType

AUDIO_EXTENSIONS = {".wav", ".flac", ".mp3", ".ogg", ".m4a", ".aac"}

# Default mock sinks (≤6) for cloud/dev without hardware.
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
    {
        "id": "sink-6",
        "name": "Garage",
        "mac": "AA:BB:CC:DD:EE:06",
        "hci": "hci5",
        "connected": False,
        "delay_ms": 60,
        "volume": 0.65,
        "mute": True,
        "enabled": False,
    },
]

PLACEHOLDER_SOURCES: list[Source] = [
    Source(
        id="stream-spotify",
        title="Spotify (later)",
        type=SourceType.STREAM,
        available=False,
        note="Phase 1 — not in MVP",
    ),
    Source(
        id="stream-apple",
        title="Apple Music (later)",
        type=SourceType.STREAM,
        available=False,
        note="Phase 1 — not in MVP",
    ),
    Source(
        id="input-1",
        title="Phone / PC input (later)",
        type=SourceType.INPUT,
        available=False,
        note="Phase 2 — not in MVP",
    ),
    Source(
        id="movie-1",
        title="Movie / HDMI capture (later)",
        type=SourceType.MOVIE,
        available=False,
        note="Phase 2 — not in MVP",
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


def load_devices() -> list[Device]:
    path = devices_config_path()
    if not path.is_file():
        return [Device.model_validate(d) for d in DEFAULT_MOCK_DEVICES]

    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}

    raw_devices = data.get("devices") if isinstance(data, dict) else data
    if not isinstance(raw_devices, list):
        raise ValueError(f"Invalid devices config at {path}: expected devices list")

    devices = [Device.model_validate(item) for item in raw_devices]
    if len(devices) > 6:
        raise ValueError("MuSync MVP supports at most 6 sinks")
    return devices


def save_devices(devices: list[Device]) -> Path:
    path = devices_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"devices": [d.model_dump() for d in devices]}
    with path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(payload, fh, sort_keys=False)
    return path


def discover_local_sources(media_dir: Path | None = None) -> list[Source]:
    root = media_dir or media_library_path()
    sources: list[Source] = []
    if not root.is_dir():
        return sources

    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in AUDIO_EXTENSIONS:
            continue
        rel = path.relative_to(root)
        source_id = f"local:{rel.as_posix()}"
        sources.append(
            Source(
                id=source_id,
                title=path.stem.replace("_", " ").replace("-", " "),
                type=SourceType.LOCAL,
                path=str(path.resolve()),
                available=True,
                # Mock / stub duration; real probing can come later on Pi.
                duration_ms=180_000,
            )
        )
    return sources


def clamp_delay_ms(value: int) -> int:
    return max(0, min(2000, int(value)))


def clamp_volume(value: float) -> float:
    return round(max(0.0, min(1.0, float(value))), 3)
