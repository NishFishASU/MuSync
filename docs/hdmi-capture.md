# HDMI / capture card → musync_feeder

Plug an **HDMI capture dongle** (or other capture card that exposes ALSA audio)
into the Pi and route it into the same feeder fan-out as local files / BT A2DP /
USB PC audio.

Audio only — picture stays on the capture display / local video path. No
HDMI-local UI is required.

```
HDMI source ──► capture dongle ──► ALSA (hw:N) ──► PipeWire alsa_input.… ──► module-loopback
                                                                                 │
                                                                                 ▼
                                                                           musync_feeder
                                                                                 │
                                                                 ┌───────────────┼───────────────┐
                                                                 ▼               ▼               ▼
                                                            Qudelix / BT600   …            Phone SPP
```

Common USB HDMI capture chips (MS2109 / Macrosilicon, Elgato, AverMedia, …)
appear as USB-Audio / USB-Video cards. MuSync classifies those as
`capture_path=hdmi` (before generic USB) when labels match HDMI/capture markers.

## Auto-detect (preferred)

On **Refresh inputs** / `GET /api/inputs/discover` (and when listing inputs):

1. Scan PipeWire `Audio/Source` nodes (`wpctl status` / `pw-cli`).
2. Scan ALSA cards (`/proc/asound/cards`, fallback `arecord -l`) for HDMI/capture.
3. Classify matching sources as `capture_path=hdmi`.
4. **Auto-bind** onto a waiting HDMI host, or create an `other` host (≤4 total).

Manual **Use** / **Re-bind** in the UI still works if you want a specific card.

## Pi bring-up

1. Plug the HDMI capture dongle into the Pi (USB-A / hub). Feed HDMI into the dongle.
2. Confirm ALSA / PipeWire see it:

```bash
cat /proc/asound/cards
arecord -l
wpctl status   # Sources: … alsa_input.…HDMI… or MS2109 / Macrosilicon …
```

3. `./scripts/run_pi.sh` → UI → **Inputs** → **Refresh inputs**.
4. HDMI row should show **live** with an `alsa_input.…` endpoint (auto-bound).
5. Song queue → **+ Live input** (or pick the HDMI host’s live catalog row) → **Play**.
6. Enabled sinks hear the HDMI/capture audio from `musync_feeder`.

Manual loopback (debug without UI):

```bash
pactl load-module module-null-sink sink_name=musync_feeder \
  sink_properties=device.description=MuSync_Feeder

pactl load-module module-loopback latency_msec=20 \
  source=alsa_input.usb-MACROSILICON_HDMI-00 sink=musync_feeder \
  sink_dont_move=true source_dont_move=true
```

## Mock / laptop

Without PipeWire, the mock backend seeds **HDMI Capture Card** as a live `hdmi`
host (`alsa_input.usb-MACROSILICON_HDMI-00`) alongside Galaxy S21 FE and USB
Audio, so the UI can select HDMI live input and Play.

## Related

- USB / PC capture: [usb-pc-capture.md](./usb-pc-capture.md)
- Phone as BT A2DP **source**: [phone-bt-source-vs-spp-sink.md](./phone-bt-source-vs-spp-sink.md)
- Feeder fan-out: [pi-pipewire.md](./pi-pipewire.md)
