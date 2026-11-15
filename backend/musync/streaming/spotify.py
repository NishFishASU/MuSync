"""Spotify OAuth + Web API + librespot Connect → PipeWire feeder.

Env (never commit secrets):
  MUSYNC_SPOTIFY_CLIENT_ID
  MUSYNC_SPOTIFY_CLIENT_SECRET
  MUSYNC_SPOTIFY_REDIRECT_URI   (default http://127.0.0.1:8080/api/streaming/spotify/callback)
  MUSYNC_SPOTIFY_TOKEN_PATH     (default config/spotify_tokens.json)
  MUSYNC_LIBRESPOT_BIN          (default: librespot on PATH, else ~/.cargo/bin)
  MUSYNC_LIBRESPOT_NAME         (Connect device name, default MuSync)
  MUSYNC_LIBRESPOT_BITRATE      (96|160|320, default 160)
  MUSYNC_PW_FEEDER              (Pulse/PipeWire sink librespot targets)
  MUSYNC_SPOTIFY_PREVIEW_FALLBACK  (1 = allow 30s mpv preview when Connect fails)
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

# cargo install librespot drops the binary here when ~/.cargo/bin is not on PATH
# (common under systemd / non-login shells on the Pi).
_LIBRESPOT_INSTALL_HINT = (
    "On the Pi: sudo apt install -y libasound2-dev && cargo install librespot "
    "(binary lands in ~/.cargo/bin — MuSync auto-finds it). "
    "Restart MuSync after install. Premium required. See docs/spotify-apple.md"
)

SPOTIFY_AUTH = "https://accounts.spotify.com"
SPOTIFY_API = "https://api.spotify.com/v1"
# Authorize scopes. Search does NOT use market=from_token (that 403s without
# user-read-private on older tokens) — it uses MUSYNC_SPOTIFY_MARKET / US.
# Space-separated list on /authorize — bumping requires Disconnect → Connect
# (show_dialog=true); refresh tokens do not gain new scopes.
SCOPES = (
    "user-read-private user-read-email "
    "user-read-playback-state user-modify-playback-state "
    "user-read-currently-playing streaming"
)
# Hard requirements for Connect playback; search only needs a valid user token.
REQUIRED_SCOPES = frozenset(
    {
        "user-read-playback-state",
        "user-modify-playback-state",
        "user-read-currently-playing",
        "streaming",
    }
)


def _search_market() -> str:
    """ISO country for /search — never ``from_token`` (Insufficient client scope)."""
    raw = (os.environ.get("MUSYNC_SPOTIFY_MARKET") or "US").strip().upper()
    if not raw or raw == "FROM_TOKEN":
        return "US"
    # Spotify expects ISO 3166-1 alpha-2
    if len(raw) == 2 and raw.isalpha():
        return raw
    return "US"


def _search_limit_max() -> int:
    """Max ``limit`` for /v1/search.

    Development Mode Spotify apps reject anything above **10** with a misleading
    ``400 Invalid limit``. Extended Quota Mode still allows up to 50 — set
    ``MUSYNC_SPOTIFY_SEARCH_MAX=50`` if your app has extended quota.
    """
    raw = (os.environ.get("MUSYNC_SPOTIFY_SEARCH_MAX") or "10").strip()
    try:
        n = int(raw)
    except ValueError:
        n = 10
    return max(1, min(n, 50))


def _coerce_query_int(value: Any, *, default: int) -> int:
    """Normalize FastAPI/httpx query values that may arrive as list/tuple/str/None."""
    if value is None or value is False:
        return default
    if isinstance(value, (list, tuple)):
        if not value:
            return default
        value = value[0]
    if isinstance(value, bool):
        return default
    if isinstance(value, float):
        value = int(value)
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _clamp_search_limit(limit: Any) -> str:
    """Spotify /search ``limit`` as digit string in 1..SEARCH_MAX (Dev Mode max 10)."""
    cap = _search_limit_max()
    default = min(10, cap)
    n = _coerce_query_int(limit, default=default)
    return str(max(1, min(n, cap)))


def _client_id() -> str:
    return (os.environ.get("MUSYNC_SPOTIFY_CLIENT_ID") or "").strip()


def _client_secret() -> str:
    return (os.environ.get("MUSYNC_SPOTIFY_CLIENT_SECRET") or "").strip()


DEFAULT_API_PORT = 8080
_CALLBACK_PATH = "/api/streaming/spotify/callback"


def api_listen_port() -> int:
    """Resolve ``MUSYNC_PORT`` — empty/unset → **8080**; never emit blank port."""
    raw = (os.environ.get("MUSYNC_PORT") or "").strip()
    if not raw:
        return DEFAULT_API_PORT
    try:
        port = int(raw)
    except ValueError as exc:
        raise ValueError(
            f"MUSYNC_PORT must be an integer 1–65535 (got {raw!r}); "
            f"default is {DEFAULT_API_PORT}"
        ) from exc
    if not (1 <= port <= 65535):
        raise ValueError(
            f"MUSYNC_PORT out of range (got {port}); default is {DEFAULT_API_PORT}"
        )
    return port


def redirect_uri_has_empty_port(uri: str) -> bool:
    """True for ``http://host:/path`` (colon present, port digits missing)."""
    raw = (uri or "").strip()
    if not raw or "://" not in raw:
        return False
    parsed = urllib.parse.urlparse(raw)
    hostport = parsed.netloc.rsplit("@", 1)[-1]
    # IPv6 in brackets: [::1]: or [::1]:8080
    if hostport.startswith("["):
        close = hostport.find("]")
        if close < 0:
            return False
        rest = hostport[close + 1 :]
        return rest == ":" or rest.startswith(":/")
    if ":" not in hostport:
        return False
    _host, _, port_part = hostport.rpartition(":")
    return port_part == ""


