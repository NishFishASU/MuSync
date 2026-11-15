"""Host default-speaker playback for MUSYNC_BACKEND=mock."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from musync.audio.host_player import (
    HostSpeakerPlayer,
    discover_host_player,
    write_demo_tone_wav,
)
from musync.audio.mock import MockAudioBackend
from musync.models import Device, QueueItemPatch, SourceType, TransportPut, TransportState


@pytest.fixture
def wav_path(tmp_path: Path) -> Path:
    return write_demo_tone_wav(tmp_path / "tone.wav", duration_s=0.25)


def test_discover_prefers_ffplay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MUSYNC_HOST_SPEAKERS", "1")
    monkeypatch.setattr(
        "musync.audio.host_player._sounddevice_available", lambda: False
    )
    monkeypatch.setattr(
        "musync.audio.host_player.shutil.which",
        lambda name: f"/usr/bin/{name}" if name == "ffplay" else None,
    )
    backend, detail = discover_host_player()
    assert backend == "ffplay"
    assert "ffplay" in detail


def test_discover_soft_fails_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MUSYNC_HOST_SPEAKERS", "0")
    backend, detail = discover_host_player()
    assert backend == "none"
    assert "disabled" in detail


def test_discover_soft_fails_without_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MUSYNC_HOST_SPEAKERS", "1")
    monkeypatch.setattr(
        "musync.audio.host_player._sounddevice_available", lambda: False
    )
    monkeypatch.setattr("musync.audio.host_player.shutil.which", lambda _name: None)
    backend, detail = discover_host_player()
    assert backend == "none"
    assert "soft-fail" in detail


def test_play_cli_starts_and_stop(
    wav_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MUSYNC_HOST_SPEAKERS", "1")
    monkeypatch.setattr(
        "musync.audio.host_player._sounddevice_available", lambda: False
    )
    monkeypatch.setattr(
        "musync.audio.host_player.shutil.which",
        lambda name: f"/usr/bin/{name}" if name == "ffplay" else None,
    )
    proc = MagicMock()
    proc.poll.return_value = None
    popen = MagicMock(return_value=proc)
    monkeypatch.setattr("musync.audio.host_player.subprocess.Popen", popen)

    player = HostSpeakerPlayer()
    assert player.play(str(wav_path), position_ms=100) is True
    assert popen.called
    argv = popen.call_args[0][0]
    assert argv[0] == "ffplay"
    assert str(wav_path) in argv
    assert "-ss" in argv
    player.stop()
    proc.terminate.assert_called()


def test_play_missing_file_soft_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MUSYNC_HOST_SPEAKERS", "1")
    monkeypatch.setattr(
        "musync.audio.host_player._sounddevice_available", lambda: False
    )
    monkeypatch.setattr(
        "musync.audio.host_player.shutil.which",
        lambda name: f"/usr/bin/{name}" if name == "ffplay" else None,
    )
    player = HostSpeakerPlayer()
    assert player.play("/no/such/file.wav") is False
    assert player._last_error == "no local file"


def test_play_popen_oserror_soft_fails(
    wav_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MUSYNC_HOST_SPEAKERS", "1")
    monkeypatch.setattr(
        "musync.audio.host_player._sounddevice_available", lambda: False
    )
    monkeypatch.setattr(
        "musync.audio.host_player.shutil.which",
        lambda name: f"/usr/bin/{name}" if name == "ffplay" else None,
    )
    monkeypatch.setattr(
        "musync.audio.host_player.subprocess.Popen",
        MagicMock(side_effect=OSError("no audio device")),
    )
    player = HostSpeakerPlayer()
    assert player.play(str(wav_path)) is False
    assert "no audio device" in player._last_error


def test_mock_backend_routes_local_file_to_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, wav_path: Path
) -> None:
    monkeypatch.setenv("MUSYNC_DEVICES_CONFIG", str(tmp_path / "devices.yaml"))
    monkeypatch.setenv("MUSYNC_MEDIA_DIR", str(tmp_path / "media"))
    monkeypatch.setenv("MUSYNC_HOST_SPEAKERS", "1")

    play_calls: list[tuple[str | None, int]] = []

    class FakePlayer:
        backend = "ffplay"
        detail = "ffplay → host default speakers"

        def stop(self) -> None:
            play_calls.append(("stop", 0))

        def play(self, path, *, position_ms: int = 0) -> bool:
            play_calls.append((path, position_ms))
            return True

        def play_demo_tone(self, *, position_ms: int = 0) -> bool:
            play_calls.append(("demo", position_ms))
            return True

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
        )
    ]
    backend = MockAudioBackend(devices=devices)
    backend._host_player = FakePlayer()  # type: ignore[assignment]

    # Point active cue at our wav.
    queue = backend.list_queue()
    assert queue
    item = queue[0]
    backend.patch_queue_item(
        item.id,
        QueueItemPatch(path=str(wav_path), source_type=SourceType.LOCAL),
    )
    playing = backend.set_transport(TransportPut(state=TransportState.PLAYING))
    assert playing.state == TransportState.PLAYING
    assert any(c[0] == str(wav_path) for c in play_calls)

    backend.set_transport(TransportPut(state=TransportState.STOPPED))
    assert ("stop", 0) in play_calls

    health = backend.health()
    assert health.host_speakers == "ffplay"
    assert "host speakers" in health.detail.lower() or "ffplay" in health.detail


def test_mock_backend_soft_fails_without_breaking_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MUSYNC_DEVICES_CONFIG", str(tmp_path / "devices.yaml"))
    monkeypatch.setenv("MUSYNC_MEDIA_DIR", str(tmp_path / "media"))
    monkeypatch.setenv("MUSYNC_HOST_SPEAKERS", "0")
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
        )
    ]
    backend = MockAudioBackend(devices=devices)
    playing = backend.set_transport(TransportPut(state=TransportState.PLAYING))
    assert playing.state == TransportState.PLAYING
    assert backend.health().host_speakers == "none"
