# Arduino Uno Q — parallel audio hub (dual-target with Pi)

**Status:** Scaffold / architecture plan (MDAS-48)  
**Audience:** Builds that use an **Arduino Uno Q** as the MuSync host (API + BT dongles + synced outs)

## Decision

MuSync is a **dual-target product**:

| Target | Role | Who it’s for |
|--------|------|----------------|
| **Raspberry Pi 5** | Primary / proven PipeWire hub | Existing demos, 6× HCI path, richest USB topology |
| **Arduino Uno Q** | **Full parallel hub** — same product shape | People who have Uno Q: FastAPI + web UI + BT dongles + per-sink delay/volume synced outs |

Uno Q is **not** Grove-only and **not** a thin remote for the Pi. Grove / MCU controls remain an optional later add-on on either host.

Both targets expose the **same `/api/*` contract** and the same SvelteKit SPA. Audio backends differ by platform; the Pi stack stays the reference implementation.

```
                    ┌─────────────────────────┐
   Sources ───────► │  Shared FastAPI + SPA   │
   (local / stream /│  devices · transport ·  │
    capture …)      │  profiles · queue …     │
                    └───────────┬─────────────┘
                                │
              ┌─────────────────┴─────────────────┐
              ▼                                   ▼
   Raspberry Pi 5                      Arduino Uno Q (Debian MPU)
   PipeWireBackend (proven)            Uno Q audio backend (port)
   BlueZ × onboard + USB HCI           BlueZ × onboard + USB HCI*
   Soft-fail ≤6 sinks                  Start at 1–2; scale only if proven
              │                                   │
              └──────── per-sink delay/volume ────┘
                        → A2DP / SPP outs
```

\*USB multi-HCI on Uno Q is a **hardware gate**, not assumed equal to Pi 5.

## Shared vs platform-specific

| Layer | Shared (one tree) | Platform-specific |
|-------|-------------------|-------------------|
| HTTP API + models | `backend/musync/app.py`, `models.py` | — |
| Web UI | `web/` | — |
| Device YAML schema | `config/*.yaml` | Example maps: Pi vs Uno Q |
| AudioBackend | Interface in `audio/base.py` | Pi: `pipewire.py` · Uno Q: port under `platforms/uno-q/audio/` → eventually same package |
| Deploy / OS | — | `scripts/run_pi.sh` vs `platforms/uno-q/scripts/` |
| Streaming feeders | Prefer shared | May soft-fail on Uno Q until host binaries / services exist |

**Selection (planned):** `MUSYNC_PLATFORM=pi|uno-q` (default `pi`) and/or `MUSYNC_BACKEND=pipewire|mock|uno_q`. Mock remains the laptop path for both.

## Uno Q Linux / audio stack — known limits

Plan the port around these constraints (Debian on Qualcomm Dragonwing QRB2210 + STM32 MCU). Details evolve; treat as working assumptions and re-measure on hardware.

| Area | Limitation | MuSync implication |
|------|------------|--------------------|
| **Onboard BT audio** | A2DP **speaker/sink output** works via BlueZ + PipeWire; **HSP/HFP mic/headset profiles unavailable** (firmware) | Outbound synced speakers: OK to pursue. Phone-as-BT-**source** via onboard radio: do **not** depend on it — prefer network/USB capture like Pi notes |
| **Headless PipeWire / BlueZ** | SSH sessions need WirePlumber BlueZ **seat-monitoring disabled**; **lightdm** greeter PipeWire can steal the BT transport | Ship `platforms/uno-q/scripts/configure_headless_bt_audio.sh` (stub → real conf from bring-up). Enable linger for the service user |
| **App Lab sandbox** | App Lab bricks often **cannot** reach host binaries (`ffmpeg`, `mpg123`, …) and struggle with USB/BT audio | Run MuSync as a **host Debian service** (venv + systemd), not inside App Lab Speaker bricks. MCU/Grove can still use App Lab later |
| **USB host / multi-HCI** | Uno Q USB topology and power ≠ Pi 5 + powered hub | **Gate:** prove 1 onboard or 1 USB dongle A2DP first → 2 → N. Soft-fail missing adapters (same pattern as Pi). Do not promise 6× until measured |
| **Audio graph** | PipeWire/WirePlumber present on Debian image; PulseAudio still appears in some community BT recipes | Prefer **PipeWire-first** (align with Pi `PipeWireBackend`). Document Pulse only as fallback if PW graph fails on-image |
| **Capture / HDMI / USB gadget** | USB Audio Class from host ALSA is reported workable; App Lab path is flaky; gadget/OTG roles unclear vs Pi | Phase Uno Q capture after local file + BT outs. Reuse ALSA discover patterns; mark HDMI/gadget as Pi-primary until proven |
| **CPU / decode** | Quad A53 @ ~2 GHz, useful NPU/GPU but not a Pi 5 class USB host | Keep one feeder; defer heavy concurrent capture+stream until outs are stable |
| **MCU half** | STM32 for real-time I/O | Optional Grove control surface **later**; out of scope for hub MVP on Uno Q |

