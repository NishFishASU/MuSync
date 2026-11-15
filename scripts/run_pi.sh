#!/usr/bin/env bash
# Run MuSync on a Raspberry Pi 5 with the PipeWire backend.
# Demo path: build SvelteKit UI → pair speakers → start API + SPA.
# Device setup is done in the web UI (discover by name + SPP details) — no YAML MAC edit.
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

# Prefer the project venv — system python3 often lacks uvicorn (ModuleNotFoundError).
if [[ -f "$ROOT/.venv/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source "$ROOT/.venv/bin/activate"
fi
if [[ -x "$ROOT/.venv/bin/python" ]]; then
  PY="$ROOT/.venv/bin/python"
else
  PY="${PYTHON:-python3}"
fi

echo "MuSync Pi launcher"
echo "  backend:  $MUSYNC_BACKEND"
echo "  python:   $PY"
echo "  listen:   http://${MUSYNC_HOST}:${MUSYNC_PORT}"
echo "  devices:  $MUSYNC_DEVICES_CONFIG"
echo "  media:    $MUSYNC_MEDIA_DIR"
echo "  web:      $MUSYNC_WEB_DIR"
echo "  feeder:   $MUSYNC_PW_FEEDER"

if ! "$PY" -c "import uvicorn" 2>/dev/null; then
  echo >&2
  echo "ERROR: uvicorn not importable with: $PY" >&2
  echo "MuSync must run inside the project venv (system python usually lacks deps)." >&2
  echo "Fix:" >&2
  echo "  cd $ROOT" >&2
  echo "  python3 -m venv .venv" >&2
  echo "  source .venv/bin/activate" >&2
  echo "  pip install -r requirements.txt" >&2
  echo "  ./scripts/run_pi.sh" >&2
  exit 1
fi

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
  echo "No devices.yaml yet — API boots with Phone SPP defaults."
  echo "  Open the UI → Device setup → enable discovered BT (e.g. Qudelix) + SPP → Save sinks."
  echo "  (Broken YAML is also tolerated; save from the UI rewrites the file safely.)"
fi

# Soft hint only — placeholders are no longer required; UI discovery is preferred.
if [[ -f "$MUSYNC_DEVICES_CONFIG" ]] && grep -Eqi 'AA:BB:CC:DD:EE:|REPLACE_WITH_' "$MUSYNC_DEVICES_CONFIG" 2>/dev/null; then
  echo
  echo "NOTE: $MUSYNC_DEVICES_CONFIG still has placeholder MACs."
  echo "  Prefer UI Device setup (Refresh discovery → enable by name → Save) over editing YAML."
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

# Optional: show orphan MuSync feeders before boot (backend purges on startup).
if command -v pactl >/dev/null 2>&1; then
  feeder_n=$(pactl list modules short 2>/dev/null | grep -c "module-null-sink.*sink_name=${MUSYNC_PW_FEEDER}" || true)
  if [[ "${feeder_n:-0}" -gt 1 ]]; then
    echo
    echo "NOTE: found ${feeder_n} ${MUSYNC_PW_FEEDER} null-sink modules — MuSync will purge orphans on startup."
  fi
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
echo "Qudelix + SPP via web UI:"
echo "  1) export MUSYNC_BACKEND=pipewire"
echo "  2) Pair: ./scripts/pair_bt600.sh hci0   # or bluetoothctl"
echo "  3) ./scripts/run_pi.sh  (this script — uses .venv)"
echo "  4) Open http://<Pi-IP>:8080 → Device setup"
echo "     - enable discovered BT by name (Qudelix) — no MAC typing"
echo "     - enable Phone SPP; set port/rate/channels if needed → Save sinks"
echo "  5) Play hub-local; phone SPP app → Pi IP:port, s16le"
echo

exec "$PY" -m musync
