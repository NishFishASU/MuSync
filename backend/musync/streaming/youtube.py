"""YouTube Data API search + yt-dlp/ffmpeg → PipeWire feeder.

Env (never commit secrets):
  MUSYNC_YOUTUBE_API_KEY     YouTube Data API v3 key (search)
  MUSYNC_YTDLP_BIN           (default: yt-dlp on PATH)
  MUSYNC_FFMPEG_BIN          (default: ffmpeg on PATH)
  MUSYNC_PW_FEEDER           Pulse/PipeWire sink for PCM (default musync_feeder)
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import signal
import subprocess
import threading
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

import httpx

log = logging.getLogger(__name__)

YOUTUBE_API = "https://www.googleapis.com/youtube/v3"
_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_ISO_DURATION_RE = re.compile(
    r"^P(?:(?P<days>\d+)D)?(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?)?$"
)


def _api_key() -> str:
    return (os.environ.get("MUSYNC_YOUTUBE_API_KEY") or "").strip()


def _ytdlp_bin() -> Optional[str]:
    override = (os.environ.get("MUSYNC_YTDLP_BIN") or "").strip()
    if override:
        return override
    return shutil.which("yt-dlp")


def _ffmpeg_bin() -> Optional[str]:
    override = (os.environ.get("MUSYNC_FFMPEG_BIN") or "").strip()
    if override:
        return override
    return shutil.which("ffmpeg")


def _feeder_sink() -> str:
    return (os.environ.get("MUSYNC_PW_FEEDER") or "musync_feeder").strip() or "musync_feeder"


def credentials_configured() -> bool:
    return bool(_api_key())


def parse_video_id(locator: str) -> Optional[str]:
    """Accept youtube:VIDEO_ID, bare 11-char ids, or common watch/share URLs."""
    raw = (locator or "").strip()
    if not raw:
        return None
    if raw.startswith("youtube:"):
        raw = raw[len("youtube:") :].strip()
    if _VIDEO_ID_RE.fullmatch(raw):
        return raw
    try:
        parsed = urlparse(raw)
    except Exception:  # noqa: BLE001
        return None
    host = (parsed.hostname or "").lower()
    if host in {"youtu.be"}:
        candidate = parsed.path.lstrip("/").split("/")[0]
        return candidate if _VIDEO_ID_RE.fullmatch(candidate) else None
    if "youtube.com" in host or "youtube-nocookie.com" in host:
        qs = parse_qs(parsed.query)
        if "v" in qs and qs["v"]:
            candidate = qs["v"][0]
            return candidate if _VIDEO_ID_RE.fullmatch(candidate) else None
        parts = [p for p in parsed.path.split("/") if p]
        if len(parts) >= 2 and parts[0] in {"embed", "shorts", "live", "v"}:
            candidate = parts[1]
            return candidate if _VIDEO_ID_RE.fullmatch(candidate) else None
    return None


def watch_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def uri_for(video_id: str) -> str:
    return f"youtube:{video_id}"


def parse_iso8601_duration_ms(value: str) -> Optional[int]:
    match = _ISO_DURATION_RE.match((value or "").strip())
    if not match:
        return None
    days = int(match.group("days") or 0)
    hours = int(match.group("hours") or 0)
    minutes = int(match.group("minutes") or 0)
    seconds = int(match.group("seconds") or 0)
    total = (((days * 24) + hours) * 60 + minutes) * 60 + seconds
    return total * 1000


@dataclass
class YouTubeTrack:
    id: str
    uri: str
    title: str
    artist: str
    duration_ms: Optional[int] = None
    album: Optional[str] = None
    cover_url: Optional[str] = None


class YouTubeClient:
    """Data API search + yt-dlp|ffmpeg Pulse sink player."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._ffmpeg: Optional[subprocess.Popen[bytes]] = None
        self._ytdlp: Optional[subprocess.Popen[bytes]] = None

    def status(self) -> dict[str, Any]:
        configured = credentials_configured()
        ytdlp = _ytdlp_bin()
        ffmpeg = _ffmpeg_bin()
        tools_ok = bool(ytdlp and ffmpeg)
        limits: list[str] = []
        if not configured:
            limits.append(
                "Set MUSYNC_YOUTUBE_API_KEY (YouTube Data API v3) in ~/musync.env "
                "— see docs/youtube.md"
            )
        if not ytdlp:
            limits.append("Install yt-dlp on PATH (or set MUSYNC_YTDLP_BIN)")
        if not ffmpeg:
            limits.append("Install ffmpeg on PATH (or set MUSYNC_FFMPEG_BIN)")
        if configured and tools_ok:
            note = "Search OK; play via yt-dlp → ffmpeg → musync_feeder"
        elif configured:
            note = "API key set — install yt-dlp + ffmpeg for hub PCM"
        else:
            note = "YouTube API key missing — search unavailable (docs/youtube.md)"
        return {
            "provider": "youtube",
            "configured": configured,
            "authenticated": configured,  # API key only; no user OAuth
            "ytdlp_available": bool(ytdlp),
            "ffmpeg_available": bool(ffmpeg),
            "playback_mode": "ytdlp_ffmpeg" if tools_ok else "unavailable",
            "feeder": _feeder_sink(),
            "limits": limits,
            "note": note,
        }

    def configured(self) -> bool:
        return credentials_configured()

    def authenticated(self) -> bool:
        return credentials_configured()

    def playable(self) -> bool:
        """Hub can attempt play when API key + yt-dlp + ffmpeg are present."""
        return bool(credentials_configured() and _ytdlp_bin() and _ffmpeg_bin())

    def search_tracks(self, query: str, *, limit: int = 15) -> list[YouTubeTrack]:
        if not credentials_configured():
            raise ValueError(
                "YouTube not configured — set MUSYNC_YOUTUBE_API_KEY in ~/musync.env "
                "(docs/youtube.md)"
            )
        q = (query or "").strip()
        if not q:
            return []
        params = {
            "part": "snippet",
            "type": "video",
            "q": q,
            "maxResults": max(1, min(int(limit), 25)),
            "key": _api_key(),
        }
        with httpx.Client(timeout=20.0) as client:
            res = client.get(f"{YOUTUBE_API}/search", params=params)
            res.raise_for_status()
            data = res.json()
        items = data.get("items") or []
        video_ids = [
            (it.get("id") or {}).get("videoId")
            for it in items
            if (it.get("id") or {}).get("videoId")
        ]
        durations = self._durations_for(video_ids) if video_ids else {}
        tracks: list[YouTubeTrack] = []
        for it in items:
            vid = (it.get("id") or {}).get("videoId")
            snippet = it.get("snippet") or {}
            if not vid:
                continue
            thumbs = snippet.get("thumbnails") or {}
            cover = None
            for key in ("medium", "high", "default"):
                if key in thumbs and thumbs[key].get("url"):
                    cover = thumbs[key]["url"]
                    break
            tracks.append(
                YouTubeTrack(
                    id=vid,
                    uri=uri_for(vid),
                    title=(snippet.get("title") or vid).strip() or vid,
                    artist=(snippet.get("channelTitle") or "YouTube").strip() or "YouTube",
                    duration_ms=durations.get(vid),
                    album=snippet.get("channelTitle"),
                    cover_url=cover,
                )
            )
        return tracks

    def _durations_for(self, video_ids: list[str]) -> dict[str, int]:
        """Best-effort contentDetails duration lookup (batched)."""
        out: dict[str, int] = {}
        if not video_ids or not credentials_configured():
            return out
        # videos.list accepts up to 50 ids.
        chunk = ",".join(video_ids[:50])
        params = {
            "part": "contentDetails",
            "id": chunk,
            "key": _api_key(),
        }
        try:
            with httpx.Client(timeout=20.0) as client:
                res = client.get(f"{YOUTUBE_API}/videos", params=params)
                res.raise_for_status()
                data = res.json()
            for it in data.get("items") or []:
                vid = it.get("id")
                raw = ((it.get("contentDetails") or {}).get("duration")) or ""
                ms = parse_iso8601_duration_ms(raw)
                if vid and ms is not None:
                    out[vid] = ms
        except Exception as exc:  # noqa: BLE001
            log.debug("youtube duration lookup: %s", exc)
        return out

    def track_metadata(self, locator: str) -> Optional[YouTubeTrack]:
        vid = parse_video_id(locator)
        if not vid or not credentials_configured():
            return None
        params = {
            "part": "snippet,contentDetails",
            "id": vid,
            "key": _api_key(),
        }
        try:
            with httpx.Client(timeout=15.0) as client:
                res = client.get(f"{YOUTUBE_API}/videos", params=params)
                res.raise_for_status()
                data = res.json()
        except Exception as exc:  # noqa: BLE001
            log.debug("youtube metadata: %s", exc)
            return None
        items = data.get("items") or []
        if not items:
            return None
        it = items[0]
        snippet = it.get("snippet") or {}
        thumbs = snippet.get("thumbnails") or {}
        cover = None
        for key in ("medium", "high", "default"):
            if key in thumbs and thumbs[key].get("url"):
                cover = thumbs[key]["url"]
                break
        raw_dur = ((it.get("contentDetails") or {}).get("duration")) or ""
        return YouTubeTrack(
            id=vid,
            uri=uri_for(vid),
            title=(snippet.get("title") or vid).strip() or vid,
            artist=(snippet.get("channelTitle") or "YouTube").strip() or "YouTube",
            duration_ms=parse_iso8601_duration_ms(raw_dur),
            album=snippet.get("channelTitle"),
            cover_url=cover,
        )

    def play_uri(self, uri: str, *, feeder: Optional[str] = None) -> dict[str, Any]:
        """Stream audio: yt-dlp stdout → ffmpeg → Pulse sink (musync_feeder)."""
        vid = parse_video_id(uri)
        if not vid:
            raise ValueError(f"Invalid YouTube locator: {uri!r}")
        ytdlp = _ytdlp_bin()
        ffmpeg = _ffmpeg_bin()
        if not ytdlp:
            raise ValueError("yt-dlp missing — install yt-dlp or set MUSYNC_YTDLP_BIN")
        if not ffmpeg:
            raise ValueError("ffmpeg missing — install ffmpeg or set MUSYNC_FFMPEG_BIN")
        sink = (feeder or _feeder_sink()).strip() or _feeder_sink()
        url = watch_url(vid)
        self.stop()
        # yt-dlp writes media to stdout; ffmpeg reads pipe:0 and writes Pulse.
        ytdlp_cmd = [
            ytdlp,
            "-f",
            "bestaudio/best",
            "-o",
            "-",
            "--no-playlist",
            "--quiet",
            "--no-warnings",
            url,
        ]
        ffmpeg_cmd = [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            "pipe:0",
            "-vn",
            "-ac",
            "2",
            "-ar",
            "48000",
            "-f",
            "pulse",
            sink,
        ]
        log.info("YouTube play %s → %s via yt-dlp|ffmpeg", vid, sink)
        try:
            ytdlp_proc = subprocess.Popen(
                ytdlp_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            assert ytdlp_proc.stdout is not None
            ffmpeg_proc = subprocess.Popen(
                ffmpeg_cmd,
                stdin=ytdlp_proc.stdout,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            # Allow yt-dlp to receive SIGPIPE / close when ffmpeg exits.
            ytdlp_proc.stdout.close()
        except OSError as exc:
            raise ValueError(f"Failed to start yt-dlp|ffmpeg: {exc}") from exc

        with self._lock:
            self._ffmpeg = ffmpeg_proc
            self._ytdlp = ytdlp_proc

        return {
            "ok": True,
            "video_id": vid,
            "uri": uri_for(vid),
            "feeder": sink,
            "mode": "ytdlp_ffmpeg",
        }

    def stop(self) -> None:
        with self._lock:
            ffmpeg = self._ffmpeg
            ytdlp = self._ytdlp
            self._ffmpeg = None
            self._ytdlp = None
        for proc in (ffmpeg, ytdlp):
            if proc is None:
                continue
            if proc.poll() is not None:
                continue
            try:
                if proc is ffmpeg:
                    try:
                        os.killpg(proc.pid, signal.SIGTERM)
                    except (ProcessLookupError, PermissionError, OSError):
                        proc.terminate()
                else:
                    proc.terminate()
                proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                try:
                    if proc is ffmpeg:
                        try:
                            os.killpg(proc.pid, signal.SIGKILL)
                        except (ProcessLookupError, PermissionError, OSError):
                            proc.kill()
                    else:
                        proc.kill()
                    proc.wait(timeout=1.0)
                except Exception:  # noqa: BLE001
                    pass
            except Exception:  # noqa: BLE001
                try:
                    proc.kill()
                except Exception:  # noqa: BLE001
                    pass

    def running(self) -> bool:
        with self._lock:
            return self._ffmpeg is not None and self._ffmpeg.poll() is None
