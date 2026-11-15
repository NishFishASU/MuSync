from __future__ import annotations

from musync.audio.pw_tools import (
    CAPTURE_LOOPBACK_LATENCY_MSEC,
    FEEDER_CHANNELS,
    FEEDER_NODE_LATENCY,
    FEEDER_RATE,
    alsa_hdmi_to_pw_node,
    alsa_usb_to_pw_node,
    bluetoothctl_connect,
    build_capture_loopback_commands,
    build_sink_commands,
    classify_capture_source,
    estimated_capture_loopback_saving_ms,
    feeder_listed_in_sinks,
    feeder_null_sink_args,
    find_all_musync_owned_loopback_ids,
    find_feeder_null_sink_ids,
    find_musync_loopback_ids,
    is_capture_candidate,
    looks_like_hdmi_capture,
    loopback_latency_args,
    match_device_node,
    merge_alsa_hdmi_into_sources,
    merge_alsa_usb_into_sources,
    normalize_mac,
    parse_arecord_l,
    parse_arecord_l_hdmi,
    parse_proc_asound_cards,
    parse_proc_asound_hdmi_cards,
    parse_wpctl_sources,
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
 │      61. bluez_input.AA_BB_CC_DD_EE_21 [vol: 1.00]
 │      62. alsa_input.usb-Generic_USB_Audio-00.analog-stereo
 │      63. alsa_input.usb-MACROSILICON_HDMI-00.analog-stereo
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


def test_feeder_and_loopback_low_latency_args() -> None:
    args = feeder_null_sink_args("musync_feeder")
    assert args[0] == "module-null-sink"
    assert f"sink_name=musync_feeder" in args
    assert f"rate={FEEDER_RATE}" in args
    assert f"channels={FEEDER_CHANNELS}" in args
    props = next(a for a in args if a.startswith("sink_properties="))
    assert "device.description=MuSync_Feeder" in props
    assert f"node.latency={FEEDER_NODE_LATENCY}" in props

    lb = loopback_latency_args(40)
    assert lb == ["latency_msec=40", "max_latency_msec=40", "adjust_time=0"]
    assert loopback_latency_args(0) == [
        "latency_msec=0",
        "max_latency_msec=10",
        "adjust_time=0",
    ]
    # Measurable win: capture path 20 → 10 ms.
    assert CAPTURE_LOOPBACK_LATENCY_MSEC == 10
    assert estimated_capture_loopback_saving_ms() == 10


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
    loop = next(c for c in cmds if "module-loopback" in c)
    assert "latency_msec=40" in loop
    assert "max_latency_msec=40" in loop
    assert "adjust_time=0" in loop
    assert any(c[:3] == ["/usr/bin/wpctl", "set-volume", "48"] for c in cmds)
    assert notes


def test_build_capture_loopback_low_latency() -> None:
    cmds, notes = build_capture_loopback_commands(
        host_id="phone-1",
        feeder="musync_feeder",
        source_ref="bluez_input.AA_BB_CC_DD_EE_21",
        tools={"pactl": "/usr/bin/pactl", "pw-link": None},
    )
    assert len(cmds) == 1
    assert f"latency_msec={CAPTURE_LOOPBACK_LATENCY_MSEC}" in cmds[0]
    assert "max_latency_msec=10" in cmds[0]
    assert "adjust_time=0" in cmds[0]
    assert "adjust_time=0" in notes[0]


def test_find_musync_loopback_ids() -> None:
    text = "12\tmodule-loopback sink_input_properties=media.name=musync_loopback_sink_1\n"
    assert find_musync_loopback_ids(text, "sink-1") == ["12"]


def test_find_all_musync_owned_loopback_ids() -> None:
    modules = """
10\tmodule-loopback sink_input_properties=media.name=musync_loopback_qudelix
11\tmodule-loopback sink_input_properties=media.name=musync_capture_phone
12\tmodule-loopback source=musync_feeder.monitor sink=bluez_output.AA_BB
13\tmodule-loopback source=bluez_input.XX sink=musync_feeder
14\tmodule-loopback source=other.monitor sink=speakers
15\tmodule-null-sink sink_name=musync_feeder
"""
    assert find_all_musync_owned_loopback_ids(modules) == ["10", "11", "12", "13"]


def test_find_feeder_null_sink_ids_and_listed() -> None:
    modules = """
1\tmodule-null-sink sink_name=musync_feeder sink_properties=device.description=MuSync_Feeder
2\tmodule-null-sink sink_name=musync_feeder sink_properties=device.description=MuSync_Feeder
3\tmodule-loopback source=musync_feeder.monitor
4\tmodule-null-sink sink_name=other
"""
    assert find_feeder_null_sink_ids(modules) == ["1", "2"]
    sinks = "10\tmusync_feeder\tPipeWire\ts16le 2ch 48000Hz\n11\taluminum\t…"
    assert feeder_listed_in_sinks(sinks) is True
    assert feeder_listed_in_sinks("10\taluminum\tPipeWire\n") is False


def test_parse_wpctl_sources_and_classify() -> None:
    sources = parse_wpctl_sources(SAMPLE_WPCTL)
    bluez = next(s for s in sources if "bluez_input" in s.name)
    usb = next(s for s in sources if "Generic_USB_Audio" in s.name)
    hdmi = next(s for s in sources if "HDMI" in s.name)
    mic = next(s for s in sources if s.name == "Mic")
    assert bluez.mac == "AA:BB:CC:DD:EE:21"
    assert classify_capture_source(bluez) == "bt_a2dp"
    assert classify_capture_source(usb) == "usb"
    assert classify_capture_source(hdmi) == "hdmi"
    assert classify_capture_source(mic) == "network"
    assert is_capture_candidate(bluez)
    assert is_capture_candidate(usb)
    assert is_capture_candidate(hdmi)
    assert not is_capture_candidate(mic)
    assert looks_like_hdmi_capture("MACROSILICON HDMI")


def test_parse_alsa_usb_cards_and_merge() -> None:
    asound = """
 0 [Headphones     ]: bcm2835_headpho - bcm2835 Headphones
                      bcm2835 Headphones
 1 [CODEC          ]: USB-Audio - USB Audio CODEC
                      Generic USB Audio CODEC at usb-0000:01:00.0-1.2, full speed
 2 [MS2109         ]: USB-Audio - USB Video
                      MACROSILICON USB Video at usb-0000:01:00.0-1.3, high speed
"""
    cards = parse_proc_asound_cards(asound)
    assert len(cards) == 1
    assert cards[0].index == "1"
    assert "USB" in cards[0].name or "CODEC" in cards[0].id

    hdmi_cards = parse_proc_asound_hdmi_cards(asound)
    assert len(hdmi_cards) == 1
    assert hdmi_cards[0].index == "2"
    assert looks_like_hdmi_capture(f"{hdmi_cards[0].id} {hdmi_cards[0].name}")

    arecord = """
**** List of CAPTURE Hardware Devices ****
card 1: CODEC [USB Audio CODEC], device 0: USB Audio [USB Audio]
  Subdevices: 1/1
  Subdevice #0: subdevice #0
card 2: MS2109 [USB Video], device 0: USB Audio [USB Audio]
  Subdevices: 1/1
  Subdevice #0: subdevice #0
"""
    from_arecord = parse_arecord_l(arecord)
    assert len(from_arecord) == 1
    assert from_arecord[0].name == "USB Audio CODEC"
    from_hdmi = parse_arecord_l_hdmi(arecord)
    assert len(from_hdmi) == 1
    assert "MS2109" in from_hdmi[0].id or "Video" in from_hdmi[0].name

    synth = alsa_usb_to_pw_node(cards[0])
    assert synth.name.startswith("alsa_input.usb-")
    assert synth.id.startswith("alsa:usb:")
    assert classify_capture_source(synth) == "usb"

    hdmi_synth = alsa_hdmi_to_pw_node(hdmi_cards[0])
    assert hdmi_synth.id.startswith("alsa:hdmi:")
    assert classify_capture_source(hdmi_synth) == "hdmi"

    # Already present PipeWire USB node → do not duplicate.
    existing = [
        PwNode(
            id="62",
            name="alsa_input.usb-Generic_USB_Audio-00",
            description="USB Audio CODEC",
            media_class="Audio/Source",
        )
    ]
    merged = merge_alsa_usb_into_sources(existing, cards)
    assert len(merged) == 1

    # ALSA-only card fills gap when PipeWire has no USB yet.
    empty_merge = merge_alsa_usb_into_sources([], cards)
    assert len(empty_merge) == 1
    assert empty_merge[0].id.startswith("alsa:usb:")

    hdmi_merge = merge_alsa_hdmi_into_sources([], hdmi_cards)
    assert len(hdmi_merge) == 1
    assert hdmi_merge[0].id.startswith("alsa:hdmi:")


def test_build_capture_loopback_commands() -> None:
    cmds, notes = build_capture_loopback_commands(
        host_id="input-1",
        feeder="musync_feeder",
        source_ref="bluez_input.AA_BB_CC_DD_EE_21",
        tools={"pactl": "/usr/bin/pactl", "pw-link": None},
    )
    flat = [" ".join(c) for c in cmds]
    assert any("module-loopback" in line for line in flat)
    assert any("bluez_input.AA_BB_CC_DD_EE_21" in line for line in flat)
    assert any("sink=musync_feeder" in line for line in flat)
    assert notes

    usb_cmds, usb_notes = build_capture_loopback_commands(
        host_id="input-2",
        feeder="musync_feeder",
        source_ref="alsa_input.usb-Generic_USB_Audio-00",
        tools={"pactl": "/usr/bin/pactl", "pw-link": None},
    )
    usb_flat = [" ".join(c) for c in usb_cmds]
    assert any("alsa_input.usb-Generic_USB_Audio-00" in line for line in usb_flat)
    assert any("sink=musync_feeder" in line for line in usb_flat)
    assert usb_notes

    hdmi_cmds, hdmi_notes = build_capture_loopback_commands(
        host_id="input-3",
        feeder="musync_feeder",
        source_ref="alsa_input.usb-MACROSILICON_HDMI-00",
        tools={"pactl": "/usr/bin/pactl", "pw-link": None},
    )
    hdmi_flat = [" ".join(c) for c in hdmi_cmds]
    assert any("MACROSILICON_HDMI" in line for line in hdmi_flat)
    assert any("sink=musync_feeder" in line for line in hdmi_flat)
    assert hdmi_notes


def test_bluetoothctl_connect_success(monkeypatch) -> None:
    from types import SimpleNamespace

    def fake_run(args, **_kwargs):
        assert args[-2:] == ["connect", "AA:BB:CC:DD:EE:01"]
        return SimpleNamespace(returncode=0, stdout="Connection successful\n", stderr="")

    monkeypatch.setattr("musync.audio.pw_tools.run_cmd", fake_run)
    ok, detail = bluetoothctl_connect("/usr/bin/bluetoothctl", "aa:bb:cc:dd:ee:01")
    assert ok is True
    assert "successful" in detail.lower()


def test_bluetoothctl_connect_rejects_bad_mac() -> None:
    ok, detail = bluetoothctl_connect("/usr/bin/bluetoothctl", "not-a-mac")
    assert ok is False
    assert "invalid" in detail.lower()
