from __future__ import annotations

import time

import pytest

from musync.audio.mock import MockAudioBackend
from musync.models import Device, DevicePatch, TransportPut, TransportState


@pytest.fixture
def backend(tmp_path, monkeypatch) -> MockAudioBackend:
    monkeypatch.setenv("MUSYNC_DEVICES_CONFIG", str(tmp_path / "devices.yaml"))
    monkeypatch.setenv("MUSYNC_MEDIA_DIR", str(tmp_path / "media"))
    devices = [
        Device(
            id="sink-1",
            name="Living Room",
            mac="AA:BB:CC:DD:EE:01",
            hci="hci0",
            connected=True,
            delay_ms=0,
            volume=0.8,
            mute=False,
            enabled=True,
        ),
        Device(
            id="sink-2",
            name="Kitchen",
            mac="AA:BB:CC:DD:EE:02",
            hci="hci1",
            connected=True,
            delay_ms=40,
            volume=0.7,
            mute=False,
            enabled=True,
        ),
    ]
    return MockAudioBackend(devices=devices)


def test_patch_delay_volume_mute_enabled(backend: MockAudioBackend) -> None:
    updated = backend.patch_device(
        "sink-1",
        DevicePatch(delay_ms=150, volume=0.55, mute=True, enabled=False),
    )
    assert updated.delay_ms == 150
    assert updated.volume == 0.55
    assert updated.mute is True
    assert updated.enabled is False


def test_delay_and_volume_clamped_via_backend_helpers(backend: MockAudioBackend) -> None:
    # API models reject out-of-range values; backend helpers still clamp raw ints/floats.
    from musync.config import clamp_delay_ms, clamp_volume

    assert clamp_delay_ms(9999) == 2000
    assert clamp_volume(2.5) == 1.0
    updated = backend.patch_device(
        "sink-1",
        DevicePatch(delay_ms=2000, volume=1.0),
    )
    assert updated.delay_ms == 2000
    assert updated.volume == 1.0


def test_transport_play_pause_stop_and_position(backend: MockAudioBackend) -> None:
    playing = backend.set_transport(TransportPut(state=TransportState.PLAYING))
    assert playing.state == TransportState.PLAYING
    time.sleep(0.05)
    mid = backend.get_transport()
    assert mid.position_ms >= 0
    assert mid.state == TransportState.PLAYING

    paused = backend.set_transport(TransportPut(state=TransportState.PAUSED))
    assert paused.state == TransportState.PAUSED
    pos = paused.position_ms

    scrubbed = backend.set_transport(TransportPut(position_ms=12_000))
    assert scrubbed.position_ms == 12_000

    stopped = backend.set_transport(TransportPut(state=TransportState.STOPPED))
    assert stopped.state == TransportState.STOPPED
    assert stopped.position_ms == 0
    assert pos >= 0


def test_health_and_sources(backend: MockAudioBackend) -> None:
    health = backend.health()
    assert health.backend == "mock"
    assert health.sink_count == 2
    assert health.pipewire_up is False
    sources = backend.list_sources()
    assert any(s.available for s in sources)
    assert any(not s.available and s.type.value == "spotify" for s in sources)
    assert any(not s.available and s.type.value == "apple" for s in sources)
    # Live S21 FE input is available; waiting USB host still contributes an unavailable row.
    assert any(s.available and s.type.value == "input" for s in sources)
    assert any(not s.available and s.type.value == "input" for s in sources)


def test_unknown_device(backend: MockAudioBackend) -> None:
    with pytest.raises(KeyError):
        backend.get_device("missing")
