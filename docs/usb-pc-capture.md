# USB / PC audio → musync_feeder

Plug a **USB-A sound card / audio interface** into the Pi and route it into the
same feeder fan-out as local files / BT A2DP input / Spotify.

**Pi 5 USB-C PD is power-only.** Connecting a laptop to the Pi’s USB-C PD port
does **not** carry audio. Use a USB-A (or hub) sound card / UAC interface — never
claim PD audio works.

```
PC / USB DAC ──► ALSA (hw:N) ──► PipeWire alsa_input.usb-… ──► module-loopback
                                                              │
                                                              ▼
                                                        musync_feeder
                                                              │
                                              ┌───────────────┼───────────────┐
                                              ▼               ▼               ▼
                                         Qudelix / USB HCI …            Phone SPP
```

## Auto-detect (preferred)

On **Refresh inputs** / `GET /api/inputs/discover` (and when listing inputs):

1. Scan PipeWire `Audio/Source` nodes (`wpctl status` / `pw-cli`).
2. Scan ALSA USB cards (`/proc/asound/cards`, fallback `arecord -l`).
3. Classify `alsa_input.usb-*` (and ALSA-only USB cards) as `capture_path=usb`.
4. **Auto-bind** onto a waiting USB/PC host, or create a PC host (≤4 total).

Manual **Use** / **Re-bind** in the UI still works if you want a specific card.

## Pi bring-up

1. Plug the USB-A audio interface into the Pi (USB2 port preferred on busy hubs).
   Do **not** use the USB-C PD power port for audio.
2. Confirm ALSA sees it:

```bash
cat /proc/asound/cards
arecord -l
wpctl status   # Sources: … alsa_input.usb-… …
```

3. `./scripts/run_pi.sh` → UI → **Phone / PC hosts** → **Refresh inputs**.
4. USB row should show **live** with an `alsa_input.usb-…` endpoint (auto-bound).
5. Song queue → **+ Live input** (or pick the USB host’s live catalog row) → **Play**.
6. Enabled sinks hear the USB/PC capture from `musync_feeder`.

Manual loopback (debug without UI):

```bash
pactl load-module module-null-sink sink_name=musync_feeder \
  rate=48000 channels=2 \
  sink_properties=device.description=MuSync_Feeder node.latency=512/48000

pactl load-module module-loopback latency_msec=10 max_latency_msec=10 adjust_time=0 \
  source=alsa_input.usb-Generic_USB_Audio-00 sink=musync_feeder \
  sink_dont_move=true source_dont_move=true
```

## Mock / laptop

Without PipeWire, the mock backend seeds **USB Audio CODEC** as a live `usb`
host (`alsa_input.usb-Generic_USB_Audio-00`) alongside Galaxy S21 FE, so the UI
can select USB live input and Play.

## Related

- HDMI / capture card: [hdmi-capture.md](./hdmi-capture.md)
- Phone as BT A2DP **source**: [phone-bt-source-vs-spp-sink.md](./phone-bt-source-vs-spp-sink.md)
- Feeder fan-out: [pi-pipewire.md](./pi-pipewire.md)
