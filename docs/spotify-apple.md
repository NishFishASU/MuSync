# Spotify + Apple Music on the Pi

Hub streaming into the existing PipeWire fan-out: **source → `musync_feeder` → Qudelix / SPP sinks**.

**Spotify is fully supported** (Premium + librespot Connect). **Apple Music is best-effort on Linux** (catalog search + optional AirPlay capture). **YouTube** (Data API + yt-dlp/ffmpeg) is documented in [docs/youtube.md](youtube.md).

Secrets stay in environment variables / local files. Never commit client secrets, tokens, or `.p8` keys (see `.gitignore`).

## What is real vs limited

| | Spotify | Apple Music |
|--|---------|-------------|
| Auth | OAuth (Authorization Code) via UI **Connect Spotify** | MusicKit developer token from Team ID + Key ID + `.p8` |
| Search | Web API — real | Catalog API — real when keys set |
| Hub PCM | **librespot** Connect device with `PULSE_SINK=musync_feeder` | **Limited** — no official Linux player |
| Play path | Web API transfers track to librespot → feeder → sinks | Optional: AirPlay → shairport-sync → PipeWire source → feeder |
| Without credentials | Rows show **unavailable**; **mock/local still work** | Same |

## Spotify setup (Pi)

### 1. Spotify Developer app

