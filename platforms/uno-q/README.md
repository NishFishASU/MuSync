# MuSync on Arduino Uno Q

**Parallel audio hub** for people with an Uno Q — same FastAPI + SPA product shape as the Raspberry Pi 5 target, with a platform-specific audio/OS bring-up.

Canonical plan: [docs/uno-q-hub.md](../../docs/uno-q-hub.md).

## What this folder is

| Path | Purpose |
|------|---------|
| `audio/backend.py` | Stub `AudioBackend` skeleton for the Uno Q port |
| `config/devices.uno-q.example.yaml` | Starter 1–2 sink device map |
| `scripts/configure_headless_bt_audio.sh` | OS notes stub for PipeWire/BlueZ headless BT |
| `scripts/run_uno_q.sh` | Host-service launcher stub |
| `scripts/smoke_api.sh` | Hit `/api/health` on a running hub |

This is **not** a Grove-only companion and **not** an App Lab brick. Run MuSync on **host Debian**.

## Bring-up order (on hardware)

1. Flash / update Uno Q Debian; SSH in; confirm `wpctl`, `bluetoothctl` exist.
2. `./scripts/configure_headless_bt_audio.sh --dry-run` then apply once reviewed.
3. From repo root (after venv + `pip install -r requirements.txt`):  
   `MUSYNC_PLATFORM=uno-q MUSYNC_BACKEND=mock ./platforms/uno-q/scripts/run_uno_q.sh`  
   → verify SPA + API on LAN.
4. Pair one A2DP speaker; confirm sink under `wpctl status`.
5. Swap stub backend for a PipeWire-class implementation (port of `backend/musync/audio/pipewire.py`).
6. Add USB HCI dongles only after single-sink delay/volume works; soft-fail extras.

## Platform env (planned)

| Variable | Meaning |
|----------|---------|
| `MUSYNC_PLATFORM=uno-q` | Selects Uno Q deploy defaults / docs path |
| `MUSYNC_BACKEND=mock` | Laptop / first boot (no audio hardware) |
| `MUSYNC_BACKEND=pipewire` or `uno_q` | Live graph once the port lands |
| `MUSYNC_DEVICES_CONFIG` | Point at `platforms/uno-q/config/devices.uno-q.example.yaml` initially |

Until the stub is wired into `backend/musync`, use the shared mock/pipewire entrypoints and treat this tree as the port staging area.
