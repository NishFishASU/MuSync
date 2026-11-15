"""SPP TCP network output sink — models, mock device, ffmpeg command shape."""

from __future__ import annotations

from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from musync import app as app_module
from musync.audio.mock import MockAudioBackend
from musync.audio.pipewire import PipeWireBackend
from musync.audio.spp import (
    build_spp_ffmpeg,
    build_spp_ffmpeg_from_file,
    build_spp_ffmpeg_from_pulse,
    build_spp_ffmpeg_idle,
    spp_tcp_url,
)
from musync.config import validate_devices
from musync.models import Device, DeviceKind, DevicePatch, TransportPut, TransportState


def test_spp_ffmpeg_matches_verified_manual_shape() -> None:
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
    # Proven manual: ffmpeg -re -i <file> -f s16le -acodec pcm_s16le -ar 48000 -ac 1 tcp://…?listen=1
    assert "-re" in file_cmd
    assert file_cmd[file_cmd.index("-i") + 1] == "media/test.wav"
    assert "-af" not in file_cmd
    assert "s16le" in file_cmd
    assert file_cmd[file_cmd.index("-acodec") + 1] == "pcm_s16le"
    assert file_cmd[file_cmd.index("-ar") + 1] == "48000"
    assert file_cmd[file_cmd.index("-ac") + 1] == "1"
    assert file_cmd[-1] == "tcp://0.0.0.0:1234?listen=1"

    idle = build_spp_ffmpeg_idle(device)
    assert "-af" not in idle
    assert "anullsrc=" in idle[idle.index("-i") + 1]
    assert idle[-1] == "tcp://0.0.0.0:1234?listen=1"

    live = build_spp_ffmpeg_from_pulse(device, feeder="musync_feeder")
    assert "musync_feeder.monitor" in live
    assert "-re" not in live
    assert "-af" not in live
    assert spp_tcp_url(device) == "tcp://0.0.0.0:1234?listen=1"


def test_spp_ffmpeg_unified_builder_kinds() -> None:
    device = Device(
        id="spp-1",
        name="Phone",
        kind=DeviceKind.NETWORK_SPP,
        listen_port=1234,
        sample_rate=48000,
        channels=1,
    )
    file_cmd = build_spp_ffmpeg(
        device, input_kind="file", input_spec="/tmp/a.wav", ffmpeg_bin="ffmpeg"
    )
    assert file_cmd[:4] == ["ffmpeg", "-hide_banner", "-loglevel", "warning"]
    assert "-re" in file_cmd and "-af" not in file_cmd


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


def test_sync_spp_does_not_restart_matching_listener(tmp_path, monkeypatch) -> None:
    """Play → _ensure_graph → _sync_spp must not kill a live session on same source."""
    monkeypatch.setenv("MUSYNC_DEVICES_CONFIG", str(tmp_path / "devices.yaml"))
    monkeypatch.setenv("MUSYNC_MEDIA_DIR", str(tmp_path / "media"))
    monkeypatch.setenv("MUSYNC_PW_DRY_RUN", "1")
    devices = [
        Device(id="sink-1", name="A", mac="AA:BB:CC:DD:EE:01", hci="hci0"),
        Device(
            id="spp-1",
            name="Phone",
            kind=DeviceKind.NETWORK_SPP,
            enabled=True,
            listen_port=1234,
        ),
    ]
    backend = PipeWireBackend(devices=devices)
    # Seed a healthy idle (anullsrc) listener.
    backend._sync_spp_listeners()
    key = backend._spp_source_keys.get("spp-1")
    assert key and key.startswith("lavfi:")

    sentinel = MagicMock()
    sentinel.poll.return_value = None  # still running
    backend._spp_procs["spp-1"] = sentinel

    restarts: list[str] = []
    monkeypatch.setattr(
        backend,
        "_restart_spp_listener",
        lambda d: restarts.append(d.id),
    )

    backend._sync_spp_listeners()
    assert restarts == []
    assert backend._devices["spp-1"].connected is True
    assert backend._spp_procs["spp-1"] is sentinel


def test_sync_spp_revives_dead_listener(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MUSYNC_DEVICES_CONFIG", str(tmp_path / "devices.yaml"))
    monkeypatch.setenv("MUSYNC_MEDIA_DIR", str(tmp_path / "media"))
    monkeypatch.setenv("MUSYNC_PW_DRY_RUN", "1")
    devices = [
        Device(id="sink-1", name="A", mac="AA:BB:CC:DD:EE:01", hci="hci0"),
        Device(
            id="spp-1",
            name="Phone",
            kind=DeviceKind.NETWORK_SPP,
            enabled=True,
            listen_port=1234,
        ),
    ]
    backend = PipeWireBackend(devices=devices)
    dead = MagicMock()
    dead.poll.return_value = 1
    backend._spp_procs["spp-1"] = dead
    backend._spp_source_keys["spp-1"] = "lavfi:stale"

    revived: list[str] = []
    monkeypatch.setattr(
        backend,
        "_restart_spp_listener",
        lambda d: revived.append(d.id),
    )
    backend._sync_spp_listeners()
    assert revived == ["spp-1"]


def test_spp_desired_source_switches_on_play(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MUSYNC_DEVICES_CONFIG", str(tmp_path / "devices.yaml"))
    media = tmp_path / "media"
    media.mkdir()
    track = media / "demo.wav"
    track.write_bytes(b"RIFF")  # path only — session may still pick it via catalog
    monkeypatch.setenv("MUSYNC_MEDIA_DIR", str(media))
    monkeypatch.setenv("MUSYNC_PW_DRY_RUN", "1")

    devices = [
        Device(
            id="spp-1",
            name="Phone",
            kind=DeviceKind.NETWORK_SPP,
            enabled=True,
            listen_port=1234,
            sample_rate=48000,
            channels=1,
        ),
    ]
    backend = PipeWireBackend(devices=devices)
    spp = backend._devices["spp-1"]
    kind, spec, key = backend._spp_desired_source(spp)
    assert kind == "lavfi"
    assert "anullsrc" in spec

    # Pretend session is playing a local file.
    monkeypatch.setattr(backend._session, "active_file_path", lambda: str(track))
    monkeypatch.setattr(
        backend._session,
        "get_transport",
        lambda: type(
            "T",
            (),
            {"state": TransportState.PLAYING, "source_id": "local:demo.wav"},
        )(),
    )
    kind2, spec2, key2 = backend._spp_desired_source(spp)
    assert kind2 == "file"
    assert spec2 == str(track)
    assert key2.startswith("file:")
