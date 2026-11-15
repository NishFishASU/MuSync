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


def test_transport_still_works_software(backend: PipeWireBackend) -> None:
    playing = backend.set_transport(TransportPut(state=TransportState.PLAYING))
    assert playing.state == TransportState.PLAYING
    stopped = backend.set_transport(TransportPut(state=TransportState.STOPPED))
    assert stopped.position_ms == 0
