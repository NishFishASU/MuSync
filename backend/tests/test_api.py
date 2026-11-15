from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from musync.audio.mock import MockAudioBackend
from musync import app as app_module
from musync.models import Device


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("MUSYNC_DEVICES_CONFIG", str(tmp_path / "devices.yaml"))
    monkeypatch.setenv("MUSYNC_MEDIA_DIR", str(tmp_path / "media"))
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
    app_module.backend = backend
    return TestClient(app_module.app)


def test_health(client: TestClient) -> None:
    res = client.get("/api/health")
    assert res.status_code == 200
    body = res.json()
    assert body["backend"] == "mock"
    assert body["ok"] is True


def test_devices_patch(client: TestClient) -> None:
    res = client.get("/api/devices")
    assert res.status_code == 200
    assert res.json()[0]["id"] == "sink-1"

    patched = client.patch(
        "/api/devices/sink-1",
        json={"delay_ms": 90, "volume": 0.4, "mute": True},
    )
    assert patched.status_code == 200
    body = patched.json()
    assert body["delay_ms"] == 90
    assert body["volume"] == 0.4
    assert body["mute"] is True


def test_transport_and_sources(client: TestClient) -> None:
    sources = client.get("/api/sources")
    assert sources.status_code == 200
    local = next(s for s in sources.json() if s["available"])

    active = client.put("/api/sources/active", json={"id": local["id"]})
    assert active.status_code == 200

    playing = client.put("/api/transport", json={"state": "playing"})
    assert playing.status_code == 200
    assert playing.json()["state"] == "playing"

    stopped = client.put("/api/transport", json={"state": "stopped"})
    assert stopped.json()["state"] == "stopped"
    assert stopped.json()["position_ms"] == 0


def test_index_served(client: TestClient) -> None:
    res = client.get("/")
    assert res.status_code == 200, res.text
    assert "MuSync" in res.text
    # SvelteKit adapter-static emits hashed assets under /_app/
    assert "/_app/" in res.text or "sveltekit" in res.text.lower()


def test_static_asset_smoke(client: TestClient) -> None:
    """Minimal UI smoke: built SPA index + at least one JS chunk."""
    index = client.get("/")
    assert index.status_code == 200
    html = index.text
    # Grab first /_app/... reference if present
    marker = '/_app/'
    if marker not in html:
        pytest.skip("Built UI has no /_app/ assets (unexpected SPA shell)")
    start = html.index(marker)
    end = html.find('"', start)
    if end < 0:
        end = html.find("'", start)
    asset_path = html[start:end]
    asset = client.get(asset_path)
    assert asset.status_code == 200
    assert len(asset.content) > 0
