from __future__ import annotations

from pathlib import Path

import pytest

from musync.audio.pipewire import PipeWireBackend
from musync.audio.pw_tools import parse_wpctl_status
from musync.models import Device, DevicePatch, TransportPut, TransportState


@pytest.fixture
def backend(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> PipeWireBackend:
    monkeypatch.setenv("MUSYNC_DEVICES_CONFIG", str(tmp_path / "devices.yaml"))
    monkeypatch.setenv("MUSYNC_MEDIA_DIR", str(tmp_path / "media"))
    monkeypatch.setenv("MUSYNC_PW_DRY_RUN", "1")
    monkeypatch.setattr(
        "musync.audio.pipewire.repo_root",
        lambda: tmp_path,
    )
    devices = [
        Device(
            id="sink-1",
            name="Speaker A",
            mac="AA:BB:CC:DD:EE:01",
            hci="hci0",
            connected=False,
            delay_ms=0,
            volume=0.8,
            enabled=True,
        ),
        Device(
            id="sink-2",
            name="Speaker B",
            mac="AA:BB:CC:DD:EE:02",
            hci="hci1",
            connected=False,
            delay_ms=40,
            volume=0.7,
            enabled=True,
        ),
    ]
    return PipeWireBackend(devices=devices)


def test_plan_graph_dry_run_writes_script(backend: PipeWireBackend, tmp_path: Path) -> None:
    plan = backend.plan_graph()
    assert plan.dry_run is True
    assert len(plan.links) == 2
    path = backend.write_graph_plan()
    text = path.read_text(encoding="utf-8")
    assert "module-loopback" in text or "pw-link" in text or "musync_feeder" in text
    assert "sink-1" in text and "sink-2" in text


def test_refresh_map_from_fixture(backend: PipeWireBackend, monkeypatch: pytest.MonkeyPatch) -> None:
    sample = """
Audio
 ├─ Sinks:
 │      48. bluez_output.AA_BB_CC_DD_EE_01.1
 │      49. bluez_output.AA_BB_CC_DD_EE_02.1
 ├─ Sources:
"""

    def fake_discover(**_kwargs):
        return parse_wpctl_status(sample), "fixture"

    monkeypatch.setattr("musync.audio.pipewire.discover_nodes", fake_discover)
    mapped = backend.refresh_sink_map()
    assert mapped["sink-1"] == "48"
    assert mapped["sink-2"] == "49"
    assert backend.get_device("sink-1").connected is True


def test_patch_delay_replans(backend: PipeWireBackend) -> None:
    updated = backend.patch_device("sink-2", DevicePatch(delay_ms=120))
    assert updated.delay_ms == 120
    plan = backend._last_plan
    assert plan is not None
    link = next(l for l in plan.links if l.device_id == "sink-2")
    assert link.delay_ms == 120


def test_disable_sink_skips_link(backend: PipeWireBackend) -> None:
    backend.patch_device("sink-2", DevicePatch(enabled=False))
    plan = backend.plan_graph()
    assert all(l.device_id != "sink-2" for l in plan.links)
    assert any("sink-2" in s for s in plan.skipped)


def test_health_reports_dry_run(backend: PipeWireBackend) -> None:
    health = backend.health()
    assert health.backend == "pipewire"
    assert health.graph_dry_run is True
    assert health.sink_count == 2
    assert "READY=" in health.detail
    assert "MISSING=" in health.detail


def test_sink_readiness_ready_vs_missing(backend: PipeWireBackend) -> None:
    backend._devices["sink-1"].connected = True
    backend._devices["sink-2"].connected = False
    ready, missing = backend._sink_readiness_summary()
    assert "sink-1" in ready
    assert "sink-2" in missing


def test_reconnect_bt_throttled_and_connects(backend: PipeWireBackend, monkeypatch) -> None:
    monkeypatch.setattr(backend, "_env_dry_run", lambda: False)
    backend._tools["bluetoothctl"] = "/usr/bin/bluetoothctl"
    # Real-looking MAC (AA:BB:CC:DD:EE:* is treated as YAML placeholder and skipped).
    backend._devices["sink-1"].mac = "11:22:33:44:55:66"
    backend._devices["sink-1"].connected = False
    backend._pw_node_ids.pop("sink-1", None)

    from musync.audio.pw_tools import BtRemote

    monkeypatch.setattr(
        "musync.audio.pipewire.discover_bluetooth_remotes",
        lambda **_k: (
            [BtRemote(mac="11:22:33:44:55:66", name="Speaker A", paired=True)],
            "fixture",
        ),
    )
    calls: list[str] = []

    def fake_connect(_bt, mac, **_k):
        calls.append(mac)
        return True, "ok"

    monkeypatch.setattr("musync.audio.pipewire.bluetoothctl_connect", fake_connect)
    first = backend._reconnect_bt_sinks(force=True)
    assert "sink-1" in first
    assert calls == ["11:22:33:44:55:66"]
    # Immediate second call without force is throttled.
    assert backend._reconnect_bt_sinks(force=False) == []


def test_transport_still_works_software(backend: PipeWireBackend) -> None:
    playing = backend.set_transport(TransportPut(state=TransportState.PLAYING))
    assert playing.state == TransportState.PLAYING


def test_ensure_feeder_idempotent_purges_orphans(
    backend: PipeWireBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Repeated soft-loads used to create dozens of MuSync_Feeder sinks."""
    monkeypatch.setenv("MUSYNC_PW_DRY_RUN", "0")
    backend._tools["pactl"] = "/usr/bin/pactl"
    modules = (
        "1\tmodule-null-sink sink_name=musync_feeder "
        "sink_properties=device.description=MuSync_Feeder\n"
        "2\tmodule-null-sink sink_name=musync_feeder "
        "sink_properties=device.description=MuSync_Feeder\n"
    )
    sinks_after_purge = "10\tmusync_feeder\tPipeWire\n"
    calls: list[list[str]] = []

    def fake_exec(args: list[str]):
        calls.append(list(args))
        from types import SimpleNamespace

        if args[1:3] == ["list", "modules"]:
            return SimpleNamespace(returncode=0, stdout=modules, stderr="")
        if args[1:3] == ["list", "sinks"]:
            # After purge+load we report present; before unload still "present"
            return SimpleNamespace(returncode=0, stdout=sinks_after_purge, stderr="")
        if args[1] == "unload-module":
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if args[1:3] == ["load-module", "module-null-sink"]:
            return SimpleNamespace(returncode=0, stdout="99\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(backend, "_exec_soft", fake_exec)
    monkeypatch.setattr(backend, "_env_dry_run", lambda: False)

    backend._ensure_feeder(reset=True)
    unload = [c for c in calls if len(c) >= 2 and c[1] == "unload-module"]
    loads = [c for c in calls if len(c) >= 3 and c[1] == "load-module"]
    assert {c[2] for c in unload} == {"1", "2"}
    assert len(loads) == 1
    assert any("sink_name=musync_feeder" in a for a in loads[0])

    # Second ensure with a single healthy feeder must not load again.
    calls.clear()
    single = (
        "99\tmodule-null-sink sink_name=musync_feeder "
        "sink_properties=device.description=MuSync_Feeder\n"
    )

    def fake_exec_one(args: list[str]):
        calls.append(list(args))
        from types import SimpleNamespace

        if args[1:3] == ["list", "modules"]:
            return SimpleNamespace(returncode=0, stdout=single, stderr="")
        if args[1:3] == ["list", "sinks"]:
            return SimpleNamespace(returncode=0, stdout=sinks_after_purge, stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(backend, "_exec_soft", fake_exec_one)
    backend._ensure_feeder()
    assert not any(c[1] == "load-module" for c in calls if len(c) > 1)
    assert not any(c[1] == "unload-module" for c in calls if len(c) > 1)
    stopped = backend.set_transport(TransportPut(state=TransportState.STOPPED))
    assert stopped.position_ms == 0
