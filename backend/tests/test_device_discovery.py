from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from musync import app as app_module
from musync.audio.mock import MockAudioBackend
from musync.audio.pw_tools import (
    PwNode,
    friendly_bluez_label,
    match_device_node,
    parse_bluetoothctl_devices,
)
from musync.audio.pw_tools import BtRemote
from musync.config import (
    default_devices,
    load_devices,
    load_devices_result,
    resolve_devices_from_config,
    save_devices,
)
from musync.models import Device, DeviceConfigEntry, DeviceKind


def test_parse_bluetoothctl_devices() -> None:
    text = """
Device AA:BB:CC:DD:EE:01 Qudelix-5K
Device 11:22:33:44:55:66 Kitchen Speaker
Junk line
"""
    remotes = parse_bluetoothctl_devices(text)
    assert len(remotes) == 2
    assert remotes[0].name == "Qudelix-5K"
    assert remotes[0].mac == "AA:BB:CC:DD:EE:01"


def test_match_device_node_by_name() -> None:
    nodes = [
        PwNode(
            id="48",
            name="bluez_output.AA_BB_CC_DD_EE_01.1",
            description="Qudelix-5K",
            mac="AA:BB:CC:DD:EE:01",
        )
    ]
    hit = match_device_node(mac="", hci="", pw_node_override="", nodes=nodes, name="Qudelix-5K")
    assert hit is not None
    assert hit.id == "48"


def test_friendly_bluez_label_prefers_remote_name() -> None:
    node = PwNode(id="1", name="bluez_output.AA_BB_CC_DD_EE_01.1", mac="AA:BB:CC:DD:EE:01")
    remotes = {"AA:BB:CC:DD:EE:01": BtRemote(mac="AA:BB:CC:DD:EE:01", name="Qudelix-5K")}
    assert friendly_bluez_label(node, remotes) == "Qudelix-5K"


def test_load_missing_and_broken_yaml_tolerated(monkeypatch, tmp_path: Path) -> None:
    missing = tmp_path / "nope.yaml"
    monkeypatch.setenv("MUSYNC_DEVICES_CONFIG", str(missing))
    devices, ok, detail = load_devices_result(backend="pipewire")
    assert ok is True
    assert any(d.kind == DeviceKind.NETWORK_SPP for d in devices)
    assert "defaults" in detail

    broken = tmp_path / "broken.yaml"
    broken.write_text("devices: {not: a list}\n", encoding="utf-8")
    monkeypatch.setenv("MUSYNC_DEVICES_CONFIG", str(broken))
    devices, ok, detail = load_devices_result(backend="pipewire")
    assert ok is False
    assert any(d.kind == DeviceKind.NETWORK_SPP for d in devices)
    # Must not raise:
    assert load_devices(backend="pipewire")


def test_atomic_save_devices(monkeypatch, tmp_path: Path) -> None:
    path = tmp_path / "devices.yaml"
    monkeypatch.setenv("MUSYNC_DEVICES_CONFIG", str(path))
    devices = default_devices(backend="pipewire")
    save_devices(devices)
    assert path.is_file()
    assert not path.with_suffix(".yaml.tmp").exists()
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert any(d["kind"] == "network_spp" for d in raw["devices"])


def test_resolve_fills_mac_from_discovery_by_name() -> None:
    entries = [
        DeviceConfigEntry(name="Qudelix-5K", kind=DeviceKind.BLUETOOTH, enabled=True),
        DeviceConfigEntry(
            name="Phone SPP",
            kind=DeviceKind.NETWORK_SPP,
            enabled=True,
            listen_port=1234,
            sample_rate=48000,
            channels=1,
        ),
    ]
    resolved = resolve_devices_from_config(
        entries,
        discovery_by_name={
            "qudelix-5k": {
                "mac": "A0:B1:C2:D3:E4:F5",
                "pw_node": "48",
                "hci": "hci0",
                "connected": True,
            }
        },
    )
    bt = next(d for d in resolved if d.kind == DeviceKind.BLUETOOTH)
    assert bt.mac == "A0:B1:C2:D3:E4:F5"
    assert bt.pw_node == "48"
    assert bt.name == "Qudelix-5K"
    assert any(d.kind == DeviceKind.NETWORK_SPP for d in resolved)


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
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


def test_discover_and_save_config_api(client: TestClient, tmp_path: Path) -> None:
    disc = client.get("/api/devices/discover")
    assert disc.status_code == 200
    body = disc.json()
    assert body["spp_default"]["kind"] == "network_spp"
    assert len(body["bluetooth"]) >= 1
    names = {b["name"] for b in body["bluetooth"]}
    assert "Qudelix-5K" in names

    qudelix = next(b for b in body["bluetooth"] if b["name"] == "Qudelix-5K")
    saved = client.put(
        "/api/devices/config",
        json={
            "devices": [
                {
                    "name": qudelix["name"],
                    "kind": "bluetooth",
                    "enabled": True,
                    "pw_node": qudelix.get("pw_node"),
                },
                {
                    "id": "spp-1",
                    "name": "Phone SPP",
                    "kind": "network_spp",
                    "enabled": True,
                    "listen_host": "0.0.0.0",
                    "listen_port": 1235,
                    "sample_rate": 48000,
                    "channels": 1,
                },
            ]
        },
    )
    assert saved.status_code == 200, saved.text
    devices = saved.json()
    bt = next(d for d in devices if d["kind"] == "bluetooth")
    spp = next(d for d in devices if d["kind"] == "network_spp")
    assert bt["name"] == "Qudelix-5K"
    assert bt["mac"]  # filled from mock discovery — not typed by client
    assert spp["listen_port"] == 1235

    cfg = tmp_path / "devices.yaml"
    assert cfg.is_file()
    raw = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    assert any(d["name"] == "Qudelix-5K" for d in raw["devices"])
