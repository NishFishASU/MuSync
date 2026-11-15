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

# Streaming credentials (Spotify/Apple) — load before API starts.
# OAuth itself is link-style Connect in the UI (no password form).
for envfile in "$HOME/musync.env" "$ROOT/config/streaming.local.env"; do
  if [[ -f "$envfile" ]]; then
    # shellcheck disable=SC1090
    set -a && source "$envfile" && set +a
    echo "  env:      loaded $envfile"
  fi
done

echo "MuSync Pi launcher"
echo "  backend:  $MUSYNC_BACKEND"
echo "  python:   $PY"
echo "  listen:   http://${MUSYNC_HOST}:${MUSYNC_PORT}"
echo "  devices:  $MUSYNC_DEVICES_CONFIG"
echo "  media:    $MUSYNC_MEDIA_DIR"
echo "  web:      $MUSYNC_WEB_DIR"
echo "  feeder:   $MUSYNC_PW_FEEDER"
if [[ -n "${MUSYNC_SPOTIFY_CLIENT_ID:-}" && -n "${MUSYNC_SPOTIFY_CLIENT_SECRET:-}" ]]; then
  echo "  spotify:  credentials present → use Connect Spotify in UI (OAuth redirect)"
else
  echo "  spotify:  NOT CONFIGURED — copy config/streaming.env.example → ~/musync.env"
fi
if command -v librespot >/dev/null 2>&1; then
  echo "  librespot: READY ($(command -v librespot))"
else
  echo "  librespot: MISSING (Spotify search/OAuth work; hub PCM needs librespot)"
fi

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

# Collect SPP listen ports from devices.yaml (default 1234).
SPP_PORTS=()
if [[ -f "$MUSYNC_DEVICES_CONFIG" ]]; then
  while IFS= read -r port; do
    [[ "$port" =~ ^[0-9]+$ ]] || continue
    SPP_PORTS+=("$port")
  done < <(grep -E '^\s*listen_port:\s*[0-9]+' "$MUSYNC_DEVICES_CONFIG" 2>/dev/null | grep -oE '[0-9]+' || true)
fi
if [[ ${#SPP_PORTS[@]} -eq 0 ]]; then
  SPP_PORTS=(1234)
fi
# Unique ports
mapfile -t SPP_PORTS < <(printf '%s\n' "${SPP_PORTS[@]}" | sort -nu)

# Kill stray ffmpeg listens so MuSync can bind (manual demo / crashed prior run).
echo
echo "Clearing stray SPP ffmpeg on ports: ${SPP_PORTS[*]}"
for port in "${SPP_PORTS[@]}"; do
  if pkill -f "ffmpeg.*:${port}" 2>/dev/null; then
    echo "  killed ffmpeg matching :${port}"
  else
    echo "  :${port} — no stray ffmpeg"
  fi
done
sleep 0.2

# Optional: show orphan MuSync feeders before boot (backend purges on startup).
if command -v pactl >/dev/null 2>&1; then
  feeder_n=$(pactl list modules short 2>/dev/null | grep -c "module-null-sink.*sink_name=${MUSYNC_PW_FEEDER}" || true)
  if [[ "${feeder_n:-0}" -gt 1 ]]; then
    echo
    echo "NOTE: found ${feeder_n} ${MUSYNC_PW_FEEDER} null-sink modules — MuSync will purge orphans on startup."
  elif [[ "${feeder_n:-0}" -eq 1 ]]; then
    echo "  feeder: one ${MUSYNC_PW_FEEDER} module present (idempotent)"
  else
    echo "  feeder: no ${MUSYNC_PW_FEEDER} yet (created on startup)"
  fi
fi

echo
echo "Preflight health:"
if command -v ffmpeg >/dev/null 2>&1; then
  echo "  ffmpeg: READY ($(command -v ffmpeg))"
else
  echo "  ffmpeg: MISSING (Phone SPP will not listen)"
fi
if command -v wpctl >/dev/null 2>&1 && wpctl status >/dev/null 2>&1; then
  echo "  PipeWire: READY"
else
  echo "  PipeWire: MISSING/down (graph dry-run until up)"
fi
for port in "${SPP_PORTS[@]}"; do
  if command -v ss >/dev/null 2>&1 && ss -ltn 2>/dev/null | grep -qE ":${port}\\s"; then
    echo "  TCP :${port}: STILL BUSY — stop the process holding it before Phone SPP can bind"
  else
    echo "  TCP :${port}: free"
  fi
done

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
echo "  1) Pair Qudelix (./scripts/pair_hci.sh hci0 or bluetoothctl)"
echo "  2) ./scripts/run_pi.sh  (this script — uses .venv, clears stray ffmpeg)"
echo "  3) Open http://<Pi-IP>:8080 → Device setup"
echo "     - Refresh → enable BT by name (Qudelix) — no MAC typing"
echo "     - enable Phone SPP → Save sinks"
echo "  4) Play hub-local; phone SPP app → Pi IP:port (not 0.0.0.0), s16le"
echo "  Checklist: docs/demo-checklist.md"
echo
echo "After boot, curl http://127.0.0.1:${MUSYNC_PORT}/api/health — look for READY=… MISSING=…"
echo

exec "$PY" -m musync
