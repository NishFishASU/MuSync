"""Unified now-playing metadata for local / streaming / live input."""

from __future__ import annotations

import html
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from .config import extract_embedded_cover, probe_media_tags
from .models import NowPlaying, SourceType, TransportState

if TYPE_CHECKING:
    from .audio.source_session import SourceSession

log = logging.getLogger(__name__)

# Laptop / mock defaults so the transport header always has title · artist · cover.
MOCK_COVER_PATH = "/api/now-playing/cover?source=mock-sample"
DEMO_SOURCE_ID = "local:demo/sample-tone"
DEMO_SAMPLE = {
    "title": "Sample Tone",
    "artist": "MuSync Demo",
    "album": "Laptop Mock",
    "cover_url": MOCK_COVER_PATH,
}
STREAMING_SAMPLES: dict[SourceType, dict[str, str]] = {
    SourceType.SPOTIFY: {
        "title": "Mock Spotify Track",
        "artist": "MuSync Connect",
        "album": "Streaming Mock",
        "cover_url": MOCK_COVER_PATH,
    },
    SourceType.APPLE: {
        "title": "Mock Apple Song",
        "artist": "MuSync MusicKit",
        "album": "Streaming Mock",
        "cover_url": MOCK_COVER_PATH,
    },
}


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


def apply_mock_sample_metadata(np: NowPlaying, session: "SourceSession") -> NowPlaying:
    """Fill title / artist / album / cover for laptop mock when real tags are absent."""
    source_type = np.source_type
    title = np.title
    artist = np.artist
    album = np.album
    cover_url = np.cover_url

    if source_type == SourceType.LOCAL:
        sid = None
        if np.queue_item_id:
            for item in session.list_queue():
                if item.id == np.queue_item_id:
                    sid = item.source_id
                    break
        transport = session.get_transport()
        sid = sid or transport.source_id
        is_demo = bool(sid and str(sid).startswith("local:demo")) or not np.locator or (
            np.locator or ""
        ).startswith("demo/")
        if is_demo or not (artist and album and cover_url):
            # Prefer real tags when present; otherwise demo sample.
            title = title or DEMO_SAMPLE["title"]
            artist = artist or DEMO_SAMPLE["artist"]
            album = album or DEMO_SAMPLE["album"]
            cover_url = cover_url or DEMO_SAMPLE["cover_url"]
    elif source_type in STREAMING_SAMPLES:
        sample = STREAMING_SAMPLES[source_type]
        title = title or sample["title"]
        artist = artist or sample["artist"]
        album = album or sample["album"]
        cover_url = cover_url or sample["cover_url"]
    elif source_type == SourceType.INPUT:
        host = session.active_input_host()
        if host:
            title = title or f"{host.name} · live"
            artist = artist or host.name
            album = album or host.capture_path.value
        else:
            title = title or "Live input"
            artist = artist or "Device"
            album = album or "Capture"
        cover_url = cover_url or MOCK_COVER_PATH
    else:
        # Stopped / empty cue — still show a friendly idle sample on mock.
        title = title or DEMO_SAMPLE["title"]
        artist = artist or DEMO_SAMPLE["artist"]
        album = album or DEMO_SAMPLE["album"]
        cover_url = cover_url or DEMO_SAMPLE["cover_url"]

    return np.model_copy(
        update={
            "title": title,
            "artist": artist,
            "album": album,
            "cover_url": cover_url,
            "has_cover": bool(cover_url),
        }
    )


def mock_cover_bytes(*, label: str = "MuSync") -> tuple[bytes, str]:
    """Tiny SVG album art for mock / demo sources (no media file required)."""
    safe = html.escape((label or "MuSync")[:28])
    svg = f"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="240" height="240" viewBox="0 0 240 240">
  <defs>
    <linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#1a3d2e"/>
      <stop offset="100%" stop-color="#0d1f18"/>
    </linearGradient>
  </defs>
  <rect width="240" height="240" fill="url(#g)"/>
  <circle cx="120" cy="105" r="52" fill="none" stroke="#c9a227" stroke-width="3"/>
  <circle cx="120" cy="105" r="10" fill="#c9a227"/>
  <text x="120" y="198" text-anchor="middle" fill="#e8d9a0"
        font-family="Georgia, serif" font-size="15">{safe}</text>
</svg>
"""
    return svg.encode("utf-8"), "image/svg+xml"


def resolve_cover_bytes(
    session: "SourceSession",
    *,
    source_id: Optional[str] = None,
    allow_mock: bool = False,
) -> tuple[bytes, str] | None:
    """Return (bytes, content_type) for embedded local cover art (or mock SVG)."""
    path: Optional[str] = None
    if source_id and source_id not in {"mock-sample"}:
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
    if path:
        cover = extract_embedded_cover(path)
        if cover and cover.is_file():
            data = cover.read_bytes()
            suffix = cover.suffix.lower()
            ctype = "image/jpeg" if suffix in {".jpg", ".jpeg"} else "image/png"
            return data, ctype

    # Mock / demo fallback (laptop UI without media tags).
    want_mock = allow_mock or source_id in {"mock-sample", DEMO_SOURCE_ID} or (
        bool(source_id) and str(source_id).startswith("local:demo")
    )
    if want_mock:
        label = DEMO_SAMPLE["title"]
        try:
            np = build_now_playing(session)
            label = np.title or label
        except Exception:  # noqa: BLE001
            pass
        return mock_cover_bytes(label=label)
    return None


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
