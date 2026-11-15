# Spotify + AirPlay on the Pi

Hub streaming into the existing PipeWire fan-out: **source → `musync_feeder` → Qudelix / SPP sinks**.

**Spotify is fully supported** (Premium + librespot Connect). **Apple devices use AirPlay receive** (shairport-sync → PipeWire → feeder) — there is no MusicKit catalog search on the hub. See [airplay-receive.md](./airplay-receive.md). **YouTube** is supported via Data API search + yt-dlp → ffmpeg → feeder — see [youtube.md](./youtube.md).

Secrets stay in environment variables / local files. Never commit client secrets or tokens (see `.gitignore`).

## What is real vs limited

| | Spotify | AirPlay receive |
|--|---------|-----------------|
| Auth | OAuth (Authorization Code) via UI **Connect Spotify** | None (receiver on the Pi) |
| Search | Web API — real | N/A — play on the Apple device |
| Hub PCM | **librespot** Connect device with `PULSE_SINK=musync_feeder` | **shairport-sync** Source → loopback → feeder |
| Play path | Web API transfers track to librespot → feeder → sinks | AirPlay to Pi → capture into feeder |
| Without credentials | Rows show **unavailable**; **mock/local still work** | Install shairport / Refresh inputs |

## Spotify setup (Pi)

### Pi vs laptop mock

| | **Pi** (`MUSYNC_BACKEND=pipewire`) | **Laptop mock** (`MUSYNC_BACKEND=mock`) |
|--|--|--|
| Port | **8080** (`run_pi.sh` / systemd) | **8080**, or **8081** if 8080 busy |
| Connect redirect | **Loopback** `http://127.0.0.1:<port>/api/streaming/spotify/callback` (UI forces this) | Same loopback path |
| UI host | `musync.local` or Pi IP OK for browsing | `127.0.0.1` |
| Hub PCM | Needs **librespot** on PATH + Premium | Optional (`MUSYNC_STREAMING_MOCK=1` plays without librespot) |

**Connect Spotify always uses loopback** (`?loopback=1`) so opening the UI via
`http://musync.local:8080` still authorizes with `127.0.0.1` — matching the laptop mock
and the Dashboard URI. After Spotify redirects to the Pi’s `127.0.0.1` callback, finish
OAuth in a browser **on the Pi** (local display / VNC) or via an SSH tunnel from your
laptop (Streaming tab → **Connect from laptop**, or the commands below).

### 1. Spotify Developer app

