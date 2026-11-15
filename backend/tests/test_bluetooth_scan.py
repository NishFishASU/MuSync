"""Bluetooth scan / connect / clear discovery API (mock backend)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from musync import app as app_module
from musync.audio.mock import MockAudioBackend
from musync.audio.pw_tools import (
    bluetoothctl_connect,
    bluetoothctl_pair,
    bluetoothctl_scan_off,
    bluetoothctl_scan_on,
    parse_bluetoothctl_devices,
)
from musync.models import Device, DeviceKind


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("MUSYNC_DEVICES_CONFIG", str(tmp_path / "devices.yaml"))
    monkeypatch.setenv("MUSYNC_MEDIA_DIR", str(tmp_path / "media"))
    backend = MockAudioBackend(
        devices=[
            Device(
                id="spp-1",
                name="Phone SPP",
                kind=DeviceKind.NETWORK_SPP,
                enabled=True,
                listen_port=1234,
            )
        ]
    )
    app_module.backend = backend
    return TestClient(app_module.app)


def test_scan_start_stop_and_clear(client: TestClient) -> None:
    start = client.post("/api/bluetooth/scan")
    assert start.status_code == 200, start.text
    body = start.json()
    assert body["scanning"] is True
    names = {b["name"] for b in body["bluetooth"]}
    assert "Qudelix-5K" in names
    assert "WH-1000XM5" in names  # scan-only fixture

    status = client.get("/api/bluetooth/scan")
    assert status.status_code == 200
    assert status.json()["scanning"] is True

    # Connect one scan-only device so clear keeps it.
    xm = next(b for b in body["bluetooth"] if b["name"] == "WH-1000XM5")
    connected = client.post(
        "/api/bluetooth/connect",
        json={"mac": xm["mac"], "name": xm["name"], "enable_as_output": True},
    )
    assert connected.status_code == 200, connected.text
    assert connected.json()["ok"] is True
    assert connected.json()["device"]["name"] == "WH-1000XM5"

    cleared = client.post("/api/bluetooth/discover/clear")
    assert cleared.status_code == 200
    kept = {b["name"] for b in cleared.json()["bluetooth"]}
    assert "WH-1000XM5" in kept  # connected
    assert "Soundcore Motion" not in kept  # not connected — cleared

    stop = client.delete("/api/bluetooth/scan")
    assert stop.status_code == 200
    assert stop.json()["scanning"] is False


def test_connect_enables_output_and_patch_delay_volume(client: TestClient) -> None:
    client.post("/api/bluetooth/scan")
    status = client.get("/api/bluetooth/scan").json()
    motion = next(b for b in status["bluetooth"] if b["name"] == "Soundcore Motion")
    res = client.post(
        "/api/bluetooth/connect",
        json={"mac": motion["mac"], "name": motion["name"], "enable_as_output": True},
    )
    assert res.status_code == 200
    device_id = res.json()["device"]["id"]

    patched = client.patch(
        f"/api/devices/{device_id}",
        json={"delay_ms": 75, "volume": 0.42},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["delay_ms"] == 75
    assert patched.json()["volume"] == 0.42


def test_bluetoothctl_helpers_without_binary() -> None:
    assert bluetoothctl_scan_on(None)[0] is False
    assert bluetoothctl_scan_off(None)[0] is False
    assert bluetoothctl_pair(None, "AA:BB:CC:DD:EE:01")[0] is False
    assert bluetoothctl_connect(None, "AA:BB:CC:DD:EE:01")[0] is False
    remotes = parse_bluetoothctl_devices(
        "Device AA:BB:CC:DD:EE:01 Demo\nDevice 11:22:33:44:55:66 Other\n"
    )
    assert [r.name for r in remotes] == ["Demo", "Other"]
