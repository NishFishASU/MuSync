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

if [[ ! -f "$MUSYNC_WEB_DIR/index.html" ]]; then
  echo "UI not built — running ./scripts/build_web.sh"
  "$ROOT/scripts/build_web.sh"
fi

exec python3 -m musync
