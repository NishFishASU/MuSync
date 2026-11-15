from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from musync import app as app_module
from musync.audio.mock import MockAudioBackend
from musync.models import Device
from musync.streaming.ssh_tunnel import (
    bash_tunnel_script,
    build_ssh_tunnel_info,
    powershell_tunnel_script,
    sanitize_tunnel_host,
    ssh_tunnel_command,
)
from musync.wifi.mock import MockWifiBackend


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("MUSYNC_DEVICES_CONFIG", str(tmp_path / "devices.yaml"))
    monkeypatch.setenv("MUSYNC_MEDIA_DIR", str(tmp_path / "media"))
    monkeypatch.setenv("MUSYNC_WIFI_BACKEND", "mock")
    monkeypatch.setenv("MUSYNC_PORT", "8080")
    monkeypatch.setenv("MUSYNC_MDNS", "0")
    monkeypatch.delenv("MUSYNC_SSH_USER", raising=False)
    audio = MockAudioBackend(
        devices=[
            Device(
                id="sink-1",
                name="Living Room",
                mac="AA:BB:CC:DD:EE:01",
                hci="hci0",
                connected=True,
            )
        ]
    )
    wifi = MockWifiBackend()
    app_module.backend = audio
    app_module.wifi_backend = wifi
    return TestClient(app_module.app)


def test_sanitize_tunnel_host_rejects_loopback_and_mdns() -> None:
    assert sanitize_tunnel_host("192.168.43.10") == "192.168.43.10"
    assert sanitize_tunnel_host("127.0.0.1") == ""
    assert sanitize_tunnel_host("localhost") == ""
    assert sanitize_tunnel_host("musync.local") == ""
    assert sanitize_tunnel_host("evil;rm") == ""


def test_ssh_tunnel_command_and_scripts(monkeypatch) -> None:
    monkeypatch.setenv("MUSYNC_PORT", "8080")
    monkeypatch.setenv("MUSYNC_SSH_USER", "pi")
    assert ssh_tunnel_command(host="192.168.43.10") == (
        "ssh -N -L 8080:127.0.0.1:8080 pi@192.168.43.10"
    )
    assert "PI_IP" in ssh_tunnel_command(host="")
    sh = bash_tunnel_script(host="10.0.0.5", port=8080, user="pi")
    assert "ssh -N -L" in sh
    assert "10.0.0.5" in sh
    assert "#!/usr/bin/env bash" in sh
    ps1 = powershell_tunnel_script(host="10.0.0.5", port=8080, user="pi")
    assert "ssh -N -L" in ps1
    assert "10.0.0.5" in ps1


def test_build_ssh_tunnel_info_from_ips(monkeypatch) -> None:
    monkeypatch.setenv("MUSYNC_PORT", "8080")
    info = build_ssh_tunnel_info(["192.168.43.10"])
    assert info["ready"] is True
    assert info["host"] == "192.168.43.10"
    assert info["command"] == "ssh -N -L 8080:127.0.0.1:8080 pi@192.168.43.10"
    assert info["open_url"] == "http://127.0.0.1:8080/streaming/"
    assert len(info["steps"]) >= 3
    # No Wi‑Fi IPs and no guessed LAN → placeholder host.
    monkeypatch.setattr(
        "musync.streaming.ssh_tunnel.pick_lan_ip",
        lambda ips=None: "",
    )
    empty = build_ssh_tunnel_info([])
    assert empty["ready"] is False
    assert "PI_IP" in empty["command"]


def test_api_streaming_includes_ssh_tunnel(client: TestClient) -> None:
    res = client.get("/api/streaming")
    assert res.status_code == 200
    tunnel = res.json()["ssh_tunnel"]
    assert tunnel["port"] == 8080
    assert tunnel["host"] == "192.168.43.10"
    assert tunnel["ready"] is True
    assert tunnel["command"] == "ssh -N -L 8080:127.0.0.1:8080 pi@192.168.43.10"
    assert tunnel["script_sh"].endswith("ssh-tunnel.sh")


def test_api_spotify_ssh_tunnel_json_and_override(client: TestClient, monkeypatch) -> None:
    monkeypatch.setenv("MUSYNC_SSH_USER", "nish")
    res = client.get("/api/streaming/spotify/ssh-tunnel")
    assert res.status_code == 200
    body = res.json()
    assert body["user"] == "nish"
    assert "192.168.43.10" in body["command"]

    over = client.get("/api/streaming/spotify/ssh-tunnel?host=10.1.2.3")
    assert over.json()["host"] == "10.1.2.3"
    assert over.json()["command"] == "ssh -N -L 8080:127.0.0.1:8080 nish@10.1.2.3"

    bad = client.get("/api/streaming/spotify/ssh-tunnel?host=127.0.0.1")
    # Loopback override rejected → falls back to Wi‑Fi LAN IP
    assert bad.json()["host"] == "192.168.43.10"


def test_api_spotify_ssh_tunnel_downloads(client: TestClient) -> None:
    sh = client.get("/api/streaming/spotify/ssh-tunnel.sh")
    assert sh.status_code == 200
    assert "attachment" in sh.headers.get("content-disposition", "")
    assert b"ssh -N -L" in sh.content
    assert b"192.168.43.10" in sh.content

    ps1 = client.get("/api/streaming/spotify/ssh-tunnel.ps1?host=10.9.8.7")
    assert ps1.status_code == 200
    assert b"10.9.8.7" in ps1.content
    assert "attachment" in ps1.headers.get("content-disposition", "")
