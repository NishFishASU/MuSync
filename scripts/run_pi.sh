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
  echo "No devices.yaml yet — seeding Qudelix + SPP profile from"
  echo "  config/devices.pi-2sink.example.yaml"
  echo "You MUST replace REPLACE_WITH_* MACs before BT sinks leave MISSING."
  cp "$ROOT/config/devices.pi-2sink.example.yaml" "$MUSYNC_DEVICES_CONFIG"
fi

# Loud warnings when still on placeholders / missing SPP (common “UI shows AA:BB MISSING”).
if grep -Eqi 'AA:BB:CC:DD:EE:|REPLACE_WITH_' "$MUSYNC_DEVICES_CONFIG" 2>/dev/null; then
  echo
  echo "WARNING: $MUSYNC_DEVICES_CONFIG still has placeholder MACs."
  echo "  UI will show MISSING and Play will not reach Qudelix/bluez."
  echo "  Fix:"
  echo "    bluetoothctl devices          # find Qudelix MAC"
  echo "    nano $MUSYNC_DEVICES_CONFIG   # set mac: \"A0:..\" on sink-1 (Qudelix)"
  echo "    # keep spp-1 kind: network_spp enabled: true"
fi
if ! grep -Eq 'kind:[[:space:]]*network_spp' "$MUSYNC_DEVICES_CONFIG" 2>/dev/null; then
  echo
  echo "WARNING: no network_spp sink in $MUSYNC_DEVICES_CONFIG — Phone SPP will not appear."
  echo "  Re-seed or append spp-1 from config/devices.pi-2sink.example.yaml"
fi

missing=0
for cmd in wpctl pactl bluetoothctl ffmpeg; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "  warn: $cmd not found"
    missing=1
  fi
done
if [[ "$missing" -eq 1 ]]; then
  echo "  Install: sudo apt install -y pipewire wireplumber pipewire-pulse pulseaudio-utils bluez mpv ffmpeg"
  echo "  Continuing — backend will dry-run the graph if PipeWire is down."
fi

if command -v bluetoothctl >/dev/null 2>&1; then
  echo
  echo "Bluetooth adapters:"
  bluetoothctl list 2>/dev/null || echo "  (none)"
  echo "Paired devices:"
  bluetoothctl devices 2>/dev/null || echo "  (none)"
fi

if command -v wpctl >/dev/null 2>&1; then
  echo
  echo "PipeWire sinks (excerpt):"
  wpctl status 2>/dev/null | sed -n '/Sinks:/,/Sources:/p' | head -n 40 || true
fi

echo
echo "Qudelix + SPP checklist:"
echo "  1) export MUSYNC_DEVICES_CONFIG=$MUSYNC_DEVICES_CONFIG"
echo "  2) export MUSYNC_BACKEND=pipewire"
echo "  3) Pair: ./scripts/pair_bt600.sh hci0   # or bluetoothctl"
echo "  4) Edit devices.yaml: Qudelix mac + spp-1 enabled (kind: network_spp, port 1234)"
echo "  5) Media in $MUSYNC_MEDIA_DIR ; UI: ./scripts/build_web.sh if needed"
echo "  6) Restart this script; UI → enable Qudelix + Phone SPP → Play"
echo "  7) Phone SPP app → Pi LAN/Tailscale IP:1234, 48000 Hz mono s16le"
echo

exec python3 -m musync