def redirect_uri_needs_port(uri: str) -> bool:
    """True when redirect has empty port or no explicit port (Spotify needs exact URI)."""
    raw = (uri or "").strip()
    if not raw or "://" not in raw:
        return True
    if redirect_uri_has_empty_port(raw):
        return True
    parsed = urllib.parse.urlparse(raw)
    return parsed.port is None


def normalize_spotify_redirect_uri(
    uri: Optional[str] = None, *, port: Optional[int] = None
) -> str:
    """Return a callback URI with an explicit non-empty port.

    - Empty / unset → ``http://127.0.0.1:<port>/api/streaming/spotify/callback``
    - ``http://host:/path`` (empty port) → insert ``port`` (default listen/8080)
    - Missing port → insert ``port``
    - Never returns ``host:/path``.
    """
    listen = port if port is not None else api_listen_port()
    raw = (uri or "").strip()
    if not raw:
        return f"http://127.0.0.1:{listen}{_CALLBACK_PATH}"

    if redirect_uri_has_empty_port(raw):
        # Refuse empty port: rewrite with listen port.
        parsed = urllib.parse.urlparse(raw)
        hostport = parsed.netloc.rsplit("@", 1)[-1]
        userinfo = ""
        if "@" in parsed.netloc:
            userinfo = parsed.netloc.rsplit("@", 1)[0] + "@"
        if hostport.startswith("["):
            close = hostport.find("]")
            host = hostport[: close + 1]
        else:
            host = hostport.rpartition(":")[0]
        netloc = f"{userinfo}{host}:{listen}"
        path = parsed.path or _CALLBACK_PATH
        return urllib.parse.urlunparse(
            (parsed.scheme or "http", netloc, path, "", "", "")
        )

    parsed = urllib.parse.urlparse(raw)
    scheme = (parsed.scheme or "http").lower()
    path = parsed.path or _CALLBACK_PATH
    if parsed.port is None:
        # HTTPS public tunnels (Cloudflare, etc.) must keep default :443 omitted —
        # injecting MUSYNC_PORT would break Dashboard exact-match (…:8080 ≠ …).
        if scheme == "https":
            return urllib.parse.urlunparse(
                (scheme, parsed.netloc, path, "", "", "")
            )
        host = parsed.hostname or "127.0.0.1"
        # Preserve IPv6 brackets when rebuilding.
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        userinfo = ""
        if parsed.username:
            userinfo = parsed.username
            if parsed.password:
                userinfo += f":{parsed.password}"
            userinfo += "@"
        netloc = f"{userinfo}{host}:{listen}"
        return urllib.parse.urlunparse((scheme, netloc, path, "", "", ""))

    # Already has a real port — keep scheme/host/port/path; drop query/fragment.
    return urllib.parse.urlunparse(
        (scheme, parsed.netloc, path, "", "", "")
    )


def default_redirect_uri() -> str:
    """Default OAuth callback: ``http://127.0.0.1:<MUSYNC_PORT>/…``.

    Pi + laptop default port is **8080**. Override ``MUSYNC_PORT=8081`` on a
    laptop when 8080 is taken (e.g. EDB Postgres). Always ``127.0.0.1`` —
    Spotify forbids ``localhost`` and ``musync.local``. Never emits empty port.
    """
    return normalize_spotify_redirect_uri(None)


def is_forbidden_oauth_host(host: Optional[str]) -> bool:
    """Spotify rejects ``localhost`` and ``*.local`` (incl. musync.local) redirects."""
    h = (host or "").strip().lower().rstrip(".")
    if not h:
        return True
    if h == "localhost" or h.endswith(".localhost"):
        return True
    if h == "local" or h.endswith(".local"):
        return True
    return False


def is_loopback_oauth_host(host: Optional[str]) -> bool:
    """True for ``127.0.0.1`` / ``::1`` (Spotify's only allowed plain-HTTP hosts)."""
    h = (host or "").strip().lower().rstrip(".")
    if not h:
        return False
    if h in {"127.0.0.1", "::1"}:
        return True
    # Other 127.0.0.0/8 — Spotify Dashboard only documents 127.0.0.1; treat as loopback-ish.
    if h.startswith("127."):
        return True
    return False


