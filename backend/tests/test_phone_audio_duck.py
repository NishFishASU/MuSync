"""Hub play must not leave audio on the phone speakers (Spotify transfer + pause)."""

from __future__ import annotations

import json
from typing import Any, Optional
from unittest.mock import MagicMock

import httpx
import pytest

from musync.audio.mock import MockAudioBackend
from musync.models import Device, SourceType, TransportPut, TransportState
from musync.streaming.service import reset_streaming_service
from musync.streaming.spotify import SpotifyClient


def _token_env(tmp_path, monkeypatch) -> None:
    token_path = tmp_path / "spotify_tokens.json"
    token_path.write_text(
        json.dumps(
            {
                "access_token": "test-access",
                "refresh_token": "test-refresh",
                "expires_at": 9999999999,
                "scope": (
                    "user-read-playback-state user-modify-playback-state "
                    "user-read-currently-playing streaming"
                ),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("MUSYNC_SPOTIFY_CLIENT_ID", "cid")
    monkeypatch.setenv("MUSYNC_SPOTIFY_CLIENT_SECRET", "csecret")
    monkeypatch.setenv("MUSYNC_SPOTIFY_TOKEN_PATH", str(token_path))
    monkeypatch.setenv("MUSYNC_LIBRESPOT_NAME", "MuSync")
    monkeypatch.delenv("MUSYNC_LIBRESPOT_BIN", raising=False)
    reset_streaming_service()


def _http_response(status: int, body: Optional[dict[str, Any]] = None) -> httpx.Response:
    return httpx.Response(
        status,
        json=body if body is not None else {},
        request=httpx.Request("PUT", "https://api.spotify.com/v1/me/player"),
    )


def test_play_uri_refuses_without_musync_connect_device(tmp_path, monkeypatch) -> None:
    """Never call /me/player/play without device_id (that keeps phone speakers alive)."""
    _token_env(tmp_path, monkeypatch)
    client = SpotifyClient()
    monkeypatch.setattr(client, "ensure_librespot", lambda feeder=None: None)
    monkeypatch.setattr(client, "_wait_for_connect_device", lambda **kwargs: None)

    puts: list[tuple[str, Any]] = []

    def _put(path: str, *, params=None, json_body=None) -> None:
        puts.append((path, params, json_body))

    monkeypatch.setattr(client, "_put_json", _put)

    with pytest.raises(ValueError, match="refusing to play on the phone"):
        client.play_uri("spotify:track:abcdefghij0123456789ab", feeder="musync_feeder")
    assert puts == []


def test_play_uri_transfers_then_plays_on_musync_device(tmp_path, monkeypatch) -> None:
    _token_env(tmp_path, monkeypatch)
    client = SpotifyClient()
    monkeypatch.setattr(client, "ensure_librespot", lambda feeder=None: "/usr/bin/librespot")
    monkeypatch.setattr(
        client, "_wait_for_connect_device", lambda **kwargs: "musync-device-id"
    )

    puts: list[tuple[str, Any, Any]] = []

    def _put(path: str, *, params=None, json_body=None) -> None:
        puts.append((path, params, json_body))

    monkeypatch.setattr(client, "_put_json", _put)

    result = client.play_uri(
        "spotify:track:abcdefghij0123456789ab", feeder="musync_feeder"
    )
    assert result["device_id"] == "musync-device-id"
    assert result["transferred"] is True
    assert puts[0][0] == "/me/player"
    assert puts[0][2] == {"device_ids": ["musync-device-id"], "play": False}
    assert puts[1][0] == "/me/player/play"
    assert puts[1][1] == {"device_id": "musync-device-id"}
    assert puts[1][2] == {"uris": ["spotify:track:abcdefghij0123456789ab"]}


def test_play_uri_still_plays_when_transfer_404(tmp_path, monkeypatch) -> None:
    """No active player yet — transfer soft-fails; play with device_id still runs."""
    _token_env(tmp_path, monkeypatch)
    client = SpotifyClient()
    monkeypatch.setattr(client, "ensure_librespot", lambda feeder=None: "/bin/librespot")
    monkeypatch.setattr(
        client, "_wait_for_connect_device", lambda **kwargs: "musync-device-id"
    )

    puts: list[str] = []

    def _put(path: str, *, params=None, json_body=None) -> None:
        puts.append(path)
        if path == "/me/player":
            raise httpx.HTTPStatusError(
                "404",
                request=httpx.Request("PUT", "https://api.spotify.com/v1/me/player"),
                response=_http_response(404, {"error": {"message": "Not found"}}),
            )

    monkeypatch.setattr(client, "_put_json", _put)
    result = client.play_uri("spotify:track:abcdefghij0123456789ab")
    assert result["transferred"] is False
    assert puts == ["/me/player", "/me/player/play"]


def test_pause_phone_playback_soft_fail_without_auth(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("MUSYNC_SPOTIFY_CLIENT_ID", raising=False)
    monkeypatch.delenv("MUSYNC_SPOTIFY_TOKEN_PATH", raising=False)
    reset_streaming_service()
    client = SpotifyClient()
    assert client.pause_phone_playback() is False


def test_pause_phone_playback_puts_pause(tmp_path, monkeypatch) -> None:
    _token_env(tmp_path, monkeypatch)
    client = SpotifyClient()
    called: list[str] = []

    def _put(path: str, *, params=None, json_body=None) -> None:
        called.append(path)

    monkeypatch.setattr(client, "_put_json", _put)
    assert client.pause_phone_playback() is True
    assert called == ["/me/player/pause"]


def test_pause_competing_on_local_mock_play(tmp_path, monkeypatch) -> None:
    """Local mock play should ask Spotify to pause phone-side playback."""
    _token_env(tmp_path, monkeypatch)
    monkeypatch.setenv("MUSYNC_DEVICES_CONFIG", str(tmp_path / "devices.yaml"))
    monkeypatch.setenv("MUSYNC_MEDIA_DIR", str(tmp_path / "media"))
    monkeypatch.setenv("MUSYNC_HOST_SPEAKERS", "0")
    reset_streaming_service()

    from musync.streaming import get_streaming_service

    svc = get_streaming_service()
    spy = MagicMock(return_value={"spotify_paused": True})
    monkeypatch.setattr(svc, "pause_competing_phone_playback", spy)

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
    local = next(q for q in backend.list_queue() if q.source_type == SourceType.LOCAL)
    backend.set_active_queue_item(local.id)
    backend.set_transport(TransportPut(state=TransportState.PLAYING))
    spy.assert_called()


def test_wait_for_connect_device_retries(tmp_path, monkeypatch) -> None:
    _token_env(tmp_path, monkeypatch)
    client = SpotifyClient()
    calls = {"n": 0}

    def _find() -> Optional[str]:
        calls["n"] += 1
        return "dev-1" if calls["n"] >= 3 else None

    monkeypatch.setattr(client, "_find_connect_device", _find)
    monkeypatch.setattr("musync.streaming.spotify.time.sleep", lambda _s: None)
    assert client._wait_for_connect_device(attempts=5, delay_s=0.01) == "dev-1"
    assert calls["n"] >= 3


def test_librespot_bin_finds_cargo_home(tmp_path, monkeypatch) -> None:
    """After cargo install, ~/.cargo/bin/librespot is found even off PATH."""
    from musync.streaming import spotify as spotify_mod

    cargo_bin = tmp_path / ".cargo" / "bin"
    cargo_bin.mkdir(parents=True)
    fake = cargo_bin / "librespot"
    fake.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake.chmod(0o755)

    monkeypatch.delenv("MUSYNC_LIBRESPOT_BIN", raising=False)
    monkeypatch.delenv("CARGO_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PATH", "/usr/bin")  # no librespot here
    monkeypatch.setattr(spotify_mod.shutil, "which", lambda _name: None)

    found = spotify_mod._librespot_bin()
    assert found is not None
    assert found.endswith("librespot")
    assert str(tmp_path) in found


def test_play_uri_error_mentions_install_hint(tmp_path, monkeypatch) -> None:
    _token_env(tmp_path, monkeypatch)
    client = SpotifyClient()
    monkeypatch.setattr(client, "ensure_librespot", lambda feeder=None: None)
    monkeypatch.setattr(client, "_wait_for_connect_device", lambda **kwargs: None)
    client._librespot_last_error = "librespot binary not found on PATH or ~/.cargo/bin."

    with pytest.raises(ValueError, match="libasound2-dev") as excinfo:
        client.play_uri("spotify:track:abcdefghij0123456789ab")
    assert "refusing to play on the phone" in str(excinfo.value)