1. Create an app at [developer.spotify.com/dashboard](https://developer.spotify.com/dashboard).
2. Add redirect URI (must match env):
   - Laptop: `http://127.0.0.1:8080/api/streaming/spotify/callback`
   - Pi LAN: `http://<Pi-IP>:8080/api/streaming/spotify/callback`
3. Copy **Client ID** and **Client Secret**.

### 2. Env on the Pi

```bash
# Prefer a file that is never committed, e.g. ~/musync.env
export MUSYNC_SPOTIFY_CLIENT_ID="…"
export MUSYNC_SPOTIFY_CLIENT_SECRET="…"
export MUSYNC_SPOTIFY_REDIRECT_URI="http://<Pi-IP>:8080/api/streaming/spotify/callback"
# optional:
# export MUSYNC_SPOTIFY_TOKEN_PATH="$HOME/MuSync/config/spotify_tokens.json"
# export MUSYNC_LIBRESPOT_BIN=librespot
# export MUSYNC_LIBRESPOT_NAME=MuSync
# export MUSYNC_PW_FEEDER=musync_feeder
```

Tokens land in `config/spotify_tokens.json` (gitignored, mode 0600).

### 3. Install librespot

librespot advertises a Spotify Connect speaker and writes PCM to Pulse/PipeWire.

```bash
# Example (pick a build that matches your Pi OS):
# cargo install librespot
# or install a distro/package build that provides `librespot` on PATH
which librespot
```

MuSync starts it as:

`PULSE_SINK=musync_feeder librespot --name MuSync --backend pulseaudio …`

**Spotify Premium is required** for Connect playback control.

### 4. Run + UI (OAuth Connect link — not a password form)

```bash
cd ~/MuSync && git pull origin main
# ./scripts/run_pi.sh auto-loads ~/musync.env + activates .venv
./scripts/build_web.sh
export MUSYNC_BACKEND=pipewire
./scripts/run_pi.sh
```

1. Open `http://<Pi-IP>:8080`
2. Device setup → enable Qudelix + Phone SPP → Save
3. **Streaming** → **Connect Spotify** (OAuth only — same-origin link → Spotify
   authorize → `/api/streaming/spotify/callback` → tokens). MuSync never asks
   for your Spotify password; Client ID/Secret stay in `~/musync.env`.
4. **Queue** → search a track → **Queue & play** (search UI lives on Queue, not Streaming)
5. Confirm audio on Qudelix / SPP (`Simple Protocol Player` → Pi IP:port)

If the UI says **not configured**, credentials are missing from the process env —
fill `~/musync.env`, restart `run_pi.sh`, then use **Connect Spotify** again.

### Pi test checklist

1. Without Spotify env: local Play still works; Spotify rows show **unavailable**.
2. With env + OAuth: `GET /api/streaming` shows `spotify.authenticated: true`.
3. Search returns hits; queue item `source_type=spotify`, `available=true`.
4. Play: `pactl list sinks short` shows one `musync_feeder`; librespot running; sinks hear audio.
5. Stop/Pause: Spotify pauses; feeder swap does not tear down BT/SPP graph.
6. Logout: tokens cleared; Spotify becomes unavailable again; local still plays.

## Apple Music (best-effort)

### Keys (optional)

From [Apple Developer → Keys](https://developer.apple.com/account/resources/authkeys/list) create a **MusicKit** key:

```bash
export MUSYNC_APPLE_TEAM_ID="…"
export MUSYNC_APPLE_KEY_ID="…"
export MUSYNC_APPLE_PRIVATE_KEY_PATH="$HOME/keys/AuthKey_XXXXX.p8"  # never commit
# export MUSYNC_APPLE_STOREFRONT=us
# export MUSYNC_APPLE_MUSIC_USER_TOKEN="…"   # optional; library/personalization
```

With keys: **search works**. Full DRM playback on the hub does **not**.

There is **no Spotify-style user OAuth redirect** for Apple Music hub PCM on
Linux. MusicKit developer tokens are mintable from Team/Key/`.p8` in env;
personalized library needs a MusicKit JS user token (browser), and DRM still
blocks a native Linux player. Prefer Spotify Connect + librespot for real hub
audio, or AirPlay capture below.

### Optional AirPlay capture

1. Run [shairport-sync](https://github.com/mikebrady/shairport-sync) so it appears as a PipeWire **source**.
2. Point MuSync at it:

```bash
export MUSYNC_APPLE_AIRPLAY_SOURCE="shairport-sync"   # exact PipeWire source name
```

3. On an iPhone/Mac, AirPlay to that receiver, then queue an Apple row and Play — MuSync loopbacks that source into `musync_feeder`.

Without AirPlay: Apple stays limited (clear UI note); use Spotify or Local for demo audio.

## Mock / laptop (no credentials)

```bash
export MUSYNC_BACKEND=mock
./scripts/run_dev.sh
```

Local queue + Play work. Streaming panel shows “not configured”. No secrets required.

Optional software-clock Spotify/Apple after OAuth on laptop:

```bash
export MUSYNC_STREAMING_MOCK=1
```

## Windows laptop mock + OAuth redirect

1. Copy `config/streaming.env.example` → `%USERPROFILE%\musync.env` **or**
   `config\streaming.local.env`. Use `KEY=value` lines (no bash `export` required;
   `export KEY=` is also accepted).
2. Set Client ID/Secret. Redirect URI must match the **API** port (default **8080**):

   `http://127.0.0.1:8080/api/streaming/spotify/callback`

   If you set `MUSYNC_PORT=8081`, use `:8081` in **both** the Spotify Developer
   Dashboard and `MUSYNC_SPOTIFY_REDIRECT_URI`. Do **not** register Vite’s
   `:5173` — OAuth callback hits FastAPI, not the Vite dev server.
3. Build UI (`cd web; npm ci; npm run build`), then:

   ```powershell
   .\scripts\run_dev.ps1
   # or: $env:PYTHONPATH="backend"; $env:MUSYNC_BACKEND="mock"; python -m musync
   ```

4. Open `http://127.0.0.1:8080` → **Connect Spotify** → approve → tokens return
   to `/api/streaming/spotify/callback` → SPA `/?spotify=connected`.
5. Optional: `MUSYNC_STREAMING_MOCK=1` so authenticated tracks play on the
   software clock without librespot.

## API

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/api/streaming` | Provider status (no secrets) |
| `GET` | `/api/streaming/spotify/login` | Start OAuth redirect |
| `GET` | `/api/streaming/spotify/authorize-url` | JSON preflight (url or error) |
| `GET` | `/api/streaming/spotify/callback` | OAuth return |
| `POST` | `/api/streaming/spotify/logout` | Clear tokens |
| `GET` | `/api/streaming/search?provider=spotify&q=` | Track search |
| `POST` | `/api/streaming/queue` | `{ provider, uri, title?, artist?, activate? }` |

## Troubleshooting

- **403 on play** — Spotify Free accounts cannot control Connect; need Premium.
- **403 after Connect / Internal Server Error on callback** — Spotify app is in **development mode** and your account is not allowlisted. After the auth code exchange, MuSync calls `GET /v1/me`; Spotify returns **403 Forbidden** until you add the signed-in user under Developer Dashboard → your app → **User Management**. MuSync redirects to `/?spotify=error&detail=…` with that message (no bare 500).
- **No Connect device** — install librespot; check `MUSYNC_LIBRESPOT_NAME` matches; ensure PipeWire/Pulse is up and `musync_feeder` exists (`./scripts/run_pi.sh` + Play once).
- **OAuth redirect mismatch** — redirect URI in the Spotify dashboard must equal `MUSYNC_SPOTIFY_REDIRECT_URI` (API port `:8080` / `:8081`, never Vite `:5173`).
- **Connect does nothing / credentials missing** — fill `%USERPROFILE%\musync.env` or `config/streaming.local.env`, restart the API; Connect shows a clear error when Client ID/Secret are unset.
- **Duplicate feeders** — fixed by idempotent feeder purge on startup (see `docs/pi-pipewire.md`).
