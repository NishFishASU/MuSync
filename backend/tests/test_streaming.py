"""Streaming status + Spotify/Apple queue playability without real credentials."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from musync.audio.mock import MockAudioBackend
from musync.models import (
    Device,
    QueueItemCreate,
    QueueItemPatch,
    SourceType,
    TransportPut,
    TransportState,
)
from musync.streaming.service import reset_streaming_service


def _backend(tmp_path, monkeypatch) -> MockAudioBackend:
    monkeypatch.setenv("MUSYNC_DEVICES_CONFIG", str(tmp_path / "devices.yaml"))
    monkeypatch.setenv("MUSYNC_MEDIA_DIR", str(tmp_path / "media"))
    monkeypatch.delenv("MUSYNC_SPOTIFY_CLIENT_ID", raising=False)
    monkeypatch.delenv("MUSYNC_SPOTIFY_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("MUSYNC_APPLE_TEAM_ID", raising=False)
    monkeypatch.delenv("MUSYNC_APPLE_KEY_ID", raising=False)
    monkeypatch.delenv("MUSYNC_APPLE_PRIVATE_KEY_PATH", raising=False)
    monkeypatch.delenv("MUSYNC_APPLE_AIRPLAY_SOURCE", raising=False)
    monkeypatch.delenv("MUSYNC_STREAMING_MOCK", raising=False)
    reset_streaming_service()
    return MockAudioBackend(
        devices=[
            Device(
                id="sink-1",
                name="Living Room",
                mac="AA:BB:CC:DD:EE:01",
                hci="hci0",
                connected=True,
            )
        ]
    )


def test_streaming_status_without_credentials(tmp_path, monkeypatch) -> None:
    from musync.app import create_app

    backend = _backend(tmp_path, monkeypatch)
    client = TestClient(create_app(audio_backend=backend))
    res = client.get("/api/streaming")
    assert res.status_code == 200
    body = res.json()
    assert body["spotify"]["configured"] is False
    assert body["spotify"]["authenticated"] is False
    assert body["apple"]["configured"] is False
    assert "stub" in body["spotify"]["note"].lower() or "missing" in body["spotify"]["note"].lower()


def test_mock_local_still_works_without_streaming_creds(tmp_path, monkeypatch) -> None:
    backend = _backend(tmp_path, monkeypatch)
    queue = backend.list_queue()
    local = next(q for q in queue if q.source_type == SourceType.LOCAL)
    assert local.available
    transport = backend.set_active_queue_item(local.id)
    playing = backend.set_transport(TransportPut(state=TransportState.PLAYING))
    assert playing.state == TransportState.PLAYING
    assert transport.queue_item_id == local.id


def test_spotify_stub_without_auth(tmp_path, monkeypatch) -> None:
    backend = _backend(tmp_path, monkeypatch)
    item = backend.add_queue_item(
        QueueItemCreate(
            source_type=SourceType.SPOTIFY,
            locator="spotify:track:abcdefghij0123456789ab",
            title="Would-be track",
        )
    )
    assert item.available is False
    with pytest.raises(ValueError):
        backend.set_transport(
            TransportPut(queue_item_id=item.id, state=TransportState.PLAYING)
        )


def test_spotify_playable_with_token_file(tmp_path, monkeypatch) -> None:
    token_path = tmp_path / "spotify_tokens.json"
    token_path.write_text(
        json.dumps(
            {
                "access_token": "test-access",
                "refresh_token": "test-refresh",
                "expires_at": 9999999999,
                "display_name": "Test User",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("MUSYNC_SPOTIFY_CLIENT_ID", "cid")
    monkeypatch.setenv("MUSYNC_SPOTIFY_CLIENT_SECRET", "csecret")
    monkeypatch.setenv("MUSYNC_SPOTIFY_TOKEN_PATH", str(token_path))
    monkeypatch.setenv("MUSYNC_STREAMING_MOCK", "1")
    monkeypatch.setenv("MUSYNC_DEVICES_CONFIG", str(tmp_path / "devices.yaml"))
    monkeypatch.setenv("MUSYNC_MEDIA_DIR", str(tmp_path / "media"))
    reset_streaming_service()

    backend = MockAudioBackend(
        devices=[
            Device(
                id="sink-1",
                name="Living Room",
                mac="AA:BB:CC:DD:EE:01",
                hci="hci0",
                connected=True,
            )
        ]
    )
    from musync.streaming import get_streaming_service

    assert get_streaming_service().spotify_ready() is True

    item = backend.add_queue_item(
        QueueItemCreate(
            source_type=SourceType.SPOTIFY,
            locator="spotify:track:abcdefghij0123456789ab",
            title="Real URI",
            duration_ms=120_000,
        )
    )
    assert item.available is True
    transport = backend.set_active_queue_item(item.id)
    playing = backend.set_transport(TransportPut(state=TransportState.PLAYING))
    assert playing.state == TransportState.PLAYING
    assert transport.source_type == SourceType.SPOTIFY


def test_streaming_queue_api_requires_auth_for_search(tmp_path, monkeypatch) -> None:
    from musync.app import create_app

    backend = _backend(tmp_path, monkeypatch)
    client = TestClient(create_app(audio_backend=backend))
    res = client.get("/api/streaming/search", params={"provider": "spotify", "q": "hello"})
    assert res.status_code == 400


def test_apple_airplay_marks_playable(tmp_path, monkeypatch) -> None:
    key = tmp_path / "AuthKey_TEST.p8"
    # Minimal placeholder — configured() only checks file exists + team/key env.
    key.write_text("-----BEGIN PRIVATE KEY-----\nMIGH\n-----END PRIVATE KEY-----\n")
    monkeypatch.setenv("MUSYNC_APPLE_TEAM_ID", "TEAM123")
    monkeypatch.setenv("MUSYNC_APPLE_KEY_ID", "KEY123")
    monkeypatch.setenv("MUSYNC_APPLE_PRIVATE_KEY_PATH", str(key))
    monkeypatch.setenv("MUSYNC_APPLE_AIRPLAY_SOURCE", "shairport-sync")
    monkeypatch.setenv("MUSYNC_DEVICES_CONFIG", str(tmp_path / "devices.yaml"))
    monkeypatch.setenv("MUSYNC_MEDIA_DIR", str(tmp_path / "media"))
    reset_streaming_service()

    backend = MockAudioBackend(
        devices=[
            Device(
                id="sink-1",
                name="Living Room",
                mac="AA:BB:CC:DD:EE:01",
                hci="hci0",
                connected=True,
            )
        ]
    )
    item = backend.add_queue_item(
        QueueItemCreate(
            source_type=SourceType.APPLE,
            locator="apple:song:1234567890",
            title="AirPlay song",
        )
    )
    assert item.available is True


def test_patch_to_spotify_hub_without_uri_stays_stub(tmp_path, monkeypatch) -> None:
    backend = _backend(tmp_path, monkeypatch)
    local = next(q for q in backend.list_queue() if q.source_type == SourceType.LOCAL)
    patched = backend.patch_queue_item(
        local.id, QueueItemPatch(source_type=SourceType.SPOTIFY)
    )
    assert patched.source_type == SourceType.SPOTIFY
    assert patched.available is False
