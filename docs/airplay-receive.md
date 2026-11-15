# AirPlay receive → musync_feeder

Stream audio from an iPhone or Mac into the Pi hub via **AirPlay receive**, then
fan out through the same PipeWire graph as local files / Spotify / BT A2DP / USB /
HDMI.

There is **no Apple Music catalog search** (MusicKit) on the hub. Apple devices
AirPlay *to* the Pi; MuSync loopbacks that PCM into `musync_feeder`.

```
iPhone / Mac ──AirPlay──► shairport-sync ──► PipeWire Source ──► module-loopback
                                                                      │
                                                                      ▼
                                                                musync_feeder
                                                                      │
                                                        ┌─────────────┼─────────────┐
                                                        ▼             ▼             ▼
                                                   Qudelix / USB HCI …         Phone SPP
```

## Install shairport-sync (Pi)

Use a build that talks to **PipeWire or Pulse** so a capture **Source** appears
(`wpctl status` → Sources). Package names vary by distro:

```bash
# Example — prefer a PipeWire-aware package when available
sudo apt update
sudo apt install -y shairport-sync
# or build from https://github.com/mikebrady/shairport-sync with
# --with-pipewire / --with-pulseaudio as appropriate
```

Advertise a clear receiver name (matches UI / `MUSYNC_AIRPLAY_NAME`, default **MuSync**):

```bash
# /etc/shairport-sync.conf (snippet)
general = {
  name = "MuSync";
};
```

Restart the service, then confirm a PipeWire source exists:

```bash
wpctl status   # look under Sources for shairport / AirPlay / raop
pactl list sources short
```

## Optional env override

If auto-discovery misses the node, set the exact PipeWire source name:

```bash
# ~/musync.env
export MUSYNC_AIRPLAY_SOURCE="shairport-sync"   # exact Source name
# export MUSYNC_AIRPLAY_NAME=MuSync              # docs / UI only
# Legacy alias still accepted:
# export MUSYNC_APPLE_AIRPLAY_SOURCE="shairport-sync"
```

`./scripts/run_pi.sh` auto-loads `~/musync.env`.

## UI / API

1. `./scripts/run_pi.sh` → open the hub UI.
2. **Streaming** — AirPlay card shows setup / ready / receiving (not MusicKit).
3. **Inputs** → **Refresh inputs** — classifies `capture_path=airplay`, auto-binds
   onto the **AirPlay Receive** host (or creates one, ≤4 hosts).
4. Song queue → **+ Device** / **+ Live** (or pick the AirPlay host) → **Play**.
5. On iPhone/Mac: AirPlay audio to **MuSync** (or your configured name).

`GET /api/streaming` returns `airplay: { configured, receiving, source, setup, note }`.
`GET /api/inputs/discover` lists AirPlay sources alongside BT / USB / HDMI.

## Manual loopback (debug)

```bash
pactl load-module module-null-sink sink_name=musync_feeder \
  rate=48000 channels=2 \
  sink_properties=device.description=MuSync_Feeder node.latency=512/48000

pactl load-module module-loopback latency_msec=10 max_latency_msec=10 adjust_time=0 \
  source=shairport-sync sink=musync_feeder \
  sink_dont_move=true source_dont_move=true
```

## Mock / laptop

Without PipeWire, the mock backend seeds **AirPlay Receive** as a live
`airplay` host (`shairport-sync`) so the Inputs / Streaming UI can be exercised.

## Related

- Spotify Connect: [spotify-apple.md](./spotify-apple.md)
- HDMI capture: [hdmi-capture.md](./hdmi-capture.md)
- USB / PC capture: [usb-pc-capture.md](./usb-pc-capture.md)
- Feeder fan-out: [pi-pipewire.md](./pi-pipewire.md)
