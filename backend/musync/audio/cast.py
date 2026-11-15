"""Google Cast / Chromecast (Nest Mini) Wi‑Fi output helpers.

Nest Mini is a Cast receiver on the LAN — higher latency than BT A2DP.
Discovery uses mDNS ``_googlecast._tcp.local.`` (zeroconf / pychromecast);
missing libs or LAN failures soft-fail so the API stays up.

PCM path (same feeder as BT/SPP)::

    musync_feeder.monitor → ffmpeg (-af adelay,volume → mp3) → HTTP listen
         Nest Mini pulls that URL via Cast Default Media Receiver

Per-sink delay/volume apply on the ffmpeg ``-af`` chain. Optional Cast
``set_volume`` is best-effort — soft-fail never blocks PATCH / play.
"""

from __future__ import annotations

import logging
import socket
import subprocess
import time
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from ..models import Device, DeviceKind, DiscoveredCastSink

log = logging.getLogger(__name__)

CastInputKind = Literal["file", "lavfi", "pulse"]

GOOGLECAST_SERVICE = "_googlecast._tcp.local."
DEFAULT_CAST_HTTP_PORT = 8090
DEFAULT_CAST_CONTROL_PORT = 8009
# Cast path buffers more than A2DP — start calibration above typical BT delays.
DEFAULT_CAST_DELAY_MS = 300


@dataclass(frozen=True)
class CastDiscoveryResult:
    devices: list[DiscoveredCastSink]
    detail: str = ""
    soft_warn: str = ""


def default_cast_http_port() -> int:
    return DEFAULT_CAST_HTTP_PORT


def cast_controls_key(device: Device) -> str:
    """Stable token so the HTTP stream restarts when delay/volume/mute change."""
    vol = 0.0 if device.mute else float(device.volume)
    return f"d{int(device.delay_ms)}:v{vol:.3f}:m{int(bool(device.mute))}"


def cast_audio_filter(device: Device) -> str:
    """Per-sink adelay + volume for this Cast HTTP stream only."""
    parts: list[str] = []
    delay = max(0, int(device.delay_ms))
    channels = max(1, int(device.channels or 2))
    if delay:
        if channels >= 2:
            parts.append(f"adelay={delay}|{delay}:all=1")
        else:
            parts.append(f"adelay={delay}:all=1")
    vol = 0.0 if device.mute else float(device.volume)
    parts.append(f"volume={vol:.4f}")
    return ",".join(parts)


def cast_anullsrc(device: Device) -> str:
    layout = "mono" if int(device.channels or 2) <= 1 else "stereo"
    rate = int(device.sample_rate or 48000)
    return f"anullsrc=channel_layout={layout}:sample_rate={rate}"


def cast_http_url(device: Device, *, host_override: str | None = None) -> str:
    """URL Nest Mini should pull (use a LAN IP, not 0.0.0.0)."""
    host = (host_override or device.listen_host or "0.0.0.0").strip() or "0.0.0.0"
    if host in {"0.0.0.0", "::", "[::]"}:
        host = guess_lan_ipv4() or "127.0.0.1"
    port = int(device.listen_port or DEFAULT_CAST_HTTP_PORT)
    return f"http://{host}:{port}/stream.mp3"


def cast_ffmpeg_listen_url(device: Device) -> str:
    """ffmpeg ``-listen 1`` bind URL (0.0.0.0 is fine for the server side)."""
    host = (device.listen_host or "0.0.0.0").strip() or "0.0.0.0"
    port = int(device.listen_port or DEFAULT_CAST_HTTP_PORT)
    return f"http://{host}:{port}/stream.mp3"


