"""Hub SSE playback push (MDAS-71)."""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from musync import app as app_module
from musync.audio.mock import MockAudioBackend
from musync.hub_events import HubEvent, bus, format_sse, publish_playback
from musync.models import Device, TransportPut, TransportState


@pytest.fixture
def backend(tmp_path, monkeypatch) -> MockAudioBackend:
    monkeypatch.setenv("MUSYNC_DEVICES_CONFIG", str(tmp_path / "devices.yaml"))
    monkeypatch.setenv("MUSYNC_MEDIA_DIR", str(tmp_path / "media"))
    return MockAudioBackend(
        devices=[
            Device(
                id="sink-1",
                name="Qudelix",
                mac="11:22:33:44:55:66",
                hci="hci0",
                connected=True,
            )
        ]
    )


@pytest.fixture
def client(backend: MockAudioBackend) -> TestClient:
    app_module.backend = backend
    with TestClient(app_module.app) as c:
        yield c


def test_format_sse_playback_block() -> None:
    raw = format_sse(
        HubEvent(
            type="playback",
            data={"reason": "play", "transport": {"state": "playing"}},
        )
    )
    assert raw.startswith("event: playback\n")
    assert '"reason":"play"' in raw.replace(" ", "")


def test_publish_playback_reaches_subscriber(backend: MockAudioBackend) -> None:
    q = bus.subscribe()
    try:
        publish_playback(
            transport=backend.get_transport(),
            now_playing=backend.get_now_playing(),
            reason="test",
        )
        event = q.get(timeout=1.0)
        assert event.data["reason"] == "test"
        assert "transport" in event.data
        assert "now_playing" in event.data
    finally:
        bus.unsubscribe(q)


def test_sse_stream_initial_snapshot(client: TestClient) -> None:
    with client.stream("GET", "/api/events") as response:
        assert response.status_code == 200
        assert "text/event-stream" in response.headers.get("content-type", "")
        buf = ""
        for chunk in response.iter_text():
            buf += chunk
            if "\n\n" in buf:
                break
        assert "event: playback" in buf
        data_line = next(
            line for line in buf.splitlines() if line.startswith("data:")
        )
        payload = json.loads(data_line[len("data:") :].strip())
        assert payload["reason"] == "snapshot"
        assert "transport" in payload
        assert "now_playing" in payload


def test_play_publishes_to_bus_via_session_hook(
    client: TestClient, backend: MockAudioBackend
) -> None:
    """After lifespan wires the hook, set_transport publishes a playback event."""
    q = bus.subscribe()
    try:
        local = next(i for i in backend.list_queue() if i.available)
        backend.set_active_queue_item(local.id)
        time.sleep(0.2)
        while True:
            try:
                q.get_nowait()
            except Exception:
                break

        playing = backend.set_transport(TransportPut(state=TransportState.PLAYING))
        assert playing.state == TransportState.PLAYING

        deadline = time.monotonic() + 2.0
        got = None
        while time.monotonic() < deadline:
            try:
                event = q.get(timeout=0.1)
            except Exception:
                continue
            if event.type == "playback" and event.data.get("reason") == "play":
                got = event.data
                break
        assert got is not None
        assert got["transport"]["state"] == "playing"
        assert "now_playing" in got
    finally:
        bus.unsubscribe(q)
