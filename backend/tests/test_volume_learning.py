"""S_u × G_d volume learning — assign, observe, cross-infer, re-fit."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from musync import app as app_module
from musync.audio.mock import MockAudioBackend
from musync.models import Device
from musync.profiles import (
    VolumeProfileStore,
    fit_sensitivity_and_device_gains,
    suggest_volume_for_user,
)


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
                name="IEM A",
                mac="AA:BB:CC:DD:EE:01",
                hci="hci0",
                connected=True,
                volume=0.5,
                gain=1.0,
            ),
            Device(
                id="sink-2",
                name="IEM B",
                mac="AA:BB:CC:DD:EE:02",
                hci="hci1",
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
                gain=1.0,
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


def test_fit_and_cross_infer_a_shared_device() -> None:
    """A sets d1+d2; B sets d1 → suggest B on d2 via S_B × G_2."""
    # A prefers louder overall; d2 is quieter hardware than d1.
    obs = {
        ("user-a", "d1"): 0.80,
        ("user-a", "d2"): 0.40,
        ("user-b", "d1"): 0.40,
    }
    sens, gains = fit_sensitivity_and_device_gains(
        obs, user_ids=["user-a", "user-b"], device_ids=["d1", "d2"]
    )
    assert "d1" in gains and "d2" in gains
    assert gains["d1"] > gains["d2"]  # louder device
    assert sens["user-a"] > sens["user-b"]  # louder listener

    suggested, reason = suggest_volume_for_user(
        "user-b",
        "d2",
        observations=obs,
        sensitivities=sens,
        device_gains=gains,
    )
    assert reason == "s_u_x_g_d"
    # Ideal: S_B · G_2 ≈ 0.20 (half of A's 0.40 on d2)
    assert 0.15 <= suggested <= 0.28


def test_assign_preset_and_volume_updates_latents(client: TestClient) -> None:
    a = client.post(
        "/api/profiles", json={"name": "Alice", "preferred_level": 0.8}
    ).json()
    b = client.post(
        "/api/profiles", json={"name": "Bob", "preferred_level": 0.5}
    ).json()

    # Assign Alice to sink-1, set volume → observation
    assign_a1 = client.put(
        "/api/profiles/assignments",
        json={"device_id": "sink-1", "profile_id": a["id"], "apply_suggested": True},
    )
    assert assign_a1.status_code == 200
    client.patch("/api/devices/sink-1", json={"volume": 0.8})

    # Alice on sink-2
    client.put(
        "/api/profiles/assignments",
        json={"device_id": "sink-2", "profile_id": a["id"], "apply_suggested": True},
    )
    client.patch("/api/devices/sink-2", json={"volume": 0.4})

    state = client.get("/api/profiles").json()
    assert state["assignments"]["sink-1"] == a["id"]
    assert state["assignments"]["sink-2"] == a["id"]
    assert len(state["volumes"]) >= 2
    assert "sink-1" in state["device_gains"]
    assert "sink-2" in state["device_gains"]

    # Bob on sink-1 with his own volume
    client.put(
        "/api/profiles/assignments",
        json={"device_id": "sink-1", "profile_id": b["id"], "apply_suggested": True},
    )
    client.patch("/api/devices/sink-1", json={"volume": 0.4})

    # Assign Bob to sink-2 → should preset via S_u × G_d cross-infer
    res = client.put(
        "/api/profiles/assignments",
        json={"device_id": "sink-2", "profile_id": b["id"], "apply_suggested": True},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["applied"] is True
    assert body["suggested_volume"] is not None
    assert 0.12 <= body["suggested_volume"] <= 0.32
    assert "s_u_x_g_d" in body["detail"] or "preset" in body["detail"]

    devices = {d["id"]: d for d in client.get("/api/devices").json()}
    assert abs(devices["sink-2"]["volume"] - body["suggested_volume"]) < 0.02

    # Adjusting from suggestion re-fits latents
    client.patch("/api/devices/sink-2", json={"volume": 0.25})
    state = client.get("/api/profiles").json()
    bob = next(p for p in state["profiles"] if p["id"] == b["id"])
    assert bob["sensitivity"] > 0
    assert "sink-2" in state["device_gains"]


def test_preferred_x_gain_ui_path_unchanged(client: TestClient) -> None:
    """#27 explicit apply still uses preferred × Device.gain."""
    created = client.post(
        "/api/profiles",
        json={"name": "Nish", "preferred_level": 0.7},
    ).json()
    client.patch("/api/devices/spp-1", json={"gain": 1.2})
    active = client.put("/api/profiles/active", json={"id": created["id"]})
    assert active.status_code == 200
    devices = {d["id"]: d for d in client.get("/api/devices").json()}
    assert devices["sink-1"]["volume"] == 0.7
    assert devices["spp-1"]["volume"] == 0.84


def test_volume_db_persists(client: TestClient, tmp_path: Path) -> None:
    created = client.post(
        "/api/profiles", json={"name": "Guest", "preferred_level": 0.5}
    ).json()
    client.put(
        "/api/profiles/assignments",
        json={"device_id": "sink-1", "profile_id": created["id"]},
    )
    client.patch("/api/devices/sink-1", json={"volume": 0.55})
    db = tmp_path / "user_volumes.sqlite"
    assert db.is_file()
    assert db.stat().st_size > 0
