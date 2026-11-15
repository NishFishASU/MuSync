# Pi PipeWire fan-out (Phase 1)

MuSync plays **one** local file into a virtual feeder sink, then fans out to
each enabled Bluetooth speaker with **per-sink delay** and **volume**.

## Graph

```
mpv --ao=pipewire --audio-device=musync_feeder
        │
        ▼
  musync_feeder          (pactl module-null-sink)
        │
        │  module-loopback latency_msec=<delay_ms>
        ├────────────────► bluez_output.<MAC_1>   (BT600 hci0)
        └────────────────► bluez_output.<MAC_2>   (BT600 hci1)
                           … up to 6
```

| Control | Mechanism |
|---------|-----------|
| Delay | `pactl load-module module-loopback latency_msec=N` |
| Volume / mute | `wpctl set-volume` / `wpctl set-mute` |
| Enable/disable | Unload that sink's loopback only (others keep playing) |
| Discovery | `wpctl status` / `pw-cli ls Node` matched to MAC / `pw_node` |

## Cloud vs Pi

| Environment | Behavior |
|-------------|----------|
| Cloud / no PipeWire | `PipeWireBackend` **dry-runs**: plans links, writes `config/pipewire-graph.plan.sh`, software transport clock |
| Pi with tools | Creates feeder, loads loopbacks, applies wpctl, plays via mpv into feeder |

Force plan-only: `MUSYNC_PW_DRY_RUN=1`.

## Bring-up: 2 speakers first

1. Install packages (see README).
2. `cp config/devices.pi-2sink.example.yaml config/devices.yaml`
3. `./scripts/pair_bt600.sh hci0` then `./scripts/pair_bt600.sh hci1`
4. Write real MACs into `devices.yaml`
5. `./scripts/run_pi.sh`
6. UI: enable both sinks, nudge `delay_ms` until slap-back disappears, Play

Scale to 4 then 6 by appending sinks (unique `id` / `mac` / `hci`) — same software path.

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
