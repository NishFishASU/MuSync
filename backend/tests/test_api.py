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
    body = sources.json()
    local = next(s for s in body if s["available"])
    assert {s["type"] for s in body} >= {"local", "spotify", "apple", "input"}

    active = client.put("/api/sources/active", json={"id": local["id"]})
    assert active.status_code == 200
    assert active.json()["source_type"] == "local"
    assert active.json()["queue_item_id"]

    playing = client.put("/api/transport", json={"state": "playing"})
    assert playing.status_code == 200
    assert playing.json()["state"] == "playing"

    stopped = client.put("/api/transport", json={"state": "stopped"})
    assert stopped.json()["state"] == "stopped"
    assert stopped.json()["position_ms"] == 0


def test_host_local_catalog_activates_as_stub_not_400(client: TestClient) -> None:
    """Regression: host:*:local used to 400 while other stubs returned 200."""
    sources = client.get("/api/sources")
    assert sources.status_code == 200
    host_local = next(
        s
        for s in sources.json()
        if s["type"] == "local" and not s["available"] and s.get("input_id")
    )
    active = client.put("/api/sources/active", json={"id": host_local["id"]})
    assert active.status_code == 200, active.text
    assert active.json()["source_type"] == "local"
    assert active.json()["input_id"] == host_local["input_id"]

    # Playable hub local still works after stub selection.
    hub = next(s for s in sources.json() if s["available"] and s["type"] == "local")
    restored = client.put("/api/sources/active", json={"id": hub["id"]})
    assert restored.status_code == 200
    playing = client.put("/api/transport", json={"state": "playing"})
    assert playing.status_code == 200
    assert playing.json()["state"] == "playing"


def test_queue_source_type_switch(client: TestClient) -> None:
    queue = client.get("/api/queue")
    assert queue.status_code == 200
    items = queue.json()
    assert items
    item_id = items[0]["id"]

    patched = client.patch(
        f"/api/queue/{item_id}",
        json={"source_type": "spotify"},
    )
    assert patched.status_code == 200
    assert patched.json()["source_type"] == "spotify"
    assert patched.json()["available"] is False

    hosts = client.get("/api/inputs")
    assert hosts.status_code == 200
    assert len(hosts.json()) >= 1
    live = next(h for h in hosts.json() if h.get("connected"))
    # Create an unbound waiting host (mock seeds BT+USB as live).
    waiting_res = client.post(
        "/api/inputs",
        json={
            "name": "Waiting phone",
            "kind": "phone",
            "capture_path": "bt_a2dp",
            "connected": False,
            "armed": False,
        },
    )
    assert waiting_res.status_code == 201
    waiting = waiting_res.json()
    assert waiting.get("connected") is False

    # Waiting (non-live) host cannot play.
    created = client.post(
        "/api/queue",
        json={
            "source_type": "input",
            "input_id": waiting["id"],
            "title": "USB-from-PC waiting",
        },
    )
    assert created.status_code == 201
    assert created.json()["available"] is False
    active = client.put("/api/queue/active", json={"id": created.json()["id"]})
    assert active.status_code == 200
    denied = client.put("/api/transport", json={"state": "playing"})
    assert denied.status_code == 400

    # Live S21 FE BT A2DP host can play (mock feeder clock).
    live_q = client.post(
        "/api/queue",
        json={
            "source_type": "input",
            "input_id": live["id"],
            "title": "S21 FE live",
        },
    )
    assert live_q.status_code == 201
    assert live_q.json()["available"] is True
    client.put("/api/queue/active", json={"id": live_q.json()["id"]})
    played = client.put("/api/transport", json={"state": "playing"})
    assert played.status_code == 200
    assert played.json()["state"] == "playing"

    # Devices still listed (sinks not torn down)
    devices = client.get("/api/devices")
    assert devices.status_code == 200
    assert len(devices.json()) >= 1


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


def test_media_file_served_for_video_preview(client: TestClient, tmp_path) -> None:
    media = tmp_path / "media"
    media.mkdir(parents=True, exist_ok=True)
    clip = media / "preview.mp4"
    clip.write_bytes(b"fake-video-bytes")
    ok = client.get("/api/media/preview.mp4")
    assert ok.status_code == 200
    assert ok.content == b"fake-video-bytes"
    missing = client.get("/api/media/nope.mp4")
    assert missing.status_code == 404
    escape = client.get("/api/media/../README.md")
    assert escape.status_code in {400, 404}
