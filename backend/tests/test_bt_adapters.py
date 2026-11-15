"""Multi-adapter Bluetooth helpers + soft-fail when capacity is 1."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from musync import app as app_module
from musync.audio.mock import MockAudioBackend
from musync.audio.pw_tools import (
    BT_CONNECT_SOFT_TIMEOUT_S,
    adapter_capacity_note,
    enrich_adapter_sysfs,
    parse_bluetoothctl_list,
    pick_free_adapter,
    BtAdapter,
)
from musync.models import BluetoothAdapter, Device, DeviceKind


def test_parse_bluetoothctl_list_onboard_and_usb() -> None:
    text = (
        "Controller AA:BB:CC:DD:EE:00 hci0 [default]\n"
        "Controller 11:22:33:44:55:66 hci1\n"
        "Controller DE:AD:BE:EF:00:01 hci2 Something\n"
    )
    adapters = parse_bluetoothctl_list(text)
    assert [a.hci for a in adapters] == ["hci0", "hci1", "hci2"]
    assert adapters[0].mac == "AA:BB:CC:DD:EE:00"
    assert adapters[1].mac == "11:22:33:44:55:66"


def test_pick_free_adapter_prefers_onboard_then_skips_dead() -> None:
    adapters = [
        BtAdapter(hci="hci0", mac="AA:00", healthy=True, onboard=True, bus="uart"),
        BtAdapter(
            hci="hci1",
            mac="BB:00",
            healthy=False,
            onboard=False,
            bus="usb",
            detail="timeout",
        ),
        BtAdapter(hci="hci2", mac="CC:00", healthy=True, onboard=False, bus="usb"),
    ]
    first = pick_free_adapter(adapters, used_hcis=set())
    assert first is not None and first.hci == "hci0"
    second = pick_free_adapter(adapters, used_hcis={"hci0"})
    assert second is not None and second.hci == "hci2"
    none = pick_free_adapter(adapters, used_hcis={"hci0", "hci2"})
    assert none is None
    note = adapter_capacity_note(adapters)
    assert "2 healthy" in note
    assert "BT600" not in note


def test_pick_free_adapter_honors_preferred_hci() -> None:
    adapters = [
        BtAdapter(hci="hci0", mac="AA:00", healthy=True, onboard=True, bus="uart"),
        BtAdapter(hci="hci1", mac="BB:00", healthy=True, onboard=False, bus="usb"),
        BtAdapter(hci="hci2", mac="CC:00", healthy=True, onboard=False, bus="usb"),
    ]
    picked = pick_free_adapter(adapters, used_hcis=set(), preferred_hci="hci2")
    assert picked is not None and picked.hci == "hci2"
    # Preferred already used → fall back to free onboard.
    fallback = pick_free_adapter(adapters, used_hcis={"hci2"}, preferred_hci="hci2")
    assert fallback is not None and fallback.hci == "hci0"


def test_connect_timeout_is_short_for_dead_dongles() -> None:
    assert BT_CONNECT_SOFT_TIMEOUT_S <= 5.0


def test_enrich_adapter_sysfs_noop_off_pi() -> None:
    """Off-Pi / no sysfs: enrich leaves bus empty and does not raise."""
    a = BtAdapter(hci="hci9", mac="DE:AD")
    enrich_adapter_sysfs(a)
    assert a.hci == "hci9"
    # Without /sys/class/bluetooth/hci9, bus stays empty (or uart/usb if present).
    assert a.bus in {"", "usb", "uart"}


def test_capacity_note_is_chip_agnostic() -> None:
    zero = adapter_capacity_note([])
    assert "USB HCI" in zero
    assert "BT600" not in zero
    one = adapter_capacity_note(
        [BtAdapter(hci="hci0", healthy=True, onboard=True, bus="uart")]
    )
    assert "one A2DP" in one
    assert "USB HCI" in one
    assert "BT600" not in one


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
    # Only onboard healthy — soft-fail second A2DP enable.
    backend._bt_adapters = [
        BluetoothAdapter(
            hci="hci0",
            mac="00:11:22:33:44:00",
            name="Pi onboard",
            powered=True,
            healthy=True,
            onboard=True,
            bus="uart",
        ),
        BluetoothAdapter(
            hci="hci1",
            mac="00:11:22:33:44:01",
            name="USB HCI dead",
            powered=False,
            healthy=False,
            onboard=False,
            bus="usb",
            detail="probe timeout",
        ),
    ]
    app_module.backend = backend
    return TestClient(app_module.app)


def test_scan_reports_adapters_and_soft_warn(client: TestClient) -> None:
    res = client.post("/api/bluetooth/scan")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["adapter_capacity"] == 1
    assert any(a["hci"] == "hci0" and a["onboard"] for a in body["adapters"])
    assert any(a["hci"] == "hci1" and a["healthy"] is False for a in body["adapters"])
    assert any(a["hci"] == "hci1" and a.get("bus") == "usb" for a in body["adapters"])
    assert "Only 1 healthy adapter" in body["soft_warn"]
    assert "BT600" not in body["soft_warn"]


def test_second_output_soft_fails_with_one_adapter(client: TestClient) -> None:
    client.post("/api/bluetooth/scan")
    first = client.post(
        "/api/bluetooth/connect",
        json={
            "mac": "AA:BB:CC:DD:EE:55",
            "name": "WH-1000XM5",
            "enable_as_output": True,
        },
    )
    assert first.status_code == 200, first.text
    assert first.json()["ok"] is True
    assert first.json()["device"] is not None
    assert first.json()["device"]["hci"] == "hci0"

    second = client.post(
        "/api/bluetooth/connect",
        json={
            "mac": "AA:BB:CC:DD:EE:66",
            "name": "Soundcore Motion",
            "enable_as_output": True,
        },
    )
    assert second.status_code == 200, second.text
    body = second.json()
    assert body["ok"] is True  # BlueZ connect still "ok" in mock
    assert body["device"] is None  # enable soft-failed
    assert "soft_warn" in body and body["soft_warn"]
    devices = client.get("/api/devices").json()
    bt = [d for d in devices if d["kind"] == "bluetooth"]
    assert len(bt) == 1


def test_preferred_hci_assignable_when_healthy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
    backend._bt_adapters = [
        BluetoothAdapter(
            hci="hci0",
            mac="00:11:22:33:44:00",
            name="Pi onboard",
            powered=True,
            healthy=True,
            onboard=True,
            bus="uart",
        ),
        BluetoothAdapter(
            hci="hci1",
            mac="00:11:22:33:44:01",
            name="USB HCI",
            powered=True,
            healthy=True,
            onboard=False,
            bus="usb",
            usb_id="0b05:1d70",
        ),
    ]
    app_module.backend = backend
    client = TestClient(app_module.app)

    res = client.post(
        "/api/bluetooth/connect",
        json={
            "mac": "AA:BB:CC:DD:EE:77",
            "name": "Qudelix-5K",
            "hci": "hci1",
            "enable_as_output": True,
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ok"] is True
    assert body["device"] is not None
    assert body["device"]["hci"] == "hci1"
    # Onboard remains free for a second sink.
    second = client.post(
        "/api/bluetooth/connect",
        json={
            "mac": "AA:BB:CC:DD:EE:88",
            "name": "Speaker 2",
            "enable_as_output": True,
        },
    )
    assert second.status_code == 200, second.text
    assert second.json()["device"]["hci"] == "hci0"


def test_health_includes_adapter_counts(client: TestClient) -> None:
    health = client.get("/api/health").json()
    assert health["adapter_count"] == 2
    assert health["healthy_adapter_count"] == 1
    assert len(health["adapters"]) == 2
