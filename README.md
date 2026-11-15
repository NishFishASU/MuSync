# MuSync

Multi-room Bluetooth audio sync on a **Raspberry Pi 5**: one local-file transport fans out through **PipeWire** to up to **6× BlueZ HCI** A2DP sinks (onboard + USB dongles), with per-sink delay/volume from a responsive web UI on the LAN.

Cloud/dev uses **MockAudioBackend**; the Pi uses **PipeWireBackend** (feeder → per-sink delay/volume → bluez). The control UI is a **SvelteKit** static SPA (`@sveltejs/adapter-static`) served by FastAPI from `web/build/`.

## Quick start (laptop / cloud)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Build the UI once (Node 20+); output → web/build/
./scripts/build_web.sh

# Mock backend (default) — no PipeWire or BT hardware required
./scripts/run_dev.sh
# → http://127.0.0.1:8080  (API + built SPA)
```

```bash
export PYTHONPATH=backend
export MUSYNC_BACKEND=mock
python3 -m musync
```

### UI hot reload (optional)

```bash
./scripts/run_dev.sh          # terminal 1 — API on :8080
cd web && npm install && npm run dev   # terminal 2 — Vite on :5173
# Vite proxies /api → http://127.0.0.1:8080 (override with MUSYNC_API_PROXY)
```

Open the UI on a phone or PC on the same network (bind `MUSYNC_HOST=0.0.0.0` if needed).

Wi-Fi join — home network or phone hotspot (pre-save SSID, join, show IP for SPP): [docs/wifi-join.md](docs/wifi-join.md).

### Tests

```bash
pip install -r requirements.txt
./scripts/build_web.sh   # needed for index smoke test
pytest
```

## Layout

```
backend/musync/audio/
  base.py           # AudioBackend interface
  mock.py           # Fake ≤6 sinks + local playback clock
  pipewire.py       # Feeder → loopback delay → bluez fan-out
  pw_tools.py       # wpctl/pactl parsers + graph plan helpers
web/                # SvelteKit SPA (adapter-static)
  src/              # UI source
  build/            # Production assets FastAPI serves
config/
  devices.pi-2sink.example.yaml   # start here on Pi
  devices.example.yaml            # full 6-sink template
docs/pi-pipewire.md
docs/demo-checklist.md                # Qudelix + Phone SPP demo path
docs/spp-network-sink.md              # phone as SPP TCP *output*
docs/phone-bt-source-vs-spp-sink.md   # phone as BT A2DP *source* vs SPP sink
docs/wifi-join.md                     # phone hotspot join (nmcli)
docs/volume-profiles.md               # silent-disco preferred × gain + S_u × G_d learning
backend/musync/wifi/       # nmcli on Pi; mock on laptop
scripts/build_web.sh  run_dev.sh  run_pi.sh  pair_hci.sh  (pair_bt600.sh → alias)
```

Phone **input** (S21 FE → `bluez_input` → feeder): [docs/phone-bt-source-vs-spp-sink.md](docs/phone-bt-source-vs-spp-sink.md).  
USB / PC capture (ALSA auto-detect → feeder): [docs/usb-pc-capture.md](docs/usb-pc-capture.md).  
HDMI / capture card → feeder: [docs/hdmi-capture.md](docs/hdmi-capture.md).  
SPP phone **sink**: enable Phone SPP in Device setup, connect Simple Protocol Player to Pi IP:port — [docs/spp-network-sink.md](docs/spp-network-sink.md).  
**Spotify** (OAuth Connect link in UI → librespot → `musync_feeder`) + **Apple Music** (best-effort search/AirPlay): [docs/spotify-apple.md](docs/spotify-apple.md) · env template `config/streaming.env.example` → `~/musync.env` (auto-loaded by `run_pi.sh`).  
USB/PC capture needs a **USB-A sound card** — Pi 5 USB-C PD is power-only: [docs/usb-pc-capture.md](docs/usb-pc-capture.md).

## API (minimum)

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/api/health` | Backend, PipeWire up?, mapped sinks, graph dry-run |
| `GET` | `/api/devices` | List sinks (Bluetooth + optional `network_spp`) |
| `PATCH` | `/api/devices/{id}` | Per-output `{ delay_ms?, volume?, gain?, mute?, enabled?, listen_port?, … }` |
| `GET`/`POST` | `/api/profiles` | Named listening profiles (silent-disco preferred level + learned S_u) |
| `PUT` | `/api/profiles/active` | Select profile (`{ id }` or `null`) → apply preferred × Device.gain |
| `PUT` | `/api/profiles/assignments` | Assign user to output (`{ device_id, profile_id?, apply_suggested? }`) → preset via S_u × G_d |
| `PATCH`/`DELETE` | `/api/profiles/{id}` | Update / delete a profile (active patch re-applies) |
| `POST` | `/api/profiles/{id}/apply` | Re-apply preferred × Device.gain without changing selection |
| `GET`/`POST`/`DELETE` | `/api/bluetooth/scan` | BlueZ scan status / start (`scan on`) / stop (`scan off`) |
| `POST` | `/api/bluetooth/discover/clear` | Clear discovery list except connected remotes |
| `POST` | `/api/bluetooth/connect` | `{ mac, name?, enable_as_output? }` — pair/connect (+ enable output) |
| `GET`/`PUT` | `/api/transport` | `{ state, position_ms, source_id?, queue_item_id?, title/artist/album/cover_url? }` |
| `GET` | `/api/now-playing` | Unified now-playing (title, artist, album, cover) |
| `GET` | `/api/now-playing/cover` | Embedded local cover bytes |
| `GET` | `/api/sources` | Catalog: `local` + Spotify/Apple hub rows + input hosts |
| `PUT` | `/api/sources/active` | `{ id }` — activate catalog entry (feeder swap only) |
| `GET`/`POST` | `/api/queue` | Song queue; each item has `source_type` + optional `input_id` |
| `PATCH` | `/api/queue/{id}` | Switch `source_type` / bind phone-PC host without tearing down sinks |
| `PUT` | `/api/queue/active` | `{ id }` — play this queue item (feeder swap only) |
| `DELETE` | `/api/queue/{id}` | Remove a queue item |
| `GET` | `/api/streaming` | Spotify/Apple auth status (no secrets) |
| `GET` | `/api/streaming/spotify/login` | Start Spotify OAuth |
| `GET` | `/api/streaming/search` | `?provider=spotify\|apple&q=` |
| `POST` | `/api/streaming/queue` | Queue a searched track (`activate` optional) |
| `GET`/`POST` | `/api/inputs` | Register ≤4 hosts (`capture_path`: `bt_a2dp`\|`usb`\|`hdmi`\|`network`) |
| `GET` | `/api/inputs/discover` | Live PipeWire sources (`bluez_input` / USB / HDMI) for host binding |
| `POST` | `/api/inputs/{id}/upload` | Network path: upload file that plays into the feeder |
| `PATCH`/`DELETE` | `/api/inputs/{id}` | Arm / bind endpoint / remove host |
| `GET` | `/api/wifi` | Wi-Fi status + IPv4 (for SPP / LAN) |
| `GET`/`POST`/`DELETE` | `/api/wifi/networks` | List / pre-save / forget SSIDs (no passwords returned) |
| `POST` | `/api/wifi/connect` | Join SSID (`password` optional if saved) |

