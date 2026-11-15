"""Hub UI mDNS advertise (musync.local) — not for Spotify OAuth redirects."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from musync import app as app_module
from musync.audio.mock import MockAudioBackend
from musync.mdns import (
    DEFAULT_HOSTNAME,
    mdns_fqdn,
    mdns_hostname_label,
    start_hub_mdns,
    stop_hub_mdns,
)
from musync.wifi.mock import MockWifiBackend


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("MUSYNC_DEVICES_CONFIG", str(tmp_path / "devices.yaml"))
    monkeypatch.setenv("MUSYNC_MEDIA_DIR", str(tmp_path / "media"))
    monkeypatch.setenv("MUSYNC_WIFI_BACKEND", "mock")
    monkeypatch.setenv("MUSYNC_MDNS", "0")  # avoid real sockets in API tests
    app_module.backend = MockAudioBackend()
    app_module.wifi_backend = MockWifiBackend()
    with TestClient(app_module.app) as c:
        yield c
    stop_hub_mdns()


def test_hostname_defaults_to_musync_not_mu_sync(monkeypatch) -> None:
    monkeypatch.delenv("MUSYNC_MDNS_HOSTNAME", raising=False)
    assert mdns_hostname_label() == DEFAULT_HOSTNAME
    assert mdns_fqdn() == "musync.local"
    assert "mu.sync" not in mdns_fqdn().lower()


def test_hostname_strips_dots_and_local_suffix(monkeypatch) -> None:
    monkeypatch.setenv("MUSYNC_MDNS_HOSTNAME", "Mu.Sync.local")
    # Dots → dashes / sanitized — never advertise Mu.Sync.local
    label = mdns_hostname_label()
    assert "." not in label
    assert label == "mu-sync"
    assert mdns_fqdn() == "mu-sync.local"


def test_mdns_disabled_soft_fail(monkeypatch) -> None:
    monkeypatch.setenv("MUSYNC_MDNS", "0")
    stop_hub_mdns()
    status = start_hub_mdns(port=8080)
    assert status.advertising is False
    assert status.hostname == "musync.local"
    assert status.ui_only is True
    assert "127.0.0.1" in status.as_dict()["spotify_note"]
    assert "musync.local" in status.as_dict()["spotify_note"]
    stop_hub_mdns()


def test_api_mdns_and_wifi_hint(client: TestClient) -> None:
    res = client.get("/api/mdns")
    assert res.status_code == 200
    body = res.json()
    assert body["hostname"] == "musync.local"
    assert body["ui_only"] is True
    assert "127.0.0.1" in body["spotify_note"]
    assert "localhost" in body["spotify_note"]

    wifi = client.get("/api/wifi")
    assert wifi.status_code == 200
    assert wifi.json().get("mdns_hostname") == "musync.local"


def test_default_redirect_uri_is_127_001_8080(monkeypatch) -> None:
    """Pi default: 127.0.0.1:8080 (laptop may override MUSYNC_PORT=8081)."""
    from musync.streaming.spotify import default_redirect_uri

    monkeypatch.delenv("MUSYNC_PORT", raising=False)
    monkeypatch.delenv("MUSYNC_SPOTIFY_REDIRECT_URI", raising=False)
    assert default_redirect_uri() == (
        "http://127.0.0.1:8080/api/streaming/spotify/callback"
    )
    monkeypatch.setenv("MUSYNC_PORT", "8081")
    assert default_redirect_uri() == (
        "http://127.0.0.1:8081/api/streaming/spotify/callback"
    )


def test_normalize_refuses_empty_port(monkeypatch) -> None:
    """Never emit http://host:/path — empty port → listen/8080."""
    from musync.streaming.spotify import (
        normalize_spotify_redirect_uri,
        redirect_uri_has_empty_port,
    )

    monkeypatch.delenv("MUSYNC_PORT", raising=False)
    bad = "http://127.0.0.1:/api/streaming/spotify/callback"
    assert redirect_uri_has_empty_port(bad) is True
    assert normalize_spotify_redirect_uri(bad) == (
        "http://127.0.0.1:8080/api/streaming/spotify/callback"
    )
    monkeypatch.setenv("MUSYNC_PORT", "")
    assert normalize_spotify_redirect_uri(None) == (
        "http://127.0.0.1:8080/api/streaming/spotify/callback"
    )
    # Missing port also gets an explicit listen port.
    assert normalize_spotify_redirect_uri(
        "http://192.168.1.5/api/streaming/spotify/callback"
    ) == ("http://192.168.1.5:8080/api/streaming/spotify/callback")
