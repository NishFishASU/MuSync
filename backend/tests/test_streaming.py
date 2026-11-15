"""Streaming status + Spotify/Apple queue playability without real credentials."""

from __future__ import annotations

import json
import os
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
    note = body["spotify"]["note"].lower()
    assert "missing" in note or "credential" in note or "unavailable" in note or "stub" in note


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
    detail = res.json()["detail"].lower()
    assert "not configured" in detail or "musync.env" in detail


def test_spotify_login_redirects_when_not_configured(tmp_path, monkeypatch) -> None:
    """Link-style OAuth: missing env → redirect back to SPA with error (not a form)."""
    from musync.app import create_app

    backend = _backend(tmp_path, monkeypatch)
    client = TestClient(create_app(audio_backend=backend))
    res = client.get("/api/streaming/spotify/login", follow_redirects=False)
    assert res.status_code in {302, 307}
    loc = res.headers.get("location") or ""
    assert "spotify=error" in loc
    assert "CLIENT" in loc.upper() or "credential" in loc.lower() or "missing" in loc.lower()


def test_spotify_authorize_url_json_when_not_configured(tmp_path, monkeypatch) -> None:
    from musync.app import create_app

    backend = _backend(tmp_path, monkeypatch)
    client = TestClient(create_app(audio_backend=backend))
    res = client.get("/api/streaming/spotify/authorize-url")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is False
    assert "CLIENT" in body["error"].upper() or "credential" in body["error"].lower()
    assert "redirect_uri" in body


def test_spotify_authorize_url_when_configured(tmp_path, monkeypatch) -> None:
    from musync.app import create_app

    backend = _backend(tmp_path, monkeypatch)
    monkeypatch.setenv("MUSYNC_SPOTIFY_CLIENT_ID", "cid")
    monkeypatch.setenv("MUSYNC_SPOTIFY_CLIENT_SECRET", "csecret")
    monkeypatch.setenv(
        "MUSYNC_SPOTIFY_REDIRECT_URI",
        "http://127.0.0.1:8081/api/streaming/spotify/callback",
    )
    reset_streaming_service()
    client = TestClient(create_app(audio_backend=backend))
    res = client.get("/api/streaming/spotify/authorize-url")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert "accounts.spotify.com/authorize" in body["url"]
    assert "8081" in body["redirect_uri"]
    login = client.get("/api/streaming/spotify/login", follow_redirects=False)
    assert login.status_code in {302, 307}
    loc = login.headers.get("location") or ""
    assert "accounts.spotify.com/authorize" in loc
    from urllib.parse import parse_qs, urlparse

    qs = parse_qs(urlparse(loc).query)
    assert qs.get("redirect_uri", [""])[0] == (
        "http://127.0.0.1:8081/api/streaming/spotify/callback"
    )


def test_load_streaming_env_parses_export_and_bom(tmp_path, monkeypatch) -> None:
    from musync.streaming.credentials import load_streaming_env_files

    env_path = tmp_path / "musync.env"
    # UTF-8 BOM + export prefix + CRLF — Windows-ish file
    env_path.write_bytes(
        b"\xef\xbb\xbfexport MUSYNC_SPOTIFY_CLIENT_ID=\"win-cid\"\r\n"
        b"set MUSYNC_SPOTIFY_CLIENT_SECRET=win-secret\r\n"
        b"MUSYNC_SPOTIFY_REDIRECT_URI=http://127.0.0.1:8081/api/streaming/spotify/callback\r\n"
        b"MUSYNC_SPOTIFY_CLIENT_ID=\r\n"  # blank must not wipe prior
    )
    monkeypatch.delenv("MUSYNC_SPOTIFY_CLIENT_ID", raising=False)
    monkeypatch.delenv("MUSYNC_SPOTIFY_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("MUSYNC_SPOTIFY_REDIRECT_URI", raising=False)
    monkeypatch.setattr(
        "musync.streaming.credentials.Path.home", lambda: tmp_path
    )
    monkeypatch.setattr(
        "musync.streaming.credentials.local_env_path",
        lambda: tmp_path / "missing.local.env",
    )
    loaded = load_streaming_env_files()
    assert loaded
    assert os.environ["MUSYNC_SPOTIFY_CLIENT_ID"] == "win-cid"
    assert os.environ["MUSYNC_SPOTIFY_CLIENT_SECRET"] == "win-secret"
    assert "8081" in os.environ["MUSYNC_SPOTIFY_REDIRECT_URI"]


def test_now_playing_api_for_local(tmp_path, monkeypatch) -> None:
    from musync.app import create_app

    backend = _backend(tmp_path, monkeypatch)
    client = TestClient(create_app(audio_backend=backend))
    res = client.get("/api/now-playing")
    assert res.status_code == 200
    body = res.json()
    assert "title" in body
    assert body.get("source_type") in {None, "local", "spotify", "apple", "input"}
    # Mock laptop path always surfaces sample title / artist / album / cover.
    assert body.get("title")
    assert body.get("artist")
    assert body.get("album")
    assert body.get("cover_url")
    assert body.get("has_cover") is True
    cover = client.get("/api/now-playing/cover?source=mock-sample")
    assert cover.status_code == 200
    assert "image" in (cover.headers.get("content-type") or "")
    assert len(cover.content) > 40


def test_now_playing_sample_for_input_and_streaming(tmp_path, monkeypatch) -> None:
    from musync.app import create_app
    from musync.models import QueueItemCreate, SourceType

    backend = _backend(tmp_path, monkeypatch)
    client = TestClient(create_app(audio_backend=backend))

    hosts = client.get("/api/inputs").json()
    assert hosts
    host_id = hosts[0]["id"]
    item = backend.add_queue_item(
        QueueItemCreate(source_type=SourceType.INPUT, input_id=host_id, title="Live cue")
    )
    backend.set_active_queue_item(item.id)
    np = client.get("/api/now-playing").json()
    assert np.get("source_type") == "input"
    assert np.get("title")
    assert np.get("artist")
    assert np.get("cover_url")

    spotify = backend.add_queue_item(
        QueueItemCreate(
            source_type=SourceType.SPOTIFY,
            locator="spotify:track:mock123",
            title=None,
        )
    )
    backend.set_active_queue_item(spotify.id)
    np2 = client.get("/api/now-playing").json()
    assert np2.get("source_type") == "spotify"
    assert np2.get("artist")
    assert np2.get("album")
    assert np2.get("cover_url")


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
