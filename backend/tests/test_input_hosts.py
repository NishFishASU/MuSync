"""Phone/PC input hosts (≤4) + per-queue active input binding + live capture."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from musync import app as app_module
from musync.audio.mock import MockAudioBackend
from musync.audio.pw_tools import parse_wpctl_sources
from musync.models import (
    CapturePath,
    Device,
    HostKind,
    InputHostCreate,
    InputHostPatch,
    QueueItemCreate,
    QueueItemPatch,
    SourceType,
    TransportPut,
    TransportState,
)


def _backend(tmp_path, monkeypatch) -> MockAudioBackend:
    monkeypatch.setenv("MUSYNC_DEVICES_CONFIG", str(tmp_path / "devices.yaml"))
    monkeypatch.setenv("MUSYNC_MEDIA_DIR", str(tmp_path / "media"))
    return MockAudioBackend(
        devices=[
            Device(id="sink-1", name="Living Room", mac="AA:BB:CC:DD:EE:01", hci="hci0", connected=True)
        ]
    )


def test_demo_hosts_and_max_four(tmp_path, monkeypatch) -> None:
    backend = _backend(tmp_path, monkeypatch)
    hosts = backend.list_inputs()
    assert 1 <= len(hosts) <= 4
    assert {h.capture_path for h in hosts} <= {
        CapturePath.BT_A2DP,
        CapturePath.USB,
        CapturePath.HDMI,
        CapturePath.AIRPLAY,
        CapturePath.NETWORK,
    }
    # Mock seeds S21 FE + USB + HDMI + AirPlay as live capture hosts (at max 4).
    s21 = next(h for h in hosts if "S21" in h.name)
    assert s21.capture_path == CapturePath.BT_A2DP
    assert s21.connected is True
    assert s21.endpoint
    usb = next(h for h in hosts if h.capture_path == CapturePath.USB)
    assert usb.connected is True
    assert usb.endpoint and "alsa_input" in usb.endpoint
    hdmi = next(h for h in hosts if h.capture_path == CapturePath.HDMI)
    assert hdmi.connected is True
    assert hdmi.endpoint and ("hdmi" in hdmi.endpoint.lower() or "alsa_input" in hdmi.endpoint)
    airplay = next(h for h in hosts if h.capture_path == CapturePath.AIRPLAY)
    assert airplay.connected is True
    assert airplay.endpoint and "shairport" in airplay.endpoint.lower()

    assert len(backend.list_inputs()) == 4
    try:
        backend.add_input(InputHostCreate(name="Overflow"))
        raised = False
    except ValueError:
        raised = True
    assert raised


def test_queue_binds_active_live_input(tmp_path, monkeypatch) -> None:
    backend = _backend(tmp_path, monkeypatch)
    host = next(h for h in backend.list_inputs() if h.connected)
    item = backend.add_queue_item(
        QueueItemCreate(source_type=SourceType.INPUT, input_id=host.id, title="Live from phone")
    )
    assert item.input_id == host.id
    assert item.source_type == SourceType.INPUT
    assert item.available is True
    assert host.capture_path.value in (item.locator or "")

    # Mock seeds 4 live hosts (at max) — free a slot before adding a waiting host.
    spare = next(
        h for h in backend.list_inputs() if h.capture_path == CapturePath.HDMI
    )
    backend.remove_input(spare.id)
    other = backend.add_input(
        InputHostCreate(
            name="Android waiting",
            kind=HostKind.PHONE,
            capture_path=CapturePath.BT_A2DP,
            connected=False,
        )
    )
    patched = backend.patch_queue_item(item.id, QueueItemPatch(input_id=other.id))
    assert patched.input_id == other.id
    assert "bt_a2dp" in (patched.locator or "")
    assert patched.available is False

    # Re-bind to live host and play.
    live = backend.patch_queue_item(item.id, QueueItemPatch(input_id=host.id))
    assert live.available is True
    transport = backend.set_active_queue_item(item.id)
    assert transport.input_id == host.id
    assert transport.source_type == SourceType.INPUT
    playing = backend.set_transport(TransportPut(state=TransportState.PLAYING))
    assert playing.state == TransportState.PLAYING


def test_per_host_catalog_live_only(tmp_path, monkeypatch) -> None:
    """Hosts contribute live input rows only — no fake on-device Spotify/Apple stubs."""
    backend = _backend(tmp_path, monkeypatch)
    host = next(h for h in backend.list_inputs() if h.connected)
    sources = backend.list_sources()
    host_rows = [s for s in sources if s.input_id == host.id]
    assert host_rows
    assert all(s.type == SourceType.INPUT for s in host_rows)
    live = next(s for s in host_rows if s.type == SourceType.INPUT)
    assert live.available is True


def test_api_inputs_discover_and_upload(tmp_path, monkeypatch) -> None:
    backend = _backend(tmp_path, monkeypatch)
    app_module.backend = backend
    client = TestClient(app_module.app)

    listed = client.get("/api/inputs")
    assert listed.status_code == 200
    assert len(listed.json()) >= 1

    discovered = client.get("/api/inputs/discover")
    assert discovered.status_code == 200
    body = discovered.json()
    assert any("S21" in s["name"] or "bluez_input" in s.get("pw_name", "") for s in body["sources"])
    assert any(
        s.get("capture_path") == "usb" or "alsa_input" in s.get("pw_name", "")
        for s in body["sources"]
    )
    assert any(s.get("capture_path") == "hdmi" for s in body["sources"])
    # USB / HDMI fixtures are auto-bound onto hosts (configured_id set).
    usb_row = next(s for s in body["sources"] if s.get("capture_path") == "usb")
    assert usb_row.get("configured_id")
    hdmi_row = next(s for s in body["sources"] if s.get("capture_path") == "hdmi")
    assert hdmi_row.get("configured_id")

    # Free a slot (mock seeds 3 live hosts) before registering a network host.
    spare = next(h for h in listed.json() if h["capture_path"] == "hdmi")
    assert client.delete(f"/api/inputs/{spare['id']}").status_code == 204

    created = client.post(
        "/api/inputs",
        json={
            "name": "Work PC",
            "kind": "pc",
            "capture_path": "network",
            "armed": True,
        },
    )
    assert created.status_code == 201
    host_id = created.json()["id"]
    assert created.json()["capture_path"] == "network"

    # Upload stub → connected + playable into feeder path.
    wav = tmp_path / "tone.wav"
    # Minimal RIFF header + silence is enough for path existence; mpv not required in mock.
    wav.write_bytes(b"RIFF" + b"\x00" * 36 + b"data" + b"\x00" * 8)
    uploaded = client.post(
        f"/api/inputs/{host_id}/upload",
        files={"file": ("tone.wav", wav.read_bytes(), "audio/wav")},
    )
    assert uploaded.status_code == 200
    assert uploaded.json()["connected"] is True
    assert uploaded.json()["endpoint"]
    assert Path(uploaded.json()["endpoint"]).is_file()

    queued = client.post(
        "/api/queue",
        json={"source_type": "input", "input_id": host_id, "title": "PC upload"},
    )
    assert queued.status_code == 201
    assert queued.json()["available"] is True

    active = client.put("/api/queue/active", json={"id": queued.json()["id"]})
    assert active.status_code == 200
    assert active.json()["input_id"] == host_id

    play = client.put("/api/transport", json={"state": "playing"})
    assert play.status_code == 200
    assert play.json()["state"] == "playing"

    deleted = client.delete(f"/api/inputs/{host_id}")
    assert deleted.status_code == 204
    assert all(h["id"] != host_id for h in client.get("/api/inputs").json())


def test_parse_wpctl_sources_bluez_input() -> None:
    sample = """
