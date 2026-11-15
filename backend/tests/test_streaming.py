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
    monkeypatch.delenv("MUSYNC_AIRPLAY_SOURCE", raising=False)
    monkeypatch.delenv("MUSYNC_YOUTUBE_API_KEY", raising=False)
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
    assert "airplay" in body
    # Mock seeds a live AirPlay host — status should report receiving/configured.
    assert body["airplay"]["configured"] is True
    assert body["airplay"].get("receiving") is True
    assert body["airplay"].get("source")
    assert body["youtube"]["configured"] is False
    note = body["spotify"]["note"].lower()
    assert "missing" in note or "credential" in note or "unavailable" in note or "stub" in note
    yt_note = body["youtube"]["note"].lower()
    assert "key" in yt_note or "missing" in yt_note or "unavailable" in yt_note


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


def test_spotify_search_requires_connect_when_configured(tmp_path, monkeypatch) -> None:
    from musync.app import create_app

    backend = _backend(tmp_path, monkeypatch)
    monkeypatch.setenv("MUSYNC_SPOTIFY_CLIENT_ID", "cid")
    monkeypatch.setenv("MUSYNC_SPOTIFY_CLIENT_SECRET", "csecret")
    reset_streaming_service()
    client = TestClient(create_app(audio_backend=backend))
    res = client.get(
        "/api/streaming/search",
        params={"provider": "spotify", "q": "Shape Of You"},
    )
    assert res.status_code == 400
    detail = res.json()["detail"].lower()
    assert "connect" in detail
    assert res.status_code != 500


def test_spotify_search_api_error_is_400_not_500(tmp_path, monkeypatch) -> None:
    """Spotify Web API 403/401 must surface as JSON detail — never Internal Server Error."""
    from unittest.mock import MagicMock, patch

    from musync.app import create_app

    token_path = tmp_path / "spotify_tokens.json"
    token_path.write_text(
        json.dumps(
            {
                "access_token": "test-access",
                "refresh_token": "test-refresh",
                "expires_at": 9999999999,
                "display_name": "Test User",
                "scope": (
                    "user-read-private user-read-email user-read-playback-state "
                    "user-modify-playback-state user-read-currently-playing streaming"
                ),
            }
        ),
        encoding="utf-8",
    )
    backend = _backend(tmp_path, monkeypatch)
    monkeypatch.setenv("MUSYNC_SPOTIFY_CLIENT_ID", "cid")
    monkeypatch.setenv("MUSYNC_SPOTIFY_CLIENT_SECRET", "csecret")
    monkeypatch.setenv("MUSYNC_SPOTIFY_TOKEN_PATH", str(token_path))
    reset_streaming_service()
    client = TestClient(create_app(audio_backend=backend))

    search_resp = MagicMock()
    search_resp.status_code = 403
    search_resp.json.return_value = {
        "error": {"status": 403, "message": "Forbidden"}
    }
    search_resp.text = '{"error":{"status":403,"message":"Forbidden"}}'

    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.__exit__.return_value = False
    mock_client.get.return_value = search_resp

    with patch("musync.streaming.spotify.httpx.Client", return_value=mock_client):
        res = client.get(
            "/api/streaming/search",
            params={"provider": "spotify", "q": "Shape Of You"},
        )

    assert res.status_code == 400
    detail = res.json()["detail"].lower()
    assert "forbidden" in detail or "403" in detail or "allowlist" in detail
    assert "internal server error" not in detail