## Serving the built UI

1. `./scripts/build_web.sh` (or `cd web && npm ci && npm run build`) → `web/build/`.
2. FastAPI defaults `MUSYNC_WEB_DIR` to `web/build` and mounts it after `/api/*`.
3. Same origin: `/` + `/_app/…` for the SPA, `/api/…` for the hub.

Pi tip: `web/build/` is gitignored — build on a laptop (or once on the Pi with Node) and rsync/copy the artifacts, or run `./scripts/build_web.sh` on-device. Node is not required at runtime once the SPA is built.

## Mock vs PipeWire

| | **Mock** (`MUSYNC_BACKEND=mock`) | **PipeWire** (`MUSYNC_BACKEND=pipewire`) |
|--|----------------------------------|------------------------------------------|
| Where | Cloud / laptop | Raspberry Pi 5 |
| Sinks | In-memory fake devices (≤6) | Same model; maps MAC/`pw_node` → WirePlumber ids |
| Delay | Stored on device object | `pactl module-loopback latency_msec=N` from feeder |
| Volume/mute | In-memory | `wpctl set-volume` / `set-mute` |
| Playback | Software clock (+ live/streaming cues) | `mpv` / librespot / capture loopback → `musync_feeder` |
| Without hardware | Always | **Dry-run**: writes `config/pipewire-graph.plan.sh` |
| UI | Same SvelteKit `web/build` | Same |

## Run on Raspberry Pi 5 — 2 speakers first

Do **not** start with six dongles. Prove sync with two, then scale.

### 1. Packages + UI build

```bash
sudo apt update
sudo apt install -y python3-pip python3-venv pipewire wireplumber \
  pipewire-audio-client-libraries pulseaudio-utils mpv bluez
# Node only if you build the UI on-device (otherwise copy a prebuilt web/build/):
# curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
# sudo apt install -y nodejs
git clone https://github.com/NishFishASU/MuSync.git
cd MuSync
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
./scripts/build_web.sh   # or rsync a laptop-built web/build/
```

### 2. Pair HCI ↔ Qudelix (onboard first)

```bash
./scripts/pair_hci.sh hci0    # enter Qudelix MAC when prompted (pairing only)
# optional second USB HCI dongle (generic BlueZ — not chip-specific):
# ./scripts/pair_hci.sh hci1
```

Confirm bluez sinks:

```bash
wpctl status | sed -n '/Sinks:/,/Sources:/p'
# look for bluez_output.<MAC_WITH_UNDERSCORES>
```