def spotify_redirect_rejection_reason(uri: str) -> Optional[str]:
    """Why Spotify Developer Dashboard will reject this redirect, or ``None`` if OK.

    Spotify allows only:
    - ``http://127.0.0.1:<port>/…`` (or ``::1``)
    - ``https://<any-host>/…``

    Plain ``http://<LAN-IP>:8080/…`` is **Invalid redirect URI** — never tell users
    to register a Pi LAN IP for Spotify OAuth.
    """
    raw = (uri or "").strip()
    if not raw:
        return "Redirect URI is empty"
    fixed = normalize_spotify_redirect_uri(raw)
    parsed = urllib.parse.urlparse(fixed)
    scheme = (parsed.scheme or "").lower()
    host = parsed.hostname
    if is_forbidden_oauth_host(host):
        return (
            "Spotify forbids localhost and *.local redirect hosts — "
            "use http://127.0.0.1:<port>/… or https://…"
        )
    if scheme == "https":
        if not host:
            return "HTTPS redirect URI needs a hostname"
        return None
    if scheme == "http":
        if is_loopback_oauth_host(host) and parsed.port:
            return None
        if host and not is_loopback_oauth_host(host):
            return (
                f"Spotify rejects plain http://{host} redirects (Invalid redirect URI). "
                "Use http://127.0.0.1:<port>/… with an SSH tunnel, or "
                "https://<tunnel>/api/streaming/spotify/callback (Cloudflare Tunnel). "
                f"Pi LAN IP is for the UI only (http://{host}:{parsed.port or api_listen_port()}/)."
            )
        return "HTTP Spotify redirect must be http://127.0.0.1:<port>/…"
    return "Spotify redirect must be http://127.0.0.1:… or https://…"


def is_spotify_allowed_redirect_uri(uri: str) -> bool:
    """True when Dashboard will accept this URI (loopback HTTP or HTTPS)."""
    return spotify_redirect_rejection_reason(uri) is None


def redirect_uri_for_request_base(
    base_url: str, *, force_loopback: bool = False
) -> str:
    """Build callback from a request base URL, rewriting invalid hosts to loopback.

    ``force_loopback=True`` always returns ``http://127.0.0.1:<port>/…`` (Pi Connect
    from a musync.local / LAN browser tab still OAuths via loopback — same as laptop mock).

    Non-loopback ``http://`` (Pi LAN IP) is rewritten to loopback — Spotify rejects
    ``http://<LAN-IP>`` as Invalid redirect URI.
    """
    if force_loopback:
        return default_redirect_uri()
    raw = (base_url or "").strip().rstrip("/")
    if not raw:
        return default_redirect_uri()
    candidate = f"{raw}{_CALLBACK_PATH}"
    parsed = urllib.parse.urlparse(candidate)
    port = parsed.port or api_listen_port()
    scheme = (parsed.scheme or "http").lower()
    # Vite / preview ports never receive the OAuth callback.
    for bad in (":5173", ":4173"):
        if bad in raw:
            return default_redirect_uri()
    if scheme == "https" and parsed.hostname and not is_forbidden_oauth_host(
        parsed.hostname
    ):
        return normalize_spotify_redirect_uri(candidate)
    # Plain HTTP: only loopback is Spotify-legal.
    if is_forbidden_oauth_host(parsed.hostname) or not is_loopback_oauth_host(
        parsed.hostname
    ):
        return normalize_spotify_redirect_uri(
            f"http://127.0.0.1:{port}{_CALLBACK_PATH}", port=port
        )
    return normalize_spotify_redirect_uri(candidate)


def _redirect_uri() -> str:
    """Effective env redirect — Spotify-illegal values fall back to loopback."""
    configured = (os.environ.get("MUSYNC_SPOTIFY_REDIRECT_URI") or "").strip()
    if not configured:
        return default_redirect_uri()
    fixed = normalize_spotify_redirect_uri(configured)
    reason = spotify_redirect_rejection_reason(fixed)
    if reason:
        log.warning(
            "MUSYNC_SPOTIFY_REDIRECT_URI rejected (%s) — using loopback instead",
            reason.split("—")[0].strip() if "—" in reason else reason[:80],
        )
        parsed = urllib.parse.urlparse(fixed)
        return normalize_spotify_redirect_uri(
            f"http://127.0.0.1:{parsed.port or api_listen_port()}{_CALLBACK_PATH}"
        )
    return fixed


def effective_oauth_redirect_uri(*, prefer_loopback: bool = False) -> str:
    """URI Connect should send to Spotify.

    - Spotify-legal ``MUSYNC_SPOTIFY_REDIRECT_URI`` wins: ``https://…`` (phone /
      Cloudflare Tunnel) or ``http://127.0.0.1:…`` (SSH tunnel / on-Pi).
    - Illegal values (``http://<LAN-IP>``, localhost, ``*.local``) fall back to
      loopback — never invent a LAN-IP callback.
    - ``prefer_loopback`` is reserved for callers that already chose the Connect
      default; HTTPS env is never demoted to loopback.
    """
    configured = (os.environ.get("MUSYNC_SPOTIFY_REDIRECT_URI") or "").strip()
    if configured:
        fixed = normalize_spotify_redirect_uri(configured)
        if is_spotify_allowed_redirect_uri(fixed):
            scheme = (urllib.parse.urlparse(fixed).scheme or "").lower()
            if scheme == "https":
                # Off-device path — never demote HTTPS to loopback for Connect.
                return fixed
            if is_loopback_oauth_host(urllib.parse.urlparse(fixed).hostname):
                return fixed
    if prefer_loopback:
        return default_redirect_uri()
    return _redirect_uri()


