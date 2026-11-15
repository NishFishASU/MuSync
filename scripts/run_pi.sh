#!/usr/bin/env bash
# Run MuSync on a Raspberry Pi 5 with the PipeWire backend.
# Demo path: build SvelteKit UI → pair speakers → start API + SPA.
# Device setup is done in the web UI (discover by name + SPP details) — no YAML MAC edit.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

export PYTHONPATH="${ROOT}/backend${PYTHONPATH:+:$PYTHONPATH}"
export MUSYNC_BACKEND="${MUSYNC_BACKEND:-pipewire}"
export MUSYNC_HOST="${MUSYNC_HOST:-0.0.0.0}"
# Pi default API port is always 8080 (laptop may override to 8081 — see run_dev).
# Refuse empty MUSYNC_PORT (would build http://127.0.0.1:/…).
if [[ -z "${MUSYNC_PORT:-}" ]]; then
  export MUSYNC_PORT=8080
fi
export MUSYNC_DEVICES_CONFIG="${MUSYNC_DEVICES_CONFIG:-$ROOT/config/devices.yaml}"
export MUSYNC_MEDIA_DIR="${MUSYNC_MEDIA_DIR:-$ROOT/media}"
export MUSYNC_WEB_DIR="${MUSYNC_WEB_DIR:-$ROOT/web/build}"
# Set MUSYNC_PW_DRY_RUN=1 to plan-only (no pactl/wpctl mutations).
# Unset / 0 for live graph on a real Pi session.
export MUSYNC_PW_FEEDER="${MUSYNC_PW_FEEDER:-musync_feeder}"
# Spotify OAuth default: 127.0.0.1:8080 (Spotify forbids localhost / musync.local).
# Dashboard should also list :8081 for laptop override; Pi keeps 8080.
_DEFAULT_SPOTIFY_REDIRECT="http://127.0.0.1:${MUSYNC_PORT}/api/streaming/spotify/callback"
export MUSYNC_SPOTIFY_REDIRECT_URI="${MUSYNC_SPOTIFY_REDIRECT_URI:-$_DEFAULT_SPOTIFY_REDIRECT}"

# cargo install librespot → ~/.cargo/bin; pipx yt-dlp → ~/.local/bin.
# Non-login shells / systemd often omit these — prepend so MuSync + preflight see them.
_path_prepend() {
  local dir="$1"
  [[ -d "$dir" ]] || return 0
  case ":${PATH:-}:" in
    *":$dir:"*) ;;
    *) export PATH="$dir${PATH:+:$PATH}" ;;
  esac
}
_path_prepend "${CARGO_HOME:-$HOME/.cargo}/bin"
_path_prepend "$HOME/.local/bin"
unset -f _path_prepend

cd "$ROOT"

# Prefer the project venv — system python3 often lacks uvicorn (ModuleNotFoundError).
# AVRCP RegisterPlayer needs apt python3-dbus + python3-gi. Isolated venvs hide
# those packages unless include-system-site-packages is true.
if [[ -f "$ROOT/.venv/pyvenv.cfg" ]]; then
  if grep -qiE '^include-system-site-packages[[:space:]]*=[[:space:]]*false' "$ROOT/.venv/pyvenv.cfg"; then
    echo "  note:     enabling system-site-packages on .venv (python3-dbus/gi for AVRCP)"
    # portable: rewrite the flag without assuming GNU sed -i backup behavior
    cfg="$ROOT/.venv/pyvenv.cfg"
    tmp="${cfg}.tmp.$$"
    sed -E 's/^include-system-site-packages[[:space:]]*=[[:space:]]*false/include-system-site-packages = true/I' "$cfg" >"$tmp" && mv "$tmp" "$cfg"
  fi
fi
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

# After env files: refuse empty port / host:/path (musync.env can override badly).
if [[ -z "${MUSYNC_PORT:-}" ]]; then
  echo "  warn:     MUSYNC_PORT empty after env load — forcing 8080"
  export MUSYNC_PORT=8080
