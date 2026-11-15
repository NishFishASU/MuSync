"""AirPlay receive → musync_feeder via shairport-sync.

Apple Music catalog / MusicKit is intentionally not supported on the hub.
iPhone/Mac AirPlay to a shairport-sync receiver; MuSync loopbacks that
PipeWire source into ``musync_feeder`` (same path as BT A2DP / USB / HDMI).

Env (optional overrides; never commit secrets):
  MUSYNC_AIRPLAY_SOURCE       PipeWire source name (preferred)
  MUSYNC_APPLE_AIRPLAY_SOURCE legacy alias for MUSYNC_AIRPLAY_SOURCE
  MUSYNC_AIRPLAY_NAME         advertised receiver name (docs / UI; default MuSync)
"""

from __future__ import annotations

import logging
import os
import shutil
from typing import Any, Optional

log = logging.getLogger(__name__)


def _env_source() -> str:
    return (
        (os.environ.get("MUSYNC_AIRPLAY_SOURCE") or "").strip()
        or (os.environ.get("MUSYNC_APPLE_AIRPLAY_SOURCE") or "").strip()
    )


def _receiver_name() -> str:
    return (os.environ.get("MUSYNC_AIRPLAY_NAME") or "MuSync").strip() or "MuSync"


def shairport_on_path() -> bool:
    return bool(shutil.which("shairport-sync"))


class AirplayReceiver:
    """Status + setup hints for AirPlay → feeder capture (no MusicKit)."""

    def configured_source(self) -> Optional[str]:
        src = _env_source()
        return src or None

    def status(
        self,
        *,
        discovered_source: Optional[str] = None,
        live: bool = False,
        host_name: Optional[str] = None,
    ) -> dict[str, Any]:
        env_src = self.configured_source()
        source = (discovered_source or "").strip() or env_src
        binary = shairport_on_path()
        configured = bool(source) or binary or live
        setup = [
            "Install shairport-sync so the Pi appears as an AirPlay speaker",
            "Point output at PipeWire/Pulse so a Source node appears "
            "(wpctl status → Sources)",
            "Optional: export MUSYNC_AIRPLAY_SOURCE=<exact PipeWire source name> "
            "in ~/musync.env",
            "UI → Inputs → Refresh inputs (auto-binds AirPlay) → + Live / Play",
            "On iPhone/Mac: AirPlay audio to the receiver "
            f"(name often {_receiver_name()!r})",
        ]
        if not configured:
            note = (
                "AirPlay receive not ready — install shairport-sync, Refresh inputs, "
                "or set MUSYNC_AIRPLAY_SOURCE (docs/airplay-receive.md)"
            )
        elif live and source:
            note = (
                f"AirPlay live · {source} → musync_feeder"
                + (f" · host {host_name}" if host_name else "")
            )
        elif source:
            note = (
                f"AirPlay source configured ({source}) — AirPlay to the Pi, "
                "then Play live input into musync_feeder"
            )
        else:
            note = (
                "shairport-sync on PATH — Refresh inputs after it exposes a "
                "PipeWire source, then Play"
            )
        return {
            "provider": "airplay",
            "configured": configured,
            "receiving": bool(live and source),
            "source": source or None,
            "receiver_name": _receiver_name(),
            "shairport_available": binary,
            "setup": setup,
            "note": note,
        }

    def play_note(self, source: Optional[str] = None) -> str:
        src = (source or self.configured_source() or "").strip()
        if src:
            return (
                f"AirPlay capture: looping {src!r} into musync_feeder. "
                "Start playback on an Apple device targeting this receiver."
            )
        return (
            "AirPlay receive not ready — install shairport-sync "
            "(docs/airplay-receive.md), Refresh inputs, or set "
            "MUSYNC_AIRPLAY_SOURCE, then use live input Play."
        )
