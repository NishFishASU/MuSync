# Demo checklist — Qudelix BT + Phone SPP

Short path for a reliable two-sink demo on a Raspberry Pi 5. No YAML MAC editing.

## Before the room

1. Pi on power + LAN (or phone hotspot — [wifi-join.md](wifi-join.md)).
2. Qudelix powered and previously paired (`./scripts/pair_hci.sh hci0` or `bluetoothctl`).
3. Phone: Simple Protocol Player installed; note Pi IP from UI **Wi‑Fi → Pi IP**.
4. Drop a short WAV/FLAC/MP3 into `media/`.
5. UI built: `./scripts/build_web.sh` (or rsync `web/build/`).

## Boot

```bash
cd ~/MuSync && git checkout main && git pull origin main
export MUSYNC_BACKEND=pipewire
./scripts/run_pi.sh
```

`run_pi.sh` clears stray `ffmpeg` on the SPP port, uses `.venv`, prints preflight READY/MISSING lines, and prints an HDMI/console **LAN URL + printable QR** (`http://<Pi-IP>:8080`). Open that URL, scan the QR, or use `http://musync.local:8080` (mDNS UI). Spotify OAuth needs `127.0.0.1` (SSH tunnel) or `https://…` (Cloudflare Tunnel) — not `musync.local` or plain `http://<Pi-IP>` ([spotify-apple.md](spotify-apple.md), [mdns.md](mdns.md), [wifi-join.md](wifi-join.md)).

## Device setup (once per reboot if sinks look MISSING)

1. **Device setup → Refresh** — Qudelix by name; backend soft-reconnects paired BT.
2. Enable Qudelix + **Phone SPP** (port `1234`, 48000 Hz, mono unless you changed it).
3. **Save sinks**.
4. Header / sink pills: **READY** (linked / listen) vs **MISSING** (not up yet).

## Play

1. Catalog → pick hub-local track → **Play**.
2. Phone SPP → host = **Pi IP** (never `0.0.0.0`), port / rate / channels matching the sink row, PCM s16le.
3. If Play switched SPP from silence → file, reconnect the phone once.
4. **Qudelix AVRCP:** with local (or Spotify/YouTube) playing to Qudelix, press headset play/pause/skip — Now playing should follow. Check `GET /api/avrcp` → `player_registered: true` (needs `python3-dbus`). Volume buttons still sync Absolute Volume → Outputs slider.

## If something fails

| Symptom | Check |
|---------|--------|
| SPP “unable to stream” | `ss -ltnp \| grep 1234` — ffmpeg listening? `config/spp-spp-1.ffmpeg.log` |
| Port busy | `./scripts/run_pi.sh` again (kills stray ffmpeg) or `pkill -f 'ffmpeg.*:1234'` |
| BT MISSING after reboot | Device setup → Refresh (auto `bluetoothctl connect`); power-cycle Qudelix |
| Header dry-run | PipeWire down / missing tools — install packages in README |
| Wrong phone host | Use Pi LAN/Tailscale IP from Wi‑Fi panel, not listen host `0.0.0.0` |

## Spotify + YouTube (hub PCM)

If local / A2DP work but Spotify or YouTube do not, follow the full bring-up:

**[pi-bringup.md](pi-bringup.md)** — `libasound2-dev` + `cargo install librespot`,
current `yt-dlp`, restart MuSync, then verify Connect device **MuSync** and one
YouTube play.

Details: [spp-network-sink.md](spp-network-sink.md), [pi-pipewire.md](pi-pipewire.md),
[spotify-apple.md](spotify-apple.md), [youtube.md](youtube.md).
