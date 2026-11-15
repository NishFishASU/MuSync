# Silent-disco volume profiles

## Explicit UI (#27)

Hub profiles store a **preferred level**. Applying a profile sets each sink to:

```text
volume = clamp(preferred_level × Device.gain)
```

Optional per-output overrides replace `preferred_level` for that sink id (still × `Device.gain`). Delay / mute / enabled are never touched. The Gain slider on each output is headphone/IEM **calibration**, not the learned latent below.

## Learned latents

Persist observations in SQLite (`config/user_volumes.sqlite`):

| Symbol | Meaning | Where |
|--------|---------|--------|
| `v_{u,d}` | Volume user `u` set on device `d` | `user_device_volumes` |
| `S_u` | Per-person sensitivity | `VolumeProfile.sensitivity` |
| `G_d` | Per-device relative loudness | `device_gains` table / API `device_gains` |

Model:

```text
v_{u,d} ≈ S_u · G_d
```

Fit with log-space ALS; pin scale so geo-mean(`S_u`)=1 over users with data.

**Suggest / preset** a new device: `volume ≈ clamp(S_u · G_d)`.

**Update:** when an assigned user moves the volume slider, the hub records `v_{u,d}` and re-fits `S_u` and `G_d`.

**Cross-infer:** if A set device1 + device2 and B set device1, then `G_2` (from A) and `S_B ≈ v_{B,1}/G_1` yield `v̂_{B,2} = S_B · G_2`.

Assign a user per output via `PUT /api/profiles/assignments` (UI: “User on this output”). Preferred × gain remains the explicit Use / Re-apply path.
