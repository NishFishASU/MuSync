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

## Bring-up: Qudelix + Phone SPP first

Symptom of wrong config: UI shows **Speaker A/B**, `AA:BB:…` **MISSING**, no **Phone SPP** — Play never reaches SPP/Qudelix.

```bash
cd /path/to/MuSync
cp config/devices.pi-2sink.example.yaml config/devices.yaml
bluetoothctl devices                    # note Qudelix MAC
nano config/devices.yaml                # set sink-1 mac; keep spp-1 enabled
export MUSYNC_DEVICES_CONFIG="$PWD/config/devices.yaml"
export MUSYNC_BACKEND=pipewire
./scripts/pair_bt600.sh hci0            # if not already paired
./scripts/build_web.sh                  # once, or rsync laptop build
# kill stray: pkill -f 'ffmpeg.*1234' ; pkill -f 'python3 -m musync'
./scripts/run_pi.sh
```

1. Install packages (see README) — include `ffmpeg` + `pipewire-pulse` for SPP.
2. Build UI: `./scripts/build_web.sh` → `web/build/` (gitignored; copy from laptop OK).
3. Stabilize BT600 HCI — see [bt600-realtek.md](./bt600-realtek.md).
4. Seed/edit `devices.yaml` as above (**real** Qudelix MAC, `spp-1` `kind: network_spp` `enabled: true`).
5. UI: enable **Qudelix** + **Phone SPP**, Play hub-local; phone SPP → Pi IP:1234 / 48000 mono. Details: [spp-network-sink.md](./spp-network-sink.md).

Scale to 4 then 6 by appending BT sinks (unique `id` / `mac` / `hci`) — same software path.

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
