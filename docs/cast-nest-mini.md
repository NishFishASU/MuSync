# Google Nest Mini (Cast / Chromecast) output

Nest Mini is a **Wi‑Fi Cast** receiver — not Bluetooth. Expect higher latency than A2DP; MuSync defaults Cast delay to **300 ms** (slider up to 2000 ms).

## Path

```
musync_feeder.monitor
        │
        ▼  ffmpeg (-af adelay,volume → mp3 HTTP listen :8090)
http://<Pi-LAN-IP>:8090/stream.mp3
        │
        ▼  Cast play_media (pychromecast; soft-fail)
   Nest Mini / Chromecast
```

Same feeder fan-out as BT loopbacks and SPP TCP. Delay/volume live on this Cast stream’s `-af` only. Optional Cast `set_volume` is best-effort.

## Discovery

- mDNS `_googlecast._tcp.local.` via **zeroconf** (Refresh in Device setup).
- Soft-fail if `zeroconf` missing or mDNS blocked — API stays up; UI shows empty Cast list + `soft_warn`.
- Mock backend always offers a **Nest Mini** fixture.

## Enable (Pi)

1. Pi + Nest Mini on the **same Wi‑Fi** (prefer home / phone hotspot — same as SPP).
2. UI → Outputs / Device setup → **Refresh** → enable Nest Mini → **Save outputs**.
3. Play a hub-local track. Nest pulls `http://<Pi-IP>:8090/stream.mp3`.
4. Calibrate delay (start ~300 ms; Cast buffers more than BT).

```bash
pip install -r requirements.txt   # zeroconf + PyChromecast
export MUSYNC_BACKEND=pipewire
./scripts/run_pi.sh
```

## Soft-fail behavior

| Failure | Behavior |
|---------|----------|
| No `zeroconf` / mDNS | Empty Cast discovery; `soft_warn`; hub OK |
| No `pychromecast` | HTTP stream still served; `play_media` / set_volume soft-fail |
| Nest offline / kick fails | Stream stays up; BT + SPP unaffected |
| Delay/volume PATCH stream restart fails | Device state saved; warn logged; API 200 |

## Notes

- Cast HTTP port defaults to **8090** (SPP stays on **1234**).
- Nest needs the Pi **LAN IP**, not `0.0.0.0` (same Wi‑Fi panel tip as SPP).
- Do not expect BT-tight sync; use delay calibration against Qudelix / phone.

See also [spp-network-sink.md](spp-network-sink.md), [wifi-join.md](wifi-join.md), [demo-checklist.md](demo-checklist.md).