Audio
 ├─ Sinks:
 │      48. bluez_output.AA_BB_CC_DD_EE_01.1
 ├─ Sources:
 │      61. bluez_input.AA_BB_CC_DD_EE_21 [vol: 1.00]
 │      62. alsa_input.usb-Generic_USB_Audio-00.analog-stereo
 ├─ Filters:
"""
    sources = parse_wpctl_sources(sample)
    assert len(sources) >= 2
    assert sources[0].id == "61"
    assert "bluez_input" in sources[0].name
    assert sources[0].mac == "AA:BB:CC:DD:EE:21"


def test_mock_usb_live_playable(tmp_path, monkeypatch) -> None:
    backend = _backend(tmp_path, monkeypatch)
    usb = next(h for h in backend.list_inputs() if h.capture_path == CapturePath.USB)
    item = backend.add_queue_item(
        QueueItemCreate(source_type=SourceType.INPUT, input_id=usb.id, title="PC USB live")
    )
    assert item.available is True
    transport = backend.set_active_queue_item(item.id)
    assert transport.input_id == usb.id
    playing = backend.set_transport(TransportPut(state=TransportState.PLAYING))
    assert playing.state == TransportState.PLAYING


def test_usb_auto_bind_on_discover_when_waiting(tmp_path, monkeypatch) -> None:
    backend = _backend(tmp_path, monkeypatch)
    # Force USB host back to waiting, then discover should re-bind the fixture.
    usb = next(h for h in backend.list_inputs() if h.capture_path == CapturePath.USB)
    backend.patch_input(
        usb.id,
        InputHostPatch(connected=False, endpoint=None, armed=False, name="Laptop (USB)"),
    )
    disc = backend.discover_capture_sources()
    assert "auto-bound" in disc.detail
    host = next(h for h in backend.list_inputs() if h.id == usb.id)
    assert host.connected is True
    assert host.endpoint and "alsa_input" in host.endpoint
    assert host.kind == HostKind.PC


def test_mock_hdmi_live_playable(tmp_path, monkeypatch) -> None:
    backend = _backend(tmp_path, monkeypatch)
    hdmi = next(h for h in backend.list_inputs() if h.capture_path == CapturePath.HDMI)
    item = backend.add_queue_item(
        QueueItemCreate(source_type=SourceType.INPUT, input_id=hdmi.id, title="HDMI live")
    )
    assert item.available is True
    transport = backend.set_active_queue_item(item.id)
    assert transport.input_id == hdmi.id
    playing = backend.set_transport(TransportPut(state=TransportState.PLAYING))
    assert playing.state == TransportState.PLAYING


def test_hdmi_auto_bind_on_discover_when_waiting(tmp_path, monkeypatch) -> None:
    backend = _backend(tmp_path, monkeypatch)
    hdmi = next(h for h in backend.list_inputs() if h.capture_path == CapturePath.HDMI)
    backend.patch_input(
        hdmi.id,
        InputHostPatch(
            connected=False, endpoint=None, armed=False, name="HDMI Capture"
        ),
    )
    disc = backend.discover_capture_sources()
    assert "auto-bound" in disc.detail
    host = next(h for h in backend.list_inputs() if h.id == hdmi.id)
    assert host.connected is True
    assert host.endpoint and "alsa_input" in host.endpoint
    assert host.capture_path == CapturePath.HDMI
    assert host.kind == HostKind.OTHER
