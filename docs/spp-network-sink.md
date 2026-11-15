# SPP TCP network sink (Simple Protocol Player)

MuSync can fan the same PipeWire feeder out to a phone running **Simple Protocol Player** over TCP, alongside Qudelix / BT600 A2DP sinks.

## Verified standalone path (user-tested)

On the Pi (listens; phone connects):

```bash
ffmpeg -re -i media/test.wav -f s16le -acodec pcm_s16le -ar 48000 -ac 1 \
  tcp://0.0.0.0:1234?listen=1
```

In SPP on the phone:

| Setting | Value |
|---------|--------|
| Host | Pi Tailscale or LAN IP |
| Port | `1234` |
| Format | PCM s16le |
| Rate | `48000` |
| Channels | `1` (mono) |

## MuSync integration

1. Seed from the Pi example (includes `spp-1` already):

```bash
cp config/devices.pi-2sink.example.yaml config/devices.yaml
# edit Qudelix MAC; leave spp-1 enabled:
#   kind: network_spp
#   listen_port: 1234
#   sample_rate: 48000
#   channels: 1
export MUSYNC_DEVICES_CONFIG="$PWD/config/devices.yaml"
export MUSYNC_BACKEND=pipewire
```

   Mock cloud defaults also include `spp-1` on port `1234` when no yaml exists.
2. Set `delay_ms` / `volume` like any other sink in the web UI.
3. With `MUSYNC_BACKEND=pipewire`, MuSync starts (while `spp-1` is **enabled** — not only on Play):

```text
ffmpeg -f pulse -i musync_feeder.monitor -af "adelay=…,volume=…" \
  -f s16le -acodec pcm_s16le -ar 48000 -ac 1 \
  tcp://0.0.0.0:1234?listen=1
```

Bluez loopbacks for Qudelix / BT600 sinks are unchanged — SPP is an extra fan-out leg from the same feeder.

## Pi + Qudelix + SPP phone

```bash
cp config/devices.pi-2sink.example.yaml config/devices.yaml
nano config/devices.yaml   # real Qudelix MAC; spp-1 enabled: true
export MUSYNC_DEVICES_CONFIG="$PWD/config/devices.yaml"
export MUSYNC_BACKEND=pipewire
# stop manual ffmpeg listen / old MuSync if any
./scripts/run_pi.sh

# UI: enable Qudelix + Phone SPP, Play a hub-local track
# Phone: open SPP → Pi LAN/Tailscale IP:1234 → 48000 mono s16le
```

Notes:

- Listener starts when the SPP sink is **enabled** (backend boot / enable toggle) — **not** only on Play. Phone can connect before Play.
- **Play does not restart** a healthy ffmpeg listen (restarting would drop the TCP session). Dead listeners are revived; changing port / rate / channels / delay / volume / mute still restarts that process only.
- Format must match the device row: default **48000 Hz / 1 ch / s16le**, port **1234**, host = Pi LAN or Tailscale IP (not `0.0.0.0` in the phone app).
- Requires PipeWire pulse compat (`pipewire-pulse`) so ffmpeg can read `musync_feeder.monitor`, plus `ffmpeg` with `--enable-libpulse`. Dry-run (`MUSYNC_PW_DRY_RUN=1`) logs the command without binding the port.
- If the phone connects while a **manual** `ffmpeg …?listen=1` is still bound to 1234, MuSync cannot bind — stop the manual listener first.
