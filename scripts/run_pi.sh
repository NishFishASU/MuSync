#!/usr/bin/env bash
# Run MuSync on a Raspberry Pi 5 with the PipeWire backend.
# Demo path: build SvelteKit UI → pair 2 speakers → start API + SPA.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

export PYTHONPATH="${ROOT}/backend${PYTHONPATH:+:$PYTHONPATH}"
export MUSYNC_BACKEND="${MUSYNC_BACKEND:-pipewire}"
export MUSYNC_HOST="${MUSYNC_HOST:-0.0.0.0}"
export MUSYNC_PORT="${MUSYNC_PORT:-8080}"
export MUSYNC_DEVICES_CONFIG="${MUSYNC_DEVICES_CONFIG:-$ROOT/config/devices.yaml}"
export MUSYNC_MEDIA_DIR="${MUSYNC_MEDIA_DIR:-$ROOT/media}"
export MUSYNC_WEB_DIR="${MUSYNC_WEB_DIR:-$ROOT/web/build}"
# Set MUSYNC_PW_DRY_RUN=1 to plan-only (no pactl/wpctl mutations).
# Unset / 0 for live graph on a real Pi session.
export MUSYNC_PW_FEEDER="${MUSYNC_PW_FEEDER:-musync_feeder}"

cd "$ROOT"

echo "MuSync Pi launcher"
echo "  backend:  $MUSYNC_BACKEND"
echo "  listen:   http://${MUSYNC_HOST}:${MUSYNC_PORT}"
echo "  devices:  $MUSYNC_DEVICES_CONFIG"
echo "  media:    $MUSYNC_MEDIA_DIR"
echo "  web:      $MUSYNC_WEB_DIR"
echo "  feeder:   $MUSYNC_PW_FEEDER"

if [[ ! -f "$MUSYNC_WEB_DIR/index.html" ]]; then
  echo
  echo "SvelteKit UI not built at $MUSYNC_WEB_DIR"
  if command -v npm >/dev/null 2>&1; then
    echo "Running ./scripts/build_web.sh …"
    "$ROOT/scripts/build_web.sh"
  else
    echo "Node/npm not found. Either:" >&2
    echo "  - install Node 20+ and run: ./scripts/build_web.sh" >&2
    echo "  - or copy a prebuilt web/build/ from a laptop" >&2
    exit 1
  fi
fi

if [[ ! -f "$MUSYNC_DEVICES_CONFIG" ]]; then
  echo
  echo "No devices.yaml yet — seeding a 2-sink Pi demo profile."
  echo "Edit MACs after pairing (./scripts/pair_bt600.sh hci0)."
  cp "$ROOT/config/devices.pi-2sink.example.yaml" "$MUSYNC_DEVICES_CONFIG"
fi

missing=0
for cmd in wpctl pactl bluetoothctl; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "  warn: $cmd not found"
    missing=1
  fi
done
if [[ "$missing" -eq 1 ]]; then
  echo "  Install: sudo apt install -y pipewire wireplumber pulseaudio-utils bluez mpv"
  echo "  Continuing — backend will dry-run the graph if PipeWire is down."
fi

if command -v bluetoothctl >/dev/null 2>&1; then
  echo
  echo "Bluetooth adapters:"
  bluetoothctl list 2>/dev/null || echo "  (none)"
fi

if command -v wpctl >/dev/null 2>&1; then
  echo
  echo "PipeWire sinks (excerpt):"
  wpctl status 2>/dev/null | sed -n '/Sinks:/,/Sources:/p' | head -n 40 || true
fi

echo
echo "First-time 2-speaker checklist:"
echo "  1) Plug 2× BT600 (hci0, hci1) — powered hub recommended later for 4–6"
echo "  2) ./scripts/pair_bt600.sh hci0"
echo "  3) ./scripts/pair_bt600.sh hci1"
echo "  4) Put real MACs into $MUSYNC_DEVICES_CONFIG"
echo "  5) Drop a WAV/FLAC/MP3 into $MUSYNC_MEDIA_DIR"
echo "  6) Ensure UI built: ./scripts/build_web.sh  (or rsync web/build/)"
echo "  7) Open http://<pi-lan-ip>:${MUSYNC_PORT} — enable both sinks, set delays, Play"
echo

exec python3 -m musync
