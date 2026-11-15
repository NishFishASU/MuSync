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

- Listener stays up while the SPP sink is **enabled** (phone can connect before Play).
- Changing port / rate / channels / delay / volume restarts that ffmpeg process only.
- Install `ffmpeg` on the Pi. Dry-run (`MUSYNC_PW_DRY_RUN=1`) logs the command without binding the port.
