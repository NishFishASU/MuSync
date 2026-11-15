"""Live capture auto-queue + AVRCP adopt (MDAS-69)."""

from __future__ import annotations

from musync.audio.mock import MockAudioBackend
from musync.audio.turn_taking import eligible_detail, eligible_turn_devices
from musync.models import (
    Device,
    DeviceKind,
    QueueItemCreate,
    SourceType,
    TransportPut,
    TransportState,
)


def _backend(tmp_path, monkeypatch) -> MockAudioBackend:
    monkeypatch.setenv("MUSYNC_DEVICES_CONFIG", str(tmp_path / "devices.yaml"))
    monkeypatch.setenv("MUSYNC_MEDIA_DIR", str(tmp_path / "media"))
    return MockAudioBackend(
        devices=[
            Device(
                id="sink-1",
                name="Qudelix",
                mac="AA:BB:CC:DD:EE:01",
                hci="hci0",
                connected=True,
            ),
            Device(
                id="phone-spp",
                name="Phone SPP",
                kind=DeviceKind.NETWORK_SPP,
                listen_port=1234,
                enabled=True,
                connected=True,
            ),
        ]
    )


def test_live_sessions_seeded_on_mock_boot(tmp_path, monkeypatch) -> None:
    backend = _backend(tmp_path, monkeypatch)
    live_rows = [
        q
        for q in backend.list_queue()
        if q.source_type == SourceType.INPUT and q.title.startswith("Live ·")
    ]
    assert live_rows
    assert all(q.available for q in live_rows)
    # Local demo stays active at boot (no force-activate).
    transport = backend.get_transport()
    assert transport.source_type != SourceType.INPUT or transport.state == TransportState.STOPPED


def test_discover_force_activates_live_when_stopped(tmp_path, monkeypatch) -> None:
    backend = _backend(tmp_path, monkeypatch)
    q = backend.list_queue()
    local = next(i for i in q if i.source_type == SourceType.LOCAL and i.available)
    backend.set_active_queue_item(local.id)
    backend.set_transport(TransportPut(state=TransportState.STOPPED))
    assert backend.get_transport().source_type == SourceType.LOCAL
    # Discover force-activates live so pass-through becomes a hub cue.
    backend.discover_capture_sources()
    transport = backend.get_transport()
    assert transport.source_type == SourceType.INPUT
    assert transport.state == TransportState.PLAYING
    assert transport.input_id
    assert any(
        i.id == transport.queue_item_id and i.title.startswith("Live ·")
        for i in backend.list_queue()
    )


def test_avrcp_play_adopts_live_when_cue_unavailable(tmp_path, monkeypatch) -> None:
    backend = _backend(tmp_path, monkeypatch)
    # Spotify stub without URI → unavailable.
    spot = backend.add_queue_item(
        QueueItemCreate(source_type=SourceType.SPOTIFY, title="No URI")
    )
    backend.set_active_queue_item(spot.id)
    assert spot.available is False or not backend.list_queue()[
        next(i for i, x in enumerate(backend.list_queue()) if x.id == spot.id)
    ].available

    playing = backend.relay_avrcp_command("play", device_id="sink-1")
    assert playing.state == TransportState.PLAYING
    assert playing.source_type == SourceType.INPUT
    paused = backend.relay_avrcp_command("pause")
    assert paused.state == TransportState.PAUSED
    resumed = backend.relay_avrcp_command("play", device_id="sink-1")
    assert resumed.state == TransportState.PLAYING
    assert resumed.source_type == SourceType.INPUT


def test_queue_settings_eligible_detail(tmp_path, monkeypatch) -> None:
    backend = _backend(tmp_path, monkeypatch)
    settings = backend.get_queue_settings()
    assert settings.turn_order
    assert settings.turn_order_labels
    assert "Qudelix" in settings.turn_order_labels or "Ready" in settings.eligible_detail
    assert settings.eligible_detail.startswith("Ready:")

    devices = [
        Device(id="bt", name="Buds", mac="11:22:33:44:55:66", connected=False, enabled=True),
    ]
    eligible = eligible_turn_devices(devices)
    assert eligible == []
    detail = eligible_detail(devices, eligible)
    assert "not connected" in detail.lower() or "No ready" in detail
