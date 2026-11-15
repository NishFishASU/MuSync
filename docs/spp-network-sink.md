# SPP TCP network sink (Simple Protocol Player)

MuSync can fan the same session out to a phone running **Simple Protocol Player** over TCP, alongside Qudelix / USB HCI A2DP sinks.

## Verified standalone path (user-tested)

On the Pi (listens; phone connects):

```bash
ffmpeg -re -i media/test.wav -f s16le -acodec pcm_s16le -ar 48000 -ac 1 \
  tcp://0.0.0.0:1234?listen=1
```

MuSync adds low-latency knobs on top of this shape: `-fflags nobuffer`,
`-flags low_delay`, `-probesize 32`, `-analyzeduration 0`, `-flush_packets 1`,
and `tcp_nodelay=1` on the listen URL (Nagle off).

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
| Continuous PCM from t=0 | Pulse starvation (not `-af` itself) left the phone with no stream |
| stderr free | `stderr=PIPE` unread could block ffmpeg |

## MuSync listen shape (fixed)

Idle (SPP enabled, not playing) — continuous silence so the phone can connect. Per-sink delay/volume use `-af` on this output only:

```text
ffmpeg -fflags nobuffer -flags low_delay -probesize 32 -analyzeduration 0 \
  -f lavfi -i anullsrc=channel_layout=mono:sample_rate=48000 \
  -af adelay=80:all=1,volume=0.7500 \
  -f s16le -acodec pcm_s16le -ar 48000 -ac 1 -flush_packets 1 \
  tcp://0.0.0.0:1234?listen=1&tcp_nodelay=1
```

Playing a hub-local file — proven listen shape + low-latency flags + the same per-sink `-af`:

```text
ffmpeg -fflags nobuffer -flags low_delay -probesize 32 -analyzeduration 0 \
  -re -i /path/to/track.wav \
  -af adelay=80:all=1,volume=0.7500 \
  -f s16le -acodec pcm_s16le -ar 48000 -ac 1 -flush_packets 1 \
  tcp://0.0.0.0:1234?listen=1&tcp_nodelay=1
```

`PATCH /api/devices/{id}` `{ delay_ms, volume, mute }` restarts **this** SPP listener only. BT sinks keep using PipeWire `module-loopback latency_msec` + `wpctl` on their own node — never a global gain.

## Pi + Qudelix + SPP phone

```bash
export MUSYNC_BACKEND=pipewire
./scripts/run_pi.sh   # clears stray ffmpeg on SPP ports; backend also frees the port on listen start

# UI Device setup: Refresh → enable Qudelix by name + Phone SPP → Save
# Sink pills: READY (listening / linked) vs MISSING
# Play a hub-local track, then connect phone (or connect during idle silence, reconnect after Play if needed)
# Phone: SPP → Pi LAN/Tailscale IP from Wi‑Fi panel (not 0.0.0.0):port → matching rate/channels s16le
```

See also [demo-checklist.md](demo-checklist.md).

## Pi verify (`ss` / `ps`)

```bash
# 1) MuSync SPP ffmpeg should be listening on 1234
ss -ltnp | grep 1234
# expect: LISTEN ...:1234 ... users:(("ffmpeg",pid=…,fd=…))

# 2) Exact argv — file or anullsrc (idle); may include -af adelay/volume; NO pulse.monitor
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
