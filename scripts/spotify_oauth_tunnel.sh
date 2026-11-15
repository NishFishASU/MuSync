#!/usr/bin/env bash
# MuSync — SSH tunnel for Spotify loopback Connect (laptop → Pi)
#
# Connect Spotify always redirects to http://127.0.0.1:<port>/…
# When the hub UI is on musync.local / Pi LAN, tunnel the port to your laptop:
#
#   ./scripts/spotify_oauth_tunnel.sh <Pi-IP>
#   # keep running, then:
#   open http://127.0.0.1:8080/streaming/  → Connect Spotify
#
# Prefer the Streaming UI "Copy" / download links (they bake in the live Pi IP).
# Env: MUSYNC_PORT (default 8080), MUSYNC_SSH_USER (default pi)
set -euo pipefail

PORT="${MUSYNC_PORT:-8080}"
USER_NAME="${MUSYNC_SSH_USER:-pi}"
HOST="${1:-}"

if [[ -z "$HOST" || "$HOST" == "PI_IP" ]]; then
  echo "Usage: $0 <Pi-IP>" >&2
  echo "Example: $0 192.168.43.10" >&2
  echo "Then open http://127.0.0.1:${PORT}/streaming/ and Connect Spotify." >&2
  exit 1
fi

echo "Tunnel: localhost:${PORT} → ${USER_NAME}@${HOST}:${PORT}"
echo "Next: open http://127.0.0.1:${PORT}/streaming/ → Connect Spotify"
exec ssh -N -L "${PORT}:127.0.0.1:${PORT}" "${USER_NAME}@${HOST}"