def test_spotify_status_surfaces_granted_scopes_and_search_market(
    tmp_path, monkeypatch
) -> None:
    """Streaming status exposes granted scopes + search market (never from_token)."""
    from musync.app import create_app

    token_path = tmp_path / "spotify_tokens.json"
    token_path.write_text(
        json.dumps(
            {
                "access_token": "test-access",
                "refresh_token": "test-refresh",
                "expires_at": 9999999999,
                "display_name": "Test User",
                # Old grant without user-read-private — search must still be allowed
                "scope": "user-read-playback-state streaming",
            }
        ),
        encoding="utf-8",
    )
    backend = _backend(tmp_path, monkeypatch)
    monkeypatch.setenv("MUSYNC_SPOTIFY_CLIENT_ID", "cid")
    monkeypatch.setenv("MUSYNC_SPOTIFY_CLIENT_SECRET", "csecret")
    monkeypatch.setenv("MUSYNC_SPOTIFY_TOKEN_PATH", str(token_path))
    monkeypatch.setenv("MUSYNC_SPOTIFY_MARKET", "GB")
    reset_streaming_service()
    client = TestClient(create_app(audio_backend=backend))

    status = client.get("/api/streaming").json()["spotify"]
    assert status["authenticated"] is True
    assert "user-read-playback-state" in status["scopes"]
    assert "streaming" in status["scopes"]
    assert "user-read-private" not in status["scopes"]
    # user-read-private is optional for search; playback gaps still listed
    assert "user-modify-playback-state" in status["missing_scopes"]
    assert status["search_market"] == "GB"
    assert status["search_market"] != "from_token"


def test_spotify_search_insufficient_scope_message(tmp_path, monkeypatch) -> None:
    from unittest.mock import MagicMock, patch

    from musync.app import create_app

    token_path = tmp_path / "spotify_tokens.json"
    token_path.write_text(
        json.dumps(
            {
                "access_token": "test-access",
                "refresh_token": "test-refresh",
                "expires_at": 9999999999,
                "display_name": "Test User",
                "scope": (
                    "user-read-private user-read-email user-read-playback-state "
                    "user-modify-playback-state user-read-currently-playing streaming"
                ),
            }
        ),
        encoding="utf-8",
    )
    backend = _backend(tmp_path, monkeypatch)
    monkeypatch.setenv("MUSYNC_SPOTIFY_CLIENT_ID", "cid")
    monkeypatch.setenv("MUSYNC_SPOTIFY_CLIENT_SECRET", "csecret")
    monkeypatch.setenv("MUSYNC_SPOTIFY_TOKEN_PATH", str(token_path))
    reset_streaming_service()
    client = TestClient(create_app(audio_backend=backend))

    search_resp = MagicMock()
    search_resp.status_code = 403
    search_resp.json.return_value = {
        "error": {"status": 403, "message": "Insufficient client scope"}
    }
    search_resp.text = '{"error":{"status":403,"message":"Insufficient client scope"}}'

    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.__exit__.return_value = False
    mock_client.get.return_value = search_resp

    with patch("musync.streaming.spotify.httpx.Client", return_value=mock_client):
        res = client.get(
            "/api/streaming/search",
            params={"provider": "spotify", "q": "Shape Of You"},
        )

    assert res.status_code == 400
    detail = res.json()["detail"].lower()
    assert "insufficient client scope" in detail
    assert "from_token" in detail or "market" in detail


