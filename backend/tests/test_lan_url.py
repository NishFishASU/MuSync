from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from musync import app as app_module
from musync.audio.mock import MockAudioBackend
from musync.lan_url import ascii_qr, format_banner, lan_ui_url, svg_qr
from musync.models import Device
from musync.wifi.mock import MockWifiBackend


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("MUSYNC_DEVICES_CONFIG", str(tmp_path / "devices.yaml"))
    monkeypatch.setenv("MUSYNC_MEDIA_DIR", str(tmp_path / "media"))
    monkeypatch.setenv("MUSYNC_WIFI_BACKEND", "mock")
    monkeypatch.setenv("MUSYNC_PORT", "8080")
    monkeypatch.setenv("MUSYNC_MDNS", "0")
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


def test_lan_ui_url_from_ips(monkeypatch) -> None:
    monkeypatch.setenv("MUSYNC_PORT", "8080")
    assert lan_ui_url(["192.168.43.10"]) == "http://192.168.43.10:8080"
    monkeypatch.setenv("MUSYNC_PORT", "8081")
    assert lan_ui_url(["10.0.0.5"], port=None) == "http://10.0.0.5:8081"


def test_ascii_and_svg_qr_roundtrip() -> None:
    url = "http://192.168.43.10:8080"
    art = ascii_qr(url)
    assert len(art.splitlines()) >= 5
    svg = svg_qr(url)
    assert "svg" in svg.lower()
    assert len(svg) > 40


def test_format_banner_includes_url_and_qr() -> None:
    text = format_banner("http://192.168.43.10:8080")
    assert "http://192.168.43.10:8080" in text
    assert "Scan QR" in text
    assert "mDNS UI" in text


def test_api_wifi_includes_lan_url(client: TestClient) -> None:
    res = client.get("/api/wifi")
    assert res.status_code == 200
    body = res.json()
    assert body["port"] == 8080
    assert body["lan_url"] == "http://192.168.43.10:8080"
    assert "192.168.43.10" in body["ips"]


def test_api_wifi_lan_qr_svg(client: TestClient) -> None:
    res = client.get("/api/wifi/lan-qr.svg")
    assert res.status_code == 200
    assert "svg" in res.headers.get("content-type", "").lower()
    assert b"<svg" in res.content.lower() or b"svg" in res.content.lower()
    assert res.headers.get("cache-control") == "no-store"
