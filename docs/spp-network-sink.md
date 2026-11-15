# SPP TCP network sink (Simple Protocol Player)

MuSync can fan the same session out to a phone running **Simple Protocol Player** over TCP, alongside Qudelix / BT600 A2DP sinks.

## Verified standalone path (user-tested)

On the Pi (listens; phone connects):

```bash
ffmpeg -re -i media/test.wav -f s16le -acodec pcm_s16le -ar 48000 -ac 1 \
  tcp://0.0.0.0:1234?listen=1
```

In SPP on the phone:

| Setting | Value |
|---------|--------|
| Host | Pi Tailscale or LAN IP |
| Port | `1234` |
| Format | PCM s16le |
| Rate | `48000` |
| Channels | `1` (mono) |

## Why MuSync used to fail (“unable to stream”)

| Manual (works) | Old MuSync (broken) |
|----------------|---------------------|
| `-re -i <file>` always emits PCM | `-f pulse -i musync_feeder.monitor` often emits **no frames** until Play feeds the null sink |
| No `-af` | `-af volume=…` / `adelay=…` delayed first PCM → SPP handshake timeout |
| stderr free | `stderr=PIPE` unread could block ffmpeg |

## MuSync listen shape (fixed)

Idle (SPP enabled, not playing) — continuous silence so the phone can connect:

```text
ffmpeg -f lavfi -i anullsrc=channel_layout=mono:sample_rate=48000 \
  -f s16le -acodec pcm_s16le -ar 48000 -ac 1 \
  tcp://0.0.0.0:1234?listen=1
```

Playing a hub-local file — **same argv as the proven manual command**:

```text
ffmpeg -re -i /path/to/track.wav \
  -f s16le -acodec pcm_s16le -ar 48000 -ac 1 \
  tcp://0.0.0.0:1234?listen=1
```

No `-af`. BT sinks still use the PipeWire feeder via mpv; SPP mirrors the local file like the manual demo.

## Pi + Qudelix + SPP phone

```bash
export MUSYNC_BACKEND=pipewire
# stop manual ffmpeg listen / old MuSync if any
pkill -f 'ffmpeg.*1234' || true
./scripts/run_pi.sh

# UI Device setup: enable Qudelix by name + Phone SPP → Save
# Play a hub-local track, then connect phone (or connect during idle silence, reconnect after Play if needed)
# Phone: SPP → Pi LAN/Tailscale IP:port → matching rate/channels s16le
```

## Pi verify (`ss` / `ps`)

```bash
# 1) MuSync SPP ffmpeg should be listening on 1234
ss -ltnp | grep 1234
# expect: LISTEN ...:1234 ... users:(("ffmpeg",pid=…,fd=…))

# 2) Exact argv — should look like manual (file) or anullsrc (idle); NO -af, NO pulse.monitor
ps -o args= -C ffmpeg | tr ' ' '\n' | head -40
# or:
tr '\0' ' ' < /proc/$(pgrep -f 'ffmpeg.*1234' | head -1)/cmdline; echo

# 3) If port missing: check log
tail -50 config/spp-spp-1.ffmpeg.log

# 4) Manual still works when MuSync is stopped
pkill -f 'python3 -m musync' || true
pkill -f 'ffmpeg.*1234' || true
ffmpeg -re -i media/test.wav -f s16le -acodec pcm_s16le -ar 48000 -ac 1 \
  tcp://0.0.0.0:1234?listen=1
```

Notes:

- Listener starts when the SPP sink is **enabled** (idle `anullsrc`), not only on Play.
- On Play with a local file, SPP switches to `-re -i <file>` (may drop one TCP session — reconnect phone after Play, or connect after Play).
- Format must match the device row: default **48000 Hz / 1 ch / s16le**, port **1234**, host = Pi LAN or Tailscale IP (not `0.0.0.0` in the phone app).
- If a **manual** `ffmpeg …?listen=1` still owns 1234, MuSync cannot bind — stop it first.