def test_spotify_search_shape_of_you_maps_hits(tmp_path, monkeypatch) -> None:
    from unittest.mock import MagicMock, patch

    from musync.app import create_app

    token_path = tmp_path / "spotify_tokens.json"
    token_path.write_text(
        json.dumps(
            {
                "access_token": "test-access",
                "refresh_token": "test-refresh",
                "expires_at": 9999999999,
                "display_name": "Test User",
                "scope": (
                    "user-read-private user-read-email user-read-playback-state "
                    "user-modify-playback-state user-read-currently-playing streaming"
                ),
            }
        ),
        encoding="utf-8",
    )
    backend = _backend(tmp_path, monkeypatch)
    monkeypatch.setenv("MUSYNC_SPOTIFY_CLIENT_ID", "cid")
    monkeypatch.setenv("MUSYNC_SPOTIFY_CLIENT_SECRET", "csecret")
    monkeypatch.setenv("MUSYNC_SPOTIFY_TOKEN_PATH", str(token_path))
    reset_streaming_service()
    client = TestClient(create_app(audio_backend=backend))

    search_resp = MagicMock()
    search_resp.status_code = 200
    search_resp.json.return_value = {
        "tracks": {
            "items": [
                {
                    "id": "7qiZfU4dY1lWllzX7mPBI3",
                    "uri": "spotify:track:7qiZfU4dY1lWllzX7mPBI3",
                    "name": "Shape of You",
                    "duration_ms": 233713,
                    "preview_url": None,
                    "artists": [{"name": "Ed Sheeran"}],
                    "album": {
                        "name": "÷ (Deluxe)",
                        "images": [
                            {"url": "https://example.com/cover-lg.jpg"},
                            {"url": "https://example.com/cover.jpg"},
                        ],
                    },
                },
                None,
            ]
        }
    }
    search_resp.text = "{}"

    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.__exit__.return_value = False
    mock_client.get.return_value = search_resp

    with patch("musync.streaming.spotify.httpx.Client", return_value=mock_client):
        res = client.get(
            "/api/streaming/search",
            params={"provider": "spotify", "q": "Shape Of You"},
        )

    assert res.status_code == 200
    body = res.json()
    assert body["provider"] == "spotify"
    assert body["query"] == "Shape Of You"
    assert len(body["hits"]) == 1
    hit = body["hits"][0]
    assert hit["title"] == "Shape of You"
    assert hit["artist"] == "Ed Sheeran"
    assert hit["uri"].startswith("spotify:track:")
    # Never send market=from_token — use explicit ISO country (default US)
    _args, kwargs = mock_client.get.call_args
    assert kwargs["params"]["market"] == "US"
    assert kwargs["params"]["market"] != "from_token"
    assert kwargs["params"]["q"] == "Shape Of You"
    # Dev Mode: limit must be digit string ≤10 (15 used to 400 Invalid limit)
    assert kwargs["params"]["limit"] == "10"
    assert isinstance(kwargs["params"]["limit"], str)
    assert kwargs["params"]["limit"].isdigit()


def test_clamp_search_limit_dev_mode_max_10(monkeypatch) -> None:
    """Dev Mode Spotify apps reject limit>10 with 400 Invalid limit."""
    from musync.streaming.spotify import _clamp_search_limit, _coerce_query_int

    monkeypatch.delenv("MUSYNC_SPOTIFY_SEARCH_MAX", raising=False)
    assert _clamp_search_limit(15) == "10"
    assert _clamp_search_limit("15") == "10"
    assert _clamp_search_limit(0) == "1"
    assert _clamp_search_limit(-3) == "1"
    assert _clamp_search_limit(99) == "10"
    assert _clamp_search_limit(10) == "10"
    assert _clamp_search_limit("nope") == "10"
    assert _clamp_search_limit(None) == "10"
    # FastAPI/query oddities: list / tuple / blank
    assert _clamp_search_limit([15]) == "10"
    assert _clamp_search_limit(["8"]) == "8"
    assert _clamp_search_limit([]) == "10"
    assert _coerce_query_int(["12"], default=10) == 12
    assert _coerce_query_int(None, default=10) == 10
    monkeypatch.setenv("MUSYNC_SPOTIFY_SEARCH_MAX", "50")
    assert _clamp_search_limit(15) == "15"
    assert _clamp_search_limit(99) == "50"
    assert all(isinstance(_clamp_search_limit(n), str) for n in (1, 10, 15))


