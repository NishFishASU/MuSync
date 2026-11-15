"""HTTPS public tunnel helper for Spotify OAuth from a phone / off-Pi browser.

Spotify Developer Dashboard accepts only:
  - http://127.0.0.1:<port>/…  (SSH local-forward / on-Pi browser)
  - https://<host>/…          (Cloudflare Tunnel, ngrok, etc.)

Plain ``http://<Pi-LAN-IP>:8080/…`` is rejected as **Invalid redirect URI**.

Quick path (ephemeral Cloudflare trycloudflare URL):

  cloudflared tunnel --url http://127.0.0.1:8080

Copy the printed ``https://….trycloudflare.com`` URL, append
``/api/streaming/spotify/callback``, register it in the Spotify Dashboard,
paste into Streaming → redirect URI → Save, then Connect while the tunnel runs.

Env already honored: ``MUSYNC_SPOTIFY_REDIRECT_URI``.
"""

from __future__ import annotations

from typing import Any

from ..lan_url import hub_port
from .spotify import _CALLBACK_PATH, default_redirect_uri


def https_callback_from_tunnel_base(base: str) -> str:
    """``https://host`` or full URL → ``https://host/api/streaming/spotify/callback``."""
    raw = (base or "").strip().rstrip("/")
    if not raw:
        return ""
    if "://" not in raw:
        raw = f"https://{raw}"
    if raw.lower().endswith(_CALLBACK_PATH):
        return raw
    # Strip accidental path noise; keep host only when user pasted UI root.
    from urllib.parse import urlparse, urlunparse

    parsed = urlparse(raw)
    if (parsed.scheme or "").lower() != "https":
        # Force https — Spotify will not accept http://tunnel hosts without TLS.
        parsed = parsed._replace(scheme="https")
    path = parsed.path or ""
    if path.rstrip("/").endswith("/api/streaming/spotify/callback"):
        return urlunparse(
            (parsed.scheme, parsed.netloc, path.rstrip("/") or _CALLBACK_PATH, "", "", "")
        )
    return urlunparse((parsed.scheme, parsed.netloc, _CALLBACK_PATH, "", "", ""))


def https_tunnel_steps(*, port: int | None = None) -> list[str]:
    p = port if port is not None else hub_port()
    return [
        f"On the Pi (or any host that can reach 127.0.0.1:{p}), install cloudflared "
        "and run: cloudflared tunnel --url "
        f"http://127.0.0.1:{p}",
        "Copy the printed https://….trycloudflare.com URL.",
        "In Spotify Developer Dashboard → your app → Redirect URIs, add "
        f"https://<that-host>{_CALLBACK_PATH} (exact match).",
        "Streaming → paste that full callback into Spotify redirect URI → Save to musync.env.",
        "Keep the tunnel running → Connect Spotify from your phone on the hotspot "
        "(UI may still be http://<Pi-IP>:8080).",
        "After Connect succeeds you can stop the tunnel; tokens stay on the Pi.",
    ]


def build_https_tunnel_info(*, port: int | None = None) -> dict[str, Any]:
    """JSON-ready HTTPS tunnel helper for ``GET /api/streaming`` + credentials UI."""
    p = port if port is not None else hub_port()
    loopback = default_redirect_uri()
    command = f"cloudflared tunnel --url http://127.0.0.1:{p}"
    example = f"https://YOUR-SUBDOMAIN.trycloudflare.com{_CALLBACK_PATH}"
    return {
        "port": p,
        "ready": True,
        "command": command,
        "example_redirect_uri": example,
        "loopback_uri": loopback,
        "callback_path": _CALLBACK_PATH,
        "script_sh": "/api/streaming/spotify/https-tunnel.sh",
        "steps": https_tunnel_steps(port=p),
        "hint": (
            "Spotify rejects http://<LAN-IP> redirects. For phone Connect use an HTTPS "
            "tunnel (cloudflared) and set MUSYNC_SPOTIFY_REDIRECT_URI to "
            f"{example}, or use the SSH tunnel + 127.0.0.1 path on a laptop."
        ),
        "docs": "docs/spotify-apple.md#connect-from-phone-https-tunnel",
    }


def bash_https_tunnel_script(*, port: int | None = None) -> str:
    """Downloadable helper — starts cloudflared quick tunnel if installed."""
    p = port if port is not None else hub_port()
    return f"""#!/usr/bin/env bash
# MuSync — HTTPS public tunnel for Spotify OAuth (phone / off-Pi)
# Spotify allows https://… redirects; plain http://<Pi-IP> is rejected.
# 1) Run this on the Pi. 2) Register https://<printed-host>/api/streaming/spotify/callback
#    in the Spotify Dashboard. 3) Paste that URI into Streaming → Save → Connect.
set -euo pipefail
PORT="{p}"
if ! command -v cloudflared >/dev/null 2>&1; then
  echo "cloudflared not found. Install from https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/install-and-setup/installation/" >&2
  echo "Then re-run: cloudflared tunnel --url http://127.0.0.1:${{PORT}}" >&2
  exit 1
fi
echo "Starting Cloudflare quick tunnel → http://127.0.0.1:${{PORT}}"
echo "Copy the https://….trycloudflare.com URL, append {_CALLBACK_PATH},"
echo "register it in Spotify Dashboard, Save as MUSYNC_SPOTIFY_REDIRECT_URI, Connect."
exec cloudflared tunnel --url "http://127.0.0.1:${{PORT}}"
"""