fi
_redir="${MUSYNC_SPOTIFY_REDIRECT_URI:-}"
# Empty port = scheme://host:/… (colon immediately before slash) or trailing host:
if [[ -z "$_redir" || "$_redir" =~ ://[^/]+:/ || "$_redir" =~ ://[^/]+:$ ]]; then
  echo "  warn:     refusing empty-port Spotify redirect — using 127.0.0.1:${MUSYNC_PORT}"
  export MUSYNC_SPOTIFY_REDIRECT_URI="http://127.0.0.1:${MUSYNC_PORT}/api/streaming/spotify/callback"
fi
unset _redir _DEFAULT_SPOTIFY_REDIRECT

echo "MuSync Pi launcher"
echo "  backend:  $MUSYNC_BACKEND"
echo "  python:   $PY"
echo "  listen:   http://${MUSYNC_HOST}:${MUSYNC_PORT}"
echo "  ui mDNS:  http://musync.local:${MUSYNC_PORT}  (UI only — Spotify OAuth uses 127.0.0.1, not .local)"
echo "  devices:  $MUSYNC_DEVICES_CONFIG"
echo "  media:    $MUSYNC_MEDIA_DIR"
echo "  web:      $MUSYNC_WEB_DIR"
echo "  feeder:   $MUSYNC_PW_FEEDER"
echo "  Spotify redirect (Pi default): ${MUSYNC_SPOTIFY_REDIRECT_URI}"
if [[ -n "${MUSYNC_SPOTIFY_CLIENT_ID:-}" && -n "${MUSYNC_SPOTIFY_CLIENT_SECRET:-}" ]]; then
  echo "  spotify:  credentials present → use Connect Spotify in UI (OAuth redirect)"
else
  echo "  spotify:  NOT CONFIGURED — copy config/streaming.env.example → ~/musync.env"
fi
if command -v librespot >/dev/null 2>&1; then
  echo "  librespot: READY ($(command -v librespot))"
elif [[ -x "${CARGO_HOME:-$HOME/.cargo}/bin/librespot" ]]; then
  echo "  librespot: READY (${CARGO_HOME:-$HOME/.cargo}/bin/librespot) — added cargo bin to PATH"
else
  echo "  librespot: MISSING (Spotify search/OAuth work; hub PCM needs librespot)"
  echo "             sudo apt install -y libasound2-dev && cargo install librespot"
  echo "             (MuSync also auto-finds ~/.cargo/bin/librespot — restart after install)"
fi
if command -v yt-dlp >/dev/null 2>&1; then
  echo "  yt-dlp:   READY ($(command -v yt-dlp) · $(yt-dlp --version 2>/dev/null | head -1))"
else
  echo "  yt-dlp:   MISSING (YouTube search needs API key; play needs yt-dlp ≥ 2025.10.14)"
fi

if ! "$PY" -c "import uvicorn" 2>/dev/null; then
  echo >&2
  echo "ERROR: uvicorn not importable with: $PY" >&2
  echo "MuSync must run inside the project venv (system python usually lacks deps)." >&2
  echo "Fix:" >&2
  echo "  cd $ROOT" >&2
  echo "  python3 -m venv --system-site-packages .venv" >&2
  echo "  source .venv/bin/activate" >&2
  echo "  pip install -r requirements.txt" >&2
  echo "  ./scripts/run_pi.sh" >&2
  exit 1
fi

# Qudelix play/pause/skip needs Media1.RegisterPlayer (dbus + gi in THIS interpreter).
if "$PY" -c "import dbus, dbus.service, dbus.mainloop.glib; from gi.repository import GLib" 2>/dev/null; then
  echo "  avrcp:    dbus/gi import OK (RegisterPlayer available)"
else
  echo "  warn:     dbus/gi NOT importable under $PY — headset buttons will soft-fail"
  echo "            sudo apt install -y python3-dbus python3-gi"
  echo "            then: python3 -m venv --system-site-packages .venv && pip install -r requirements.txt"
  echo "            curl /api/avrcp → player_registered / dbus_import_ok for truth"
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

# Optional: show orphan MuSync feeders / loopbacks before boot (backend purges on startup).
if command -v pactl >/dev/null 2>&1; then
  feeder_n=$(pactl list modules short 2>/dev/null | grep -c "module-null-sink.*sink_name=${MUSYNC_PW_FEEDER}" || true)
  loop_n=$(pactl list modules short 2>/dev/null | grep -cE "module-loopback.*(musync_loopback_|musync_capture_|sink=${MUSYNC_PW_FEEDER}|source=${MUSYNC_PW_FEEDER}\\.monitor)" || true)
  if [[ "${feeder_n:-0}" -gt 1 ]]; then
    echo
    echo "NOTE: found ${feeder_n} ${MUSYNC_PW_FEEDER} null-sink modules — MuSync will purge orphans on startup."
  elif [[ "${feeder_n:-0}" -eq 1 ]]; then
    echo "  feeder: one ${MUSYNC_PW_FEEDER} module present (reused unless reset)"
  else
    echo "  feeder: no ${MUSYNC_PW_FEEDER} yet (created on startup)"
  fi
  if [[ "${loop_n:-0}" -gt 0 ]]; then
    echo "NOTE: found ${loop_n} leftover MuSync loopback module(s) — purged on startup / Ctrl+C teardown."
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
echo "  3) Open http://musync.local:${MUSYNC_PORT} (or http://<Pi-IP>:${MUSYNC_PORT}) → Device setup"
echo "     - Refresh → enable BT by name (Qudelix) — no MAC typing"
echo "     - enable Phone SPP → Save sinks"
echo "  4) Play hub-local; phone SPP app → Pi IP:port (not 0.0.0.0), s16le"
echo "  Spotify: Dashboard needs 127.0.0.1:8080 and :8081 callbacks (not musync.local) — docs/spotify-apple.md"
echo "  Checklist: docs/demo-checklist.md"
echo
echo "After boot, curl http://127.0.0.1:${MUSYNC_PORT}/api/health — look for READY=… MISSING=…"
echo "  mDNS:     curl http://127.0.0.1:${MUSYNC_PORT}/api/mdns"
echo

# HDMI / console walk-up: big http://PI_IP:PORT + printable ASCII QR (scan from TV).
# --console also writes /dev/console + /dev/tty1 when permitted (soft-fail OK).
"$PY" -m musync.lan_banner --console || echo "  (LAN banner soft-fail — pip install -r requirements.txt)"
echo

exec "$PY" -m musync