def test_spotify_search_api_clamps_default_limit_to_10(tmp_path, monkeypatch) -> None:
    """GET /api/streaming/search without limit must not send Spotify limit=15."""
    from unittest.mock import MagicMock, patch

    from musync.app import create_app

    token_path = tmp_path / "spotify_tokens.json"
    token_path.write_text(
        json.dumps(
            {
                "access_token": "test-access",
                "refresh_token": "test-refresh",
                "expires_at": 9999999999,
                "display_name": "Test User",
                "scope": "streaming",
            }
        ),
        encoding="utf-8",
    )
    backend = _backend(tmp_path, monkeypatch)
    monkeypatch.setenv("MUSYNC_SPOTIFY_CLIENT_ID", "cid")
    monkeypatch.setenv("MUSYNC_SPOTIFY_CLIENT_SECRET", "csecret")
    monkeypatch.setenv("MUSYNC_SPOTIFY_TOKEN_PATH", str(token_path))
    monkeypatch.delenv("MUSYNC_SPOTIFY_SEARCH_MAX", raising=False)
    reset_streaming_service()
    client = TestClient(create_app(audio_backend=backend))

    search_resp = MagicMock()
    search_resp.status_code = 200
    search_resp.json.return_value = {"tracks": {"items": []}}
    search_resp.text = "{}"
    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.__exit__.return_value = False
    mock_client.get.return_value = search_resp

    with patch("musync.streaming.spotify.httpx.Client", return_value=mock_client):
        # Explicit oversized limit — must clamp to 10 for Dev Mode
        res = client.get(
            "/api/streaming/search",
            params={"provider": "spotify", "q": "Shape Of You", "limit": 15},
        )
        assert res.status_code == 200
        assert mock_client.get.call_args.kwargs["params"]["limit"] == "10"

        # Duplicate query keys / stringy list-ish values still coerce
        res2 = client.get(
            "/api/streaming/search?provider=spotify&q=test&limit=15&limit=20"
        )
        assert res2.status_code == 200
        sent = mock_client.get.call_args.kwargs["params"]["limit"]
        assert sent == "10"
        assert isinstance(sent, str)


def test_spotify_search_works_without_user_read_private(tmp_path, monkeypatch) -> None:
    """Catalog search must succeed with old tokens that lack user-read-private."""
    from unittest.mock import MagicMock, patch

    from musync.app import create_app

    token_path = tmp_path / "spotify_tokens.json"
    token_path.write_text(
        json.dumps(
            {
                "access_token": "test-access",
                "refresh_token": "test-refresh",
                "expires_at": 9999999999,
                "display_name": "Test User",
                "scope": "user-read-playback-state streaming",
            }
        ),
        encoding="utf-8",
    )
    backend = _backend(tmp_path, monkeypatch)
    monkeypatch.setenv("MUSYNC_SPOTIFY_CLIENT_ID", "cid")
    monkeypatch.setenv("MUSYNC_SPOTIFY_CLIENT_SECRET", "csecret")
    monkeypatch.setenv("MUSYNC_SPOTIFY_TOKEN_PATH", str(token_path))
    reset_streaming_service()
    client = TestClient(create_app(audio_backend=backend))

    search_resp = MagicMock()
    search_resp.status_code = 200
    search_resp.json.return_value = {
        "tracks": {
            "items": [
                {
                    "id": "abc",
                    "uri": "spotify:track:abc",
                    "name": "Shape of You",
                    "duration_ms": 1000,
                    "artists": [{"name": "Ed Sheeran"}],
                    "album": {"name": "÷", "images": []},
                }
            ]
        }
    }
    search_resp.text = "{}"
    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.__exit__.return_value = False
    mock_client.get.return_value = search_resp

    with patch("musync.streaming.spotify.httpx.Client", return_value=mock_client):
        res = client.get(
            "/api/streaming/search",
            params={"provider": "spotify", "q": "Shape Of You"},
        )

    assert res.status_code == 200
    assert res.json()["hits"][0]["title"] == "Shape of You"
    assert mock_client.get.call_args.kwargs["params"]["market"] == "US"


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
    scope = qs.get("scope", [""])[0]
    assert "user-read-private" in scope
    assert "streaming" in scope
    assert qs.get("show_dialog", [""])[0] == "true"


