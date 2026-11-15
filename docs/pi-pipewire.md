# Pi PipeWire fan-out (Phase 1)

MuSync plays **one** local file into a virtual feeder sink, then fans out to
each enabled Bluetooth speaker with **per-sink delay** and **volume**.

## Graph

```
mpv local file  OR  bluez_input / USB capture (module-loopback)
        │
        ▼
  musync_feeder          (pactl module-null-sink)
        │
        │  module-loopback latency_msec=<delay_ms>
        ├────────────────► bluez_output.<MAC_1>   (BT600 hci0)
        └────────────────► bluez_output.<MAC_2>   (BT600 hci1)
                           … up to 6 (+ optional SPP TCP)
```

Phone as **A2DP source** (S21 FE → `bluez_input`) vs phone as **SPP sink**: see [phone-bt-source-vs-spp-sink.md](./phone-bt-source-vs-spp-sink.md).  
USB / PC capture (ALSA auto-detect → feeder): [usb-pc-capture.md](./usb-pc-capture.md).

| Control | Mechanism |
|---------|-----------|
| Delay | `pactl load-module module-loopback latency_msec=N` |
| Volume / mute | `wpctl set-volume` / `wpctl set-mute` |
| Enable/disable | Unload that sink's loopback only (others keep playing) |
| Discovery | `wpctl status` / `bluetoothctl` → UI by friendly name (MAC auto-filled) |
| Feeder | One `musync_feeder` null-sink — startup purges orphan `MuSync_Feeder` modules |
| Streaming | Spotify librespot → feeder; Apple best-effort AirPlay — [spotify-apple.md](spotify-apple.md) |

## Cloud vs Pi

| Environment | Behavior |
|-------------|----------|
| Cloud / no PipeWire | `PipeWireBackend` **dry-runs**: plans links, writes `config/pipewire-graph.plan.sh`, software transport clock |
| Pi with tools | Creates feeder, loads loopbacks, applies wpctl, plays via mpv into feeder |

Force plan-only: `MUSYNC_PW_DRY_RUN=1`.

## Bring-up: Qudelix + Phone SPP first (web UI — no YAML MAC edit)

Missing/broken `devices.yaml` is fine: the API boots with **Phone SPP** defaults and discovery. Configure sinks in the UI.

```bash
cd /path/to/MuSync
export MUSYNC_BACKEND=pipewire
./scripts/pair_bt600.sh hci0            # if not already paired
./scripts/build_web.sh                  # once, or rsync laptop build
# kill stray: pkill -f 'ffmpeg.*1234' ; pkill -f 'python3 -m musync'
./scripts/run_pi.sh
# Open http://<Pi-IP>:8080 → Device setup
#   Refresh discovery → enable Qudelix by name → enable Phone SPP
#   set port / sample rate / channels → Save sinks
```

1. Install packages (see README) — include `ffmpeg` + `pipewire-pulse` for SPP.
2. Build UI: `./scripts/build_web.sh` → `web/build/` (gitignored; copy from laptop OK).
3. Stabilize BT600 HCI — see [bt600-realtek.md](./bt600-realtek.md).
4. **Device setup** in the web UI: pick BT by friendly name (MAC filled from bluez/bluetoothctl), set sink type + SPP host/port/rate/channels, Save → atomic write to `devices.yaml`.
5. Play hub-local; phone SPP → Pi IP:port / configured rate & channels. Details: [spp-network-sink.md](./spp-network-sink.md).

Optional YAML templates (`config/devices.pi-2sink.example.yaml`) remain for advanced edits; they are not required for the demo path.

Scale to 4 then 6 by enabling more discovered BT sinks in the UI (≤6 A2DP) — same software path.

## Manual command cheat-sheet

```bash
pactl load-module module-null-sink sink_name=musync_feeder \
  sink_properties=device.description=MuSync_Feeder

pactl load-module module-loopback latency_msec=40 \
  source=musync_feeder.monitor sink=bluez_output.AA_BB_CC_DD_EE_02 \
  sink_dont_move=true source_dont_move=true

wpctl status
wpctl set-volume <ID> 0.8
wpctl set-mute <ID> 0

mpv --no-video --ao=pipewire --audio-device=musync_feeder ~/MuSync/media/track.flac
```
