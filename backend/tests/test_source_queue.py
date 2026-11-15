"""Song-level source switching: local | spotify | apple | input."""

from __future__ import annotations

import wave
from pathlib import Path

from musync.audio.mock import MockAudioBackend
from musync.models import (
    Device,
    MediaKind,
    QueueItemCreate,
    QueueItemPatch,
    SourceType,
    TransportPut,
    TransportState,
)


def _write_silence_wav(path: Path, *, seconds: float = 0.8, rate: int = 8000) -> None:
    frames = int(rate * seconds)
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
                id="sink-1",
                name="Living Room",
                mac="AA:BB:CC:DD:EE:01",
                hci="hci0",
                connected=True,
            ),
            Device(
                id="sink-2",
                name="Kitchen",
                mac="AA:BB:CC:DD:EE:02",
                hci="hci1",
                connected=True,
                delay_ms=40,
            ),
        ]
    )


def test_catalog_has_typed_stubs(tmp_path, monkeypatch) -> None:
    backend = _backend(tmp_path, monkeypatch)
    sources = backend.list_sources()
    types = {s.type for s in sources}
    assert SourceType.LOCAL in types
    assert SourceType.SPOTIFY in types
    assert SourceType.APPLE in types
    assert SourceType.INPUT in types
    non_local = [s for s in sources if s.type != SourceType.LOCAL]
    assert non_local
    assert all(s.type.value in {"spotify", "apple", "input"} for s in non_local)
    # Spotify/Apple remain stubs; live BT input may be available on mock.
    assert all(not s.available for s in non_local if s.type != SourceType.INPUT)
    assert any(s.available for s in non_local if s.type == SourceType.INPUT)


def test_queue_seeded_and_source_type_switch(tmp_path, monkeypatch) -> None:
    backend = _backend(tmp_path, monkeypatch)
    queue = backend.list_queue()
    assert queue
    local = next(q for q in queue if q.source_type == SourceType.LOCAL)
    assert local.available

    # Switch song to Spotify stub — sinks untouched (mock has no graph teardown).
    patched = backend.patch_queue_item(
        local.id, QueueItemPatch(source_type=SourceType.SPOTIFY)
    )
    assert patched.source_type == SourceType.SPOTIFY
    assert patched.available is False
    assert patched.note and "stub" in patched.note.lower()

    # Switch back to local.
    restored = backend.patch_queue_item(
        local.id, QueueItemPatch(source_type=SourceType.LOCAL, source_id=local.source_id)
    )
    assert restored.source_type == SourceType.LOCAL
    assert restored.available is True


def test_active_queue_switch_keeps_devices(tmp_path, monkeypatch) -> None:
    backend = _backend(tmp_path, monkeypatch)
    before = backend.list_devices()
    q = backend.list_queue()
    spotify = backend.add_queue_item(
        QueueItemCreate(source_type=SourceType.SPOTIFY, title="Stub song")
    )
    transport = backend.set_active_queue_item(spotify.id)
    assert transport.queue_item_id == spotify.id
    assert transport.source_type == SourceType.SPOTIFY
    assert transport.state == TransportState.STOPPED

    after = backend.list_devices()
    assert [d.model_dump() for d in before] == [d.model_dump() for d in after]


def test_cannot_play_stub(tmp_path, monkeypatch) -> None:
    backend = _backend(tmp_path, monkeypatch)
    item = backend.add_queue_item(QueueItemCreate(source_type=SourceType.APPLE))
    backend.set_active_queue_item(item.id)
    try:
        backend.set_transport(TransportPut(state=TransportState.PLAYING))
        raised = False
    except ValueError as exc:
        raised = True
        assert "stub" in str(exc).lower() or "apple" in str(exc).lower()
    assert raised


def test_local_playback_still_works(tmp_path, monkeypatch) -> None:
    backend = _backend(tmp_path, monkeypatch)
    local = next(q for q in backend.list_queue() if q.available)
    backend.set_active_queue_item(local.id)
    playing = backend.set_transport(TransportPut(state=TransportState.PLAYING))
    assert playing.state == TransportState.PLAYING
    assert playing.source_type == SourceType.LOCAL
    stopped = backend.set_transport(TransportPut(state=TransportState.STOPPED))
    assert stopped.state == TransportState.STOPPED
    assert stopped.position_ms == 0


def test_catalog_active_source_api(tmp_path, monkeypatch) -> None:
    backend = _backend(tmp_path, monkeypatch)
    local = next(s for s in backend.list_sources() if s.available)
    transport = backend.set_active_source(local.id)
    assert transport.source_id == local.id
    assert transport.queue_item_id is not None


def test_transport_duration_matches_probed_file(tmp_path, monkeypatch) -> None:
    media = tmp_path / "media"
    _write_silence_wav(media / "short_clip.wav", seconds=0.5)
    backend = _backend(tmp_path, monkeypatch)
    local = next(q for q in backend.list_queue() if q.available)
    assert local.duration_ms is not None
    assert abs(local.duration_ms - 500) <= 20
    transport = backend.set_active_queue_item(local.id)
    assert abs(transport.duration_ms - 500) <= 20
    assert transport.duration_ms != 180_000


def test_local_video_plays_into_queue(tmp_path, monkeypatch) -> None:
    media = tmp_path / "media"
    media.mkdir(parents=True, exist_ok=True)
    (media / "demo_clip.mp4").write_bytes(b"fake-mp4")
    backend = _backend(tmp_path, monkeypatch)
    video = next(s for s in backend.list_sources() if s.media_kind == MediaKind.VIDEO)
    assert video.available is True
    transport = backend.set_active_source(video.id)
    assert transport.media_kind == MediaKind.VIDEO
    assert transport.source_type == SourceType.LOCAL
    playing = backend.set_transport(TransportPut(state=TransportState.PLAYING))
    assert playing.state == TransportState.PLAYING
    assert playing.media_kind == MediaKind.VIDEO
    # Sink graph identity unchanged (mock devices still listed).
    assert len(backend.list_devices()) >= 1
