#!/usr/bin/env bash
# Run MuSync on Arduino Uno Q (host Debian) — dual-target hub launcher stub.
# Default backend is mock until platforms/uno-q/audio/backend.py is wired.
# See docs/uno-q-hub.md.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"

export PYTHONPATH="${ROOT}/backend${PYTHONPATH:+:$PYTHONPATH}"
export MUSYNC_PLATFORM="${MUSYNC_PLATFORM:-uno-q}"
export MUSYNC_BACKEND="${MUSYNC_BACKEND:-mock}"
export MUSYNC_HOST="${MUSYNC_HOST:-0.0.0.0}"
export MUSYNC_PORT="${MUSYNC_PORT:-8080}"
export MUSYNC_DEVICES_CONFIG="${MUSYNC_DEVICES_CONFIG:-$ROOT/platforms/uno-q/config/devices.uno-q.example.yaml}"
export MUSYNC_MEDIA_DIR="${MUSYNC_MEDIA_DIR:-$ROOT/media}"
export MUSYNC_WEB_DIR="${MUSYNC_WEB_DIR:-$ROOT/web/build}"
export MUSYNC_PW_FEEDER="${MUSYNC_PW_FEEDER:-musync_feeder}"

cd "$ROOT"

if [[ -f "$ROOT/.venv/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source "$ROOT/.venv/bin/activate"
fi
if [[ -x "$ROOT/.venv/bin/python" ]]; then
  PY="$ROOT/.venv/bin/python"
else
  PY="${PYTHON:-python3}"
fi

for envfile in "$HOME/musync.env" "$ROOT/config/streaming.local.env"; do
  if [[ -f "$envfile" ]]; then
    # shellcheck disable=SC1090
    set -a && source "$envfile" && set +a
    echo "  env:      loaded $envfile"
  fi
done

if [[ ! -f "$MUSYNC_WEB_DIR/index.html" ]]; then
  echo "UI not built — running ./scripts/build_web.sh"
  "$ROOT/scripts/build_web.sh"
fi

echo "MuSync Uno Q launcher (scaffold)"
echo "  platform: $MUSYNC_PLATFORM"
echo "  backend:  $MUSYNC_BACKEND"
echo "  python:   $PY"
echo "  listen:   http://${MUSYNC_HOST}:${MUSYNC_PORT}"
echo "  devices:  $MUSYNC_DEVICES_CONFIG"
echo "  media:    $MUSYNC_MEDIA_DIR"
echo "  web:      $MUSYNC_WEB_DIR"
if [[ "$MUSYNC_BACKEND" == "uno_q" ]]; then
  echo "  note:     MUSYNC_BACKEND=uno_q is not wired yet — use mock|pipewire"
fi

exec "$PY" -m musync
