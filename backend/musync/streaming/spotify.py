"""Spotify OAuth + Web API + librespot Connect → PipeWire feeder.

Env (never commit secrets):
  MUSYNC_SPOTIFY_CLIENT_ID
  MUSYNC_SPOTIFY_CLIENT_SECRET
  MUSYNC_SPOTIFY_REDIRECT_URI   (default http://127.0.0.1:8080/api/streaming/spotify/callback)
  MUSYNC_SPOTIFY_TOKEN_PATH     (default config/spotify_tokens.json)
  MUSYNC_LIBRESPOT_BIN          (default: librespot on PATH)
  MUSYNC_LIBRESPOT_NAME         (Connect device name, default MuSync)
  MUSYNC_LIBRESPOT_BITRATE      (96|160|320, default 160)
  MUSYNC_PW_FEEDER              (Pulse/PipeWire sink librespot targets)
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import threading
import time
import urllib.parse
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import httpx

from ..config import repo_root

log = logging.getLogger(__name__)

SPOTIFY_AUTH = "https://accounts.spotify.com"
SPOTIFY_API = "https://api.spotify.com/v1"
SCOPES = "user-read-playback-state user-modify-playback-state user-read-currently-playing streaming"


def _client_id() -> str:
    return (os.environ.get("MUSYNC_SPOTIFY_CLIENT_ID") or "").strip()


def _client_secret() -> str:
    return (os.environ.get("MUSYNC_SPOTIFY_CLIENT_SECRET") or "").strip()


def default_redirect_uri() -> str:
    """Default callback URL using MUSYNC_PORT (8080, or 8081 if you override the port)."""
    port = (os.environ.get("MUSYNC_PORT") or "8080").strip() or "8080"
    return f"http://127.0.0.1:{port}/api/streaming/spotify/callback"


def _redirect_uri() -> str:
    return (os.environ.get("MUSYNC_SPOTIFY_REDIRECT_URI") or default_redirect_uri()).strip()


def _token_path() -> Path:
    raw = os.environ.get("MUSYNC_SPOTIFY_TOKEN_PATH")
    if raw:
        return Path(raw).expanduser().resolve()
    return (repo_root() / "config" / "spotify_tokens.json").resolve()


def _librespot_bin() -> Optional[str]:
    override = (os.environ.get("MUSYNC_LIBRESPOT_BIN") or "").strip()
    if override:
        return override if Path(override).exists() or shutil.which(override) else override
    return shutil.which("librespot")


def _librespot_name() -> str:
    return (os.environ.get("MUSYNC_LIBRESPOT_NAME") or "MuSync").strip() or "MuSync"


def _librespot_bitrate() -> str:
    raw = (os.environ.get("MUSYNC_LIBRESPOT_BITRATE") or "160").strip()
    return raw if raw in {"96", "160", "320"} else "160"


def _feeder_sink() -> str:
    return (os.environ.get("MUSYNC_PW_FEEDER") or "musync_feeder").strip() or "musync_feeder"


def credentials_configured() -> bool:
    return bool(_client_id() and _client_secret())


@dataclass
class SpotifyTrack:
    id: str
    uri: str
    title: str
    artist: str
    duration_ms: Optional[int] = None
    album: Optional[str] = None
    preview_url: Optional[str] = None
    cover_url: Optional[str] = None


class SpotifyClient:
    """OAuth + Web API client with optional librespot Connect player."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._tokens: dict[str, Any] = {}
        self._oauth_state: Optional[str] = None
        self._oauth_redirect: Optional[str] = None
        self._librespot_proc: Optional[subprocess.Popen[str]] = None
        self._load_tokens()

    # --- config / status ---

    def status(self) -> dict[str, Any]:
        with self._lock:
            configured = credentials_configured()
            authed = bool(self._tokens.get("access_token") or self._tokens.get("refresh_token"))
            librespot = _librespot_bin()
            return {
                "provider": "spotify",
                "configured": configured,
                "authenticated": authed and configured,
                "display_name": self._tokens.get("display_name"),
                "librespot_available": bool(librespot),
                "librespot_running": self._librespot_alive(),
                "librespot_name": _librespot_name(),
                "feeder": _feeder_sink(),
                "redirect_uri": _redirect_uri(),
                "premium_required": True,
                "limits": (
                    []
                    if configured
                    else ["Set MUSYNC_SPOTIFY_CLIENT_ID + MUSYNC_SPOTIFY_CLIENT_SECRET"]
                )
                + (
                    []
                    if librespot
                    else [
                        "Install librespot for real PCM into musync_feeder "
                        "(search/auth still work; Play needs Connect device)"
                    ]
                ),
                "note": (
                    (
                        "Spotify Premium + librespot Connect → Pulse sink musync_feeder"
                        if librespot
                        else "Connected/search OK — install librespot for hub PCM "
                        "(Play needs Connect device)"
                    )
                    if configured
                    else "Credentials missing — set MUSYNC_SPOTIFY_CLIENT_ID/SECRET "
                    "in ~/musync.env (Windows: %USERPROFILE%\\musync.env), then Connect Spotify"
                ),
            }

    def configured(self) -> bool:
        return credentials_configured()

    def authenticated(self) -> bool:
        with self._lock:
            return credentials_configured() and bool(
                self._tokens.get("access_token") or self._tokens.get("refresh_token")
            )

    # --- OAuth ---

    def auth_url(self, *, redirect_uri: Optional[str] = None) -> str:
        if not credentials_configured():
            raise ValueError(
                "Spotify not configured — set MUSYNC_SPOTIFY_CLIENT_ID and "
                "MUSYNC_SPOTIFY_CLIENT_SECRET (see docs/spotify-apple.md)"
            )
        state = uuid.uuid4().hex
        redir = (redirect_uri or _redirect_uri()).strip()
        with self._lock:
            self._oauth_state = state
            self._oauth_redirect = redir
        params = {
            "client_id": _client_id(),
            "response_type": "code",
            "redirect_uri": redir,
            "scope": SCOPES,
            "state": state,
            "show_dialog": "false",
        }
        return f"{SPOTIFY_AUTH}/authorize?{urllib.parse.urlencode(params)}"

    def handle_callback(self, *, code: str, state: str) -> dict[str, Any]:
        with self._lock:
            expected = self._oauth_state
            redir = getattr(self, "_oauth_redirect", None) or _redirect_uri()
            self._oauth_state = None
            self._oauth_redirect = None
        if not expected or state != expected:
            raise ValueError("Invalid OAuth state — restart Connect from the MuSync UI")
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redir,
        }
        token = self._token_request(data)
        self._store_tokens(token)
        profile = self._get_json("/me")
        with self._lock:
            self._tokens["display_name"] = profile.get("display_name") or profile.get("id")
            self._save_tokens()
        return self.status()

    def logout(self) -> dict[str, Any]:
        with self._lock:
            self._tokens = {}
            path = _token_path()
            if path.exists():
                path.unlink()
        self.stop_librespot()
        return self.status()

    # --- catalog ---

    def search_tracks(self, query: str, *, limit: int = 15) -> list[SpotifyTrack]:
        q = (query or "").strip()
        if not q:
            return []
        if not self.authenticated():
            raise ValueError("Connect a Spotify account first")
        data = self._get_json(
            "/search",
            params={"q": q, "type": "track", "limit": max(1, min(limit, 25))},
        )
        items = (data.get("tracks") or {}).get("items") or []
        out: list[SpotifyTrack] = []
        for raw in items:
            artists = ", ".join(a.get("name", "") for a in (raw.get("artists") or []) if a.get("name"))
            images = (raw.get("album") or {}).get("images") or []
            cover = None
            if images:
                # Prefer mid-size art when present.
                cover = images[min(1, len(images) - 1)].get("url") or images[0].get("url")
            out.append(
                SpotifyTrack(
                    id=raw.get("id") or "",
                    uri=raw.get("uri") or f"spotify:track:{raw.get('id')}",
                    title=raw.get("name") or "Track",
                    artist=artists or "Unknown",
                    duration_ms=raw.get("duration_ms"),
                    album=(raw.get("album") or {}).get("name"),
                    preview_url=raw.get("preview_url"),
                    cover_url=cover,
                )
            )
        return out

    def track_metadata(self, uri: str) -> Optional[SpotifyTrack]:
        """Fetch title/artist/album/cover for a Spotify URI (now-playing)."""
        track_id = ""
        raw_uri = (uri or "").strip()
        if raw_uri.startswith("spotify:track:"):
            track_id = raw_uri.split(":")[-1]
        elif len(raw_uri) == 22 and raw_uri.isalnum():
            track_id = raw_uri
        if not track_id or not self.authenticated():
            return None
        try:
            raw = self._get_json(f"/tracks/{track_id}")
        except Exception as exc:  # noqa: BLE001
            log.debug("track metadata: %s", exc)
            return None
        artists = ", ".join(a.get("name", "") for a in (raw.get("artists") or []) if a.get("name"))
        images = (raw.get("album") or {}).get("images") or []
        cover = None
        if images:
            cover = images[min(1, len(images) - 1)].get("url") or images[0].get("url")
        return SpotifyTrack(
            id=raw.get("id") or track_id,
            uri=raw.get("uri") or f"spotify:track:{track_id}",
            title=raw.get("name") or "Track",
            artist=artists or "Unknown",
            duration_ms=raw.get("duration_ms"),
            album=(raw.get("album") or {}).get("name"),
            preview_url=raw.get("preview_url"),
            cover_url=cover,
        )

    def currently_playing(self) -> Optional[dict[str, Any]]:
        """Spotify Web API currently-playing (best-effort)."""
        if not self.authenticated():
            return None
        try:
            with httpx.Client(timeout=10.0) as client:
                resp = client.get(
                    f"{SPOTIFY_API}/me/player/currently-playing",
                    headers=self._headers(),
                )
            if resp.status_code == 204:
                return None
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:  # noqa: BLE001
            log.debug("currently-playing: %s", exc)
            return None

    # --- playback ---

    def ensure_librespot(self, *, feeder: Optional[str] = None) -> Optional[str]:
        """Start librespot targeting the PipeWire/Pulse feeder sink. Returns bin or None."""
        bin_path = _librespot_bin()
        if not bin_path:
            return None
        sink = (feeder or _feeder_sink()).strip() or _feeder_sink()
        with self._lock:
            if self._librespot_alive():
                return bin_path
            env = os.environ.copy()
            # Pulse/PipeWire: force output onto the MuSync null-sink.
            env["PULSE_SINK"] = sink
            env.setdefault("PIPEWIRE_LATENCY", "128/48000")
            cache = repo_root() / "config" / "librespot-cache"
            cache.mkdir(parents=True, exist_ok=True)
            cmd = [
                bin_path,
                "--name",
                _librespot_name(),
                "--backend",
                "pulseaudio",
                "--bitrate",
                _librespot_bitrate(),
                "--cache",
                str(cache),
                "--disable-audio-cache",
                "--initial-volume",
                "100",
            ]
            try:
                self._librespot_proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    text=True,
                    env=env,
                )
                log.info(
                    "Started librespot %r → PULSE_SINK=%s (pid %s)",
                    _librespot_name(),
                    sink,
                    self._librespot_proc.pid,
                )
            except OSError as exc:
                log.warning("Failed to start librespot: %s", exc)
                self._librespot_proc = None
                return None
        # Give Zeroconf a moment to advertise before device lookup.
        time.sleep(1.2)
        return bin_path

    def stop_librespot(self) -> None:
        with self._lock:
            proc = self._librespot_proc
            self._librespot_proc = None
        if proc is None:
            return
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                proc.kill()

    def play_uri(self, uri: str, *, feeder: Optional[str] = None) -> dict[str, Any]:
        """Transfer playback to librespot Connect device (Premium required)."""
        track_uri = (uri or "").strip()
        if not track_uri.startswith("spotify:"):
            if track_uri.startswith("http"):
                # spotify open URL → uri
                track_uri = self._open_url_to_uri(track_uri) or track_uri
            elif len(track_uri) == 22 and track_uri.isalnum():
                track_uri = f"spotify:track:{track_uri}"
            else:
                raise ValueError(f"Not a Spotify URI: {uri!r}")

        if not self.authenticated():
            raise ValueError("Connect a Spotify account first")

        librespot = self.ensure_librespot(feeder=feeder)
        if not librespot and not self._find_connect_device():
            raise ValueError(
                "Spotify hub playback needs librespot on PATH "
                f"(Connect name={_librespot_name()!r}) or another Connect device. "
                "Install librespot, set MUSYNC_LIBRESPOT_BIN if needed, and use "
                "Spotify Premium. Search/auth still work without it."
            )
        device_id = self._find_connect_device()
        body: dict[str, Any] = {"uris": [track_uri]}
        params: dict[str, str] = {}
        if device_id:
            params["device_id"] = device_id
        elif librespot:
            # Device may not have appeared yet — retry once.
            time.sleep(1.5)
            device_id = self._find_connect_device()
            if device_id:
                params["device_id"] = device_id

        try:
            self._put_json("/me/player/play", params=params or None, json_body=body)
        except httpx.HTTPStatusError as exc:
            detail = self._spotify_error_detail(exc)
            if exc.response.status_code == 404 and not device_id:
                raise ValueError(
                    "No Spotify Connect device found. Install/start librespot "
                    f"(name={_librespot_name()!r}) on the Pi, or open Spotify on a phone "
                    f"and transfer to {_librespot_name()!r}. Premium required. {detail}"
                ) from exc
            if exc.response.status_code == 403:
                raise ValueError(
                    "Spotify play forbidden — Premium is required for Connect control. "
                    f"{detail}"
                ) from exc
            raise ValueError(f"Spotify play failed: {detail}") from exc

        return {
            "uri": track_uri,
            "device_id": device_id,
            "librespot": bool(librespot),
            "feeder": feeder or _feeder_sink(),
        }

    def pause(self) -> None:
        if not self.authenticated():
            return
        try:
            self._put_json("/me/player/pause", json_body=None)
        except Exception as exc:  # noqa: BLE001
            log.debug("Spotify pause: %s", exc)

    def preview_url_for(self, uri: str) -> Optional[str]:
        """30s preview fallback when Connect is unavailable (degraded)."""
        track_id = uri.split(":")[-1] if uri else ""
        if not track_id or not self.authenticated():
            return None
        try:
            data = self._get_json(f"/tracks/{track_id}")
            return data.get("preview_url")
        except Exception:  # noqa: BLE001
            return None

    # --- internals ---

    def _librespot_alive(self) -> bool:
        proc = self._librespot_proc
        return proc is not None and proc.poll() is None

    def _find_connect_device(self) -> Optional[str]:
        try:
            data = self._get_json("/me/player/devices")
        except Exception as exc:  # noqa: BLE001
            log.debug("devices list failed: %s", exc)
            return None
        name = _librespot_name().lower()
        for dev in data.get("devices") or []:
            dname = (dev.get("name") or "").lower()
            if dname == name or "musync" in dname:
                return dev.get("id")
        return None

    def _open_url_to_uri(self, url: str) -> Optional[str]:
        # https://open.spotify.com/track/ID?...
        try:
            path = urllib.parse.urlparse(url).path.strip("/")
            parts = path.split("/")
            if len(parts) >= 2 and parts[0] == "track":
                return f"spotify:track:{parts[1]}"
        except Exception:  # noqa: BLE001
            return None
        return None

    def _load_tokens(self) -> None:
        path = _token_path()
        if not path.exists():
            return
        try:
            self._tokens = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("Could not read Spotify tokens: %s", exc)
            self._tokens = {}

    def _save_tokens(self) -> None:
        path = _token_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._tokens, indent=2), encoding="utf-8")
        tmp.chmod(0o600)
        tmp.replace(path)

    def _store_tokens(self, token: dict[str, Any]) -> None:
        with self._lock:
            merged = dict(self._tokens)
            merged.update(token)
            merged["obtained_at"] = time.time()
            if "expires_in" in token:
                merged["expires_at"] = time.time() + float(token["expires_in"]) - 30
            self._tokens = merged
            self._save_tokens()

    def _access_token(self) -> str:
        with self._lock:
            token = self._tokens.get("access_token")
            expires_at = float(self._tokens.get("expires_at") or 0)
            refresh = self._tokens.get("refresh_token")
        if token and expires_at and time.time() < expires_at:
            return str(token)
        if refresh:
            data = self._token_request(
                {"grant_type": "refresh_token", "refresh_token": refresh}
            )
            self._store_tokens(data)
            with self._lock:
                return str(self._tokens.get("access_token") or "")
        if token:
            return str(token)
        raise ValueError("Not authenticated with Spotify")

    def _token_request(self, data: dict[str, str]) -> dict[str, Any]:
        if not credentials_configured():
            raise ValueError("Spotify client credentials missing")
        with httpx.Client(timeout=20.0) as client:
            resp = client.post(
                f"{SPOTIFY_AUTH}/api/token",
                data=data,
                auth=(_client_id(), _client_secret()),
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            if resp.status_code >= 400:
                raise ValueError(f"Spotify token error: {resp.status_code} {resp.text[:200]}")
            return resp.json()

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._access_token()}"}

    def _get_json(self, path: str, *, params: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        with httpx.Client(timeout=20.0) as client:
            resp = client.get(f"{SPOTIFY_API}{path}", headers=self._headers(), params=params)
            resp.raise_for_status()
            return resp.json()

    def _put_json(
        self,
        path: str,
        *,
        params: Optional[dict[str, str]] = None,
        json_body: Optional[dict[str, Any]],
    ) -> None:
        with httpx.Client(timeout=20.0) as client:
            resp = client.put(
                f"{SPOTIFY_API}{path}",
                headers=self._headers(),
                params=params,
                json=json_body if json_body is not None else {},
            )
            # 204 No Content is success for player endpoints.
            if resp.status_code >= 400:
                raise httpx.HTTPStatusError(
                    f"HTTP {resp.status_code}", request=resp.request, response=resp
                )

    @staticmethod
    def _spotify_error_detail(exc: httpx.HTTPStatusError) -> str:
        try:
            body = exc.response.json()
            err = body.get("error") or {}
            if isinstance(err, dict):
                return str(err.get("message") or body)[:240]
            return str(body)[:240]
        except Exception:  # noqa: BLE001
            return (exc.response.text or str(exc))[:240]