def _token_path() -> Path:
    raw = os.environ.get("MUSYNC_SPOTIFY_TOKEN_PATH")
    if raw:
        return Path(raw).expanduser().resolve()
    return (repo_root() / "config" / "spotify_tokens.json").resolve()


def _is_executable(path: Path) -> bool:
    try:
        return path.is_file() and os.access(path, os.X_OK)
    except OSError:
        return False


def _cargo_home_librespot() -> Optional[str]:
    """Find ``librespot`` under CARGO_HOME / ``~/.cargo/bin`` (post ``cargo install``)."""
    candidates: list[Path] = []
    cargo_home = (os.environ.get("CARGO_HOME") or "").strip()
    if cargo_home:
        candidates.append(Path(cargo_home).expanduser() / "bin" / "librespot")
    candidates.append(Path.home() / ".cargo" / "bin" / "librespot")
    seen: set[str] = set()
    for path in candidates:
        try:
            resolved = str(path.resolve())
        except OSError:
            resolved = str(path)
        if resolved in seen:
            continue
        seen.add(resolved)
        if _is_executable(path):
            return resolved
    return None


def _librespot_bin() -> Optional[str]:
    """Resolve librespot: env override → PATH → ``~/.cargo/bin/librespot``."""
    override = (os.environ.get("MUSYNC_LIBRESPOT_BIN") or "").strip()
    if override:
        as_path = Path(override).expanduser()
        if _is_executable(as_path):
            try:
                return str(as_path.resolve())
            except OSError:
                return str(as_path)
        which = shutil.which(override)
        if which:
            return which
        # Keep the override so spawn fails with a clear "No such file" path.
        return override
    found = shutil.which("librespot")
    if found:
        return found
    return _cargo_home_librespot()


def librespot_install_hint() -> str:
    return _LIBRESPOT_INSTALL_HINT


