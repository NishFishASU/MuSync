# MuSync

Multi-room Bluetooth audio sync on a **Raspberry Pi 5**: one local-file transport fans out through **PipeWire** to up to **6× ASUS BT600** A2DP sinks, with per-sink delay/volume from a responsive web UI on the LAN.

Cloud/dev uses **MockAudioBackend**; the Pi uses **PipeWireBackend** (feeder → per-sink delay/volume → bluez).

## Quick start (laptop / cloud)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Mock backend (default) — no PipeWire or BT hardware required
./scripts/run_dev.sh
# → http://127.0.0.1:8080
```

```bash
export PYTHONPATH=backend
export MUSYNC_BACKEND=mock
python3 -m musync
```

### Tests

```bash
pip install -r requirements.txt
pytest
```

## Layout

```
backend/musync/audio/
  base.py           # AudioBackend interface
  mock.py           # Fake ≤6 sinks + local playback clock
  pipewire.py       # Feeder → loopback delay → bluez fan-out
  pw_tools.py       # wpctl/pactl parsers + graph plan helpers
web/                # Static responsive UI (no Node build)
config/
  devices.pi-2sink.example.yaml   # start here on Pi
  devices.example.yaml            # full 6-sink template
docs/pi-pipewire.md
scripts/run_dev.sh  run_pi.sh  pair_bt600.sh
```

## API (minimum)

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/api/health` | Backend, PipeWire up?, mapped sinks, graph dry-run |
| `GET` | `/api/devices` | List sinks |
| `PATCH` | `/api/devices/{id}` | `{ delay_ms?, volume?, mute?, enabled?, pw_node? }` |
| `GET`/`PUT` | `/api/transport` | `{ state, position_ms, source_id? }` |
| `GET` | `/api/sources` | Local files + disabled later stubs |
| `PUT` | `/api/sources/active` | `{ id }` source picker |

## Mock vs PipeWire

| | **Mock** (`MUSYNC_BACKEND=mock`) | **PipeWire** (`MUSYNC_BACKEND=pipewire`) |
|--|----------------------------------|------------------------------------------|
| Where | Cloud / laptop | Raspberry Pi 5 |
| Sinks | In-memory fake devices (≤6) | Same model; maps MAC/`pw_node` → WirePlumber ids |
| Delay | Stored on device object | `pactl module-loopback latency_msec=N` from feeder |
| Volume/mute | In-memory | `wpctl set-volume` / `set-mute` |
| Playback | Software clock | `mpv --ao=pipewire --audio-device=musync_feeder` |
| Without hardware | Always | **Dry-run**: writes `config/pipewire-graph.plan.sh` |

## Run on Raspberry Pi 5 — 2 speakers first

Do **not** start with six dongles. Prove sync with two, then scale.

### 1. Packages

```bash
sudo apt update
sudo apt install -y python3-pip python3-venv pipewire wireplumber \
  pipewire-audio-client-libraries pulseaudio-utils mpv bluez
git clone https://github.com/NishFishASU/MuSync.git
cd MuSync
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Seed a 2-sink profile

```bash
cp config/devices.pi-2sink.example.yaml config/devices.yaml
```

`run_pi.sh` will also auto-seed this file if `devices.yaml` is missing.

### 3. Pair two BT600 ↔ speaker pairs

Plug **two** ASUS BT600 adapters (they should show up as `hci0` and `hci1`):

```bash
./scripts/pair_bt600.sh hci0    # scan, enter speaker A MAC
./scripts/pair_bt600.sh hci1    # speaker B
```

Edit `config/devices.yaml` with the real MACs (and names). Keep `enabled: true` on both.

Confirm sinks:

```bash
wpctl status | sed -n '/Sinks:/,/Sources:/p'
# look for bluez_output.AA_BB_…
```

### 4. Media + launch

```bash
cp /path/to/track.flac media/
./scripts/run_pi.sh
# UI: http://<pi-lan-ip>:8080
```

In the UI: select both sinks, set relative `delay_ms` until slap-back disappears, Play.

Graph details and manual `pactl`/`wpctl` commands: [docs/pi-pipewire.md](docs/pi-pipewire.md).

### 5. Scale to 4 then 6

Append sinks to `devices.yaml` (unique `id`, `mac`, `hci`). Use a **powered USB hub**. Same software path — no redesign. Pair each new dongle with `./scripts/pair_bt600.sh hciN`.

### Optional systemd

Edit user/paths in `deploy/systemd/musync.service`, then:

```bash
sudo cp deploy/systemd/musync.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now musync
```

### ASUS BT600 notes

- One A2DP sink per adapter ⇒ six speakers need six BT600s (`hci0`…`hci5`).
- Powered hub strongly recommended beyond two dongles.
- Prefer the same codec (SBC or AAC) across sinks.
- Onboard Pi Bluetooth is unused for sinks; BT600s own the links.
- Losing one sink should not stop the others (MuSync unloads only that sink's loopback).

## Environment variables

| Variable | Default | Meaning |
|----------|---------|---------|
| `MUSYNC_BACKEND` | `mock` | `mock` or `pipewire` |
| `MUSYNC_HOST` | `0.0.0.0` (`127.0.0.1` in `run_dev.sh`) | Bind address |
| `MUSYNC_PORT` | `8080` | HTTP port |
| `MUSYNC_DEVICES_CONFIG` | `config/devices.yaml` | Device profile path |
| `MUSYNC_MEDIA_DIR` | `media/` | Local library root |
| `MUSYNC_WEB_DIR` | `web/` | Static UI root |
| `MUSYNC_PW_FEEDER` | `musync_feeder` | Virtual sink name for fan-out |
| `MUSYNC_PW_DRY_RUN` | auto | `1` force plan-only; `0` force live CLI; unset = dry-run when PipeWire down |
| `MUSYNC_RELOAD` | unset | Set `1` for uvicorn reload |

## Out of scope (this MVP)

Spotify / Apple Music, phone/PC network inputs, movie/HDMI capture, HDMI-local UI, Uno Q / Grove, wired outs, guided clap calibration.

## License

Private.
