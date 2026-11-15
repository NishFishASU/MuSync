"""SSH ``-L`` helper for Spotify loopback OAuth from a laptop.

Connect Spotify always redirects to ``http://127.0.0.1:<port>/…``. When the
hub UI is opened via ``musync.local`` / Pi LAN IP, finish OAuth in a browser
that can reach the Pi's loopback — typically:

  ssh -N -L 8080:127.0.0.1:8080 pi@<Pi-IP>

Then open ``http://127.0.0.1:8080/streaming/`` on the laptop and Connect.

Env:
  MUSYNC_SSH_USER   SSH login (default ``pi``)
  MUSYNC_PORT       Hub listen port (default 8080)
"""

from __future__ import annotations

import os
import re
from typing import Any, Iterable, Optional

from ..lan_url import hub_port, pick_lan_ip

_HOST_SAFE = re.compile(r"^[A-Za-z0-9._:-]+$")
_USER_SAFE = re.compile(r"^[A-Za-z0-9._-]+$")


def ssh_tunnel_user() -> str:
    """SSH username for the tunnel command (``MUSYNC_SSH_USER``, default ``pi``)."""
    raw = (os.environ.get("MUSYNC_SSH_USER") or "pi").strip() or "pi"
    if not _USER_SAFE.match(raw):
        return "pi"
    return raw


def sanitize_tunnel_host(raw: str | None) -> str:
    """Allow only hostname / IPv4-ish tokens for the generated command."""
    host = (raw or "").strip()
    if not host or not _HOST_SAFE.match(host):
        return ""
    if host.startswith("127.") or host.lower() in {"localhost", "musync.local"}:
        return ""
    return host


def resolve_tunnel_host(
    ips: Iterable[str] | None = None,
    *,
    override: str | None = None,
) -> str:
    """Prefer explicit override, else first LAN IPv4 from ``ips`` / guess."""
    from_override = sanitize_tunnel_host(override)
    if from_override:
        return from_override
    return sanitize_tunnel_host(pick_lan_ip(ips))


def ssh_tunnel_command(
    *,
    host: str,
    port: int | None = None,
    user: str | None = None,
) -> str:
    """``ssh -N -L <port>:127.0.0.1:<port> user@host`` (or ``PI_IP`` placeholder)."""
    p = port if port is not None else hub_port()
    u = user or ssh_tunnel_user()
    h = sanitize_tunnel_host(host) or "PI_IP"
    return f"ssh -N -L {p}:127.0.0.1:{p} {u}@{h}"


def ssh_tunnel_open_url(port: int | None = None) -> str:
    """Laptop URL after the tunnel is up — Streaming tab on loopback."""
    p = port if port is not None else hub_port()
    return f"http://127.0.0.1:{p}/streaming/"


def ssh_tunnel_steps(*, port: int | None = None) -> list[str]:
    """Short Connect-from-laptop guide (shown in Streaming UI)."""
    p = port if port is not None else hub_port()
    return [
        f"Copy the SSH command below and run it on your laptop (keep the window open).",
        f"Open {ssh_tunnel_open_url(p)} in a browser on the same laptop "
        f"(not musync.local — Spotify needs 127.0.0.1).",
        "Streaming → Connect Spotify → approve OAuth; the callback hits the Pi via the tunnel.",
        "You can close the tunnel after Connect succeeds; search/play use the Pi over LAN.",
    ]


def build_ssh_tunnel_info(
    ips: Iterable[str] | None = None,
    *,
    host: str | None = None,
    port: int | None = None,
    user: str | None = None,
) -> dict[str, Any]:
    """JSON-ready tunnel helper for ``GET /api/streaming`` + dedicated endpoints."""
    p = port if port is not None else hub_port()
    u = user or ssh_tunnel_user()
    h = resolve_tunnel_host(ips, override=host)
    ready = bool(h)
    command = ssh_tunnel_command(host=h or "PI_IP", port=p, user=u)
    hint = (
        "Spotify Connect uses loopback 127.0.0.1 — tunnel the Pi port to your laptop, "
        f"then open {ssh_tunnel_open_url(p)} and Connect. "
        "Override user with MUSYNC_SSH_USER; fill PI_IP from the Wi‑Fi tab if missing."
    )
    if not ready:
        hint = (
            "No LAN IP yet — join Wi‑Fi first (or replace PI_IP with the Pi address). "
            + hint
        )
    return {
        "port": p,
        "host": h,
        "user": u,
        "ready": ready,
        "command": command,
        "open_url": ssh_tunnel_open_url(p),
        "script_sh": "/api/streaming/spotify/ssh-tunnel.sh",
        "script_ps1": "/api/streaming/spotify/ssh-tunnel.ps1",
        "steps": ssh_tunnel_steps(port=p),
        "hint": hint,
    }


def bash_tunnel_script(
    *,
    host: str,
    port: int | None = None,
    user: str | None = None,
) -> str:
    """Downloadable bash helper — fills host/port/user at request time."""
    p = port if port is not None else hub_port()
    u = user or ssh_tunnel_user()
    safe = sanitize_tunnel_host(host)
    if safe:
        host_block = f'HOST="{safe}"\n'
    else:
        host_block = (
            'HOST="${1:-}"\n'
            'if [[ -z "$HOST" || "$HOST" == "PI_IP" ]]; then\n'
            '  echo "Usage: $0 <Pi-IP>" >&2\n'
            "  exit 1\n"
            "fi\n"
        )
    return f"""#!/usr/bin/env bash
# MuSync — SSH tunnel for Spotify loopback Connect (laptop → Pi)
# Keep this running, then open http://127.0.0.1:{p}/streaming/ and Connect Spotify.
set -euo pipefail
PORT="{p}"
USER_NAME="{u}"
{host_block}echo "Tunnel: localhost:${{PORT}} → ${{USER_NAME}}@${{HOST}}:${{PORT}}"
echo "Next: open http://127.0.0.1:${{PORT}}/streaming/ → Connect Spotify"
exec ssh -N -L "${{PORT}}:127.0.0.1:${{PORT}}" "${{USER_NAME}}@${{HOST}}"
"""


def powershell_tunnel_script(
    *,
    host: str,
    port: int | None = None,
    user: str | None = None,
) -> str:
    """Downloadable PowerShell helper for Windows OpenSSH."""
    p = port if port is not None else hub_port()
    u = user or ssh_tunnel_user()
    h = sanitize_tunnel_host(host)
    host_expr = f'"{h}"' if h else '($args[0])'
    return f"""# MuSync — SSH tunnel for Spotify loopback Connect (laptop → Pi)
# Keep this running, then open http://127.0.0.1:{p}/streaming/ and Connect Spotify.
$ErrorActionPreference = "Stop"
$Port = {p}
$User = "{u}"
$HostName = {host_expr}
if (-not $HostName -or $HostName -eq "PI_IP") {{
  Write-Error "Usage: .\\ssh-tunnel.ps1 <Pi-IP>"
}}
Write-Host "Tunnel: localhost:$Port → ${{User}}@${{HostName}}:$Port"
Write-Host "Next: open http://127.0.0.1:$Port/streaming/ → Connect Spotify"
ssh -N -L "${{Port}}:127.0.0.1:${{Port}}" "${{User}}@${{HostName}}"
"""
