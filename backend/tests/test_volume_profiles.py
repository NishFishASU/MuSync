"""User volume preference profiles — silent-disco preferred × gain apply."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from musync import app as app_module
from musync.audio.mock import MockAudioBackend
from musync.models import Device
from musync.profiles import (
    VolumeProfileStore,
    compute_output_volume,
    volumes_for_devices,
)
from musync.models import VolumeProfile


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("MUSYNC_DEVICES_CONFIG", str(tmp_path / "devices.yaml"))
    monkeypatch.setenv("MUSYNC_MEDIA_DIR", str(tmp_path / "media"))
    monkeypatch.setenv("MUSYNC_PROFILES_CONFIG", str(tmp_path / "profiles.yaml"))
    monkeypatch.setenv("MUSYNC_VOLUME_DB", str(tmp_path / "user_volumes.sqlite"))
    backend = MockAudioBackend(
        devices=[
            Device(
                id="sink-1",
                name="Qudelix",
                mac="AA:BB:CC:DD:EE:01",
                hci="hci0",
                connected=True,
                volume=0.5,
                gain=1.0,
            ),
            Device(
                id="spp-1",
                name="Phone SPP",
                kind="network_spp",
                connected=True,
                enabled=True,
                volume=0.5,
                gain=1.2,
                listen_port=1234,
            ),
        ]
    )
    store = VolumeProfileStore(
        path=tmp_path / "profiles.yaml",
        volume_db=tmp_path / "user_volumes.sqlite",
    )
    app_module.backend = backend
    app_module.profile_store = store
    return TestClient(app_module.app)


def test_compute_output_volume_pref_times_gain() -> None:
    assert compute_output_volume(0.8, 1.0) == 0.8
    assert compute_output_volume(0.8, 1.25) == 1.0  # clamped
    assert compute_output_volume(0.5, 0.5) == 0.25
    assert compute_output_volume(0.8, 1.0, override=0.4) == 0.4
    assert compute_output_volume(0.8, 2.0, override=0.4) == 0.8


def test_volumes_for_devices_honors_overrides() -> None:
    profile = VolumeProfile(
        id="user-a",
        name="A",
        preferred_level=0.7,
        output_overrides={"spp-1": 0.5},
    )
    devices = [
        Device(id="sink-1", name="BT", mac="AA:BB:CC:DD:EE:01", gain=1.0),
        Device(
            id="spp-1",
            name="SPP",
            kind="network_spp",
            gain=1.2,
            listen_port=1234,
        ),
    ]
    vols = volumes_for_devices(profile, devices)
    assert vols["sink-1"] == 0.7
    assert vols["spp-1"] == 0.6  # 0.5 × 1.2


def test_create_select_apply_delete_profiles(client: TestClient) -> None:
    empty = client.get("/api/profiles")
    assert empty.status_code == 200
    assert empty.json() == {
        "profiles": [],
        "active_id": None,
        "assignments": {},
        "volumes": [],
        "device_gains": {},
    }

    created = client.post(
        "/api/profiles",
        json={"name": "Nish", "preferred_level": 0.7},
    )
    assert created.status_code == 201
    profile = created.json()
    assert profile["name"] == "Nish"
    assert profile["preferred_level"] == 0.7
    assert profile["id"].startswith("user-")

    listed = client.get("/api/profiles")
    assert len(listed.json()["profiles"]) == 1

    # Select + apply: sink-1 → 0.7×1.0, spp-1 → 0.7×1.2 = 0.84
    active = client.put("/api/profiles/active", json={"id": profile["id"]})
    assert active.status_code == 200
    assert active.json()["active_id"] == profile["id"]

    devices = {d["id"]: d for d in client.get("/api/devices").json()}
    assert devices["sink-1"]["volume"] == 0.7
    assert devices["spp-1"]["volume"] == 0.84
    # Delay untouched
    assert devices["sink-1"]["delay_ms"] == 0

    # Patch preferred level while active re-applies
    patched = client.patch(
        f"/api/profiles/{profile['id']}",
        json={"preferred_level": 0.5},
    )
    assert patched.status_code == 200
    devices = {d["id"]: d for d in client.get("/api/devices").json()}
    assert devices["sink-1"]["volume"] == 0.5
    assert devices["spp-1"]["volume"] == 0.6

    # Per-output override on SPP
    client.patch(
        f"/api/profiles/{profile['id']}",
        json={"output_overrides": {"spp-1": 0.4}},
    )
    devices = {d["id"]: d for d in client.get("/api/devices").json()}
    assert devices["sink-1"]["volume"] == 0.5
    assert devices["spp-1"]["volume"] == 0.48  # 0.4 × 1.2

    # Gain calibration on device then re-apply
    client.patch("/api/devices/sink-1", json={"gain": 0.5})
    applied = client.post(f"/api/profiles/{profile['id']}/apply")
    assert applied.status_code == 200
    by_id = {d["id"]: d for d in applied.json()}
    assert by_id["sink-1"]["volume"] == 0.25  # 0.5 × 0.5
    assert by_id["sink-1"]["gain"] == 0.5

    deleted = client.delete(f"/api/profiles/{profile['id']}")
    assert deleted.status_code == 204
    state = client.get("/api/profiles").json()
    assert state["profiles"] == []
    assert state["active_id"] is None


def test_profiles_persist_to_yaml(client: TestClient, tmp_path: Path) -> None:
    created = client.post(
        "/api/profiles",
        json={"name": "Guest", "preferred_level": 0.4},
    )
    assert created.status_code == 201
    path = tmp_path / "profiles.yaml"
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert "Guest" in text
    assert "preferred_level: 0.4" in text


def test_clear_active_profile(client: TestClient) -> None:
    created = client.post(
        "/api/profiles",
        json={"name": "Temp", "preferred_level": 0.9},
    ).json()
    client.put("/api/profiles/active", json={"id": created["id"]})
    cleared = client.put("/api/profiles/active", json={"id": None})
    assert cleared.status_code == 200
    assert cleared.json()["active_id"] is None
