"""Phone/PC input hosts (≤4) + per-queue active input binding."""

from __future__ import annotations

from fastapi.testclient import TestClient

from musync import app as app_module
from musync.audio.mock import MockAudioBackend
from musync.models import (
    CapturePath,
    Device,
    HostKind,
    InputHostCreate,
    InputHostPatch,
    QueueItemCreate,
    QueueItemPatch,
    SourceType,
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
        CapturePath.NETWORK,
    }

    # Fill to 4
    while len(backend.list_inputs()) < 4:
        n = len(backend.list_inputs()) + 1
        backend.add_input(
            InputHostCreate(name=f"Host {n}", kind=HostKind.PHONE, capture_path=CapturePath.BT_A2DP)
        )
    assert len(backend.list_inputs()) == 4
    try:
        backend.add_input(InputHostCreate(name="Overflow"))
        raised = False
    except ValueError:
        raised = True
    assert raised


def test_queue_binds_active_input(tmp_path, monkeypatch) -> None:
    backend = _backend(tmp_path, monkeypatch)
    host = backend.list_inputs()[0]
    item = backend.add_queue_item(
        QueueItemCreate(source_type=SourceType.INPUT, input_id=host.id, title="Live from phone")
    )
    assert item.input_id == host.id
    assert item.source_type == SourceType.INPUT
    assert item.available is False
    assert host.capture_path.value in (item.locator or "")

    other = backend.add_input(
        InputHostCreate(name="Android", kind=HostKind.PHONE, capture_path=CapturePath.BT_A2DP)
    )
    patched = backend.patch_queue_item(item.id, QueueItemPatch(input_id=other.id))
    assert patched.input_id == other.id
    assert "bt_a2dp" in (patched.locator or "")

    transport = backend.set_active_queue_item(item.id)
    assert transport.input_id == other.id
    assert transport.source_type == SourceType.INPUT


def test_per_host_catalog_contributions(tmp_path, monkeypatch) -> None:
    backend = _backend(tmp_path, monkeypatch)
    host = backend.list_inputs()[0]
    sources = backend.list_sources()
    host_rows = [s for s in sources if s.input_id == host.id]
    types = {s.type for s in host_rows}
    assert SourceType.LOCAL in types
    assert SourceType.SPOTIFY in types
    assert SourceType.APPLE in types
    assert SourceType.INPUT in types
    assert all(not s.available for s in host_rows)


def test_api_inputs_and_queue_binding(tmp_path, monkeypatch) -> None:
    backend = _backend(tmp_path, monkeypatch)
    app_module.backend = backend
    client = TestClient(app_module.app)

    listed = client.get("/api/inputs")
    assert listed.status_code == 200
    assert len(listed.json()) >= 1

    created = client.post(
        "/api/inputs",
        json={
            "name": "Work PC",
            "kind": "pc",
            "capture_path": "usb",
            "armed": True,
        },
    )
    assert created.status_code == 201
    host_id = created.json()["id"]
    assert created.json()["capture_path"] == "usb"

    patched = client.patch(f"/api/inputs/{host_id}", json={"capture_path": "network"})
    assert patched.status_code == 200
    assert patched.json()["capture_path"] == "network"

    queued = client.post(
        "/api/queue",
        json={"source_type": "input", "input_id": host_id, "title": "PC line-in"},
    )
    assert queued.status_code == 201
    assert queued.json()["input_id"] == host_id

    active = client.put("/api/queue/active", json={"id": queued.json()["id"]})
    assert active.status_code == 200
    assert active.json()["input_id"] == host_id

    deleted = client.delete(f"/api/inputs/{host_id}")
    assert deleted.status_code == 204
    assert all(h["id"] != host_id for h in client.get("/api/inputs").json())
