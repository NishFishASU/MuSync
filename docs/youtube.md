# YouTube on the Pi

Hub streaming into the existing PipeWire fan-out: **YouTube Data API search → queue → `yt-dlp` `-g` → `ffmpeg` → `musync_feeder` → Qudelix / SPP sinks**.

Secrets stay in environment variables / local files. Never commit API keys (see `.gitignore`).

## What is real

| | YouTube |
|--|---------|
| Auth | API key only (no user OAuth) |
| Search | YouTube Data API v3 — real when `MUSYNC_YOUTUBE_API_KEY` is set |
| Hub PCM | **yt-dlp** extracts best audio → **ffmpeg** writes Pulse/PipeWire sink `musync_feeder` |
| Without key | Rows show **unavailable**; local / Spotify still work |

## Setup (Pi)

### 1. Google Cloud API key

1. Open [Google Cloud Console](https://console.cloud.google.com/).
2. Create or select a project.
3. Enable **YouTube Data API v3** (APIs & Services → Library).
4. Create an **API key** (APIs & Services → Credentials → Create credentials → API key).
5. Optional but recommended: restrict the key to YouTube Data API v3.

### 2. Env on the Pi

**Preferred:** Streaming tab → paste the API key → **Save to musync.env**. The API
writes `~/musync.env` and returns only a masked status (never the raw key).

Or edit the file by hand:

```bash
# Prefer a file that is never committed, e.g. ~/musync.env
export MUSYNC_YOUTUBE_API_KEY="…"
# optional:
# export MUSYNC_YTDLP_BIN=yt-dlp
# export MUSYNC_FFMPEG_BIN=ffmpeg
# export MUSYNC_PW_FEEDER=musync_feeder
```

Copy from the template:

```bash
cp config/streaming.env.example ~/musync.env   # or edit existing
# fill MUSYNC_YOUTUBE_API_KEY=…
```

`run_pi.sh` / API startup auto-load `~/musync.env` and `config/streaming.local.env`.
Empty key soft-fails (local / Spotify / AirPlay still work).

### 3. Install yt-dlp + ffmpeg

**Use a current yt-dlp.** Apt packages on Raspberry Pi OS are often months behind
(e.g. `2025.04.30`) and fail YouTube extract / bot checks — MuSync soft-fails Play
with a clear **Upgrade yt-dlp** message while Search still works.

```bash
# Debian/Raspberry Pi OS examples:
sudo apt update
sudo apt install -y ffmpeg

# Prefer a current yt-dlp (not apt alone):
pipx install yt-dlp
# or self-update if already installed:
sudo yt-dlp -U
# pipx upgrade yt-dlp
# python3 -m pip install -U yt-dlp   # only if you manage PATH carefully

which yt-dlp ffmpeg
yt-dlp --version   # need ≥ 2025.10.14 (see MUSYNC_YTDLP_MIN_VERSION)
```

MuSync plays as:

1. `yt-dlp -f bestaudio/best -g URL` → direct media URL (failures surface here)
2. `ffmpeg -i <url> -vn -ac 2 -ar 48000 -f pulse musync_feeder`

If YouTube still blocks after a current yt-dlp, export browser cookies and set
`MUSYNC_YTDLP_COOKIES=/path/to/cookies.txt` (or
`MUSYNC_YTDLP_COOKIES_FROM_BROWSER=chromium`) in `~/musync.env`.

### 4. Run + UI

```bash
cd ~/MuSync && git pull origin main
./scripts/build_web.sh
export MUSYNC_BACKEND=pipewire
./scripts/run_pi.sh
```

1. Open `http://<Pi-IP>:8080`
2. Device setup → enable Qudelix + Phone SPP → Save
3. **Streaming** → confirm YouTube status (API key + tools)
4. Select **YouTube**, search, **Queue & play**
5. Or from **Queue** use **+ YouTube** to jump to search
6. Confirm audio on Qudelix / SPP

If the UI says **not configured**, the key is missing from the process env —
fill `~/musync.env`, restart `run_pi.sh`.

### Pi test checklist

1. Without YouTube env: local Play still works; YouTube rows show **unavailable**.
2. With API key: `GET /api/streaming` shows `youtube.configured: true`.
3. With yt-dlp + ffmpeg: `youtube.playback_mode` is `ytdlp_ffmpeg`.
4. Search returns hits; queue item `source_type=youtube`, `available=true`.
5. Play: `pactl list sinks short` shows one `musync_feeder`; sinks hear audio.
6. Stop/Pause: player stops; feeder swap does not tear down BT/SPP graph.

## Mock / laptop

```bash
export MUSYNC_BACKEND=mock
# Optional: exercise queue playability without yt-dlp/ffmpeg
export MUSYNC_YOUTUBE_API_KEY="dev-key"
export MUSYNC_STREAMING_MOCK=1
./scripts/run_dev.sh
```

Local queue + Play work without a real key. With `MUSYNC_STREAMING_MOCK=1` and a key set, queued `youtube:VIDEO_ID` items become playable on the software clock (no PCM).

## API

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/api/streaming` | Provider status (no secrets) — includes `youtube` |
| `GET`/`PUT` | `/api/streaming/credentials` | Masked keys → `~/musync.env` (YouTube key never returned raw) |
| `GET` | `/api/streaming/search?provider=youtube&q=` | Video search |
| `POST` | `/api/streaming/queue` | `{ provider: "youtube", uri: "youtube:VIDEO_ID", title?, artist?, activate? }` |

Locator form: `youtube:<11-char video id>` (watch URLs are also accepted when activating play).

## Troubleshooting

- **Search 400 / not configured** — set `MUSYNC_YOUTUBE_API_KEY` in `~/musync.env` or `config/streaming.local.env`, restart the API.
- **API key invalid / 403** — enable YouTube Data API v3 on the Google Cloud project; check key restrictions and quotas.
- **Search OK, Play soft-fails / no audio** — open Streaming status / health detail for
  `YouTube play soft-fail: …`. Common causes:
  - **Upgrade yt-dlp** (most common on Pi) — status shows
    `Upgrade yt-dlp (have 2025.04.30; need ≥2025.10.14)`. Run
    `sudo yt-dlp -U` or `pipx upgrade yt-dlp`, confirm `yt-dlp --version`,
    restart `./scripts/run_pi.sh`. Apt-only installs stay stale.
  - **yt-dlp bot check** (“Sign in to confirm you’re not a bot”) — upgrade first;
    if still blocked, export cookies (`MUSYNC_YTDLP_COOKIES` or
    `MUSYNC_YTDLP_COOKIES_FROM_BROWSER`), restart.
  - **yt-dlp / ffmpeg missing** — install both (or set `MUSYNC_YTDLP_BIN` /
    `MUSYNC_FFMPEG_BIN`).
  - **ffmpeg → musync_feeder failed** — `pactl list sinks short` must show
    `musync_feeder`; pipewire-pulse must be up. Check
    `config/youtube-<id>.ffmpeg.log`. Local/S21 audio OK means the fan-out is fine —
    the YouTube resolve/ffmpeg step is the problem.
- **Play fails: yt-dlp missing** — install `yt-dlp` (or set `MUSYNC_YTDLP_BIN`).
- **Play fails: ffmpeg missing** — install `ffmpeg` (or set `MUSYNC_FFMPEG_BIN`).
- **No audio (local also silent)** — feeder/sinks: see `docs/pi-pipewire.md`.
- **Quota exceeded** — Data API has a daily quota; cache searches / reduce query rate during demos.

Optional env: `MUSYNC_YTDLP_MIN_VERSION=2025.10.14` (floor), `MUSYNC_YTDLP_ALLOW_OLD=1`
(skip the version gate — not recommended on the Pi).
