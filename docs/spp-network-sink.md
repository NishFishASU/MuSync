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

1. Add (or enable) a device with `kind: network_spp` in `config/devices.yaml` — mock defaults include `spp-1` on port `1234`.
2. Set `delay_ms` / `volume` like any other sink in the web UI.
3. With `MUSYNC_BACKEND=pipewire`, MuSync starts:

```text
ffmpeg -f pulse -i musync_feeder.monitor -af "adelay=…,volume=…" \
  -f s16le -acodec pcm_s16le -ar 48000 -ac 1 \
  tcp://0.0.0.0:1234?listen=1
```

Bluez loopbacks for Qudelix / BT600 sinks are unchanged — SPP is an extra fan-out leg from the same feeder.

## Pi + Qudelix + SPP phone

```bash
# devices.yaml: at least one bluetooth sink (Qudelix) + spp-1
export MUSYNC_BACKEND=pipewire
./scripts/run_pi.sh   # or: PYTHONPATH=backend python3 -m musync

# UI: enable Qudelix + Phone SPP, set relative delays, Play a local track
# Phone: open SPP → Pi IP:1234 → 48000 mono
```

Notes:

- Listener starts when the SPP sink is **enabled** (backend boot / enable toggle) — **not** only on Play. Phone can connect before Play.
- **Play does not restart** a healthy ffmpeg listen (restarting would drop the TCP session). Dead listeners are revived; changing port / rate / channels / delay / volume / mute still restarts that process only.
- Format must match the device row: default **48000 Hz / 1 ch / s16le**, port **1234**, host = Pi LAN or Tailscale IP (not `0.0.0.0` in the phone app).
- Requires PipeWire pulse compat (`pipewire-pulse`) so ffmpeg can read `musync_feeder.monitor`, plus `ffmpeg` with `--enable-libpulse`. Dry-run (`MUSYNC_PW_DRY_RUN=1`) logs the command without binding the port.
- If the phone connects while a **manual** `ffmpeg …?listen=1` is still bound to 1234, MuSync cannot bind — stop the manual listener first.