You do **not** need to hand-edit `devices.yaml` MACs — the web UI discovers speakers by name.

### 3. Media + launch

```bash
export MUSYNC_BACKEND=pipewire
cp /path/to/track.flac media/
cp /path/to/clip.mp4 media/   # optional — audio → musync_feeder; muted preview in UI
./scripts/run_pi.sh   # clears stray ffmpeg on SPP ports; prints READY/MISSING preflight
# UI: http://<pi-lan-ip>:8080
```

Missing or broken `devices.yaml` is fine — MuSync boots with **Phone SPP** defaults.

**Demo day:** short Qudelix + Phone SPP checklist → **[docs/demo-checklist.md](docs/demo-checklist.md)**.

### 4. Device setup in the web UI (no YAML copy)

1. Open **Device setup**.
2. **Refresh** — Qudelix (and other bluez remotes) appear by friendly name; paired BT is soft-reconnected after reboot.
3. Enable the BT sink(s) you want; enable **Phone SPP** and set host/port/rate/channels.
4. **Save sinks** — writes `config/devices.yaml` atomically (MAC filled from discovery).
5. Sink pills: **READY** vs **MISSING** (SPP listen / BT linked). Play a hub-local file. Phone Simple Protocol Player → **Pi IP** (from Wi‑Fi panel, not `0.0.0.0`):`port`, matching rate/channels s16le.

Graph details: [docs/pi-pipewire.md](docs/pi-pipewire.md). SPP details: [docs/spp-network-sink.md](docs/spp-network-sink.md).

### 5. Scale to 4 then 6 (USB HCI dongles)

Enable more discovered BT sinks in the UI (≤6 A2DP). Use a **powered USB hub**. Pair each new dongle with `./scripts/pair_hci.sh hciN`. The UI inventories onboard + USB HCI adapters and can assign a free healthy HCI on Connect.

**Untested without hardware:** multi-dongle A2DP scale-out is wired and soft-fails unhealthy sticks, but live USB HCI verification is pending (ASUS BT600 was dead). Onboard Qudelix remains the proven path.

### Optional systemd

Edit user/paths in `deploy/systemd/musync.service`, then:

```bash
sudo cp deploy/systemd/musync.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now musync
```

### Extra USB HCI / BlueZ adapters

- One A2DP sink per HCI adapter (`hci0`…`hci5`) — chip-agnostic (any BlueZ USB dongle).
- Onboard-only soft-fails gracefully (1 A2DP); UI shows adapter ready / in use / dead chips.
- Unhealthy USB dongles are skipped with short BlueZ timeouts — they must not block onboard scan/reconnect.
- Powered hub strongly recommended beyond two dongles.
- Prefer the same codec (SBC or AAC) across sinks.
- Onboard Pi Bluetooth validates the software path (e.g. Qudelix-5K + local file); USB HCI expands capacity when healthy.
- Losing one sink should not stop the others (MuSync unloads only that sink's loopback).
- ASUS BT600 / RTL8761 hardware notes (`0b05:1d70`): **[docs/bt600-realtek.md](docs/bt600-realtek.md)** (chip-specific; not required by MuSync).

## Environment variables

| Variable | Default | Meaning |
|----------|---------|---------|
| `MUSYNC_BACKEND` | `mock` | `mock` or `pipewire` |
| `MUSYNC_HOST` | `0.0.0.0` (`127.0.0.1` in `run_dev.sh`) | Bind address |
| `MUSYNC_PORT` | `8080` | HTTP port |
| `MUSYNC_DEVICES_CONFIG` | `config/devices.yaml` | Device profile path |
| `MUSYNC_PROFILES_CONFIG` | `config/profiles.yaml` | User volume preference profiles |
| `MUSYNC_VOLUME_DB` | `config/user_volumes.sqlite` | Per-user×device volumes + learned G_d |
| `MUSYNC_MEDIA_DIR` | `media/` | Local library root |
| `MUSYNC_WEB_DIR` | `web/build/` | Built SvelteKit static root |
| `MUSYNC_PW_FEEDER` | `musync_feeder` | Virtual sink name for fan-out |
| `MUSYNC_PW_DRY_RUN` | auto | `1` force plan-only; `0` force live CLI; unset = dry-run when PipeWire down |
| `MUSYNC_RELOAD` | unset | Set `1` for uvicorn reload |
| `MUSYNC_API_PROXY` | `http://127.0.0.1:8080` | Vite dev proxy target (`web/`) |
| `MUSYNC_WIFI_BACKEND` | `auto` | `auto` / `nmcli` / `mock` — phone-hotspot join ([docs/wifi-join.md](docs/wifi-join.md)) |

## Out of scope (this MVP)

Lip-sync picture path (HDMI video display), HDMI-local UI, Uno Q / Grove, wired outs, guided clap calibration.
(Local video + HDMI/capture **audio** → feeder are supported; picture stays local to the capture/display path.)

## License

Private.