def test_spotify_callback_me_403_redirects_allowlist_error(tmp_path, monkeypatch) -> None:
    """Dev-mode allowlist: token OK, GET /me 403 → SPA error, never HTTP 500."""
    from unittest.mock import MagicMock, patch
    from urllib.parse import parse_qs, unquote, urlparse

    import httpx

    from musync.app import create_app
    from musync.streaming import get_streaming_service

    token_path = tmp_path / "spotify_tokens.json"
    backend = _backend(tmp_path, monkeypatch)
    monkeypatch.setenv("MUSYNC_SPOTIFY_CLIENT_ID", "cid")
    monkeypatch.setenv("MUSYNC_SPOTIFY_CLIENT_SECRET", "csecret")
    monkeypatch.setenv("MUSYNC_SPOTIFY_TOKEN_PATH", str(token_path))
    monkeypatch.setenv(
        "MUSYNC_SPOTIFY_REDIRECT_URI",
        "http://127.0.0.1:8081/api/streaming/spotify/callback",
    )
    reset_streaming_service()
    client = TestClient(create_app(audio_backend=backend))

    login = client.get("/api/streaming/spotify/login", follow_redirects=False)
    assert login.status_code in {302, 307}
    qs = parse_qs(urlparse(login.headers["location"]).query)
    state = qs["state"][0]
    assert qs["redirect_uri"][0] == (
        "http://127.0.0.1:8081/api/streaming/spotify/callback"
    )

    token_resp = MagicMock()
    token_resp.status_code = 200
    token_resp.json.return_value = {
        "access_token": "at",
        "refresh_token": "rt",
        "expires_in": 3600,
        "token_type": "Bearer",
    }
    token_resp.text = "{}"

    me_resp = MagicMock()
    me_resp.status_code = 403
    me_resp.json.return_value = {
        "error": {"status": 403, "message": "Forbidden"}
    }
    me_resp.text = '{"error":{"status":403,"message":"Forbidden"}}'
    me_req = MagicMock()

    def _raise_me() -> None:
        raise httpx.HTTPStatusError("403", request=me_req, response=me_resp)

    me_resp.raise_for_status.side_effect = _raise_me

    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.__exit__.return_value = False
    mock_client.post.return_value = token_resp
    mock_client.get.return_value = me_resp

    with patch("musync.streaming.spotify.httpx.Client", return_value=mock_client):
        res = client.get(
            "/api/streaming/spotify/callback",
            params={"code": "auth-code", "state": state},
            follow_redirects=False,
        )

    assert res.status_code in {302, 307}
    loc = res.headers.get("location") or ""
    assert "spotify=error" in loc
    detail = unquote(parse_qs(urlparse(loc).query).get("detail", [""])[0]).lower()
    assert "403" in detail or "forbidden" in detail
    assert "user management" in detail or "allowlist" in detail or "dashboard" in detail
    assert not token_path.exists()
    assert get_streaming_service().spotify.authenticated() is False


def test_spotify_callback_missing_secret_redirects(tmp_path, monkeypatch) -> None:
    from urllib.parse import parse_qs, unquote, urlparse

    from musync.app import create_app
    from musync.streaming import get_streaming_service

    backend = _backend(tmp_path, monkeypatch)
    monkeypatch.setenv("MUSYNC_SPOTIFY_CLIENT_ID", "cid")
    monkeypatch.setenv("MUSYNC_SPOTIFY_CLIENT_SECRET", "csecret")
    reset_streaming_service()
    client = TestClient(create_app(audio_backend=backend))
    login = client.get("/api/streaming/spotify/login", follow_redirects=False)
    state = parse_qs(urlparse(login.headers["location"]).query)["state"][0]

    # Drop secret after authorize (simulates misconfigured Windows env reload).
    monkeypatch.delenv("MUSYNC_SPOTIFY_CLIENT_SECRET", raising=False)
    res = client.get(
        "/api/streaming/spotify/callback",
        params={"code": "auth-code", "state": state},
        follow_redirects=False,
    )
    assert res.status_code in {302, 307}
    loc = res.headers.get("location") or ""
    assert "spotify=error" in loc
    detail = unquote(parse_qs(urlparse(loc).query).get("detail", [""])[0]).lower()
    assert "secret" in detail or "credential" in detail or "missing" in detail
    assert get_streaming_service().spotify.authenticated() is False


