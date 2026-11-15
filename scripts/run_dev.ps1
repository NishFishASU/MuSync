# Run MuSync on Windows with the mock audio backend + built SvelteKit SPA.
# Loads %USERPROFILE%\musync.env and config\streaming.local.env via Python
# (see musync.streaming.credentials) when you start the API.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
if (-not $Root) { $Root = (Resolve-Path "$PSScriptRoot\..").Path }

$env:PYTHONPATH = if ($env:PYTHONPATH) { "$Root\backend;$env:PYTHONPATH" } else { "$Root\backend" }
$env:MUSYNC_BACKEND = if ($env:MUSYNC_BACKEND) { $env:MUSYNC_BACKEND } else { "mock" }
$env:MUSYNC_HOST = if ($env:MUSYNC_HOST) { $env:MUSYNC_HOST } else { "127.0.0.1" }
# Refuse empty MUSYNC_PORT (would build http://127.0.0.1:/…).
if (-not $env:MUSYNC_PORT) { $env:MUSYNC_PORT = "8080" }
$env:MUSYNC_WEB_DIR = if ($env:MUSYNC_WEB_DIR) { $env:MUSYNC_WEB_DIR } else { "$Root\web\build" }

Set-Location $Root

# Refuse empty-port Spotify redirect from env (host:/path).
$redir = $env:MUSYNC_SPOTIFY_REDIRECT_URI
if ($redir -and ($redir -match '://[^/]+:/' -or $redir -match '://[^/]+:$')) {
  Write-Host "  warn:    refusing empty-port Spotify redirect — using 127.0.0.1:$($env:MUSYNC_PORT)"
  $env:MUSYNC_SPOTIFY_REDIRECT_URI = "http://127.0.0.1:$($env:MUSYNC_PORT)/api/streaming/spotify/callback"
}

if (-not (Test-Path "$env:MUSYNC_WEB_DIR\index.html")) {
  Write-Host "UI not built — run: cd web; npm ci; npm run build"
  throw "Missing web/build — build the SvelteKit UI first"
}

Write-Host "MuSync mock (Windows)"
Write-Host "  listen:  http://$($env:MUSYNC_HOST):$($env:MUSYNC_PORT)"
Write-Host "  Spotify Dashboard: register BOTH 127.0.0.1:8080 and :8081 (not localhost / musync.local)"
Write-Host "  Spotify redirect (this run): http://127.0.0.1:$($env:MUSYNC_PORT)/api/streaming/spotify/callback"
Write-Host "  env file: $env:USERPROFILE\musync.env  or  $Root\config\streaming.local.env"
Write-Host "  Tip: Pi default is :8080; set `$env:MUSYNC_PORT=8081 when 8080 is taken (EDB). Not Vite :5173."

python -m musync
