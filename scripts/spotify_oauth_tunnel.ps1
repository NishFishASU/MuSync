# MuSync — SSH tunnel for Spotify loopback Connect (laptop → Pi)
#
#   .\scripts\spotify_oauth_tunnel.ps1 <Pi-IP>
#   # keep running, then open http://127.0.0.1:8080/streaming/ → Connect Spotify
#
# Prefer the Streaming UI download link (bakes in the live Pi IP).
# Env: MUSYNC_PORT (default 8080), MUSYNC_SSH_USER (default pi)
$ErrorActionPreference = "Stop"
$Port = if ($env:MUSYNC_PORT) { [int]$env:MUSYNC_PORT } else { 8080 }
$User = if ($env:MUSYNC_SSH_USER) { $env:MUSYNC_SSH_USER } else { "pi" }
$HostName = $args[0]
if (-not $HostName -or $HostName -eq "PI_IP") {
  Write-Error "Usage: .\spotify_oauth_tunnel.ps1 <Pi-IP>"
}
Write-Host "Tunnel: localhost:$Port → ${User}@${HostName}:$Port"
Write-Host "Next: open http://127.0.0.1:$Port/streaming/ → Connect Spotify"
ssh -N -L "${Port}:127.0.0.1:${Port}" "${User}@${HostName}"
