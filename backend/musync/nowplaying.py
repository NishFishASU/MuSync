"""Unified now-playing metadata for local / streaming / live input."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from .config import extract_embedded_cover, probe_media_tags
from .models import NowPlaying, SourceType, TransportState

if TYPE_CHECKING:
    from .audio.source_session import SourceSession

log = logging.getLogger(__name__)


def build_now_playing(session: "SourceSession") -> NowPlaying:
    """Assemble now-playing from transport + queue + tags / streaming APIs."""
    transport = session.get_transport()
    item = None
    if transport.queue_item_id:
        try:
            item = next(q for q in session.list_queue() if q.id == transport.queue_item_id)
        except StopIteration:
            item = None

    title = transport.title or (item.title if item else None)
    artist = transport.artist or (item.artist if item else None)
    album = transport.album or (item.album if item else None)
    cover_url = transport.cover_url or (item.cover_url if item else None)
    note: Optional[str] = item.note if item and not item.available else None
    locator = item.locator if item else session.active_locator()
    source_type = transport.source_type
    has_cover = bool(cover_url)

    # Local file tags / embedded cover (ffprobe + ffmpeg).
    path = item.path if item else None
    if source_type == SourceType.LOCAL and path and Path(path).is_file():
        tags = probe_media_tags(path)
        title = title or tags.get("title") or Path(path).stem
        artist = artist or tags.get("artist")
        album = album or tags.get("album")
        if not cover_url:
            cover = extract_embedded_cover(path)
            if cover:
                cover_url = "/api/now-playing/cover"
                has_cover = True

    # Spotify Web API metadata / currently-playing.
    if source_type == SourceType.SPOTIFY and locator:
        try:
            from .streaming import get_streaming_service

            svc = get_streaming_service()
            meta = svc.spotify.track_metadata(locator)
            if meta:
                title = title or meta.title
                artist = artist or meta.artist
                album = album or meta.album
                if meta.cover_url:
                    cover_url = meta.cover_url
                    has_cover = True
            playing = svc.spotify.currently_playing()
            if playing and playing.get("item"):
                raw = playing["item"]
                title = raw.get("name") or title
                artists = ", ".join(
                    a.get("name", "") for a in (raw.get("artists") or []) if a.get("name")
                )
                artist = artists or artist
                album = (raw.get("album") or {}).get("name") or album
                images = (raw.get("album") or {}).get("images") or []
                if images:
                    cover_url = images[min(1, len(images) - 1)].get("url") or cover_url
                    has_cover = bool(cover_url)
        except Exception as exc:  # noqa: BLE001
            log.debug("spotify now-playing enrich: %s", exc)

    # Apple MusicKit catalog metadata.
    if source_type == SourceType.APPLE and locator:
        try:
            from .streaming import get_streaming_service

            meta = get_streaming_service().apple.track_metadata(locator)
            if meta:
                title = title or meta.title
                artist = artist or meta.artist
                album = album or meta.album
                if meta.cover_url:
                    cover_url = meta.cover_url
                    has_cover = True
            if not get_streaming_service().apple_playable():
                note = note or get_streaming_service().apple_play_note()
        except Exception as exc:  # noqa: BLE001
            log.debug("apple now-playing enrich: %s", exc)

    # Live input / BT AVRCP best-effort: host name + capture path.
    if source_type == SourceType.INPUT:
        host = session.active_input_host()
        if host:
            title = title or f"{host.name} · live"
            artist = artist or host.name
            album = album or host.capture_path.value
            note = note or (
                None
                if host.connected and host.endpoint
                else "Waiting for live capture (BT A2DP / USB-A sound card)"
            )
            # Best-effort BlueZ media player title (AVRCP) when available.
            avrcp = _probe_avrcp_title()
            if avrcp:
                title = avrcp.get("title") or title
                artist = avrcp.get("artist") or artist
                album = avrcp.get("album") or album

    return NowPlaying(
        title=title,
        artist=artist,
        album=album,
        cover_url=cover_url,
        has_cover=has_cover,
        source_type=source_type,
        input_id=transport.input_id,
        queue_item_id=transport.queue_item_id,
        locator=locator,
        state=transport.state or TransportState.STOPPED,
        position_ms=transport.position_ms,
        duration_ms=transport.duration_ms,
        note=note,
    )


def resolve_cover_bytes(
    session: "SourceSession",
    *,
    source_id: Optional[str] = None,
) -> tuple[bytes, str] | None:
    """Return (bytes, content_type) for embedded local cover art."""
    path: Optional[str] = None
    if source_id:
        for src in session.list_sources():
            if src.id == source_id and src.path:
                path = src.path
                break
    if not path:
        transport = session.get_transport()
        if transport.queue_item_id:
            for item in session.list_queue():
                if item.id == transport.queue_item_id and item.path:
                    path = item.path
                    break
    if not path:
        return None
    cover = extract_embedded_cover(path)
    if not cover or not cover.is_file():
        return None
    data = cover.read_bytes()
    suffix = cover.suffix.lower()
    ctype = "image/jpeg" if suffix in {".jpg", ".jpeg"} else "image/png"
    return data, ctype


def _probe_avrcp_title() -> Optional[dict[str, str]]:
    """Best-effort: ``playerctl`` / bluetooth media player metadata (may be empty)."""
    import shutil
    import subprocess

    playerctl = shutil.which("playerctl")
    if not playerctl:
        return None
    out: dict[str, str] = {}
    try:
        for key, prop in (
            ("title", "xesam:title"),
            ("artist", "xesam:artist"),
            ("album", "xesam:album"),
        ):
            proc = subprocess.run(
                [playerctl, "metadata", prop],
                capture_output=True,
                text=True,
                timeout=1.5,
                check=False,
            )
            val = (proc.stdout or "").strip()
            if proc.returncode == 0 and val:
                out[key] = val
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out or None
