from __future__ import annotations

from pathlib import Path

import yaml

from musync.config import (
    clamp_delay_ms,
    clamp_volume,
    discover_local_sources,
    load_devices,
    save_devices,
)
from musync.models import Device


def test_clamp_delay_and_volume() -> None:
    assert clamp_delay_ms(-10) == 0
    assert clamp_delay_ms(2500) == 2000
    assert clamp_delay_ms(120) == 120
    assert clamp_volume(-0.5) == 0.0
    assert clamp_volume(1.5) == 1.0
    assert clamp_volume(0.3333) == 0.333


def test_load_default_devices(monkeypatch, tmp_path: Path) -> None:
    missing = tmp_path / "missing.yaml"
    monkeypatch.setenv("MUSYNC_DEVICES_CONFIG", str(missing))
    devices = load_devices()
    assert 1 <= len(devices) <= 6
    assert all(isinstance(d, Device) for d in devices)


def test_save_and_load_devices(monkeypatch, tmp_path: Path) -> None:
    path = tmp_path / "devices.yaml"
    monkeypatch.setenv("MUSYNC_DEVICES_CONFIG", str(path))
    original = [
        Device(
            id="a",
            name="A",
            mac="11:22:33:44:55:66",
            hci="hci0",
            connected=True,
            delay_ms=25,
            volume=0.5,
            mute=False,
            enabled=True,
        )
    ]
    save_devices(original)
    loaded = load_devices()
    assert loaded[0].delay_ms == 25
    assert loaded[0].volume == 0.5
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert raw["devices"][0]["id"] == "a"


def test_discover_local_sources(monkeypatch, tmp_path: Path) -> None:
    media = tmp_path / "media"
    media.mkdir()
    (media / "track_one.wav").write_bytes(b"RIFF")
    (media / "notes.txt").write_text("ignore", encoding="utf-8")
    monkeypatch.setenv("MUSYNC_MEDIA_DIR", str(media))
    sources = discover_local_sources()
    assert len(sources) == 1
    assert sources[0].id.endswith("track_one.wav")
    assert sources[0].available is True
