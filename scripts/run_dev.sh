#!/usr/bin/env bash
# Run MuSync locally with the mock audio backend.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PYTHONPATH="${ROOT}/backend${PYTHONPATH:+:$PYTHONPATH}"
export MUSYNC_BACKEND="${MUSYNC_BACKEND:-mock}"
export MUSYNC_HOST="${MUSYNC_HOST:-127.0.0.1}"
export MUSYNC_PORT="${MUSYNC_PORT:-8080}"
cd "$ROOT"
exec python3 -m musync
