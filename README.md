# MuSync

Multi-room Bluetooth audio sync on a **Raspberry Pi 5**: one local-file transport fans out through **PipeWire** to up to **6× ASUS BT600** A2DP sinks, with per-sink delay/volume from a responsive web UI on the LAN.

Phase 0 MVP scaffold — cloud/dev runs on a **MockAudioBackend**; the Pi swaps in **PipeWireBackend**.

## Quick start (laptop / cloud)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Mock backend (default) — no PipeWire or BT hardware required
./scripts/run_dev.sh
# → http://127.0.0.1:8080
```

Or:

```bash
export PYTHONPATH=backend
export MUSYNC_BACKEND=mock
python3 -m musync
```

Open the UI on a phone or PC on the same network (bind `MUSYNC_HOST=0.0.0.0` if needed).

### Tests

```bash
pip install -r requirements.txt
pytest
```

## Layout

```
backend/musync/          # FastAPI app + audio backends
  audio/base.py          # AudioBackend interface
  audio/mock.py          # Fake ≤6 sinks + local playback clock
  audio/pipewire.py      # Pi-ready wpctl/pw-cli hooks + docs
web/                     # Static responsive UI (no Node build)
config/devices.example.yaml
media/                   # Drop WAV/FLAC/MP3 here
deploy/systemd/musync.service
```

## API (minimum)

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/api/health` | Backend name, PipeWire up?, sink count, HCI map |
| `GET` | `/api/devices` | List sinks |
| `PATCH` | `/api/devices/{id}` | `{ delay_ms?, volume?, mute?, enabled? }` |
| `GET`/`PUT` | `/api/transport` | `{ state, position_ms, source_id? }` |
| `GET` | `/api/sources` | Local files + disabled later stubs |
| `PUT` | `/api/sources/active` | `{ id }` source picker |

## Mock vs PipeWire

| | **MockAudioBackend** (`MUSYNC_BACKEND=mock`) | **PipeWireBackend** (`MUSYNC_BACKEND=pipewire`) |
|--|-----------------------------------------------|--------------------------------------------------|
| Where | Cloud / laptop | Raspberry Pi 5 |
| Sinks | In-memory fake devices (≤6) from config/defaults | Same device model; applies volume/mute/delay via `wpctl` / `pw-cli` when node IDs are mapped |
| Playback | Software clock (position advances; no real audio) | Same transport API; starts `mpv --ao=pipewire` or `pw-play` when tools + file path exist |
| Graph | N/A | Documented feeder → delay → volume → `bluez_output.*` hooks in `pipewire.py` |
| Health | `pipewire_up: false` | Probes `wpctl status` / `pw-cli` |

Switch:

```bash
export MUSYNC_BACKEND=mock      # default
export MUSYNC_BACKEND=pipewire  # Pi
```

## Run on Raspberry Pi 5

1. **OS:** Raspberry Pi OS (Bookworm+) with PipeWire + WirePlumber.
2. **Install deps:**
   ```bash
   sudo apt update
   sudo apt install -y python3-pip python3-venv pipewire wireplumber \
     pipewire-audio-client-libraries mpv bluez
   git clone https://github.com/NishFishASU/MuSync.git
   cd MuSync
   python3 -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   cp config/devices.example.yaml config/devices.yaml
   ```
3. **BT600 setup (see below)** — pair speakers, fill MAC ↔ `hciN` in `config/devices.yaml`.
4. **Media:** copy tracks into `media/` (WAV/FLAC/MP3).
5. **Run:**
   ```bash
   ./scripts/run_pi.sh
   # UI: http://<pi-lan-ip>:8080
   ```
6. **Optional systemd** (edit paths/user in the unit first):
   ```bash
   sudo cp deploy/systemd/musync.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now musync
   ```

### ASUS BT600 notes

- Linux typically allows **one A2DP sink per Bluetooth adapter**. Six speakers ⇒ **six BT600 dongles** (`hci0`…`hci5`).
- Use a **powered USB hub** for 4–6 dongles; unpowered hubs often flake on power/bandwidth.
- Bring up in stages: **2 → 4 → 6**. Persist each speaker MAC with its HCI index in `devices.yaml`.
- Prefer the **same codec** (SBC or AAC) across sinks to reduce relative drift.
- Onboard Pi Bluetooth is unused for sinks in this design; dedicated BT600s own the A2DP links.
- Pairing sketch per dongle:
  ```bash
  bluetoothctl select <adapter-mac>
  bluetoothctl power on
  bluetoothctl scan on
  bluetoothctl pair <speaker-mac>
  bluetoothctl trust <speaker-mac>
  bluetoothctl connect <speaker-mac>
  ```
- After connect, WirePlumber should expose a `bluez_output.*` PipeWire sink. Phase 0 still needs the graph wiring on-device (`PipeWireBackend._ensure_graph` / delay nodes) — the HTTP + UI path is ready.

## Environment variables

| Variable | Default | Meaning |
|----------|---------|---------|
| `MUSYNC_BACKEND` | `mock` | `mock` or `pipewire` |
| `MUSYNC_HOST` | `0.0.0.0` (`127.0.0.1` in `run_dev.sh`) | Bind address |
| `MUSYNC_PORT` | `8080` | HTTP port |
| `MUSYNC_DEVICES_CONFIG` | `config/devices.yaml` | Device profile path |
| `MUSYNC_MEDIA_DIR` | `media/` | Local library root |
| `MUSYNC_WEB_DIR` | `web/` | Static UI root |
| `MUSYNC_RELOAD` | unset | Set `1` for uvicorn reload |

## Out of scope (this MVP)

Spotify / Apple Music, phone/PC network inputs, movie/HDMI capture, HDMI-local UI, Uno Q / Grove, wired outs, guided clap calibration.

## License

Private — SunHacks / MuSync.
