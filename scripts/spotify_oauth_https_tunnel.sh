#!/usr/bin/env bash
# MuSync — HTTPS public tunnel for Spotify OAuth from a phone / off-Pi browser.
#
# Spotify Developer Dashboard accepts ONLY:
#   http://127.0.0.1:<port>/api/streaming/spotify/callback
#   https://<host>/api/streaming/spotify/callback
# Plain http://<Pi-LAN-IP>:8080/... is rejected as Invalid redirect URI.
#
# Usage (on the Pi, hub already listening):
#   ./scripts/spotify_oauth_https_tunnel.sh
# Then:
#   1) Copy https://….trycloudflare.com from the log
#   2) Register https://….trycloudflare.com/api/streaming/spotify/callback in Spotify Dashboard
#   3) Streaming UI → paste that URI → Save to musync.env → Connect Spotify
#   4) Keep this tunnel up until Connect finishes
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck disable=SC1091
[[ -f "${HOME}/musync.env" ]] && set -a && source "${HOME}/musync.env" && set +a || true
PORT="${MUSYNC_PORT:-8080}"
if ! command -v cloudflared >/dev/null 2>&1; then
  echo "cloudflared not found." >&2
  echo "Install: https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/install-and-setup/installation/" >&2
  echo "Or:     https://github.com/cloudflare/cloudflared/releases" >&2
  exit 1
fi
echo "MuSync HTTPS tunnel → http://127.0.0.1:${PORT}"
echo "After the URL prints: register …/api/streaming/spotify/callback in Spotify Dashboard,"
echo "Save as MUSYNC_SPOTIFY_REDIRECT_URI (Streaming tab), then Connect from your phone."
echo "(Repo root: ${ROOT})"
exec cloudflared tunnel --url "http://127.0.0.1:${PORT}"
