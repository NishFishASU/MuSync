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
        ├────────────────► bluez_output.<MAC_1>   (HCI hci0 — onboard or USB)
        └────────────────► bluez_output.<MAC_2>   (HCI hci1 — USB dongle)
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
| Streaming | Spotify librespot → feeder; AirPlay receive (shairport) — [spotify-apple.md](spotify-apple.md), [airplay-receive.md](airplay-receive.md) |
| AVRCP | Absolute Volume → sliders; play/pause/skip via BlueZ `Media1.RegisterPlayer` (MPRIS) → hub transport (local/Spotify/YouTube); soft-fail without `python3-dbus` / bluetoothd |

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
./scripts/pair_hci.sh hci0              # if not already paired (generic BlueZ HCI)
./scripts/build_web.sh                  # once, or rsync laptop build
./scripts/run_pi.sh                     # clears stray ffmpeg; soft-reconnects paired BT
# Open http://<Pi-IP>:8080 → Device setup
#   Refresh → enable Qudelix by name → enable Phone SPP
#   set port / sample rate / channels → Save sinks
#   READY vs MISSING on sink pills; /api/health detail lists READY=/MISSING=
```

Demo path: [demo-checklist.md](demo-checklist.md).

1. Install packages (see README) — include `ffmpeg` + `pipewire-pulse` for SPP.
2. Build UI: `./scripts/build_web.sh` → `web/build/` (gitignored; copy from laptop OK).
3. Optional USB HCI chips (e.g. ASUS BT600 / RTL8761) — see [bt600-realtek.md](./bt600-realtek.md). MuSync itself is chip-agnostic.
4. **Device setup** in the web UI: pick BT by friendly name (MAC filled from bluez/bluetoothctl), set sink type + SPP host/port/rate/channels, Save → atomic write to `devices.yaml`. Adapter chips show onboard/USB · ready/in use/dead; assign HCI on Connect when multiple are free.
5. Play hub-local; phone SPP → Pi IP:port / configured rate & channels. Details: [spp-network-sink.md](./spp-network-sink.md).

Optional YAML templates (`config/devices.pi-2sink.example.yaml`) remain for advanced edits; they are not required for the demo path.

Scale to 4 then 6 by enabling more discovered BT sinks in the UI (≤6 A2DP) — same software path.
Onboard-only is fine (one A2DP sink); extra USB HCI dongles expand capacity when healthy.
Unhealthy USB HCI sticks are soft-skipped (short BlueZ timeouts) so they never block onboard scan/reconnect.

**Untested without hardware:** multi-dongle USB HCI A2DP is inventoried and soft-fail ready in software; live verification awaits a working dongle (BT600 was dead).

## Manual command cheat-sheet

```bash
pactl load-module module-null-sink sink_name=musync_feeder \
  rate=48000 channels=2 \
  sink_properties=device.description=MuSync_Feeder node.latency=512/48000

pactl load-module module-loopback latency_msec=40 max_latency_msec=40 \
  adjust_time=0 \
  source=musync_feeder.monitor sink=bluez_output.AA_BB_CC_DD_EE_02 \
  sink_dont_move=true source_dont_move=true

wpctl status
wpctl set-volume <ID> 0.8
wpctl set-mute <ID> 0

mpv --no-video --ao=pipewire --audio-device=musync_feeder ~/MuSync/media/track.flac

# Local video — same path (audio only into feeder; UI can show a muted preview)
mpv --no-video --ao=pipewire --audio-device=musync_feeder ~/MuSync/media/clip.mp4
```

## Local video

Drop `.mp4` / `.mkv` / `.webm` / `.mov` / `.m4v` / `.avi` under `media/` alongside audio.
The catalog lists them as **Local** with a **Video** chip. Play/pause/seek uses the same
transport; **mpv `--no-video`** extracts audio into `musync_feeder` so BT/SPP sinks stay in
sync. Optional muted HTML preview is served from `GET /api/media/<rel-path>`.