Community references (external, verify on your image): Arduino forum threads on BlueZ headless WirePlumber seat monitoring / lightdm masking; App Lab USB sound-card limits; Bluetooth A2DP speaker-only on Uno Q Debian.

## Target capability matrix

| Capability | Pi 5 (now) | Uno Q (this track) |
|------------|------------|--------------------|
| FastAPI `/api/*` + SPA | Yes | Yes (same) |
| Local file → synced BT outs | Yes | **Goal** — stub → bring-up |
| Per-sink delay / volume / mute | Yes | **Goal** — same API |
| Multi USB HCI dongles | ≤6 soft-fail | **Prove stepwise**; config template starts small |
| SPP TCP sink | Yes | Stretch after BT outs |
| Spotify / YouTube / AirPlay feeders | Yes (with host pkgs) | Soft-fail until host services installed |
| Phone BT A2DP **input** | Yes (dongle/onboard) | Unlikely on onboard; optional USB HCI later |
| Grove physical UI | Deferred | Deferred (optional add-on, not required for hub) |

## Repo layout (scaffold)

```
docs/uno-q-hub.md                 # this plan
platforms/uno-q/
  README.md                       # bring-up order + how stubs plug in
  audio/
    README.md                     # backend port notes
    backend.py                    # stub AudioBackend skeleton
  config/
    devices.uno-q.example.yaml    # 1–2 sink starter map
  scripts/
    configure_headless_bt_audio.sh
    run_uno_q.sh
    smoke_api.sh
```

Pi code under `backend/musync/audio/pipewire.py` remains the **reference**. The Uno Q stub mirrors the interface; implementation fills in once headless BT + `wpctl` paths are verified on-device.

## Bring-up phases

1. **Scaffold (this PR, draft):** dual-target docs + `platforms/uno-q` stubs + README pointers. No merge required for Pi demo day.
2. **Host service on Uno Q:** Debian venv, `MUSYNC_BACKEND=mock` first (API/UI only), then wire PipeWire-class backend.
3. **Headless BT audio OS config:** seat-monitoring + lightdm mask + linger; one A2DP speaker in `wpctl status`.
4. **Single-sink sync path:** local file feeder → delay/volume → one bluez sink via shared API.
5. **USB dongle #2:** soft-fail enumeration; document power/hub needs.
6. **Parity features:** SPP, streaming feeders, capture — only after outs are stable.
7. **Optional Grove:** MCU events → local hub API (not a Pi remote).

## Explicit non-goals (this scaffold)

- Replacing or freezing the Pi hub  
- Claiming 6× A2DP on Uno Q without measurement  
- Shipping App Lab–only audio as the hub  
- Grove-first / companion-only design  
- SunHacks / prize strategy text in this tree  

## References

- Pi PipeWire graph: [pi-pipewire.md](./pi-pipewire.md)  
- Shared API / layout: repo root `README.md`  
- Stub tree: `platforms/uno-q/`  
- Web contract: `web/src/lib/api.js`
