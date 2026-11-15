from __future__ import annotations

from musync.audio.pw_tools import (
    build_sink_commands,
    find_musync_loopback_ids,
    match_device_node,
    normalize_mac,
    parse_wpctl_status,
    PwNode,
)


SAMPLE_WPCTL = """
Audio
 ├─ Devices:
 │
 ├─ Sinks:
 │  *   48. bluez_output.AA_BB_CC_DD_EE_01.1 [vol: 0.80]
 │      49. bluez_output.AA_BB_CC_DD_EE_02.1 [vol: 0.70]
 │      50. Dummy Output
 │
 ├─ Sources:
 │      51. Mic
 │
 ├─ Filters:
 │
 └─ Streams:
"""


def test_normalize_mac() -> None:
    assert normalize_mac("aa-bb-cc-dd-ee-01") == "AA:BB:CC:DD:EE:01"
    assert normalize_mac("AA_BB_CC_DD_EE_02") == "AA:BB:CC:DD:EE:02"


def test_parse_wpctl_status_bluez() -> None:
    nodes = parse_wpctl_status(SAMPLE_WPCTL)
    assert len(nodes) >= 2
    assert nodes[0].id == "48"
    assert normalize_mac(nodes[0].mac) == "AA:BB:CC:DD:EE:01"
    assert nodes[1].mac == "AA:BB:CC:DD:EE:02"


def test_match_device_node_by_mac() -> None:
    nodes = parse_wpctl_status(SAMPLE_WPCTL)
    hit = match_device_node(
        mac="AA:BB:CC:DD:EE:02",
        hci="hci1",
        pw_node_override="",
        nodes=nodes,
    )
    assert hit is not None
    assert hit.id == "49"


def test_match_device_node_override() -> None:
    hit = match_device_node(
        mac="",
        hci="",
        pw_node_override="99",
        nodes=[],
    )
    assert hit is not None
    assert hit.id == "99"


def test_build_sink_commands_with_pactl() -> None:
    cmds, notes = build_sink_commands(
        device_id="sink-1",
        feeder="musync_feeder",
        target_node_id="48",
        target_name="bluez_output.AA_BB_CC_DD_EE_01.1",
        delay_ms=40,
        volume=0.8,
        mute=False,
        tools={"pactl": "/usr/bin/pactl", "wpctl": "/usr/bin/wpctl", "pw-link": None},
    )
    assert any("module-loopback" in c for c in cmds)
    assert any(c[:3] == ["/usr/bin/wpctl", "set-volume", "48"] for c in cmds)
    assert notes


def test_find_musync_loopback_ids() -> None:
    text = "12\tmodule-loopback sink_input_properties=media.name=musync_loopback_sink_1\n"
    assert find_musync_loopback_ids(text, "sink-1") == ["12"]
