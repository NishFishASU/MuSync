#!/usr/bin/env bash
# Run MuSync on a Raspberry Pi 5 with the PipeWire backend.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PYTHONPATH="${ROOT}/backend${PYTHONPATH:+:$PYTHONPATH}"
export MUSYNC_BACKEND="${MUSYNC_BACKEND:-pipewire}"
export MUSYNC_HOST="${MUSYNC_HOST:-0.0.0.0}"
export MUSYNC_PORT="${MUSYNC_PORT:-8080}"
export MUSYNC_DEVICES_CONFIG="${MUSYNC_DEVICES_CONFIG:-$ROOT/config/devices.yaml}"
export MUSYNC_MEDIA_DIR="${MUSYNC_MEDIA_DIR:-$ROOT/media}"
cd "$ROOT"
exec python3 -m musync
