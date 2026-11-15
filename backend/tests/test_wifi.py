from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from musync import app as app_module
from musync.audio.mock import MockAudioBackend
from musync.models import Device
from musync.wifi.mock import MockWifiBackend
from musync.wifi.nmcli import _redact_text


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("MUSYNC_DEVICES_CONFIG", str(tmp_path / "devices.yaml"))
    monkeypatch.setenv("MUSYNC_MEDIA_DIR", str(tmp_path / "media"))
    monkeypatch.setenv("MUSYNC_WIFI_BACKEND", "mock")
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


def test_wifi_status_mock(client: TestClient) -> None:
    res = client.get("/api/wifi")
    assert res.status_code == 200
    body = res.json()
    assert body["backend"] == "mock"
    assert body["connected"] is True
    assert body["ssid"] == "MuSync-Dev"
    assert "192.168.43.10" in body["ips"]
    assert "password" not in body


def test_wifi_list_and_save(client: TestClient) -> None:
    listed = client.get("/api/wifi/networks")
    assert listed.status_code == 200
    names = {n["ssid"] for n in listed.json()}
    assert "MuSync-Dev" in names
    for row in listed.json():
        assert "password" not in row

    saved = client.post(
        "/api/wifi/networks",
        json={"ssid": "CampHotspot", "password": "super-secret-pass"},
    )
    assert saved.status_code == 200
    assert saved.json()["ssid"] == "CampHotspot"
    assert saved.json()["has_password"] is True
    assert "password" not in saved.json()
    assert "super-secret-pass" not in saved.text


def test_wifi_connect_and_forget(client: TestClient) -> None:
    joined = client.post(
        "/api/wifi/connect",
        json={"ssid": "PhoneHotspot", "password": "temp-pass"},
    )
    assert joined.status_code == 200
    assert joined.json()["ssid"] == "PhoneHotspot"
    assert joined.json()["connected"] is True
    assert "temp-pass" not in joined.text

    # Pre-saved connect without re-sending password
    again = client.post("/api/wifi/connect", json={"ssid": "PhoneHotspot"})
    assert again.status_code == 200
    assert again.json()["ssid"] == "PhoneHotspot"

    deleted = client.delete("/api/wifi/networks/PhoneHotspot")
    assert deleted.status_code == 204

    missing = client.delete("/api/wifi/networks/PhoneHotspot")
    assert missing.status_code == 404


def test_wifi_connect_requires_password_when_unknown(client: TestClient) -> None:
    res = client.post("/api/wifi/connect", json={"ssid": "NeverSeen"})
    assert res.status_code == 400
    assert "password" in res.json()["detail"].lower()


def test_password_never_logged(caplog: pytest.LogCaptureFixture) -> None:
    secret = "hunter2-not-for-logs"
    with caplog.at_level(logging.DEBUG):
        # Simulate what nmcli would redact
        safe = _redact_text(f"nmcli … password {secret}", [secret])
        assert secret not in safe
        assert "***" in safe
