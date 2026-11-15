#!/usr/bin/env bash
# Run MuSync locally with the mock audio backend + built SvelteKit SPA.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PYTHONPATH="${ROOT}/backend${PYTHONPATH:+:$PYTHONPATH}"
export MUSYNC_BACKEND="${MUSYNC_BACKEND:-mock}"
export MUSYNC_HOST="${MUSYNC_HOST:-127.0.0.1}"
export MUSYNC_PORT="${MUSYNC_PORT:-8080}"
export MUSYNC_WEB_DIR="${MUSYNC_WEB_DIR:-$ROOT/web/build}"
cd "$ROOT"

# Streaming credentials — same files as run_pi.sh / API startup loader.
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

echo "MuSync mock"
echo "  listen:   http://${MUSYNC_HOST}:${MUSYNC_PORT}"
echo "  Spotify Dashboard: register BOTH 127.0.0.1:8080 and :8081 callbacks (not localhost / musync.local)"
echo "  Spotify redirect (this run): http://127.0.0.1:${MUSYNC_PORT}/api/streaming/spotify/callback"
if [[ -n "${MUSYNC_SPOTIFY_CLIENT_ID:-}" && -n "${MUSYNC_SPOTIFY_CLIENT_SECRET:-}" ]]; then
  echo "  spotify:  credentials present → Connect Spotify in UI"
else
  echo "  spotify:  NOT CONFIGURED — copy config/streaming.env.example → ~/musync.env"
fi

exec python3 -m musync
