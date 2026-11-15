"""Simple Protocol Player (SPP) TCP network output helpers.

Verified phone path (user)::

    ffmpeg -re -i media/test.wav -f s16le -acodec pcm_s16le -ar 48000 -ac 1 \\
      tcp://0.0.0.0:1234?listen=1

SPP app: host = Pi LAN/Tailscale IP, port 1234, 48000 Hz mono s16le.

MuSync fans the PipeWire feeder into the same TCP listen shape so Qudelix
bluez sinks and the phone SPP sink share one transport clock.
"""

from __future__ import annotations

from ..models import Device, DeviceKind


def spp_tcp_url(device: Device) -> str:
    host = (device.listen_host or "0.0.0.0").strip() or "0.0.0.0"
    return f"tcp://{host}:{int(device.listen_port)}?listen=1"


def spp_audio_filter(device: Device) -> str:
    """ffmpeg -af chain: optional adelay + volume (mute → 0)."""
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


def build_spp_ffmpeg_from_pulse(
    device: Device,
    *,
    feeder: str,
    ffmpeg_bin: str = "ffmpeg",
) -> list[str]:
    """Live fan-out: pulse/PipeWire feeder.monitor → TCP listen (s16le).

    No ``-re`` — source is already realtime. Delay/volume via ``-af``.
    """
    if device.kind != DeviceKind.NETWORK_SPP:
        raise ValueError(f"{device.id} is not a network_spp sink")
    return [
        ffmpeg_bin,
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "pulse",
        "-i",
        f"{feeder}.monitor",
        "-af",
        spp_audio_filter(device),
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


def build_spp_ffmpeg_from_file(
    device: Device,
    *,
    path: str,
    ffmpeg_bin: str = "ffmpeg",
) -> list[str]:
    """Standalone verified shape (file → TCP). Useful for docs / dry-run notes."""
    return [
        ffmpeg_bin,
        "-hide_banner",
        "-loglevel",
        "info",
        "-re",
        "-i",
        path,
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


def spp_plan_notes(device: Device, *, feeder: str) -> list[str]:
    cmd = " ".join(build_spp_ffmpeg_from_pulse(device, feeder=feeder))
    return [
        f"{device.id}: SPP TCP listen {spp_tcp_url(device)} "
        f"({device.sample_rate} Hz / {device.channels} ch s16le)",
        f"{device.id}: {cmd}",
    ]
