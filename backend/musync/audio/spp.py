"""Simple Protocol Player (SPP) TCP network output helpers.

Verified phone path (user-tested)::

    ffmpeg -re -i media/test.wav -f s16le -acodec pcm_s16le -ar 48000 -ac 1 \\
      tcp://0.0.0.0:1234?listen=1

SPP app: host = Pi LAN/Tailscale IP, port 1234, 48000 Hz mono s16le.

Per-sink delay/volume belong on this output only (not a global transport
control). On ``file`` / ``lavfi`` (anullsrc) inputs we attach ``-af``
adelay+volume: those sources emit PCM immediately, so the phone handshake
still gets frames (adelay inserts silence samples; it does not starve the
socket). Prefer ``-re -i <file>`` while playing and ``anullsrc`` while idle —
never rely on pulse ``feeder.monitor`` for the handshake (it often emits no
frames until something plays into the feeder).
"""

from __future__ import annotations

import logging
import subprocess
import time
from typing import Literal

from ..models import Device, DeviceKind

log = logging.getLogger(__name__)

SppInputKind = Literal["file", "lavfi", "pulse"]


def free_spp_listen_port(port: int, *, settle_s: float = 0.2) -> str:
    """Best-effort: stop orphan ``ffmpeg`` holding an SPP TCP listen port.

    Manual demos and crashed MuSync runs often leave ``ffmpeg … :PORT?listen=1``
    bound, so the next MuSync start cannot listen. Safe to call before every
    SPP restart — our tracked listener should already be terminated.
    """
    port = int(port)
    if port < 1 or port > 65535:
        return "invalid port"
    # Match both ``tcp://0.0.0.0:1234?listen=1`` and ``:1234`` argv forms.
    pattern = f"ffmpeg.*:{port}"
    try:
        proc = subprocess.run(
            ["pkill", "-f", pattern],
            capture_output=True,
            text=True,
            timeout=3.0,
        )
    except FileNotFoundError:
        return "pkill missing"
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"pkill error: {exc}"
    if settle_s > 0:
        time.sleep(settle_s)
    # pkill returns 0 when it signaled something, 1 when no match.
    if proc.returncode == 0:
        return f"killed stray ffmpeg on :{port}"
    if proc.returncode == 1:
        return f"port :{port} free (no stray ffmpeg)"
    detail = (proc.stderr or proc.stdout or "").strip()
    return f"pkill rc={proc.returncode}" + (f" ({detail})" if detail else "")


def spp_tcp_url(device: Device) -> str:
    host = (device.listen_host or "0.0.0.0").strip() or "0.0.0.0"
    return f"tcp://{host}:{int(device.listen_port)}?listen=1"


def spp_anullsrc(device: Device) -> str:
    """Continuous silent PCM at the sink's rate/channels (idle listen)."""
    layout = "mono" if int(device.channels) <= 1 else "stereo"
    return f"anullsrc=channel_layout={layout}:sample_rate={int(device.sample_rate)}"


def spp_audio_filter(device: Device) -> str:
    """Per-sink adelay + volume chain for this SPP output only."""
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


def spp_controls_key(device: Device) -> str:
    """Stable token so listener restarts when per-sink delay/volume/mute change."""
    vol = 0.0 if device.mute else float(device.volume)
    return f"d{int(device.delay_ms)}:v{vol:.3f}:m{int(bool(device.mute))}"


def build_spp_ffmpeg(
    device: Device,
    *,
    input_kind: SppInputKind,
    input_spec: str,
    ffmpeg_bin: str = "ffmpeg",
    apply_controls: bool | None = None,
) -> list[str]:
    """Build SPP argv: proven listen shape + optional per-sink ``-af``.

    Manual baseline::

        ffmpeg -re -i <file> -f s16le -acodec pcm_s16le -ar 48000 -ac 1 \\
          tcp://0.0.0.0:1234?listen=1

    Per-sink delay/volume use ``-af adelay,volume`` on ``file`` / ``lavfi``
    (continuous PCM). Pulse fallback stays filter-free — monitor starvation
    already breaks the handshake; filters would not help.
    """
    if device.kind != DeviceKind.NETWORK_SPP:
        raise ValueError(f"{device.id} is not a network_spp sink")
    if not input_spec.strip():
        raise ValueError("SPP ffmpeg input_spec must be non-empty")

    if apply_controls is None:
        apply_controls = input_kind in {"file", "lavfi"}

    cmd: list[str] = [ffmpeg_bin, "-hide_banner", "-loglevel", "warning"]
    if input_kind == "file":
        # Proven shape: realtime file read → raw PCM TCP listen (+ optional -af).
        cmd += ["-re", "-i", input_spec]
    elif input_kind == "lavfi":
        # Idle keep-alive: continuous silence so the phone can connect before Play.
        cmd += ["-f", "lavfi", "-i", input_spec]
    elif input_kind == "pulse":
        # Optional live fan-out; may starve until the feeder has clients.
        cmd += ["-f", "pulse", "-i", input_spec]
    else:
        raise ValueError(f"Unknown SPP input_kind: {input_kind!r}")

    if apply_controls:
        af = spp_audio_filter(device)
        if af:
            cmd += ["-af", af]

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
    """File → TCP listen with per-sink delay/volume ``-af`` when needed."""
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
        apply_controls=False,
    )


def build_spp_ffmpeg_idle(
    device: Device,
    *,
    ffmpeg_bin: str = "ffmpeg",
) -> list[str]:
    """Silent PCM listen so SPP can connect before Play (honors delay/volume)."""
    return build_spp_ffmpeg(
        device,
        input_kind="lavfi",
        input_spec=spp_anullsrc(device),
        ffmpeg_bin=ffmpeg_bin,
    )


def spp_plan_notes(device: Device, *, feeder: str) -> list[str]:
    idle = " ".join(build_spp_ffmpeg_idle(device))
    pulse = " ".join(build_spp_ffmpeg_from_pulse(device, feeder=feeder))
    af = spp_audio_filter(device)
    return [
        f"{device.id}: SPP TCP listen {spp_tcp_url(device)} "
        f"({device.sample_rate} Hz / {device.channels} ch s16le) "
        f"— per-sink -af {af}",
        f"{device.id}: idle → {idle}",
        f"{device.id}: play(local file) → ffmpeg -re -i <path> -af {af} … "
        f"{spp_tcp_url(device)}",
        f"{device.id}: pulse fallback → {pulse}",
    ]
