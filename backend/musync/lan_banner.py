"""Pi HDMI / console boot banner: big LAN URL + printable ASCII QR.

Usage::

    python -m musync.lan_banner
    python -m musync.lan_banner --console   # also write /dev/console + /dev/tty1
    MUSYNC_PORT=8080 python -m musync.lan_banner --url http://192.168.1.20:8080

``scripts/run_pi.sh`` invokes this before starting uvicorn so a walk-up demo
can scan the QR from the Pi's HDMI console without opening the web UI first.
"""

from __future__ import annotations

import argparse
import sys

from .lan_url import banner_from_env, format_banner, lan_ui_url, write_banner


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Print MuSync LAN UI URL + printable QR for HDMI/console."
    )
    parser.add_argument(
        "--url",
        default="",
        help="Override LAN URL (default: detect from Wi‑Fi / interfaces)",
    )
    parser.add_argument(
        "--console",
        action="store_true",
        help="Also write to /dev/console and /dev/tty1 (HDMI getty)",
    )
    parser.add_argument(
        "--no-detect",
        action="store_true",
        help="Skip Wi‑Fi backend probe (use --url or empty)",
    )
    args = parser.parse_args(argv)

    if args.url.strip():
        text = format_banner(args.url.strip())
        write_banner(text, also_console=args.console)
        return 0
    if args.no_detect:
        text = format_banner(lan_ui_url([]))
        write_banner(text, also_console=args.console)
        return 0
    banner_from_env(also_console=args.console)
    return 0


if __name__ == "__main__":
    sys.exit(main())
