"""Queue loop (repeat-all) + turn-taking device rotation on track end."""

from __future__ import annotations

import time
import wave
from pathlib import Path

from musync.audio.mock import MockAudioBackend
from musync.audio.turn_taking import (
    eligible_turn_devices,
    next_turn_device_id,
)
from musync.models import (
    Device,
    QueueItemCreate,
    QueueSettingsPut,
    SourceType,
    TransportPut,
    TransportState,
)


def _write_silence_wav(path: Path, *, seconds: float = 0.05, rate: int = 8000) -> None:
    frames = max(1, int(rate * seconds))
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(b"\x00\x00" * frames)


def _backend(tmp_path, monkeypatch) -> MockAudioBackend:
    monkeypatch.setenv("MUSYNC_DEVICES_CONFIG", str(tmp_path / "devices.yaml"))
    monkeypatch.setenv("MUSYNC_MEDIA_DIR", str(tmp_path / "media"))
    return MockAudioBackend(
        devices=[
            Device(
                id="sink-a",
                name="Alice",
                mac="AA:BB:CC:DD:EE:01",
                hci="hci0",
                connected=True,
            ),
            Device(
                id="sink-b",
                name="Bob",
                mac="AA:BB:CC:DD:EE:02",
                hci="hci1",
                connected=True,
            ),
            Device(
                id="sink-c",
                name="Cara",
                mac="AA:BB:CC:DD:EE:03",
                hci="hci2",
                connected=False,
                enabled=True,
            ),
        ]
    )


def _add_local_cue(
    backend: MockAudioBackend, tmp_path, *, name: str, duration_ms: int
):
    path = tmp_path / "media" / f"{name}.wav"
    _write_silence_wav(path, seconds=max(0.05, duration_ms / 1000.0))
    return backend.add_queue_item(
        QueueItemCreate(
            source_type=SourceType.LOCAL,
            title=name,
            duration_ms=duration_ms,
            path=str(path),
            source_id=f"local:{name}",
        )
    )


def test_eligible_skips_disconnected_bt(tmp_path, monkeypatch) -> None:
    backend = _backend(tmp_path, monkeypatch)
    devices = backend.list_devices()
    eligible = eligible_turn_devices(devices)
    assert [d.id for d in eligible] == ["sink-a", "sink-b"]


def test_eligible_prefers_assigned_users(tmp_path, monkeypatch) -> None:
    backend = _backend(tmp_path, monkeypatch)
    devices = backend.list_devices()
    eligible = eligible_turn_devices(
        devices, assignments={"sink-b": "user-bob", "sink-c": "user-cara"}
    )
    # sink-c disconnected → only sink-b among assigned+ready
    assert [d.id for d in eligible] == ["sink-b"]


def test_next_turn_rotates_and_wraps() -> None:
    order = ["a", "b", "c"]
    assert next_turn_device_id(order, "a", rotate=True) == "b"
    assert next_turn_device_id(order, "c", rotate=True) == "a"
    assert next_turn_device_id(order, "b", rotate=False) == "b"
    assert next_turn_device_id(order, None, rotate=True) == "a"
    assert next_turn_device_id(order, "a", rotate=True, promote_id="c") == "c"
    assert next_turn_device_id(order, "a", rotate=False, promote_id="missing") == "a"


def test_turn_taking_solos_first_eligible(tmp_path, monkeypatch) -> None:
    backend = _backend(tmp_path, monkeypatch)
    settings = backend.get_queue_settings()
    assert settings.turn_taking is True
    assert settings.turn_order == ["sink-a", "sink-b"]
    assert settings.turn_device_id == "sink-a"
    by_id = {d.id: d for d in backend.list_devices()}
    assert by_id["sink-a"].mute is False
    assert by_id["sink-b"].mute is True


def test_queue_loop_and_settings_api(tmp_path, monkeypatch) -> None:
    backend = _backend(tmp_path, monkeypatch)
    assert backend.get_queue_settings().loop is False
    updated = backend.set_queue_settings(QueueSettingsPut(loop=True))
    assert updated.loop is True
    assert backend.get_queue_settings().loop is True
    off = backend.set_queue_settings(QueueSettingsPut(turn_taking=False))
    assert off.turn_taking is False
    # Fan-out restored — enabled sinks unmuted.
    by_id = {d.id: d for d in backend.list_devices()}
    assert by_id["sink-a"].mute is False
    assert by_id["sink-b"].mute is False


def test_track_end_advances_and_rotates(tmp_path, monkeypatch) -> None:
    backend = _backend(tmp_path, monkeypatch)
    # Clear seeded queue; two short local cues.
    for item in list(backend.list_queue()):
        backend.remove_queue_item(item.id)
    a = _add_local_cue(backend, tmp_path, name="one", duration_ms=40)
    b = _add_local_cue(backend, tmp_path, name="two", duration_ms=40)
    assert a.available and b.available
    backend.set_active_queue_item(a.id)
    backend.set_transport(TransportPut(state=TransportState.PLAYING))
    assert backend.get_queue_settings().turn_device_id == "sink-a"

    # Wait past first cue duration → auto-advance + rotate.
    time.sleep(0.08)
    transport = backend.get_transport()
    assert transport.queue_item_id == b.id
    assert transport.state == TransportState.PLAYING
    assert transport.position_ms < 40
    settings = backend.get_queue_settings()
    assert settings.turn_device_id == "sink-b"
    by_id = {d.id: d for d in backend.list_devices()}
    assert by_id["sink-b"].mute is False
    assert by_id["sink-a"].mute is True


def test_track_end_stops_without_loop(tmp_path, monkeypatch) -> None:
    backend = _backend(tmp_path, monkeypatch)
    for item in list(backend.list_queue()):
        backend.remove_queue_item(item.id)
    only = _add_local_cue(backend, tmp_path, name="solo", duration_ms=30)
    backend.set_queue_settings(QueueSettingsPut(loop=False))
    backend.set_active_queue_item(only.id)
    backend.set_transport(TransportPut(state=TransportState.PLAYING))
    time.sleep(0.06)
    transport = backend.get_transport()
    assert transport.state == TransportState.STOPPED
    assert transport.queue_item_id == only.id


def test_track_end_loops_to_first(tmp_path, monkeypatch) -> None:
    backend = _backend(tmp_path, monkeypatch)
    for item in list(backend.list_queue()):
        backend.remove_queue_item(item.id)
    a = _add_local_cue(backend, tmp_path, name="one", duration_ms=30)
    b = _add_local_cue(backend, tmp_path, name="two", duration_ms=30)
    backend.set_queue_settings(QueueSettingsPut(loop=True))
    backend.set_active_queue_item(b.id)
    backend.set_transport(TransportPut(state=TransportState.PLAYING))
    time.sleep(0.06)
    transport = backend.get_transport()
    assert transport.state == TransportState.PLAYING
    assert transport.queue_item_id == a.id


def test_notify_playback_ended_advances(tmp_path, monkeypatch) -> None:
    backend = _backend(tmp_path, monkeypatch)
    for item in list(backend.list_queue()):
        backend.remove_queue_item(item.id)
    a = _add_local_cue(backend, tmp_path, name="one", duration_ms=60_000)
    b = _add_local_cue(backend, tmp_path, name="two", duration_ms=60_000)
    backend.set_active_queue_item(a.id)
    backend.set_transport(TransportPut(state=TransportState.PLAYING))
    transport = backend.notify_playback_ended()
    assert transport.queue_item_id == b.id
    assert transport.state == TransportState.PLAYING
