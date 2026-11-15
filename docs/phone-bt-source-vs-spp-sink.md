# Phone as BT A2DP source vs SPP network sink

Two different phone roles on MuSync — do not confuse them.

| Role | Direction | PipeWire / path | Phone UI |
|------|-----------|-----------------|----------|
| **BT A2DP source (input)** | Phone → Pi → speakers | `bluez_input.*` → `module-loopback` → `musync_feeder` → sinks | Phone **media output** to Pi (Pi appears as a Bluetooth speaker / car audio) |
| **SPP network sink (output)** | Pi → Phone | `musync_feeder.monitor` → ffmpeg TCP listen → phone | [Simple Protocol Player](./spp-network-sink.md) connects to Pi IP:port |

```
                    ┌── BT A2DP SOURCE (this doc) ──┐
 Phone media ──────► bluez_input ──► musync_feeder ─┼──► Qudelix / USB HCI sinks
                                                    └──► SPP TCP ──► other phone (SPP sink)
```

## Pair phone as A2DP source (Galaxy S21 FE)

Goal: S21 FE shows up as `bluez_input…` on the Pi, MuSync routes it into the feeder, and enabled sinks hear the phone.

1. On the Pi, ensure PipeWire + bluetooth are up (`wpctl status`, `bluetoothctl`).
2. Put the S21 FE next to the Pi. Prefer pairing on an adapter that is **not** fully occupied by A2DP **sinks** (onboard `hci` or a spare USB HCI). Using all six HCI adapters as outbound sinks leaves no radio for inbound A2DP — then use USB or SPP instead.
3. From the Pi:

```bash
bluetoothctl
# power on / agent on / default-agent
scan on
# wait for "Galaxy S21 FE" (or similar)
pair <MAC>
trust <MAC>
connect <MAC>
```

4. On the phone: Bluetooth → MuSync Pi / adapter name → use it as the **audio output** (media audio). Start playback on the phone (Spotify, YouTube, Files, …).
5. Confirm the source:

```bash
wpctl status
# Sources: … bluez_input.XX_XX_XX_XX_XX_XX …
pactl list sources short | grep -i bluez
```

6. MuSync UI → **Phone / PC hosts** → **Refresh live inputs** → **Use as input** on the S21 FE row (binds `bt_a2dp` + endpoint).
7. Song queue → **+ Live input** (or set a row’s type to Live input + that host) → **Play**.
8. Enabled Qudelix / USB HCI / SPP sinks receive the phone audio from the same feeder fan-out.

Manual loopback (debug without UI):

```bash
pactl load-module module-null-sink sink_name=musync_feeder \
  rate=48000 channels=2 \
  sink_properties=device.description=MuSync_Feeder node.latency=512/48000

pactl load-module module-loopback latency_msec=10 max_latency_msec=10 adjust_time=0 \
  source=bluez_input.XX_XX_XX_XX_XX_XX sink=musync_feeder \
  sink_dont_move=true source_dont_move=true
```

## SPP sink (phone hears the hub)

Opposite direction: hub plays a local track (or live capture) and the phone **listens**. See [spp-network-sink.md](./spp-network-sink.md).

- Enable **Phone SPP** in Device setup, Save.
- On the phone: Simple Protocol Player → Pi LAN/Tailscale IP, port `1234`, 48000 Hz, mono s16le.
- That phone is an **output**, not the capture source.

## When to use which

| Need | Use |
|------|-----|
| Phone app audio on all speakers | **BT A2DP source** (`bluez_input` → feeder) |
| Extra earpiece / check sync on phone | **SPP sink** (feeder → TCP → SPP) |
| PC line-in | **USB** capture path — [usb-pc-capture.md](./usb-pc-capture.md) |
| No BT radio left for inbound A2DP | Upload file on a **network** host (plays into feeder) or SPP-only demo |

## Simultaneous phone speakers + hub

If the phone’s **own** speakers keep playing while hub headphones/SPP also play:

1. **Spotify from MuSync Queue** — Connect must transfer to the Pi librespot device (`MuSync`). MuSync refuses Web API play without that device so the phone is not left as the active player. See [spotify-apple.md](./spotify-apple.md#phone-speakers-vs-hub-exclusive--duck).
2. **Local file / YouTube on the hub** — MuSync soft-pauses Spotify on the linked account. It **cannot** mute YouTube Music, Files, or other apps; stop those on the phone.
3. **Phone as A2DP source** — set the phone’s media output to the Pi (Bluetooth speaker). If media still uses phone speakers, Android/iOS routing is wrong — MuSync cannot force exclusive audio from the browser SPA.
4. **SPP sink** — Simple Protocol Player is an *output*; turn down/mute the phone’s other media so you only hear SPP.

## Mock / laptop

Without PipeWire, the mock backend seeds **Galaxy S21 FE** as a live `bt_a2dp` host (`bluez_input.AA_BB_CC_DD_EE_21` fixture) so the UI can select live input and Play. Capture discovery returns the same fixture names.
