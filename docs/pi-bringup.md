# Pi 5 bring-up — Spotify + YouTube hub play

Short checklist after cloning MuSync on a Raspberry Pi 5. Local files / phone A2DP
can work before this; **Spotify** and **YouTube** need the tools below.

## 1. Pull + base tools

```bash
cd ~/MuSync && git checkout main && git pull origin main
sudo apt update
sudo apt install -y pipewire wireplumber pipewire-pulse pulseaudio-utils \
  bluez mpv ffmpeg build-essential pkg-config libasound2-dev libssl-dev
./scripts/build_web.sh
```

## 2. Spotify Connect (librespot)

```bash
# Rust if needed: curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
# then: source "$HOME/.cargo/env"

sudo apt install -y libasound2-dev   # required — cargo fails without ALSA headers
cargo install librespot

# Confirm binary (MuSync also auto-finds ~/.cargo/bin without PATH):
ls -l ~/.cargo/bin/librespot
~/.cargo/bin/librespot --version
```

OAuth + Premium still required — [spotify-apple.md](./spotify-apple.md)
(SSH tunnel or HTTPS tunnel for Connect).

## 3. YouTube (yt-dlp + ffmpeg)

Apt yt-dlp is often too old (e.g. `2025.04.30`). Prefer a current build:

```bash
sudo apt install -y ffmpeg
pipx install yt-dlp        # or: sudo yt-dlp -U / pipx upgrade yt-dlp
which yt-dlp ffmpeg
yt-dlp --version           # need ≥ 2025.10.14
```

API key: Streaming tab → Save, or `MUSYNC_YOUTUBE_API_KEY` in `~/musync.env`
— [youtube.md](./youtube.md).

## 4. Restart MuSync

```bash
# Stop any old process, then:
./scripts/run_pi.sh
# Preflight should show: librespot READY, yt-dlp READY, ffmpeg READY, PipeWire READY
```

Systemd users: `sudo systemctl restart musync` (unit PATH includes
`~/.cargo/bin` and `~/.local/bin`).

## 5. Verify (copy-paste on the Pi)

```bash
# Feeder present
pactl list sinks short | grep musync_feeder

# Streaming status (librespot_available / ytdlp_version)
curl -s http://127.0.0.1:8080/api/streaming | python3 -m json.tool | head -80

# After Queue → Spotify play: librespot process + Connect name MuSync
pgrep -a librespot
# Phone Spotify app → Devices should list "MuSync"

# YouTube: search works with API key; Play needs current yt-dlp
yt-dlp --version
# Then UI: Streaming YouTube → search → Queue & play one track → hear Qudelix/SPP
```

| Check | Expect |
|-------|--------|
| Streaming → librespot | **on PATH** or **running · MuSync** |
| Spotify Queue & play | Audio on hub sinks; phone shows **MuSync** (not phone speakers) |
| Play without librespot | Clear UI error (install hint) — not silent / not phone |
| YouTube play | PCM on sinks; if soft-fail, banner says **Upgrade yt-dlp** or cookies |

## Related

- Demo day (Qudelix + SPP): [demo-checklist.md](./demo-checklist.md)
- Spotify OAuth / tunnels: [spotify-apple.md](./spotify-apple.md)
- YouTube details: [youtube.md](./youtube.md)
- PipeWire graph: [pi-pipewire.md](./pi-pipewire.md)
