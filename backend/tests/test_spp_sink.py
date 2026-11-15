"""SPP TCP network output sink — models, mock device, ffmpeg command shape."""

from __future__ import annotations

from fastapi.testclient import TestClient

from musync import app as app_module
from musync.audio.mock import MockAudioBackend
from musync.audio.spp import build_spp_ffmpeg_from_file, build_spp_ffmpeg_from_pulse, spp_tcp_url
from musync.config import validate_devices
from musync.models import Device, DeviceKind, DevicePatch


def test_spp_ffmpeg_matches_verified_shape() -> None:
    device = Device(
        id="spp-1",
        name="Phone SPP",
        kind=DeviceKind.NETWORK_SPP,
        listen_host="0.0.0.0",
        listen_port=1234,
        sample_rate=48000,
        channels=1,
    )
    file_cmd = build_spp_ffmpeg_from_file(device, path="media/test.wav")
    assert "tcp://0.0.0.0:1234?listen=1" in file_cmd
    assert file_cmd[file_cmd.index("-ar") + 1] == "48000"
    assert file_cmd[file_cmd.index("-ac") + 1] == "1"
    assert "-re" in file_cmd

    live = build_spp_ffmpeg_from_pulse(device, feeder="musync_feeder")
    assert "musync_feeder.monitor" in live
    assert "-re" not in live
    assert spp_tcp_url(device) == "tcp://0.0.0.0:1234?listen=1"
    af = live[live.index("-af") + 1]
    assert "volume=" in af


def test_validate_allows_bt_plus_spp() -> None:
    devices = [
        Device(id="sink-1", name="A", mac="AA:BB:CC:DD:EE:01", hci="hci0"),
        Device(id="sink-2", name="B", mac="AA:BB:CC:DD:EE:02", hci="hci1"),
        Device(
            id="spp-1",
            name="Phone",
            kind=DeviceKind.NETWORK_SPP,
            listen_port=1234,
        ),
    ]
    assert len(validate_devices(devices)) == 3


def test_mock_lists_and_patches_spp(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MUSYNC_DEVICES_CONFIG", str(tmp_path / "devices.yaml"))
    monkeypatch.setenv("MUSYNC_MEDIA_DIR", str(tmp_path / "media"))
    backend = MockAudioBackend()
    spp = next(d for d in backend.list_devices() if d.kind == DeviceKind.NETWORK_SPP)
    assert spp.listen_port == 1234
    updated = backend.patch_device(
        spp.id,
        DevicePatch(delay_ms=80, volume=0.5, listen_port=1235, sample_rate=44100),
    )
    assert updated.delay_ms == 80
    assert updated.volume == 0.5
    assert updated.listen_port == 1235
    assert updated.sample_rate == 44100
    assert updated.kind == DeviceKind.NETWORK_SPP


def test_api_spp_device(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MUSYNC_DEVICES_CONFIG", str(tmp_path / "devices.yaml"))
    monkeypatch.setenv("MUSYNC_MEDIA_DIR", str(tmp_path / "media"))
    backend = MockAudioBackend()
    app_module.backend = backend
    client = TestClient(app_module.app)

    devices = client.get("/api/devices")
    assert devices.status_code == 200
    spp = next(d for d in devices.json() if d.get("kind") == "network_spp")
    patched = client.patch(
        f"/api/devices/{spp['id']}",
        json={"delay_ms": 25, "listen_port": 1234, "channels": 1},
    )
    assert patched.status_code == 200
    assert patched.json()["delay_ms"] == 25
    assert patched.json()["kind"] == "network_spp"
