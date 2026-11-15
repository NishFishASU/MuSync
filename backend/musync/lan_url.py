"""LAN UI URL helpers — big ``http://PI_IP:PORT`` for Wi‑Fi panel + Pi boot banner.

Used by:
- ``GET /api/wifi`` (``lan_url`` / ``port``)
- ``GET /api/wifi/lan-qr.svg`` (printable QR)
- ``python -m musync.lan_banner`` / ``run_pi.sh`` HDMI console banner
"""

from __future__ import annotations

import os
import socket
import sys
from typing import TYPE_CHECKING, Iterable, TextIO

if TYPE_CHECKING:
    from .models import WifiStatus


def hub_port() -> int:
    """HTTP port the hub listens on (``MUSYNC_PORT``, default 8080)."""
    raw = (os.environ.get("MUSYNC_PORT") or "8080").strip() or "8080"
    try:
        port = int(raw)
    except ValueError:
        return 8080
    if port < 1 or port > 65535:
        return 8080
    return port


def guess_lan_ipv4() -> str:
    """Best-effort primary LAN IPv4 (not loopback)."""
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


def pick_lan_ip(ips: Iterable[str] | None = None) -> str:
    """Prefer the first non-loopback IPv4 from ``ips``, else ``guess_lan_ipv4()``."""
    for raw in ips or ():
        ip = (raw or "").strip()
        if not ip or ip.startswith("127."):
            continue
        # Skip IPv6 for the big phone-friendly URL card.
        if ":" in ip:
            continue
        return ip
    return guess_lan_ipv4()


def lan_ui_url(ips: Iterable[str] | None = None, port: int | None = None) -> str:
    """``http://PI_IP:PORT`` for phones on the same LAN / hotspot."""
    ip = pick_lan_ip(ips)
    if not ip:
        return ""
    return f"http://{ip}:{port if port is not None else hub_port()}"


def enrich_wifi_status(status: "WifiStatus") -> "WifiStatus":
    """Attach ``port`` + ``lan_url`` for the Wi‑Fi / home URL card."""
    port = hub_port()
    status.port = port
    status.lan_url = lan_ui_url(status.ips, port=port) or None
    return status


def ascii_qr(data: str, *, invert: bool = True) -> str:
    """Terminal-printable QR (phones can scan from HDMI). Soft-empty if qrcode missing."""
    data = (data or "").strip()
    if not data:
        return ""
    try:
        import qrcode
    except ImportError:
        return "(install qrcode for printable console QR: pip install qrcode)"
    qr = qrcode.QRCode(border=1, box_size=1)
    qr.add_data(data)
    qr.make(fit=True)
    # print_ascii writes to a stream; capture for callers.
    from io import StringIO

    buf = StringIO()
    qr.print_ascii(out=buf, invert=invert)
    return buf.getvalue().rstrip("\n")


def svg_qr(data: str) -> str:
    """SVG QR markup for the in-app Wi‑Fi card. Raises if ``data`` empty."""
    data = (data or "").strip()
    if not data:
        raise ValueError("QR data must be non-empty")
    import qrcode
    import qrcode.image.svg

    factory = qrcode.image.svg.SvgPathImage
    img = qrcode.make(data, image_factory=factory, border=1)
    return img.to_string().decode("utf-8") if isinstance(img.to_string(), bytes) else str(img.to_string())


def format_banner(
    url: str,
    *,
    extra_ips: Iterable[str] | None = None,
    mdns_hint: str | None = "musync.local",
) -> str:
    """Multi-line boot banner: huge URL + ASCII QR for HDMI/console."""
    lines: list[str] = [
        "",
        "=" * 56,
        "  MuSync — open on phone / laptop (same Wi‑Fi)",
        "=" * 56,
    ]
    if url:
        lines.append("")
        lines.append(f"  {url}")
        lines.append("")
        # Extra visual weight for HDMI walk-up demos.
        big = url.upper()
        lines.append(f"  >>>  {big}  <<<")
    else:
        lines.append("")
        lines.append("  (no LAN IP yet — join Wi‑Fi in the UI, then re-run)")
    others = [ip for ip in (extra_ips or ()) if ip and f"http://{ip}:" not in (url or "")]
    if others:
        lines.append(f"  also: {', '.join(others)}")
    port = hub_port()
    if mdns_hint:
        lines.append(f"  mDNS UI: http://{mdns_hint}:{port}  (not for Spotify OAuth)")
    lines.append("")
    if url:
        qr = ascii_qr(url)
        if qr:
            lines.append("  Scan QR (HDMI / console):")
            for row in qr.splitlines():
                lines.append(f"  {row}")
            lines.append("")
    lines.append("=" * 56)
    lines.append("")
    return "\n".join(lines)


def write_banner(
    text: str,
    *,
    streams: Iterable[TextIO] | None = None,
    also_console: bool = False,
) -> None:
    """Write banner to stdout (and optionally ``/dev/console`` for HDMI getty)."""
    targets: list[TextIO] = list(streams) if streams is not None else [sys.stdout]
    if also_console:
        for path in ("/dev/console", "/dev/tty1"):
            try:
                targets.append(open(path, "w", encoding="utf-8", errors="replace"))
            except OSError:
                continue
    seen: set[int] = set()
    for stream in targets:
        try:
            key = stream.fileno()
        except Exception:  # noqa: BLE001 — StringIO etc.
            key = id(stream)
        if key in seen:
            continue
        seen.add(key)
        try:
            stream.write(text)
            stream.flush()
        except OSError:
            pass
        finally:
            # Close only the console handles we opened.
            if getattr(stream, "name", None) in ("/dev/console", "/dev/tty1"):
                try:
                    stream.close()
                except OSError:
                    pass


def banner_from_env(*, also_console: bool = False) -> str:
    """Resolve URL from env / live interfaces and print the boot banner."""
    # Lazy import avoids circular import at module load in tests.
    try:
        from .wifi import create_wifi_backend

        ips = create_wifi_backend().status().ips
    except Exception:  # noqa: BLE001 — banner must never block boot
        ips = []
    url = lan_ui_url(ips)
    text = format_banner(url, extra_ips=ips)
    write_banner(text, also_console=also_console)
    return text
