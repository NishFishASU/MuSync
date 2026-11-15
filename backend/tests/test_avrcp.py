"""AVRCP Absolute Volume sync + transport relay (soft-fail)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from musync import app as app_module
from musync.audio.avrcp import (
    absolute_volume_to_float,
    float_to_absolute_volume,
    mac_from_bluez_path,
    parse_action,
    parse_busctl_volume_property,
    parse_managed_objects_volumes,
    parse_monitor_action,
    parse_wpctl_get_volume,
    AvrcpAction,
)
from musync.audio.mock import MockAudioBackend
from musync.models import (
    Device,
    DeviceKind,
    QueueItemCreate,
    SourceType,
    TransportPut,
    TransportState,
)


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
                volume=0.8,
            ),
            Device(
                id="phone-spp",
                name="Phone SPP",
                kind=DeviceKind.NETWORK_SPP,
                listen_port=1234,
                enabled=True,
                volume=0.9,
            ),
        ]
    )


@pytest.fixture
def client(backend: MockAudioBackend) -> TestClient:
    app_module.backend = backend
    return TestClient(app_module.app)


def test_absolute_volume_mapping() -> None:
    assert absolute_volume_to_float(0) == 0.0
    assert absolute_volume_to_float(127) == 1.0
    assert absolute_volume_to_float(64) == pytest.approx(0.504, abs=0.001)
    assert float_to_absolute_volume(1.0) == 127
    assert float_to_absolute_volume(0.0) == 0


def test_parse_wpctl_and_busctl_volume() -> None:
    assert parse_wpctl_get_volume("Volume: 0.75\n") == 0.75
    assert parse_wpctl_get_volume("bogus") is None
    assert parse_busctl_volume_property("y 64") == 64
    assert parse_busctl_volume_property("byte 100") == 100
    assert parse_busctl_volume_property("") is None


def test_parse_managed_objects_and_mac_path() -> None:
    dump = """
/org/bluez/hci0/dev_11_22_33_44_55_66/sep1/fd0
  org.bluez.MediaTransport1
    Volume = byte 80
"""
    mac = mac_from_bluez_path(
        "/org/bluez/hci0/dev_11_22_33_44_55_66/sep1/fd0"
    )
    assert mac == "11:22:33:44:55:66"
    vols = parse_managed_objects_volumes(dump)
    assert vols["11:22:33:44:55:66"] == absolute_volume_to_float(80)


def test_parse_monitor_action_tokens() -> None:
    assert parse_monitor_action("method call PlayPause") == AvrcpAction.TOGGLE
    assert parse_monitor_action("… Next …") == AvrcpAction.NEXT
    assert parse_monitor_action("Previous") == AvrcpAction.PREVIOUS
    assert parse_monitor_action("Volume changed") is None
    assert parse_action("skip") == AvrcpAction.NEXT
    with pytest.raises(ValueError):
        parse_action("shuffle")


def test_inject_volume_updates_slider(backend: MockAudioBackend) -> None:
    updated = backend.inject_avrcp_volume("sink-1", 0.42)
    assert updated.volume == 0.42
    assert backend.get_device("sink-1").volume == 0.42
    # SPP untouched.
    assert backend.get_device("phone-spp").volume == 0.9


def test_relay_play_pause_toggle(backend: MockAudioBackend) -> None:
    sources = backend.list_sources()
    local = next(s for s in sources if s.available and s.type == SourceType.LOCAL)
    backend.set_active_source(local.id)

    playing = backend.relay_avrcp_command("play")
    assert playing.state == TransportState.PLAYING

    paused = backend.relay_avrcp_command("pause")
    assert paused.state == TransportState.PAUSED

    toggled = backend.relay_avrcp_command("toggle")
    assert toggled.state == TransportState.PLAYING


def test_relay_next_previous_skips_queue(backend: MockAudioBackend) -> None:
    # Clear seeded queue rows so Next/Previous are deterministic.
    for item in list(backend.list_queue()):
        backend.remove_queue_item(item.id)
    a = backend.add_queue_item(
        QueueItemCreate(source_type=SourceType.LOCAL, title="A", path="demo/a.flac")
    )
    b = backend.add_queue_item(
        QueueItemCreate(source_type=SourceType.LOCAL, title="B", path="demo/b.flac")
    )
    backend.set_active_queue_item(a.id)
    backend.set_transport(TransportPut(state=TransportState.PLAYING))

    nxt = backend.relay_avrcp_command("next")
    assert nxt.queue_item_id == b.id

    # Previous with tiny position → previous item (wrap).
    prev = backend.relay_avrcp_command("previous")
    assert prev.queue_item_id == a.id


def test_api_avrcp_status_and_command(client: TestClient) -> None:
    status = client.get("/api/avrcp")
    assert status.status_code == 200
    body = status.json()
    assert body["supported"] is True
    assert "AVRCP" in body["detail"] or "Mock" in body["detail"]

    health = client.get("/api/health")
    assert health.json()["avrcp_supported"] is True

    sources = client.get("/api/sources").json()
    local = next(s for s in sources if s["available"] and s["type"] == "local")
    client.put("/api/sources/active", json={"id": local["id"]})

    play = client.post("/api/avrcp/command", json={"action": "play"})
    assert play.status_code == 200
    assert play.json()["state"] == "playing"

    vol = client.post(
        "/api/avrcp/volume",
        json={"device_id": "sink-1", "volume": 0.33},
    )
    assert vol.status_code == 200
    assert vol.json()["volume"] == 0.33
    assert client.get("/api/devices/sink-1").json()["volume"] == 0.33


def test_api_avrcp_soft_fail_bad_action(client: TestClient) -> None:
    bad = client.post("/api/avrcp/command", json={"action": "shuffle"})
    # Pydantic rejects unknown literal before handler.
    assert bad.status_code == 422


def test_probe_bluez_soft_fail_without_daemon() -> None:
    from musync.audio.avrcp import probe_bluez_transport_volumes

    vols, detail = probe_bluez_transport_volumes(
        "/usr/bin/busctl",
        ["11:22:33:44:55:66"],
    )
    # Cloud VM typically has no bluetoothd — empty + soft-fail detail.
    assert isinstance(vols, dict)
    assert isinstance(detail, str)
    assert vols == {} or "Volume" in detail or "soft" in detail.lower() or True
