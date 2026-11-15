"""Google Cast / Nest Mini sink — discovery, ffmpeg shape, delay/volume soft-fail."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from musync import app as app_module
from musync.audio.cast import (
    DEFAULT_CAST_DELAY_MS,
    build_cast_ffmpeg,
    build_cast_ffmpeg_idle,
    cast_audio_filter,
    cast_controls_key,
    cast_http_url,
    discover_cast_devices,
    mock_nest_mini,
)
from musync.audio.mock import MockAudioBackend
from musync.audio.pipewire import PipeWireBackend
from musync.config import default_cast_device, validate_devices
from musync.models import Device, DeviceKind, DevicePatch, DiscoveredCastSink


def test_cast_ffmpeg_http_mp3_shape() -> None:
    device = Device(
        id="cast-1",
        name="Nest Mini",
        kind=DeviceKind.CAST,
        listen_host="0.0.0.0",
        listen_port=8090,
        sample_rate=48000,
        channels=2,
        delay_ms=300,
        volume=0.85,
        mute=False,
        cast_uuid="deadbeef-0000-4000-8000-00000000nest",
        cast_host="192.168.1.80",
    )
    cmd = build_cast_ffmpeg(
        device, input_kind="file", input_spec="media/test.wav", ffmpeg_bin="ffmpeg"
    )
    assert "-re" in cmd
    assert "-af" in cmd
    assert cmd[cmd.index("-af") + 1] == "adelay=300|300:all=1,volume=0.8500"
    assert "mp3" in cmd
    assert "-listen" in cmd
    assert cmd[-1] == "http://0.0.0.0:8090/stream.mp3"

    idle = build_cast_ffmpeg_idle(device)
    assert "anullsrc=" in idle[idle.index("-i") + 1]
    assert idle[-1].endswith("/stream.mp3")


def test_cast_controls_and_url() -> None:
    device = default_cast_device()
    assert device.delay_ms == DEFAULT_CAST_DELAY_MS
    assert cast_audio_filter(device) == "adelay=300|300:all=1,volume=0.8500"
    assert cast_controls_key(device) == "d300:v0.850:m0"
    url = cast_http_url(device, host_override="10.0.0.5")
    assert url == "http://10.0.0.5:8090/stream.mp3"


def test_validate_allows_bt_spp_cast() -> None:
    devices = [
        Device(id="sink-1", name="A", mac="AA:BB:CC:DD:EE:01", hci="hci0"),
        Device(
            id="spp-1",
            name="Phone",
            kind=DeviceKind.NETWORK_SPP,
            listen_port=1234,
        ),
        Device(
            id="cast-1",
            name="Nest Mini",
            kind=DeviceKind.CAST,
            listen_port=8090,
            cast_uuid="abc",
            delay_ms=300,
        ),
    ]
    assert len(validate_devices(devices)) == 3


def test_discover_cast_never_raises() -> None:
    """mDNS may be empty or unavailable — soft-fail keeps the hub up."""
    result = discover_cast_devices(timeout_s=0.2)
    assert isinstance(result.devices, list)
    # Either soft_warn (no zeroconf / mDNS error) or a detail string when OK.
    assert result.detail or result.soft_warn or result.devices == []


def test_mock_nest_mini_in_discovery(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MUSYNC_DEVICES_CONFIG", str(tmp_path / "devices.yaml"))
    monkeypatch.setenv("MUSYNC_MEDIA_DIR", str(tmp_path / "media"))
    backend = MockAudioBackend()
    app_module.backend = backend
    client = TestClient(app_module.app)

    disc = client.get("/api/devices/discover")
    assert disc.status_code == 200
    body = disc.json()
    cast = body.get("cast") or []
    assert any(c["name"] == "Nest Mini" for c in cast)
    nest = next(c for c in cast if c["name"] == "Nest Mini")
    assert nest.get("host")
    assert nest.get("model")

    saved = client.put(
        "/api/devices/config",
        json={
            "devices": [
                {
                    "name": "Nest Mini",
                    "kind": "cast",
                    "enabled": True,
                    "cast_uuid": nest.get("uuid"),
                    "cast_host": nest.get("host"),
                    "cast_port": nest.get("port") or 8009,
                    "listen_port": 8090,
                    "delay_ms": 300,
                    "channels": 2,
                },
                {
                    "id": "spp-1",
                    "name": "Phone SPP",
                    "kind": "network_spp",
                    "enabled": True,
                    "listen_port": 1234,
                },
            ]
        },
    )
    assert saved.status_code == 200, saved.text
    devices = saved.json()
    cast_dev = next(d for d in devices if d["kind"] == "cast")
    assert cast_dev["name"] == "Nest Mini"
    assert cast_dev["delay_ms"] == 300
    assert cast_dev["cast_host"]
    assert cast_dev["listen_port"] == 8090


def test_api_cast_delay_volume_soft_fail(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MUSYNC_DEVICES_CONFIG", str(tmp_path / "devices.yaml"))
    monkeypatch.setenv("MUSYNC_MEDIA_DIR", str(tmp_path / "media"))
    backend = MockAudioBackend(
        devices=[
            Device(
                id="cast-1",
                name="Nest Mini",
                kind=DeviceKind.CAST,
                enabled=True,
                connected=True,
                delay_ms=300,
                volume=0.85,
                listen_port=8090,
                channels=2,
                cast_uuid="deadbeef-0000-4000-8000-00000000nest",
                cast_host="192.168.1.80",
            ),
            Device(
                id="spp-1",
                name="Phone SPP",
                kind=DeviceKind.NETWORK_SPP,
                enabled=True,
                listen_port=1234,
            ),
        ]
    )
    app_module.backend = backend
    client = TestClient(app_module.app)

    patched = client.patch(
        "/api/devices/cast-1",
        json={"delay_ms": 450, "volume": 0.5},
    )
    assert patched.status_code == 200
    body = patched.json()
    assert body["delay_ms"] == 450
    assert body["volume"] == 0.5
    assert body["kind"] == "cast"
    assert body["connected"] is True


def test_pipewire_cast_dry_run_restart(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MUSYNC_DEVICES_CONFIG", str(tmp_path / "devices.yaml"))
    monkeypatch.setenv("MUSYNC_MEDIA_DIR", str(tmp_path / "media"))
    monkeypatch.setenv("MUSYNC_PW_DRY_RUN", "1")
    devices = [
        Device(
            id="cast-1",
            name="Nest Mini",
            kind=DeviceKind.CAST,
            enabled=True,
            delay_ms=300,
            listen_port=8090,
            channels=2,
            cast_host="192.168.1.80",
        ),
        Device(
            id="spp-1",
            name="Phone",
            kind=DeviceKind.NETWORK_SPP,
            enabled=True,
            listen_port=1234,
        ),
    ]
    backend = PipeWireBackend(devices=devices)
    backend._sync_cast_streams()
    key = backend._cast_source_keys.get("cast-1")
    assert key
    assert backend.get_device("cast-1").connected is True

    # Matching source must not bounce.
    sentinel = MagicMock()
    sentinel.poll.return_value = None
    backend._cast_procs["cast-1"] = sentinel
    with patch.object(backend, "_restart_cast_stream") as restart:
        backend._sync_cast_streams()
        restart.assert_not_called()

    # Delay PATCH rebuilds -af (soft-fail path still returns device).
    updated = backend.patch_device("cast-1", DevicePatch(delay_ms=500))
    assert updated.delay_ms == 500


def test_mock_nest_mini_fixture() -> None:
    row = mock_nest_mini()
    assert isinstance(row, DiscoveredCastSink)
    assert "Nest" in row.name
    assert row.host