def preview_fallback_enabled() -> bool:
    return (os.environ.get("MUSYNC_SPOTIFY_PREVIEW_FALLBACK") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


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
        self._librespot_last_error: str = ""
        self._load_tokens()

    # --- config / status ---

    def granted_scopes(self) -> set[str]:
        with self._lock:
            raw = str(self._tokens.get("scope") or "")
        return {s for s in raw.replace(",", " ").split() if s}

    def missing_scopes(self) -> list[str]:
        """Scopes we request that are absent from the stored token (needs re-consent)."""
        granted = self.granted_scopes()
        if not granted:
            # Older token files predate scope persistence — treat as unknown/missing.
            return sorted(REQUIRED_SCOPES)
        return sorted(REQUIRED_SCOPES - granted)

    def status(self) -> dict[str, Any]:
        with self._lock:
            configured = credentials_configured()
            authed = bool(self._tokens.get("access_token") or self._tokens.get("refresh_token"))
            display_name = self._tokens.get("display_name")
            librespot = _librespot_bin()
            running = self._librespot_alive()
        missing = self.missing_scopes() if authed and configured else []
        limits: list[str] = []
        if not configured:
            limits.append("Set MUSYNC_SPOTIFY_CLIENT_ID + MUSYNC_SPOTIFY_CLIENT_SECRET")
        if not librespot:
            limits.append(
                "Install librespot for hub PCM (Connect device MuSync). "
                + _LIBRESPOT_INSTALL_HINT
            )
        if missing:
            limits.append(
                "Reconnect Spotify (Disconnect → Connect) to approve updated scopes: "
                + ", ".join(missing)
            )
        if self._librespot_last_error:
            limits.append(self._librespot_last_error)
        if not configured:
            note = (
                "Credentials missing — set MUSYNC_SPOTIFY_CLIENT_ID/SECRET "
                "in ~/musync.env (Windows: %USERPROFILE%\\musync.env), then Connect Spotify"
            )
        elif missing:
            note = (
                "Connected but token is missing scopes for search/playback — "
                "Streaming → Disconnect → Connect Spotify (re-consent), then search on Queue"
            )
        elif librespot and running:
            note = (
                f"librespot running as {_librespot_name()!r} → {_feeder_sink()} "
                f"({librespot}; Premium Connect)"
            )
        elif librespot:
            note = (
                f"librespot found ({librespot}, name={_librespot_name()!r}) — "
                f"starts on Play → {_feeder_sink()}"
            )
        else:
            note = (
                "Connected/search OK — Pi hub PCM needs librespot "
                f"(auto-finds ~/.cargo/bin). {_LIBRESPOT_INSTALL_HINT}"
            )
        if self._librespot_last_error:
            note = f"{note}. Last librespot error: {self._librespot_last_error}"
        loopback = default_redirect_uri()
        effective = effective_oauth_redirect_uri()
        scheme = (urllib.parse.urlparse(effective).scheme or "").lower()
        if scheme == "https":
            oauth_hint = (
                "Connect uses HTTPS redirect "
                f"{effective} — register that exact URI in the Spotify Dashboard. "
                "Keep the Cloudflare (or similar) tunnel up while authorizing. "
                "Pi LAN IP is UI-only; Spotify rejects http://<LAN-IP> redirects."
            )
        else:
            oauth_hint = (
                "Connect uses loopback "
                f"{loopback} — open the callback on the Pi (SSH -L / local UI) at "
                "127.0.0.1, or set an https:// tunnel URI for phone Connect. "
                "Spotify rejects http://<LAN-IP> and musync.local redirects."
            )
        return {
            "provider": "spotify",
            "configured": configured,
            "authenticated": authed and configured,
            "display_name": display_name,
            "librespot_available": bool(librespot),
            "librespot_running": running,
            "librespot_name": _librespot_name(),
            "librespot_bin": librespot,
            "feeder": _feeder_sink(),
            "redirect_uri": effective,
            "oauth_loopback_uri": loopback,
            "oauth_hint": oauth_hint,
            "premium_required": True,
            "scopes": sorted(self.granted_scopes()),
            "missing_scopes": missing,
            "search_market": _search_market(),
            "search_limit_max": _search_limit_max(),
            "limits": limits,
            "note": note,
            "last_error": self._librespot_last_error or None,
            "install_hint": None if librespot else _LIBRESPOT_INSTALL_HINT,
        }

    def configured(self) -> bool:
        return credentials_configured()

    def authenticated(self) -> bool:
        with self._lock:
            return credentials_configured() and bool(
                self._tokens.get("access_token") or self._tokens.get("refresh_token")
            )

    # --- OAuth ---

    def auth_url(
        self, *, redirect_uri: Optional[str] = None, force_consent: bool = True
    ) -> str:
        """Build Spotify authorize URL.

        ``force_consent=True`` sets ``show_dialog=true`` so users always re-approve
        scopes (required after SCOPES bumps — refresh tokens keep the old grant).
        """
        if not credentials_configured():
            raise ValueError(
                "Spotify not configured — set MUSYNC_SPOTIFY_CLIENT_ID and "
                "MUSYNC_SPOTIFY_CLIENT_SECRET (see docs/spotify-apple.md)"
            )
        state = uuid.uuid4().hex
        # Guard: never send host:/path to Spotify (empty port → listen/8080).
        redir = normalize_spotify_redirect_uri(redirect_uri or _redirect_uri())
        with self._lock:
            self._oauth_state = state
            self._oauth_redirect = redir
        params = {
            "client_id": _client_id(),
            "response_type": "code",
            "redirect_uri": redir,
            "scope": SCOPES,
            "state": state,
            # Always re-prompt so scope upgrades (e.g. user-read-private for search) apply.
            "show_dialog": "true" if force_consent else "false",
        }
        return f"{SPOTIFY_AUTH}/authorize?{urllib.parse.urlencode(params)}"

    def handle_callback(
        self, *, code: str, state: str, redirect_uri: Optional[str] = None
    ) -> dict[str, Any]:
        with self._lock:
            expected = self._oauth_state
            # Prefer the URI we sent to /authorize (must match token exchange).
            # Fall back to the callback request URL, then env/default.
            redir = normalize_spotify_redirect_uri(
                getattr(self, "_oauth_redirect", None)
                or (redirect_uri or "").strip()
                or _redirect_uri()
            )
            self._oauth_state = None
            self._oauth_redirect = None
        if not expected or state != expected:
            raise ValueError("Invalid OAuth state — restart Connect from the MuSync UI")
        if not credentials_configured():
            raise ValueError(
                "Spotify client credentials missing — set MUSYNC_SPOTIFY_CLIENT_ID and "
                "MUSYNC_SPOTIFY_CLIENT_SECRET in ~/musync.env "
                "(Windows: %USERPROFILE%\\musync.env), restart, then Connect again"
            )
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redir,
        }
        token = self._token_request(data)
        self._store_tokens(token)
        # /me can 403 when the Spotify app is in development mode and the
        # signed-in user is not on the Dashboard allowlist — never 500.
        try:
            profile = self._get_json("/me")
        except ValueError as exc:
            self.logout()
            raise
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
        # Do not gate search on playback scopes / user-read-private — catalog search
        # only needs a user access token + an explicit country market (not from_token).
        data = self._get_json(
            "/search",
            params={
                "q": q,
                "type": "track",
                # Spotify requires limit as an integer query value in 1..50.
                "limit": _clamp_search_limit(limit),
                "market": _search_market(),
            },
        )
        items = (data.get("tracks") or {}).get("items") or []
        out: list[SpotifyTrack] = []
        for raw in items:
            if not isinstance(raw, dict):
                continue
            artists = ", ".join(
                a.get("name", "") for a in (raw.get("artists") or []) if isinstance(a, dict) and a.get("name")
            )
            album = raw.get("album") if isinstance(raw.get("album"), dict) else {}
            images = album.get("images") or []
            cover = None
            if images:
                # Prefer mid-size art when present.
                mid = images[min(1, len(images) - 1)] if isinstance(images[0], dict) else None
                first = images[0] if isinstance(images[0], dict) else None
                cover = (mid or {}).get("url") or (first or {}).get("url")
            track_id = str(raw.get("id") or "").strip()
            uri = str(raw.get("uri") or (f"spotify:track:{track_id}" if track_id else "")).strip()
            if not uri:
                continue
            duration = raw.get("duration_ms")
            if duration is not None and not isinstance(duration, int):
                try:
                    duration = int(duration)
                except (TypeError, ValueError):
                    duration = None
            out.append(
                SpotifyTrack(
                    id=track_id or uri,
                    uri=uri,
                    title=str(raw.get("name") or "Track"),
                    artist=artists or "Unknown",
                    duration_ms=duration,
                    album=album.get("name"),
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

    def _librespot_log_path(self) -> Path:
        path = repo_root() / "config" / "librespot.stderr.log"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        return path

    def _tail_librespot_log(self, *, limit: int = 600) -> str:
        path = self._librespot_log_path()
        try:
            raw = path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            return ""
        if len(raw) > limit:
            return raw[-limit:]
        return raw

    def ensure_librespot(self, *, feeder: Optional[str] = None) -> Optional[str]:
        """Start librespot targeting the PipeWire/Pulse feeder sink. Returns bin or None."""
        bin_path = _librespot_bin()
        if not bin_path:
            self._librespot_last_error = (
                "librespot binary not found on PATH or ~/.cargo/bin. "
                + _LIBRESPOT_INSTALL_HINT
            )
            return None
        # Explicit override / bad path: fail early with a clear message.
        if not shutil.which(bin_path) and not _is_executable(Path(bin_path)):
            self._librespot_last_error = (
                f"librespot binary not executable: {bin_path!r}. "
                + _LIBRESPOT_INSTALL_HINT
            )
            return None
        sink = (feeder or _feeder_sink()).strip() or _feeder_sink()
        with self._lock:
            if self._librespot_alive():
                return bin_path
            env = os.environ.copy()
            # Pulse/PipeWire: force output onto the MuSync null-sink.
            env["PULSE_SINK"] = sink
            env.setdefault("PIPEWIRE_LATENCY", "128/48000")
            # Ensure cargo-installed librespot can find dynamic libs when spawned.
            cargo_bin = str(Path.home() / ".cargo" / "bin")
            path_now = env.get("PATH") or ""
            if cargo_bin not in path_now.split(":"):
                env["PATH"] = f"{cargo_bin}:{path_now}" if path_now else cargo_bin
            cache = repo_root() / "config" / "librespot-cache"
            cache.mkdir(parents=True, exist_ok=True)
            log_path = self._librespot_log_path()
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
                log_fh = open(log_path, "wb")  # noqa: SIM115
            except OSError:
                log_fh = subprocess.DEVNULL  # type: ignore[assignment]
            try:
                self._librespot_proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=log_fh,
                    text=False,
                    env=env,
                )
                log.info(
                    "Started librespot %r → PULSE_SINK=%s (pid %s, bin=%s)",
                    _librespot_name(),
                    sink,
                    self._librespot_proc.pid,
                    bin_path,
                )
            except OSError as exc:
                log.warning("Failed to start librespot (%s): %s", bin_path, exc)
                self._librespot_proc = None
                self._librespot_last_error = (
                    f"Failed to start librespot ({bin_path}): {exc}. "
                    + _LIBRESPOT_INSTALL_HINT
                )
                return None
            finally:
                if log_fh is not subprocess.DEVNULL:
                    try:
                        log_fh.close()
                    except Exception:  # noqa: BLE001
                        pass
        # Give Zeroconf a moment to advertise before device lookup.
        time.sleep(1.2)
        if not self._librespot_alive():
            detail = self._tail_librespot_log() or "exited with no stderr"
            self._librespot_last_error = (
                f"librespot exited immediately ({bin_path}): {detail}. "
                "Check pipewire-pulse is up and PULSE_SINK=musync_feeder exists "
                f"(pactl list sinks short). {_LIBRESPOT_INSTALL_HINT}"
            )
            log.warning("%s", self._librespot_last_error)
            with self._lock:
                self._librespot_proc = None
            return None
        self._librespot_last_error = ""
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
                # Keep short — local/YouTube Play must not wait 2s on a hung librespot.
                proc.wait(timeout=0.25)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=0.25)
                except subprocess.TimeoutExpired:
                    pass

    def play_uri(self, uri: str, *, feeder: Optional[str] = None) -> dict[str, Any]:
        """Force-transfer playback to the MuSync librespot Connect device.

        Never calls ``/me/player/play`` without ``device_id`` — that would keep
        (or start) audio on the phone speakers while the hub also plays.
        Premium + librespot required for hub PCM.
        """
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
        device_id = self._wait_for_connect_device(
            attempts=6 if librespot else 2,
            delay_s=1.0,
        )
        if not device_id:
            spawn_detail = self._librespot_last_error
            if not librespot:
                raise ValueError(
                    "No MuSync Spotify Connect device — refusing to play on the phone. "
                    f"librespot did not start (name={_librespot_name()!r}, "
                    f"PULSE_SINK={feeder or _feeder_sink()}). "
                    + (f"{spawn_detail} " if spawn_detail else "")
                    + _LIBRESPOT_INSTALL_HINT
                )
            raise ValueError(
                "No MuSync Spotify Connect device — refusing to play on the phone. "
                f"librespot is running ({librespot}, name={_librespot_name()!r}) but "
                "Spotify Web API does not list it yet. Use Spotify Premium, wait a few "
                "seconds, open Spotify → Devices and confirm MuSync, then Queue & play "
                "again. Without a Connect device, Web API play would keep phone speakers "
                "alive."
                + (f" Last error: {spawn_detail}" if spawn_detail else "")
            )

        # Explicit transfer first so the phone Spotify app yields to MuSync.
        transferred = self.transfer_playback(device_id, play=False)
        body: dict[str, Any] = {"uris": [track_uri]}
        params: dict[str, str] = {"device_id": device_id}
        try:
            self._put_json("/me/player/play", params=params, json_body=body)
        except httpx.HTTPStatusError as exc:
            detail = self._spotify_error_detail(exc)
            if exc.response.status_code == 404:
                raise ValueError(
                    "Spotify Connect device gone after transfer. Keep librespot running "
                    f"(name={_librespot_name()!r}) and retry. Premium required. {detail}"
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
            "transferred": transferred,
            "feeder": feeder or _feeder_sink(),
        }

    def transfer_playback(self, device_id: str, *, play: bool = False) -> bool:
        """PUT /me/player — move the active Spotify session onto ``device_id``.

        Soft-fails (returns False) when Spotify has no active device yet; the
        subsequent ``/me/player/play?device_id=…`` still targets MuSync.
        """
        did = (device_id or "").strip()
        if not did or not self.authenticated():
            return False
        try:
            self._put_json(
                "/me/player",
                json_body={"device_ids": [did], "play": bool(play)},
            )
            log.info("Spotify Connect transferred → device %s (play=%s)", did, play)
            return True
        except httpx.HTTPStatusError as exc:
            # 404 = nothing to transfer yet; play with device_id still works.
            log.debug(
                "Spotify transfer soft-fail (%s): %s",
                exc.response.status_code,
                self._spotify_error_detail(exc),
            )
            return False
        except Exception as exc:  # noqa: BLE001
            log.debug("Spotify transfer soft-fail: %s", exc)
            return False

    def pause(self) -> None:
        """Pause the active Spotify Connect player (phone or hub). Soft-fail."""
        if not self.authenticated():
            return
        try:
            # Short timeout — pause must not gate hub local/YouTube PCM start.
            self._put_json("/me/player/pause", json_body=None, timeout=3.0)
        except Exception as exc:  # noqa: BLE001
            log.debug("Spotify pause: %s", exc)

    def pause_phone_playback(self) -> bool:
        """Best-effort pause so phone Spotify stops fighting hub local/YouTube/….

        Uses Web API ``/me/player/pause`` when OAuth tokens exist. Returns True
        when the pause request succeeded. Cannot mute non-Spotify phone apps —
        the MuSync SPA has no OS-level exclusive-audio API on Android/iOS.

        Keep the HTTP timeout short: hub Play starts PCM first and pauses the
        phone asynchronously — a 20s Spotify hang must not delay local audio.
        """
        if not self.authenticated():
            return False
        try:
            self._put_json("/me/player/pause", json_body=None, timeout=3.0)
            log.info("Paused Spotify remote playback (phone/other devices yield to hub)")
            return True
        except Exception as exc:  # noqa: BLE001
            log.debug("Spotify phone-side pause soft-fail: %s", exc)
            return False

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

    def _wait_for_connect_device(
        self, *, attempts: int = 5, delay_s: float = 1.0
    ) -> Optional[str]:
        """Poll ``/me/player/devices`` until librespot/MuSync appears."""
        attempts = max(1, int(attempts))
        for i in range(attempts):
            device_id = self._find_connect_device()
            if device_id:
                return device_id
            if i + 1 < attempts:
                time.sleep(max(0.0, float(delay_s)))
        return self._find_connect_device()

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
        try:
            tmp.chmod(0o600)
        except OSError:
            # Windows / some FS ignore POSIX modes — still write the file.
            pass
        tmp.replace(path)

    def _store_tokens(self, token: dict[str, Any]) -> None:
        with self._lock:
            merged = dict(self._tokens)
            merged.update(token)
            merged["obtained_at"] = time.time()
            if "expires_in" in token:
                merged["expires_at"] = time.time() + float(token["expires_in"]) - 30
            # Persist granted scopes (authorize + refresh responses include them).
            if token.get("scope"):
                merged["scope"] = str(token.get("scope") or "").strip()
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
            raise ValueError(
                "Spotify client credentials missing — set MUSYNC_SPOTIFY_CLIENT_ID and "
                "MUSYNC_SPOTIFY_CLIENT_SECRET, restart MuSync, then Connect again"
            )
        try:
            with httpx.Client(timeout=20.0) as client:
                resp = client.post(
                    f"{SPOTIFY_AUTH}/api/token",
                    data=data,
                    auth=(_client_id(), _client_secret()),
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
        except httpx.RequestError as exc:
            raise ValueError(
                f"Spotify token exchange network error — check connectivity and retry. ({exc})"
            ) from exc
        if resp.status_code >= 400:
            raise ValueError(self._token_error_message(resp))
        try:
            return resp.json()
        except ValueError as exc:
            raise ValueError("Spotify token response was not valid JSON") from exc

    @staticmethod
    def _token_error_message(resp: httpx.Response) -> str:
        body_text = (resp.text or "")[:300]
        err = ""
        desc = ""
        try:
            body = resp.json()
            err = str(body.get("error") or "")
            desc = str(body.get("error_description") or "")
        except Exception:  # noqa: BLE001
            pass
        combined = f"{err} {desc} {body_text}".lower()
        if "redirect_uri" in combined:
            return (
                "Spotify redirect URI mismatch — Dashboard must list the API callback "
                "(register both http://127.0.0.1:8080/... and :8081/...; set env to the "
                "port you run). Use 127.0.0.1 — not localhost, musync.local, or Vite :5173. "
                f"Spotify said: {desc or body_text or resp.status_code}"
            )
        if err in {"invalid_client", "invalid_client_id"} or "invalid_client" in combined:
            return (
                "Spotify rejected Client ID/Secret — check MUSYNC_SPOTIFY_CLIENT_ID and "
                "MUSYNC_SPOTIFY_CLIENT_SECRET in musync.env, then restart and Connect again"
            )
        if desc:
            return f"Spotify token error ({resp.status_code}): {desc}"
        return f"Spotify token error: {resp.status_code} {body_text}"

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._access_token()}"}

    def _get_json(self, path: str, *, params: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        """GET Spotify Web API JSON — always raises ValueError (never bare HTTP 500)."""
        try:
            with httpx.Client(timeout=20.0) as client:
                resp = client.get(
                    f"{SPOTIFY_API}{path}", headers=self._headers(), params=params
                )
        except httpx.RequestError as exc:
            raise ValueError(
                f"Spotify API network error on {path} — check connectivity and retry. ({exc})"
            ) from exc
        if resp.status_code >= 400:
            raise ValueError(self._web_api_error_message(path, resp))
        try:
            data = resp.json()
        except ValueError as exc:
            raise ValueError(f"Spotify API returned non-JSON on {path}") from exc
        if not isinstance(data, dict):
            raise ValueError(f"Spotify API returned unexpected JSON on {path}")
        return data

    @classmethod
    def _web_api_error_message(cls, path: str, resp: httpx.Response) -> str:
        status = resp.status_code
        detail = cls._spotify_response_detail(resp)
        detail_l = detail.lower()
        if "insufficient client scope" in detail_l:
            return (
                "Spotify: Insufficient client scope — reconnect on Streaming if playback "
                "scopes are missing. Catalog search no longer uses market=from_token "
                f"(uses {_search_market()}). ({detail})"
            )
        if path == "/me" and status == 403:
            return (
                "Spotify /me returned 403 Forbidden — this app is likely in development "
                "mode and your account is not allowlisted. In Spotify Developer Dashboard → "
                "your app → User Management, add the Spotify email/username you signed in "
                "with, then Connect again. "
                f"({detail})"
            )
        if status == 401:
            return (
                "Spotify access token rejected — open Streaming → Connect Spotify again. "
                f"({detail})"
            )
        if path.startswith("/search"):
            if "invalid limit" in detail_l:
                return (
                    "Spotify search rejected limit — Development Mode apps allow at most "
                    f"10 results per /search request (set MUSYNC_SPOTIFY_SEARCH_MAX=10). "
                    f"MuSync now clamps to {_search_limit_max()}. ({detail})"
                )
            return f"Spotify search failed ({status}): {detail}"
        if status == 403:
            return (
                "Spotify API forbidden (403) — check Developer Dashboard User Management "
                f"allowlist and app scopes, then Connect again. ({path}: {detail})"
            )
        return f"Spotify API error on {path} ({status}): {detail}"

    @staticmethod
    def _spotify_response_detail(resp: httpx.Response) -> str:
        try:
            body = resp.json()
            err = body.get("error") or {}
            if isinstance(err, dict):
                return str(err.get("message") or body)[:240]
            if err:
                return str(err)[:240]
            return str(body)[:240]
        except Exception:  # noqa: BLE001
            return (resp.text or f"HTTP {resp.status_code}")[:240]

    def _put_json(
        self,
        path: str,
        *,
        params: Optional[dict[str, str]] = None,
        json_body: Optional[dict[str, Any]],
        timeout: float = 20.0,
    ) -> None:
        with httpx.Client(timeout=float(timeout)) as client:
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