def guess_lan_ipv4() -> str:
    """Best-effort primary LAN IPv4 for Cast clients (not loopback)."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(("8.8.8.8", 80))
            ip = sock.getsockname()[0]
        finally:
            sock.close()
        if ip and not ip.startswith("127."):
            return ip
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip and not ip.startswith("127."):
                return ip
    except OSError:
        pass
    return ""


def free_cast_listen_port(port: int, *, settle_s: float = 0.2) -> str:
    """Best-effort: stop orphan ffmpeg holding a Cast HTTP listen port."""
    port = int(port)
    if port < 1 or port > 65535:
        return "invalid port"
    pattern = f"ffmpeg.*:{port}/stream"
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
    if proc.returncode == 0:
        return f"killed stray ffmpeg on :{port}"
    if proc.returncode == 1:
        return f"port :{port} free (no stray ffmpeg)"
    detail = (proc.stderr or proc.stdout or "").strip()
    return f"pkill rc={proc.returncode}" + (f" ({detail})" if detail else "")


def build_cast_ffmpeg(
    device: Device,
    *,
    input_kind: CastInputKind,
    input_spec: str,
    ffmpeg_bin: str = "ffmpeg",
    apply_controls: bool | None = None,
) -> list[str]:
    """Build ffmpeg argv: feeder/file/silence → mp3 HTTP listen for Cast.

    Nest Mini / Chromecast Default Media Receiver plays ``audio/mpeg``.
    """
    if device.kind != DeviceKind.CAST:
        raise ValueError(f"{device.id} is not a cast sink")
    if not input_spec.strip():
        raise ValueError("Cast ffmpeg input_spec must be non-empty")

    if apply_controls is None:
        apply_controls = True

    cmd: list[str] = [ffmpeg_bin, "-hide_banner", "-loglevel", "warning"]
    if input_kind == "file":
        cmd += ["-re", "-i", input_spec]
    elif input_kind == "lavfi":
        cmd += ["-f", "lavfi", "-i", input_spec]
    elif input_kind == "pulse":
        cmd += ["-f", "pulse", "-i", input_spec]
    else:
        raise ValueError(f"Unknown Cast input_kind: {input_kind!r}")

    if apply_controls:
        af = cast_audio_filter(device)
        if af:
            cmd += ["-af", af]

    channels = max(1, int(device.channels or 2))
    rate = int(device.sample_rate or 48000)
    cmd += [
        "-ac",
        str(channels),
        "-ar",
        str(rate),
        "-f",
        "mp3",
        "-content_type",
        "audio/mpeg",
        "-listen",
        "1",
        cast_ffmpeg_listen_url(device),
    ]
    return cmd


def build_cast_ffmpeg_idle(device: Device, *, ffmpeg_bin: str = "ffmpeg") -> list[str]:
    return build_cast_ffmpeg(
        device,
        input_kind="lavfi",
        input_spec=cast_anullsrc(device),
        ffmpeg_bin=ffmpeg_bin,
    )


def build_cast_ffmpeg_from_file(
    device: Device, *, path: str, ffmpeg_bin: str = "ffmpeg"
) -> list[str]:
    return build_cast_ffmpeg(
        device, input_kind="file", input_spec=path, ffmpeg_bin=ffmpeg_bin
    )


def build_cast_ffmpeg_from_pulse(
    device: Device, *, feeder: str, ffmpeg_bin: str = "ffmpeg"
) -> list[str]:
    return build_cast_ffmpeg(
        device,
        input_kind="pulse",
        input_spec=f"{feeder}.monitor",
        ffmpeg_bin=ffmpeg_bin,
    )


def cast_plan_notes(device: Device, *, feeder: str, lan_ip: str = "") -> list[str]:
    url = cast_http_url(device, host_override=lan_ip or None)
    af = cast_audio_filter(device)
    return [
        f"{device.id}: Cast HTTP {url} (Nest Mini / Chromecast) — per-sink -af {af}",
        f"{device.id}: default delay {DEFAULT_CAST_DELAY_MS}ms (Cast > BT latency)",
        f"{device.id}: feeder monitor → {feeder}.monitor; kick play_media soft-fail",
        f"{device.id}: uuid={device.cast_uuid or '—'} host={device.cast_host or '—'}",
    ]


def _txt_get(properties: dict, *keys: bytes) -> str:
    for key in keys:
        raw = properties.get(key)
        if raw is None:
            continue
        if isinstance(raw, bytes):
            try:
                return raw.decode("utf-8", errors="replace").strip()
            except Exception:  # noqa: BLE001
                return ""
        return str(raw).strip()
    return ""


def discover_cast_devices(*, timeout_s: float = 2.5) -> CastDiscoveryResult:
    """Browse Google Cast receivers on the LAN (Nest Mini, Chromecast, …).

    Soft-fails when zeroconf is missing or mDNS errors — empty list + warn.
    """
    try:
        from zeroconf import ServiceBrowser, ServiceStateChange, Zeroconf
    except ImportError:
        return CastDiscoveryResult(
            devices=[],
            detail="Cast discovery unavailable",
            soft_warn="zeroconf missing — Cast discovery soft-fail (pip install zeroconf)",
        )

    found: dict[str, DiscoveredCastSink] = {}

    def _on_service_state_change(
        zeroconf: object,
        service_type: str,
        name: str,
        state_change: object,
    ) -> None:
        try:
            from zeroconf import ServiceStateChange as SSC
        except ImportError:
            return
        if state_change is not SSC.Added and state_change is not getattr(
            SSC, "Updated", object()
        ):
            return
        try:
            info = zeroconf.get_service_info(service_type, name, timeout=800)  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001
            log.debug("Cast service_info %s: %s", name, exc)
            return
        if info is None:
            return
        props = dict(info.properties or {})
        friendly = _txt_get(props, b"fn", b"md") or name.split(".")[0]
        model = _txt_get(props, b"md") or ""
        uuid = _txt_get(props, b"id", b"bs", b"cd")
        host = ""
        try:
            addrs = info.parsed_addresses()
            host = addrs[0] if addrs else ""
        except Exception:  # noqa: BLE001
            host = ""
        if not host and info.server:
            host = str(info.server).rstrip(".")
        port = int(info.port or DEFAULT_CAST_CONTROL_PORT)
        key = (uuid or f"{host}:{port}").lower()
        found[key] = DiscoveredCastSink(
            name=friendly or "Cast device",
            uuid=uuid,
            host=host,
            port=port,
            model=model,
            connected=bool(host),
        )

    zc = None
    try:
        zc = Zeroconf()
        ServiceBrowser(zc, GOOGLECAST_SERVICE, handlers=[_on_service_state_change])
        time.sleep(max(0.3, float(timeout_s)))
    except Exception as exc:  # noqa: BLE001 — soft-fail discovery
        log.warning("Cast discovery soft-fail: %s", exc)
        return CastDiscoveryResult(
            devices=[],
            detail=f"Cast discovery error: {exc}",
            soft_warn="Cast mDNS soft-fail — check Wi‑Fi / avahi",
        )
    finally:
        if zc is not None:
            try:
                zc.close()
            except Exception:  # noqa: BLE001
                pass

    devices = sorted(found.values(), key=lambda d: d.name.lower())
    nestish = sum(
        1
        for d in devices
        if "nest" in d.name.lower()
        or "nest" in (d.model or "").lower()
        or "google home mini" in d.name.lower()
    )
    detail = (
        f"Cast mDNS: {len(devices)} device{'s' if len(devices) != 1 else ''}"
        + (f" ({nestish} Nest/Home Mini)" if nestish else "")
    )
    return CastDiscoveryResult(devices=devices, detail=detail)


def apply_cast_volume(device: Device) -> tuple[bool, str]:
    """Best-effort Cast receiver volume. Soft-fail if pychromecast missing/offline."""
    if device.kind != DeviceKind.CAST:
        return False, "not a cast sink"
    if not device.cast_host and not device.cast_uuid:
        return False, "no cast host/uuid"
    try:
        import pychromecast
    except ImportError:
        return False, "pychromecast missing — volume soft-fail (ffmpeg -af still applies)"

    vol = 0.0 if device.mute else float(device.volume)
    try:
        cast = _resolve_chromecast(device)
        if cast is None:
            return False, "cast device not found — volume soft-fail"
        cast.set_volume(max(0.0, min(1.0, vol)))
        return True, f"cast volume → {vol:.3f}"
    except Exception as exc:  # noqa: BLE001
        log.debug("Cast volume soft-fail %s: %s", device.id, exc)
        return False, f"cast volume soft-fail: {exc}"


def kick_cast_playback(device: Device, *, media_url: str | None = None) -> tuple[bool, str]:
    """Tell Nest Mini to play our HTTP mp3 stream. Soft-fail keeps hub audio up."""
    if device.kind != DeviceKind.CAST:
        return False, "not a cast sink"
    url = media_url or cast_http_url(device)
    try:
        import pychromecast  # noqa: F401
    except ImportError:
        return (
            False,
            "pychromecast missing — Cast kick soft-fail (HTTP stream still served)",
        )
    try:
        cast = _resolve_chromecast(device)
        if cast is None:
            return False, "cast device not found — kick soft-fail"
        cast.wait(timeout=5)
        mc = cast.media_controller
        mc.play_media(url, "audio/mpeg")
        try:
            mc.block_until_active(timeout=5)
        except Exception:  # noqa: BLE001 — some firmwares skip READY
            pass
        return True, f"cast play_media {url}"
    except Exception as exc:  # noqa: BLE001
        log.warning("Cast kick soft-fail %s: %s", device.id, exc)
        return False, f"cast kick soft-fail: {exc}"


def _resolve_chromecast(device: Device):  # type: ignore[no-untyped-def]
    """Locate a Cast by UUID or host; returns None on soft-fail."""
    import pychromecast
    from pychromecast.models import CastInfo
    from pychromecast.socket_client import HostServiceInfo

    browser = None
    try:
        if device.cast_uuid:
            try:
                uid = UUID(device.cast_uuid)
            except ValueError:
                uid = None
            if uid is not None:
                chromecasts, browser = pychromecast.get_listed_chromecasts(
                    uuids=[uid], tries=1, retry_wait=0.5, timeout=3
                )
                if chromecasts:
                    return chromecasts[0]
        if device.cast_host:
            host = device.cast_host.strip()
            port = int(device.cast_port or DEFAULT_CAST_CONTROL_PORT)
            # Prefer known_hosts discovery; fall back to CastInfo direct connect.
            chromecasts, browser = pychromecast.get_listed_chromecasts(
                known_hosts=[host], tries=1, retry_wait=0.5, timeout=3
            )
            if chromecasts:
                return chromecasts[0]
            try:
                uid = UUID(device.cast_uuid) if device.cast_uuid else UUID(int=0)
            except ValueError:
                uid = UUID(int=0)
            info = CastInfo(
                services={HostServiceInfo(host, port)},
                uuid=uid,
                model_name=None,
                friendly_name=device.name or None,
                host=host,
                port=port,
                cast_type=None,
                manufacturer=None,
            )
            cast = pychromecast.Chromecast(info)
            cast.wait(timeout=5)
            return cast
        name = (device.name or "").strip()
        if name:
            chromecasts, browser = pychromecast.get_listed_chromecasts(
                friendly_names=[name], tries=1, retry_wait=0.5, timeout=3
            )
            if chromecasts:
                return chromecasts[0]
    except Exception as exc:  # noqa: BLE001
        log.debug("resolve chromecast soft-fail: %s", exc)
        return None
    finally:
        if browser is not None:
            try:
                pychromecast.discovery.stop_discovery(browser)
            except Exception:  # noqa: BLE001
                pass
    return None


def mock_nest_mini() -> DiscoveredCastSink:
    """Laptop/dev fixture — Nest Mini on Wi‑Fi (higher latency than BT)."""
    return DiscoveredCastSink(
        name="Nest Mini",
        uuid="deadbeef-0000-4000-8000-00000000nest",
        host="192.168.1.80",
        port=DEFAULT_CAST_CONTROL_PORT,
        model="Google Nest Mini",
        connected=True,
    )