1. Create an app at [developer.spotify.com/dashboard](https://developer.spotify.com/dashboard).
2. Add **both** loopback redirect URIs (Spotify allows multiple; must match the port you run):
   - `http://127.0.0.1:8080/api/streaming/spotify/callback`
   - `http://127.0.0.1:8081/api/streaming/spotify/callback`
3. Optional Pi LAN OAuth (phone browser on the hub IP — **not** mDNS); use Connect with
   `?loopback=0` only if this URI is registered:
   - `http://<Pi-IP>:8080/api/streaming/spotify/callback`
4. **Do not** register `localhost` or `musync.local` — Spotify forbids those redirect hosts.
5. Copy **Client ID** and **Client Secret**.

### 2. Env on the Pi

**Defaults:** `MUSYNC_PORT=8080` and
`MUSYNC_SPOTIFY_REDIRECT_URI=http://127.0.0.1:8080/api/streaming/spotify/callback`
(`run_pi.sh` and `deploy/systemd/musync.service` set these when unset).

```bash
# Prefer a file that is never committed, e.g. ~/musync.env
export MUSYNC_SPOTIFY_CLIENT_ID="…"
export MUSYNC_SPOTIFY_CLIENT_SECRET="…"
# Pi default (omit to use run_pi.sh / systemd default):
# export MUSYNC_SPOTIFY_REDIRECT_URI="http://127.0.0.1:8080/api/streaming/spotify/callback"
# Browser on another device → use http://<Pi-IP>:8080/... instead (also in Dashboard).
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

1. Open `http://musync.local:8080` (mDNS UI) or `http://<Pi-IP>:8080`
2. Device setup → enable Qudelix + Phone SPP → Save
3. **Streaming** → paste Client ID/Secret → **Save to musync.env**
   (redirect defaults to `http://127.0.0.1:8080/…`). Status shows **OAuth callback**,
   **librespot** (on PATH / running), and scopes.
4. **Connect Spotify** — authorize URL uses **loopback** `127.0.0.1` (not musync.local).
   Complete the Spotify redirect in a browser that can open `http://127.0.0.1:8080`
   on the Pi (local UI, VNC, or SSH tunnel from a laptop — see below).
5. **Queue** → search a track → **Queue & play**
6. Confirm **librespot** status turns running on Play; audio on Qudelix / SPP

If the UI says **not configured**, credentials are missing — use the Streaming
form or edit `~/musync.env`, then **Connect Spotify** again. Empty keys soft-fail
(local / AirPlay / YouTube-without-key still work).

### Connect from laptop (SSH tunnel)

Spotify’s callback is always `http://127.0.0.1:<port>/…`. When you browse the hub at
`musync.local` / Pi IP, tunnel that port to your laptop:

1. Streaming → **Connect from laptop (SSH tunnel)** → **Copy** (or download `.sh` / `.ps1`).
2. On the laptop (keep the window open):

   ```bash
   ssh -N -L 8080:127.0.0.1:8080 pi@<Pi-IP>
   # or: ./scripts/spotify_oauth_tunnel.sh <Pi-IP>
   ```

3. Open `http://127.0.0.1:8080/streaming/` on the **same laptop** → **Connect Spotify**.
4. After Connect succeeds you can close the tunnel; search/play use the Pi over LAN.

Override SSH user with `MUSYNC_SSH_USER` (default `pi`). API:
`GET /api/streaming/spotify/ssh-tunnel` (+ `.sh` / `.ps1` downloads).

### Pi test checklist

1. Without Spotify env: local Play still works; Spotify rows show **unavailable**.
2. With env + OAuth: `GET /api/streaming` shows `spotify.authenticated: true`.
3. Search returns hits; queue item `source_type=spotify`, `available=true`.
4. Play: `pactl list sinks short` shows one `musync_feeder`; librespot running; sinks hear audio.
5. Stop/Pause: Spotify pauses; feeder swap does not tear down BT/SPP graph.
6. Logout: tokens cleared; Spotify becomes unavailable again; local still plays.

## AirPlay receive (Apple devices)

MusicKit / Apple Music **search is not offered** on the hub. To play from an
iPhone or Mac into MuSync:

1. Install and run [shairport-sync](https://github.com/mikebrady/shairport-sync).
2. **Inputs** → **Refresh inputs** (auto-binds `capture_path=airplay`).
3. Queue → **+ Live** / **+ Device** on the AirPlay host → **Play**.
4. AirPlay audio to the Pi from the Apple device.

Optional: `MUSYNC_AIRPLAY_SOURCE=<PipeWire source name>` in `~/musync.env`.

Full steps: [airplay-receive.md](./airplay-receive.md).

## Mock / laptop (no credentials)

```bash
export MUSYNC_BACKEND=mock
./scripts/run_dev.sh
```

Local queue + Play work. Streaming Spotify shows “not configured”; AirPlay card
shows mock/setup status. No secrets required.

Optional software-clock Spotify after OAuth on laptop:

```bash
export MUSYNC_STREAMING_MOCK=1
```

## Windows laptop mock + OAuth redirect

1. Copy `config/streaming.env.example` → `%USERPROFILE%\musync.env` **or**
   `config\streaming.local.env`. Use `KEY=value` lines (no bash `export` required;
   `export KEY=` is also accepted).
2. Set Client ID/Secret. In the Spotify Developer Dashboard register **both**:

   - `http://127.0.0.1:8080/api/streaming/spotify/callback`
   - `http://127.0.0.1:8081/api/streaming/spotify/callback`

   **Pi default is always `:8080`.** On the laptop, keep `:8080` unless it is
   taken — then `MUSYNC_PORT=8081` and set `MUSYNC_SPOTIFY_REDIRECT_URI` to the
   `:8081` URI. Spotify rejects `localhost` and `*.local` — always use
   `127.0.0.1`. Do **not** register Vite’s `:5173` — OAuth callback hits
   FastAPI, not the Vite dev server.
3. Build UI (`cd web; npm ci; npm run build`), then:

   ```powershell
   .\scripts\run_dev.ps1
   # or: $env:PYTHONPATH="backend"; $env:MUSYNC_BACKEND="mock"; python -m musync
   ```

4. Open `http://127.0.0.1:8080` → **Streaming** → **Connect Spotify** → approve
   the consent dialog (includes `user-read-private` for search) → tokens return
   to `/api/streaming/spotify/callback` → SPA `/?spotify=connected`.
5. **Queue** → search tracks → **Queue & play**.
6. Optional: `MUSYNC_STREAMING_MOCK=1` so authenticated tracks play on the
   software clock without librespot.

## API

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/api/streaming` | Spotify + AirPlay status (no secrets) |
| `GET` | `/api/mdns` | UI `musync.local` advertise (not for OAuth redirects) |
| `GET`/`PUT` | `/api/streaming/credentials` | Masked keys → `~/musync.env` (secrets never raw) |
| `GET` | `/api/streaming/spotify/login` | Start OAuth redirect |
| `GET` | `/api/streaming/spotify/authorize-url` | JSON preflight (url or error) |
| `GET` | `/api/streaming/spotify/ssh-tunnel` | Copyable `ssh -L` + steps (optional `?host=`) |
| `GET` | `/api/streaming/spotify/ssh-tunnel.sh` | Downloadable bash tunnel script |
| `GET` | `/api/streaming/spotify/ssh-tunnel.ps1` | Downloadable PowerShell tunnel script |
| `GET` | `/api/streaming/spotify/callback` | OAuth return |
| `POST` | `/api/streaming/spotify/logout` | Clear tokens |
| `GET` | `/api/streaming/search?provider=spotify&q=` | Track search |
| `POST` | `/api/streaming/queue` | `{ provider: "spotify", uri, title?, … }` |

## Troubleshooting

- **403 on play** — Spotify Free accounts cannot control Connect; need Premium.
- **403 after Connect / Internal Server Error on callback** — Spotify app is in **development mode** and your account is not allowlisted. After the auth code exchange, MuSync calls `GET /v1/me`; Spotify returns **403 Forbidden** until you add the signed-in user under Developer Dashboard → your app → **User Management**. MuSync redirects to `/?spotify=error&detail=…` with that message (no bare 500).
- **Queue search 403 “Insufficient client scope”** — search no longer sends `market=from_token` (that required `user-read-private`). It uses `MUSYNC_SPOTIFY_MARKET` (default **US**). Streaming status shows **granted scopes** + search market. Reconnect on Streaming only if playback scopes are missing.
- **Queue search 400 “Invalid limit”** — Spotify **Development Mode** apps allow at most **10** results per `/v1/search` request (limit 15–50 returns this misleading error). MuSync clamps to 10 by default; set `MUSYNC_SPOTIFY_SEARCH_MAX=50` only if the app has Extended Quota Mode.
- **No Connect device** — install librespot; check `MUSYNC_LIBRESPOT_NAME` matches; ensure PipeWire/Pulse is up and `musync_feeder` exists (`./scripts/run_pi.sh` + Play once).
- **OAuth redirect mismatch** — Dashboard must list the exact `MUSYNC_SPOTIFY_REDIRECT_URI` (`127.0.0.1:8080` and/or `:8081`; never Vite `:5173`, never `localhost` / `musync.local`). mDNS `musync.local` is UI-only — see [mdns.md](./mdns.md).
- **Connect does nothing / credentials missing** — use Streaming → Save to musync.env, or fill `%USERPROFILE%\musync.env` / `config/streaming.local.env`; Connect shows a clear error when Client ID/Secret are unset.
- **Duplicate feeders** — fixed by idempotent feeder purge on startup (see `docs/pi-pipewire.md`).
- **AirPlay silent** — confirm shairport Source in `wpctl status`, Refresh inputs, Play the AirPlay live host, then AirPlay *to* the Pi (see [airplay-receive.md](./airplay-receive.md)).