def test_spotify_callback_redirect_uri_mismatch_message(tmp_path, monkeypatch) -> None:
    from unittest.mock import MagicMock, patch
    from urllib.parse import parse_qs, unquote, urlparse

    from musync.app import create_app

    backend = _backend(tmp_path, monkeypatch)
    monkeypatch.setenv("MUSYNC_SPOTIFY_CLIENT_ID", "cid")
    monkeypatch.setenv("MUSYNC_SPOTIFY_CLIENT_SECRET", "csecret")
    monkeypatch.setenv("MUSYNC_SPOTIFY_TOKEN_PATH", str(tmp_path / "tok.json"))
    reset_streaming_service()
    client = TestClient(create_app(audio_backend=backend))
    login = client.get("/api/streaming/spotify/login", follow_redirects=False)
    state = parse_qs(urlparse(login.headers["location"]).query)["state"][0]

    token_resp = MagicMock()
    token_resp.status_code = 400
    token_resp.json.return_value = {
        "error": "invalid_grant",
        "error_description": "Invalid redirect URI",
    }
    token_resp.text = '{"error":"invalid_grant","error_description":"Invalid redirect URI"}'

    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.__exit__.return_value = False
    mock_client.post.return_value = token_resp

    with patch("musync.streaming.spotify.httpx.Client", return_value=mock_client):
        res = client.get(
            "/api/streaming/spotify/callback",
            params={"code": "auth-code", "state": state},
            follow_redirects=False,
        )
    assert res.status_code in {302, 307}
    detail = unquote(
        parse_qs(urlparse(res.headers["location"]).query).get("detail", [""])[0]
    ).lower()
    assert "redirect" in detail


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


