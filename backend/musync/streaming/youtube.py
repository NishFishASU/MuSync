"""YouTube Data API search + yt-dlp/ffmpeg → PipeWire feeder.

Env (never commit secrets):
  MUSYNC_YOUTUBE_API_KEY     YouTube Data API v3 key (search)
  MUSYNC_YTDLP_BIN           (default: yt-dlp on PATH)
  MUSYNC_FFMPEG_BIN          (default: ffmpeg on PATH)
  MUSYNC_PW_FEEDER           Pulse/PipeWire sink for PCM (default musync_feeder)
  MUSYNC_YTDLP_COOKIES       Netscape cookies.txt for yt-dlp (bot / age gates)
  MUSYNC_YTDLP_COOKIES_FROM_BROWSER  e.g. chrome, chromium, firefox
  MUSYNC_YTDLP_EXTRA_ARGS    Extra yt-dlp argv (shell-split), optional
"""

from __future__ import annotations

import logging
import os
import re
import shlex
import shutil
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

import httpx

log = logging.getLogger(__name__)

YOUTUBE_API = "https://www.googleapis.com/youtube/v3"
_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_ISO_DURATION_RE = re.compile(
    r"^P(?:(?P<days>\d+)D)?(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?)?$"
)
# Prefer a single audio stream (avoids video+audio merge when piping / -g).
_YTDLP_FORMAT = "bestaudio/best"
_RESOLVE_TIMEOUT_S = 45.0
_STARTUP_SETTLE_S = 0.9


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


def _ytdlp_auth_args() -> list[str]:
    """Optional cookies so yt-dlp can pass YouTube bot / age gates on the Pi."""
    args: list[str] = []
    cookies = (os.environ.get("MUSYNC_YTDLP_COOKIES") or "").strip()
    if cookies:
        args.extend(["--cookies", cookies])
    browser = (os.environ.get("MUSYNC_YTDLP_COOKIES_FROM_BROWSER") or "").strip()
    if browser:
        args.extend(["--cookies-from-browser", browser])
    extra = (os.environ.get("MUSYNC_YTDLP_EXTRA_ARGS") or "").strip()
    if extra:
        try:
            args.extend(shlex.split(extra))
        except ValueError:
            log.warning("MUSYNC_YTDLP_EXTRA_ARGS could not be parsed — ignoring")
    return args


def _summarize_ytdlp_stderr(stderr: str) -> str:
    """Turn yt-dlp noise into a short operator-facing reason."""
    if not isinstance(stderr, str):
        stderr = str(stderr or "")
    text = stderr.strip()
    if not text:
        return "yt-dlp failed with no error text"
    lower = text.lower()
    # Prefer the last ERROR: line.
    err_lines = [
        ln.strip()
        for ln in text.splitlines()
        if ln.strip().upper().startswith("ERROR:")
    ]
    core = err_lines[-1] if err_lines else text.splitlines()[-1].strip()
    core = re.sub(r"^ERROR:\s*", "", core, flags=re.IGNORECASE).strip()
    if "sign in to confirm" in lower or "not a bot" in lower:
        return (
            f"yt-dlp blocked by YouTube bot check ({core}). "
            "Export cookies on the Pi (MUSYNC_YTDLP_COOKIES=…/cookies.txt or "
            "MUSYNC_YTDLP_COOKIES_FROM_BROWSER=chromium) — see docs/youtube.md"
        )
    if "http error 403" in lower or "403:" in lower:
        return (
            f"yt-dlp got HTTP 403 ({core}). Update yt-dlp and/or set cookies "
            "(docs/youtube.md)"
        )
    if "age" in lower and ("confirm" in lower or "restricted" in lower):
        return f"yt-dlp age-gate ({core}). Set cookies — docs/youtube.md"
    if len(core) > 280:
        core = core[:277] + "…"
    return f"yt-dlp failed: {core}"


