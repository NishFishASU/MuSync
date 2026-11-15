# Uno Q audio backend (stub)

**Reference implementation:** `backend/musync/audio/pipewire.py` (Pi).

## Intent

Provide an `AudioBackend` that:

1. Builds one feeder → N delayed/volumed sinks on Uno Q’s PipeWire graph.
2. Enumerates BlueZ A2DP sinks (onboard first, then USB HCI when proven).
3. Soft-fails missing adapters (same resilience pattern as Pi).
4. Does **not** assume HSP/HFP or phone-as-BT-mic on onboard radio.

## Port checklist

- [ ] Reuse `pw_tools.py` parsers where CLI output matches
- [ ] Confirm `wpctl` / `pactl` node naming on Uno Q Debian
- [ ] Headless BlueZ seat-monitoring + linger (see `../scripts/configure_headless_bt_audio.sh`)
- [ ] Local file playback into feeder (ffplay/mpv/pw-play — host packages, not App Lab)
- [ ] `PATCH` delay_ms / volume apply latency acceptable on-device
- [ ] Gate multi-HCI: document max stable dongle count

`backend.py` in this folder is a **skeleton only** — not imported by the main app until wired behind `MUSYNC_BACKEND`.