def test_credentials_api_masks_secrets_and_persists(tmp_path, monkeypatch) -> None:
    """GET never returns raw secrets; PUT writes ~/musync.env and masks response."""
    from musync.app import create_app
    from musync.streaming.credentials import _MASK

    monkeypatch.setattr("musync.streaming.credentials.Path.home", lambda: tmp_path)
    monkeypatch.setattr(
        "musync.streaming.credentials.local_env_path",
        lambda: tmp_path / "missing.local.env",
    )
    monkeypatch.delenv("MUSYNC_SPOTIFY_CLIENT_ID", raising=False)
    monkeypatch.delenv("MUSYNC_SPOTIFY_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("MUSYNC_SPOTIFY_REDIRECT_URI", raising=False)
    monkeypatch.delenv("MUSYNC_YOUTUBE_API_KEY", raising=False)
    monkeypatch.delenv("MUSYNC_PORT", raising=False)
    reset_streaming_service()

    backend = _backend(tmp_path, monkeypatch)
    client = TestClient(create_app(audio_backend=backend))

    empty = client.get("/api/streaming/credentials")
    assert empty.status_code == 200
    body = empty.json()
    assert body["spotify_configured"] is False
    assert body["youtube_configured"] is False
    assert body["spotify_client_secret"]["set"] is False
    assert body["youtube_api_key"]["set"] is False
    # Pi default redirect prefill when unset (127.0.0.1:8080 — not localhost/.local)
    assert body["spotify_redirect_uri"]["set"] is False
    assert body["spotify_redirect_uri"]["value"] == (
        "http://127.0.0.1:8080/api/streaming/spotify/callback"
    )
    # Soft-fail empty PUT — no write, no crash
    soft = client.put("/api/streaming/credentials", json={})
    assert soft.status_code == 200
    assert soft.json()["saved"] is False
    assert not (tmp_path / "musync.env").exists()

    secret = "super-secret-value-xyz"
    yt_key = "AIzaSyTestYouTubeKey123456"
    put = client.put(
        "/api/streaming/credentials",
        json={
            "spotify_client_id": "my-client-id-abcd",
            "spotify_client_secret": secret,
            "spotify_redirect_uri": "http://127.0.0.1:8081/api/streaming/spotify/callback",
            "youtube_api_key": yt_key,
        },
    )
    assert put.status_code == 200
    saved = put.json()
    assert saved["saved"] is True
    assert saved["spotify_configured"] is True
    assert saved["youtube_configured"] is True
    raw = put.text
    assert secret not in raw
    assert yt_key not in raw
    assert saved["spotify_client_secret"]["set"] is True
    assert saved["spotify_client_secret"]["masked"] == _MASK
    assert saved["youtube_api_key"]["masked"] == _MASK
    assert "value" not in saved["spotify_client_secret"] or saved[
        "spotify_client_secret"
    ].get("value") in (None, "")
    assert saved["spotify_redirect_uri"]["value"].endswith("/callback")
    assert "abcd" in saved["spotify_client_id"]["masked"]

    env_file = tmp_path / "musync.env"
    assert env_file.is_file()
    disk = env_file.read_text(encoding="utf-8")
    assert "MUSYNC_SPOTIFY_CLIENT_SECRET=" in disk
    assert secret in disk  # on disk only — never in API
    assert yt_key in disk

    # Process env applied; status reflects configured
    status = client.get("/api/streaming")
    assert status.status_code == 200
    st = status.json()
    assert st["spotify"]["configured"] is True
    assert st["youtube"]["configured"] is True
    assert secret not in status.text
    assert yt_key not in status.text

    # Blank secret field omitted → keep existing
    keep = client.put(
        "/api/streaming/credentials",
        json={"spotify_client_id": "rotated-id-9999"},
    )
    assert keep.status_code == 200
    assert os.environ["MUSYNC_SPOTIFY_CLIENT_SECRET"] == secret
    assert "9999" in keep.json()["spotify_client_id"]["masked"]
    assert secret not in keep.text


def test_mask_secret_never_echoes_raw() -> None:
    from musync.streaming.credentials import mask_secret, _MASK

    assert mask_secret("") == ""
    assert mask_secret("abc", key="MUSYNC_SPOTIFY_CLIENT_SECRET") == _MASK
    assert mask_secret("abcdefghij", key="MUSYNC_SPOTIFY_CLIENT_ID").endswith("ghij")
    assert "abcdef" not in mask_secret("abcdefghij", key="MUSYNC_SPOTIFY_CLIENT_ID")


def test_now_playing_api_for_local(tmp_path, monkeypatch) -> None:
    from musync.app import create_app

    backend = _backend(tmp_path, monkeypatch)
    client = TestClient(create_app(audio_backend=backend))
    res = client.get("/api/now-playing")
    assert res.status_code == 200
    body = res.json()
    assert "title" in body
    assert body.get("source_type") in {None, "local", "spotify", "apple", "youtube", "input"}
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


def test_airplay_env_marks_apple_source_playable(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MUSYNC_AIRPLAY_SOURCE", "shairport-sync")
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
            locator="airplay:receive",
            title="AirPlay receive",
        )
    )
    assert item.available is True


def test_apple_music_search_rejected(tmp_path, monkeypatch) -> None:
    from musync.app import create_app

    backend = _backend(tmp_path, monkeypatch)
    client = TestClient(create_app(audio_backend=backend))
    res = client.get("/api/streaming/search", params={"provider": "apple", "q": "hello"})
    assert res.status_code == 400
    detail = res.json()["detail"].lower()
    assert "airplay" in detail or "removed" in detail or "spotify" in detail


def test_patch_to_spotify_hub_without_uri_stays_stub(tmp_path, monkeypatch) -> None:
    backend = _backend(tmp_path, monkeypatch)
    local = next(q for q in backend.list_queue() if q.source_type == SourceType.LOCAL)
    patched = backend.patch_queue_item(
        local.id, QueueItemPatch(source_type=SourceType.SPOTIFY)
    )
    assert patched.source_type == SourceType.SPOTIFY
    assert patched.available is False


def test_youtube_search_requires_api_key(tmp_path, monkeypatch) -> None:
    from musync.app import create_app

    backend = _backend(tmp_path, monkeypatch)
    client = TestClient(create_app(audio_backend=backend))
    res = client.get("/api/streaming/search", params={"provider": "youtube", "q": "lofi"})
    assert res.status_code == 400
    detail = res.json()["detail"].lower()
    assert "youtube" in detail and ("api" in detail or "key" in detail or "musync.env" in detail)