def _tail_text(path: Path, *, limit: int = 800) -> str:
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    raw = raw.strip()
    if len(raw) > limit:
        return raw[-limit:]
    return raw


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
    """Data API search + yt-dlp → ffmpeg Pulse sink player."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._ffmpeg: Optional[subprocess.Popen[bytes]] = None
        self._ytdlp: Optional[subprocess.Popen[bytes]] = None
        self._last_error: str = ""
        self._log_paths: list[Path] = []

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
        if self._last_error:
            note = f"{note}. Last play soft-fail: {self._last_error}"
            limits.append(self._last_error)
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
            "last_error": self._last_error or None,
            "running": self.running(),
        }

    def configured(self) -> bool:
        return credentials_configured()

    def authenticated(self) -> bool:
        return credentials_configured()

    def playable(self) -> bool:
        """Hub can attempt play when API key + yt-dlp + ffmpeg are present."""
        return bool(credentials_configured() and _ytdlp_bin() and _ffmpeg_bin())

    def last_error(self) -> str:
        return self._last_error

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

    def _resolve_media_url(self, page_url: str, *, ytdlp: str) -> str:
        """Ask yt-dlp for a direct media URL (clear stderr on failure)."""
        cmd = [
            ytdlp,
            "-f",
            _YTDLP_FORMAT,
            "-g",
            "--no-playlist",
            "--no-warnings",
            # Prefer android player client — fewer bot challenges on Pi LANs.
            "--extractor-args",
            "youtube:player_client=android,web",
            *(_ytdlp_auth_args()),
            page_url,
        ]
        log.info("YouTube resolve: %s", " ".join(cmd[:-1] + ["<url>"]))
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=_RESOLVE_TIMEOUT_S,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ValueError(
                f"yt-dlp timed out resolving media URL (>{_RESOLVE_TIMEOUT_S:.0f}s)"
            ) from exc
        except OSError as exc:
            raise ValueError(f"Failed to run yt-dlp: {exc}") from exc
        if proc.returncode != 0:
            raise ValueError(_summarize_ytdlp_stderr(proc.stderr or proc.stdout or ""))
        urls = [ln.strip() for ln in (proc.stdout or "").splitlines() if ln.strip()]
        if not urls:
            raise ValueError(
                "yt-dlp returned no media URL — update yt-dlp or set cookies "
                "(docs/youtube.md)"
            )
        # bestaudio usually one URL; if multiple, take the first (audio).
        return urls[0]

    def _ffmpeg_log_path(self, video_id: str) -> Path:
        from ..config import repo_root

        path = repo_root() / "config" / f"youtube-{video_id}.ffmpeg.log"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        return path

    def play_uri(self, uri: str, *, feeder: Optional[str] = None) -> dict[str, Any]:
        """Stream audio: yt-dlp (-g) → ffmpeg → Pulse sink (musync_feeder).

        Resolves a direct media URL first so yt-dlp failures surface as clear
        errors instead of a silent empty pipe. Soft-fails the hub only when the
        caller catches ValueError — sinks stay up.
        """
        vid = parse_video_id(uri)
        if not vid:
            raise ValueError(f"Invalid YouTube locator: {uri!r}")
        ytdlp = _ytdlp_bin()
        ffmpeg = _ffmpeg_bin()
        if not ytdlp:
            self._last_error = "yt-dlp missing — install yt-dlp or set MUSYNC_YTDLP_BIN"
            raise ValueError(self._last_error)
        if not ffmpeg:
            self._last_error = "ffmpeg missing — install ffmpeg or set MUSYNC_FFMPEG_BIN"
            raise ValueError(self._last_error)
        sink = (feeder or _feeder_sink()).strip() or _feeder_sink()
        page = watch_url(vid)
        self.stop()

        try:
            media_url = self._resolve_media_url(page, ytdlp=ytdlp)
        except ValueError as exc:
            self._last_error = str(exc)
            raise

        log_path = self._ffmpeg_log_path(vid)
        ffmpeg_cmd = [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "warning",
            "-reconnect",
            "1",
            "-reconnect_streamed",
            "1",
            "-reconnect_delay_max",
            "5",
            "-i",
            media_url,
            "-vn",
            "-ac",
            "2",
            "-ar",
            "48000",
            "-f",
            "pulse",
            sink,
        ]
        log.info("YouTube play %s → %s via yt-dlp|-g|ffmpeg", vid, sink)
        try:
            log_fh = open(log_path, "wb")  # noqa: SIM115
        except OSError:
            log_fh = subprocess.DEVNULL  # type: ignore[assignment]

        try:
            ffmpeg_proc = subprocess.Popen(
                ffmpeg_cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=log_fh,
                start_new_session=True,
            )
        except OSError as exc:
            if log_fh is not subprocess.DEVNULL:
                try:
                    log_fh.close()
                except Exception:  # noqa: BLE001
                    pass
            self._last_error = f"Failed to start ffmpeg: {exc}"
            raise ValueError(self._last_error) from exc
        finally:
            # Child owns the fd after Popen; close our copy when it's a real file.
            if log_fh is not subprocess.DEVNULL:
                try:
                    log_fh.close()
                except Exception:  # noqa: BLE001
                    pass

        with self._lock:
            self._ffmpeg = ffmpeg_proc
            self._ytdlp = None
            self._log_paths = [log_path]

        # Brief settle — catch immediate pulse/URL failures instead of silent play.
        time.sleep(_STARTUP_SETTLE_S)
        if ffmpeg_proc.poll() is not None:
            detail = _tail_text(log_path) or f"ffmpeg exited rc={ffmpeg_proc.returncode}"
            self.stop()
            self._last_error = (
                f"ffmpeg → {sink} failed at start: {detail}. "
                "Ensure musync_feeder exists (pactl list sinks short) and "
                "pipewire-pulse is running — docs/youtube.md"
            )
            raise ValueError(self._last_error)

        self._last_error = ""
        return {
            "ok": True,
            "video_id": vid,
            "uri": uri_for(vid),
            "feeder": sink,
            "mode": "ytdlp_ffmpeg",
            "resolved": True,
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
