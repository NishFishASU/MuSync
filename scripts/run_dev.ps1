# Run MuSync on Windows with the mock audio backend + built SvelteKit SPA.
# Loads %USERPROFILE%\musync.env and config\streaming.local.env via Python
# (see musync.streaming.credentials) when you start the API.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
if (-not $Root) { $Root = (Resolve-Path "$PSScriptRoot\..").Path }

$env:PYTHONPATH = if ($env:PYTHONPATH) { "$Root\backend;$env:PYTHONPATH" } else { "$Root\backend" }
$env:MUSYNC_BACKEND = if ($env:MUSYNC_BACKEND) { $env:MUSYNC_BACKEND } else { "mock" }
$env:MUSYNC_HOST = if ($env:MUSYNC_HOST) { $env:MUSYNC_HOST } else { "127.0.0.1" }
$env:MUSYNC_PORT = if ($env:MUSYNC_PORT) { $env:MUSYNC_PORT } else { "8080" }
$env:MUSYNC_WEB_DIR = if ($env:MUSYNC_WEB_DIR) { $env:MUSYNC_WEB_DIR } else { "$Root\web\build" }

Set-Location $Root

if (-not (Test-Path "$env:MUSYNC_WEB_DIR\index.html")) {
  Write-Host "UI not built — run: cd web; npm ci; npm run build"
  throw "Missing web/build — build the SvelteKit UI first"
}

Write-Host "MuSync mock (Windows)"
Write-Host "  listen:  http://$($env:MUSYNC_HOST):$($env:MUSYNC_PORT)"
Write-Host "  Spotify redirect (dashboard): http://127.0.0.1:$($env:MUSYNC_PORT)/api/streaming/spotify/callback"
Write-Host "  env file: $env:USERPROFILE\musync.env  or  $Root\config\streaming.local.env"
Write-Host "  Tip: use API port $($env:MUSYNC_PORT) in Spotify Dashboard — not Vite :5173."

python -m musync