def test_youtube_queue_playable_with_mock(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MUSYNC_YOUTUBE_API_KEY", "test-key")
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

    assert get_streaming_service().youtube_ready() is True
    assert get_streaming_service().youtube_playable() is True

    item = backend.add_queue_item(
        QueueItemCreate(
            source_type=SourceType.YOUTUBE,
            locator="youtube:dQw4w9WgXcQ",
            title="Never Gonna Give You Up",
            artist="Rick Astley",
            duration_ms=213_000,
        )
    )
    assert item.available is True
    transport = backend.set_active_queue_item(item.id)
    playing = backend.set_transport(TransportPut(state=TransportState.PLAYING))
    assert playing.state == TransportState.PLAYING
    assert transport.source_type == SourceType.YOUTUBE


def test_youtube_hub_without_video_stays_stub(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MUSYNC_YOUTUBE_API_KEY", "test-key")
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
    item = backend.add_queue_item(
        QueueItemCreate(source_type=SourceType.YOUTUBE, title="Hub placeholder")
    )
    assert item.available is False
    assert item.locator == "youtube:hub"


def test_youtube_search_mocked_api(tmp_path, monkeypatch) -> None:
    from unittest.mock import MagicMock, patch

    from musync.app import create_app

    backend = _backend(tmp_path, monkeypatch)
    monkeypatch.setenv("MUSYNC_YOUTUBE_API_KEY", "test-key")
    reset_streaming_service()
    client = TestClient(create_app(audio_backend=backend))

    search_resp = MagicMock()
    search_resp.status_code = 200
    search_resp.raise_for_status = MagicMock()
    search_resp.json.return_value = {
        "items": [
            {
                "id": {"videoId": "dQw4w9WgXcQ"},
                "snippet": {
                    "title": "Never Gonna Give You Up",
                    "channelTitle": "Rick Astley",
                    "thumbnails": {
                        "medium": {"url": "https://i.ytimg.com/vi/dQw4w9WgXcQ/mqdefault.jpg"}
                    },
                },
            }
        ]
    }

    videos_resp = MagicMock()
    videos_resp.status_code = 200
    videos_resp.raise_for_status = MagicMock()
    videos_resp.json.return_value = {
        "items": [
            {
                "id": "dQw4w9WgXcQ",
                "contentDetails": {"duration": "PT3M33S"},
            }
        ]
    }

    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.__exit__.return_value = False

    def _get(url, params=None):  # noqa: ANN001
        if "search" in url:
            return search_resp
        return videos_resp

    mock_client.get.side_effect = _get

    with patch("musync.streaming.youtube.httpx.Client", return_value=mock_client):
        res = client.get(
            "/api/streaming/search", params={"provider": "youtube", "q": "rick"}
        )
    assert res.status_code == 200
    body = res.json()
    assert body["provider"] == "youtube"
    assert len(body["hits"]) == 1
    assert body["hits"][0]["uri"] == "youtube:dQw4w9WgXcQ"
    assert body["hits"][0]["duration_ms"] == 213_000

    queued = client.post(
        "/api/streaming/queue",
        json={
            "provider": "youtube",
            "uri": "youtube:dQw4w9WgXcQ",
            "title": "Never Gonna Give You Up",
            "artist": "Rick Astley",
            "activate": True,
        },
    )
    assert queued.status_code == 201
    qbody = queued.json()
    assert qbody["source_type"] == "youtube"
    assert qbody["locator"] == "youtube:dQw4w9WgXcQ"


def test_parse_youtube_locator_helpers() -> None:
    from musync.streaming.youtube import parse_iso8601_duration_ms, parse_video_id

    assert parse_video_id("youtube:dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert parse_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert parse_video_id("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert parse_video_id("https://www.youtube.com/shorts/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert parse_video_id("not-a-real-id") is None
    assert parse_video_id("short") is None
    assert parse_iso8601_duration_ms("PT1H2M3S") == ((1 * 3600) + (2 * 60) + 3) * 1000
    assert parse_iso8601_duration_ms("PT45S") == 45_000
