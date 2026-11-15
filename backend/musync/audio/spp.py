"""Simple Protocol Player (SPP) TCP network output helpers.

Verified phone path (user-tested)::

    ffmpeg -re -i media/test.wav -f s16le -acodec pcm_s16le -ar 48000 -ac 1 \\
      tcp://0.0.0.0:1234?listen=1

SPP app: host = Pi LAN/Tailscale IP, port 1234, 48000 Hz mono s16le.

MuSync matches that argv shape. Do **not** insert ``-af adelay/volume`` on the
TCP listen path — delay before first PCM makes Simple Protocol Player report
"unable to stream". Pulse ``feeder.monitor`` is also a poor idle source: it
often produces no frames until something plays into the feeder, so the phone
handshake starves. Prefer ``-re -i <file>`` while playing, and ``anullsrc``
silence while idle so the port stays fed with PCM.
"""

from __future__ import annotations

from typing import Literal

from ..models import Device, DeviceKind

SppInputKind = Literal["file", "lavfi", "pulse"]


def spp_tcp_url(device: Device) -> str:
    host = (device.listen_host or "0.0.0.0").strip() or "0.0.0.0"
    return f"tcp://{host}:{int(device.listen_port)}?listen=1"


def spp_anullsrc(device: Device) -> str:
    """Continuous silent PCM at the sink's rate/channels (idle listen)."""
    layout = "mono" if int(device.channels) <= 1 else "stereo"
    return f"anullsrc=channel_layout={layout}:sample_rate={int(device.sample_rate)}"


def spp_audio_filter(device: Device) -> str:
    """Optional adelay+volume chain — **not** used on the proven SPP listen path."""
    parts: list[str] = []
    delay = max(0, int(device.delay_ms))
    if delay:
        if int(device.channels) >= 2:
            parts.append(f"adelay={delay}|{delay}:all=1")
        else:
            parts.append(f"adelay={delay}:all=1")
    vol = 0.0 if device.mute else float(device.volume)
    parts.append(f"volume={vol:.4f}")
    return ",".join(parts)


def build_spp_ffmpeg(
    device: Device,
    *,
    input_kind: SppInputKind,
    input_spec: str,
    ffmpeg_bin: str = "ffmpeg",
) -> list[str]:
    """Build SPP argv matching the proven manual listen shape (no ``-af``).

    Manual::

        ffmpeg -re -i <file> -f s16le -acodec pcm_s16le -ar 48000 -ac 1 \\
          tcp://0.0.0.0:1234?listen=1
    """
    if device.kind != DeviceKind.NETWORK_SPP:
        raise ValueError(f"{device.id} is not a network_spp sink")
    if not input_spec.strip():
        raise ValueError("SPP ffmpeg input_spec must be non-empty")

    cmd: list[str] = [ffmpeg_bin, "-hide_banner", "-loglevel", "warning"]
    if input_kind == "file":
        # Exact proven shape: realtime file read → raw PCM TCP listen.
        cmd += ["-re", "-i", input_spec]
    elif input_kind == "lavfi":
        # Idle keep-alive: continuous silence so the phone can connect before Play.
        cmd += ["-f", "lavfi", "-i", input_spec]
    elif input_kind == "pulse":
        # Optional live fan-out; may starve until the feeder has clients.
        cmd += ["-f", "pulse", "-i", input_spec]
    else:
        raise ValueError(f"Unknown SPP input_kind: {input_kind!r}")

    cmd += [
        "-f",
        "s16le",
        "-acodec",
        "pcm_s16le",
        "-ar",
        str(int(device.sample_rate)),
        "-ac",
        str(int(device.channels)),
        spp_tcp_url(device),
    ]
    return cmd


def build_spp_ffmpeg_from_file(
    device: Device,
    *,
    path: str,
    ffmpeg_bin: str = "ffmpeg",
) -> list[str]:
    """Verified shape: ``ffmpeg -re -i <file> … tcp://…?listen=1``."""
    return build_spp_ffmpeg(
        device, input_kind="file", input_spec=path, ffmpeg_bin=ffmpeg_bin
    )


def build_spp_ffmpeg_from_pulse(
    device: Device,
    *,
    feeder: str,
    ffmpeg_bin: str = "ffmpeg",
) -> list[str]:
    """Pulse monitor → TCP (no ``-af``). Prefer file/anullsrc for phone handshake."""
    return build_spp_ffmpeg(
        device,
        input_kind="pulse",
        input_spec=f"{feeder}.monitor",
        ffmpeg_bin=ffmpeg_bin,
    )


def build_spp_ffmpeg_idle(
    device: Device,
    *,
    ffmpeg_bin: str = "ffmpeg",
) -> list[str]:
    """Silent PCM listen so SPP can connect before Play."""
    return build_spp_ffmpeg(
        device,
        input_kind="lavfi",
        input_spec=spp_anullsrc(device),
        ffmpeg_bin=ffmpeg_bin,
    )


def spp_plan_notes(device: Device, *, feeder: str) -> list[str]:
    idle = " ".join(build_spp_ffmpeg_idle(device))
    pulse = " ".join(build_spp_ffmpeg_from_pulse(device, feeder=feeder))
    return [
        f"{device.id}: SPP TCP listen {spp_tcp_url(device)} "
        f"({device.sample_rate} Hz / {device.channels} ch s16le) — no -af",
        f"{device.id}: idle → {idle}",
        f"{device.id}: play(local file) → ffmpeg -re -i <path> … {spp_tcp_url(device)}",
        f"{device.id}: pulse fallback → {pulse}",
    ]
