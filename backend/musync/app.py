"""FastAPI application — REST surface + static web UI."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote

from fastapi import FastAPI, File, HTTPException, Query, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .audio import AudioBackend, create_backend
from .config import media_library_path, resolve_media_file, web_root
from .streaming.spotify import default_redirect_uri
from .models import (
    ActiveQueuePut,
    ActiveSourcePut,
    BluetoothConnectRequest,
    BluetoothConnectResponse,
    BluetoothScanStatus,
    CaptureDiscoverResponse,
    Device,
    DeviceDiscoverResponse,
    DevicePatch,
    DevicesConfigPut,
    Health,
    InputHost,
    InputHostCreate,
    InputHostPatch,
    NowPlaying,
    QueueItem,
    QueueItemCreate,
    QueueItemPatch,
    Source,
    SourceType,
    StreamingQueueAdd,
    StreamingSearchResponse,
    StreamingStatus,
    Transport,
    TransportPut,
    DeviceUserAssign,
    DeviceUserAssignResult,
    VolumeProfile,
    VolumeProfileActivePut,
    VolumeProfileCreate,
    VolumeProfilePatch,
    VolumeProfilesState,
    WifiConnectRequest,
    WifiNetwork,
    WifiSaveRequest,
    WifiStatus,
)
from .nowplaying import resolve_cover_bytes
from .profiles import VolumeProfileStore, apply_profile_to_backend
from .streaming import get_streaming_service
from .streaming.credentials import load_streaming_env_files
from .wifi import WifiBackend, create_wifi_backend

# Client ID/secret from ~/musync.env or config/streaming.local.env before backends boot.
load_streaming_env_files()

backend: AudioBackend = create_backend()
wifi_backend: WifiBackend = create_wifi_backend()
profile_store: VolumeProfileStore = VolumeProfileStore()

app = FastAPI(
    title="MuSync",
    version=__version__,
    description="Multi-device Bluetooth audio sync hub — song-level source switching",
)

# Vite (:5173) / preview (:4173) and alternate mock ports (:8081) call the API
# cross-origin; OAuth itself is a top-level redirect, but status/search need CORS.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://127.0.0.1:4173",
        "http://localhost:4173",
        "http://127.0.0.1:8080",
        "http://localhost:8080",
        "http://127.0.0.1:8081",
        "http://localhost:8081",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(KeyError)
async def key_error_handler(_request: Request, exc: KeyError) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(ValueError)
async def value_error_handler(_request: Request, exc: ValueError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(RuntimeError)
async def runtime_error_handler(_request: Request, exc: RuntimeError) -> JSONResponse:
    # nmcli / system failures (passwords already redacted in wifi backends)
    return JSONResponse(status_code=502, content={"detail": str(exc)})


@app.get("/api/health", response_model=Health)
def api_health() -> Health:
    return backend.health()


@app.get("/api/devices", response_model=list[Device])
def api_list_devices() -> list[Device]:
    return backend.list_devices()


@app.get("/api/devices/discover", response_model=DeviceDiscoverResponse)
def api_discover_devices() -> DeviceDiscoverResponse:
    """Auto-discover bluez sinks by name + always-available SPP defaults.

    UI sets sink type and SPP host/port/rate/channels — no manual MAC entry.
    """
    return backend.discover_device_setup()


@app.put("/api/devices/config", response_model=list[Device])
def api_put_devices_config(body: DevicesConfigPut) -> list[Device]:
    """Save enabled sinks from the web UI (MAC filled from discovery by name)."""
    if not body.devices:
        raise ValueError("At least one sink is required (enable Phone SPP or a BT device)")
    return backend.apply_devices_config(body.devices)


@app.get("/api/devices/{device_id}", response_model=Device)
def api_get_device(device_id: str) -> Device:
    return backend.get_device(device_id)


@app.patch("/api/devices/{device_id}", response_model=Device)
def api_patch_device(device_id: str, patch: DevicePatch) -> Device:
    """Patch sink controls. Volume changes by an assigned user update S_u / G_d."""
    updated = backend.patch_device(device_id, patch)
    if patch.volume is not None:
        user_id = profile_store.assigned_user(device_id)
        if user_id:
            try:
                profile_store.record_volume(user_id, device_id, float(patch.volume))
            except KeyError:
                pass
    return updated


@app.get("/api/profiles", response_model=VolumeProfilesState)
def api_list_profiles() -> VolumeProfilesState:
    """Named listening preference profiles (silent-disco) stored on the hub."""
    return profile_store.get_state()


@app.post("/api/profiles", response_model=VolumeProfile, status_code=201)
def api_create_profile(body: VolumeProfileCreate) -> VolumeProfile:
    return profile_store.create(body)


@app.put("/api/profiles/active", response_model=VolumeProfilesState)
def api_set_active_profile(body: VolumeProfileActivePut) -> VolumeProfilesState:
    """Select a profile (or clear) and apply preferred × Device.gain volumes."""
    state = profile_store.set_active(body.id)
    if body.id:
        profile = profile_store.get(body.id)
        apply_profile_to_backend(profile, backend)
    return state


@app.put("/api/profiles/assignments", response_model=DeviceUserAssignResult)
def api_assign_device_user(body: DeviceUserAssign) -> DeviceUserAssignResult:
    """Assign a listening profile/user to one output; preset via S_u × G_d."""
    try:
        device = backend.get_device(body.device_id)
    except KeyError as exc:
        raise KeyError(f"Unknown device: {body.device_id}") from exc
    return profile_store.assign_device(body, backend=backend, device=device)


@app.get("/api/profiles/{profile_id}", response_model=VolumeProfile)
def api_get_profile(profile_id: str) -> VolumeProfile:
    return profile_store.get(profile_id)


@app.patch("/api/profiles/{profile_id}", response_model=VolumeProfile)
def api_patch_profile(profile_id: str, body: VolumeProfilePatch) -> VolumeProfile:
    """Update name / preferred level / per-output overrides; re-apply if active."""
    updated = profile_store.patch(profile_id, body)
    active = profile_store.active_profile()
    if active and active.id == updated.id:
        apply_profile_to_backend(updated, backend)
    return updated


@app.delete("/api/profiles/{profile_id}", status_code=204)
def api_delete_profile(profile_id: str) -> Response:
    profile_store.delete(profile_id)
    return Response(status_code=204)


@app.post("/api/profiles/{profile_id}/apply", response_model=list[Device])
def api_apply_profile(profile_id: str) -> list[Device]:
    """Apply a profile's preferred levels to all outputs without changing selection."""
    profile = profile_store.get(profile_id)
    return apply_profile_to_backend(profile, backend)


@app.get("/api/bluetooth/scan", response_model=BluetoothScanStatus)
def api_bluetooth_scan_status() -> BluetoothScanStatus:
    """BlueZ scan session status + accumulated discovery rows."""
    return backend.bluetooth_scan_status()


@app.post("/api/bluetooth/scan", response_model=BluetoothScanStatus)
def api_bluetooth_scan_start() -> BluetoothScanStatus:
    """Start ``bluetoothctl scan on`` and refresh discovery."""
    return backend.bluetooth_scan_start()


@app.delete("/api/bluetooth/scan", response_model=BluetoothScanStatus)
def api_bluetooth_scan_stop() -> BluetoothScanStatus:
    """Stop ``bluetoothctl scan off``."""
    return backend.bluetooth_scan_stop()


@app.post("/api/bluetooth/discover/clear", response_model=BluetoothScanStatus)
def api_bluetooth_clear_discovery() -> BluetoothScanStatus:
    """Clear discovery list except currently connected remotes."""
    return backend.bluetooth_clear_discovery()


@app.post("/api/bluetooth/connect", response_model=BluetoothConnectResponse)
def api_bluetooth_connect(body: BluetoothConnectRequest) -> BluetoothConnectResponse:
    """Pair/connect a remote by MAC (optionally enable as an output sink)."""
    return backend.bluetooth_connect(body)


@app.get("/api/transport", response_model=Transport)
def api_get_transport() -> Transport:
    return backend.get_transport()


@app.put("/api/transport", response_model=Transport)
def api_put_transport(body: TransportPut) -> Transport:
    return backend.set_transport(body)


@app.get("/api/now-playing", response_model=NowPlaying)
def api_now_playing() -> NowPlaying:
    """Unified title / artist / album / cover for the active cue."""
    return backend.get_now_playing()


@app.get("/api/now-playing/cover")
def api_now_playing_cover(source: str | None = Query(default=None)) -> Response:
    """Serve embedded cover art bytes for a local library file (or active cue)."""
    session = getattr(backend, "_session", None)
    if session is None:
        raise HTTPException(status_code=404, detail="No cover available")
    # Mock backend always falls back to sample SVG so laptop UI shows album art.
    health = getattr(backend, "health", None)
    allow_mock = False
    try:
        allow_mock = bool(health and health().backend == "mock")
    except Exception:  # noqa: BLE001
        allow_mock = False
    result = resolve_cover_bytes(session, source_id=source, allow_mock=allow_mock)
    if result is None:
        raise HTTPException(status_code=404, detail="No embedded cover art")
    data, ctype = result
    return Response(content=data, media_type=ctype, headers={"Cache-Control": "private, max-age=3600"})


@app.get("/api/sources", response_model=list[Source])
def api_list_sources() -> list[Source]:
    return backend.list_sources()


@app.put("/api/sources/active", response_model=Transport)
def api_set_active_source(body: ActiveSourcePut) -> Transport:
    """Pick a catalog source; creates/reuses a queue item. Sinks stay up."""
    return backend.set_active_source(body.id)


@app.get("/api/media/{rel_path:path}")
def api_get_media(rel_path: str) -> FileResponse:
    """Serve a hub-local media file for optional muted video preview in the UI.

    Audio for sinks still comes from mpv → musync_feeder, not this response.
    """
    try:
        path = resolve_media_file(rel_path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return FileResponse(path)


@app.get("/api/queue", response_model=list[QueueItem])
def api_list_queue() -> list[QueueItem]:
    return backend.list_queue()


@app.post("/api/queue", response_model=QueueItem, status_code=201)
def api_add_queue_item(body: QueueItemCreate) -> QueueItem:
    return backend.add_queue_item(body)


@app.patch("/api/queue/{item_id}", response_model=QueueItem)
def api_patch_queue_item(item_id: str, patch: QueueItemPatch) -> QueueItem:
    """Switch source_type on a song without tearing down the sink graph."""
    return backend.patch_queue_item(item_id, patch)


@app.delete("/api/queue/{item_id}", status_code=204)
def api_delete_queue_item(item_id: str) -> Response:
    backend.remove_queue_item(item_id)
    return Response(status_code=204)


@app.put("/api/queue/active", response_model=Transport)
def api_set_active_queue_item(body: ActiveQueuePut) -> Transport:
    """Activate a queue item (feeder swap only)."""
    return backend.set_active_queue_item(body.id)


@app.get("/api/inputs", response_model=list[InputHost])
def api_list_inputs() -> list[InputHost]:
    """Registered phone/PC hosts (≤4). Live hosts refresh from PipeWire sources."""
    return backend.list_inputs()


@app.get("/api/inputs/discover", response_model=CaptureDiscoverResponse)
def api_discover_inputs() -> CaptureDiscoverResponse:
    """Discover bluez_input / USB / HDMI Audio/Source nodes for host binding."""
    return backend.discover_capture_sources()


@app.post("/api/inputs", response_model=InputHost, status_code=201)
def api_add_input(body: InputHostCreate) -> InputHost:
    return backend.add_input(body)


@app.patch("/api/inputs/{host_id}", response_model=InputHost)
def api_patch_input(host_id: str, patch: InputHostPatch) -> InputHost:
    return backend.patch_input(host_id, patch)


@app.post("/api/inputs/{host_id}/upload", response_model=InputHost)
async def api_upload_input(host_id: str, file: UploadFile = File(...)) -> InputHost:
    """Network capture stub: save upload and route playback into the feeder."""
    hosts = {h.id: h for h in backend.list_inputs()}
    if host_id not in hosts:
        raise KeyError(f"Unknown input host: {host_id}")

    raw_name = Path(file.filename or "upload.bin").name
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", raw_name) or "upload.bin"
    dest_dir = media_library_path() / "uploads" / host_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / safe
    data = await file.read()
    if not data:
        raise ValueError("Uploaded file is empty")
    dest.write_bytes(data)
    return backend.bind_input_upload(host_id, str(dest.resolve()))


@app.delete("/api/inputs/{host_id}", status_code=204)
def api_delete_input(host_id: str) -> Response:
    backend.remove_input(host_id)
    return Response(status_code=204)


@app.get("/api/streaming", response_model=StreamingStatus)
def api_streaming_status() -> StreamingStatus:
    """Spotify / Apple auth + capability status (secrets never returned)."""
    return get_streaming_service().status()


def _spotify_redirect_uri(request: Request) -> str:
    """Resolve the OAuth redirect URI Spotify will call back to.

    Prefer explicit ``MUSYNC_SPOTIFY_REDIRECT_URI``. Otherwise derive from the
    request Host (Pi LAN IP or 127.0.0.1:PORT). Never use the Vite port
    (:5173) — callback must hit the FastAPI process (default :8080, or
    ``MUSYNC_PORT`` e.g. :8081 on Windows mock).
    """
    import os

    configured = (os.environ.get("MUSYNC_SPOTIFY_REDIRECT_URI") or "").strip()
    if configured:
        return configured
    base = str(request.base_url).rstrip("/")
    # If a reverse-proxy / Vite forwarded Host is a frontend-only port, fall
    # back to the API default so Spotify dashboard URI stays on the API port.
    for bad in (":5173", ":4173"):
        if bad in base:
            return default_redirect_uri()
    return f"{base}/api/streaming/spotify/callback"


@app.get("/api/streaming/spotify/login")
def api_spotify_login(request: Request) -> RedirectResponse:
    """Link-style OAuth: redirect browser to Spotify authorize → callback → tokens.

    Not a password form. Client ID/secret come from env (``~/musync.env`` /
    ``%USERPROFILE%\\musync.env`` on Windows).
    """
    from urllib.parse import quote

    if not get_streaming_service().spotify.configured():
        return RedirectResponse(
            "/?spotify=error&detail="
            + quote(
                "Spotify app credentials missing — set MUSYNC_SPOTIFY_CLIENT_ID and "
                "MUSYNC_SPOTIFY_CLIENT_SECRET in ~/musync.env "
                "(Windows: %USERPROFILE%\\musync.env or config/streaming.local.env), "
                "restart MuSync, then Connect again"
            )
        )
    redirect_uri = _spotify_redirect_uri(request)
    try:
        url = get_streaming_service().spotify.auth_url(redirect_uri=redirect_uri)
    except ValueError as exc:
        return RedirectResponse("/?spotify=error&detail=" + quote(str(exc)))
    return RedirectResponse(url)


@app.get("/api/streaming/spotify/authorize-url")
def api_spotify_authorize_url(request: Request) -> dict[str, str | bool]:
    """JSON helper for the Connect button — returns authorize URL or a clear error.

    Prefer the same-origin ``/login`` redirect; this exists so the SPA can
    preflight credentials and show errors without a confusing blank navigation.
    """
    svc = get_streaming_service().spotify
    if not svc.configured():
        return {
            "ok": False,
            "error": (
                "Spotify app credentials missing — set MUSYNC_SPOTIFY_CLIENT_ID and "
                "MUSYNC_SPOTIFY_CLIENT_SECRET in ~/musync.env "
                "(Windows: %USERPROFILE%\\musync.env), restart, then Connect again"
            ),
            "redirect_uri": _spotify_redirect_uri(request),
        }
    redirect_uri = _spotify_redirect_uri(request)
    try:
        return {
            "ok": True,
            "url": svc.auth_url(redirect_uri=redirect_uri),
            "redirect_uri": redirect_uri,
        }
    except ValueError as exc:
        return {"ok": False, "error": str(exc), "redirect_uri": redirect_uri}


@app.get("/api/streaming/spotify/callback")
def api_spotify_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    """OAuth redirect URI — stores tokens, then returns to the SPA.

    Always redirects to the SPA with ``spotify=connected`` or ``spotify=error`` —
    never returns a bare 500 for token exchange / profile / missing-secret failures.
    """
    from urllib.parse import quote

    def _err(detail: str) -> RedirectResponse:
        return RedirectResponse("/?spotify=error&detail=" + quote(detail))

    if error:
        return _err(error)
    if not code or not state:
        return _err("Missing OAuth code/state — try Connect again")
    # Exact URI Spotify hit (no query) — must match authorize redirect_uri.
    callback_uri = str(request.url.replace(query="", fragment=""))
    try:
        get_streaming_service().spotify.handle_callback(
            code=code, state=state, redirect_uri=callback_uri
        )
    except ValueError as exc:
        return _err(str(exc))
    except Exception as exc:  # noqa: BLE001 — surface any leftover failure in the UI
        return _err(f"Spotify Connect failed: {exc}")
    # Refresh queue availability now that auth is live.
    try:
        backend.list_sources()
        if hasattr(backend, "_session"):
            backend._session.refresh_live_availability()  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass
    return RedirectResponse("/?spotify=connected")


@app.post("/api/streaming/spotify/logout", response_model=StreamingStatus)
def api_spotify_logout() -> StreamingStatus:
    get_streaming_service().spotify.logout()
    if hasattr(backend, "_session"):
        try:
            backend._session.refresh_live_availability()  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass
    return get_streaming_service().status()


@app.get("/api/streaming/search", response_model=StreamingSearchResponse)
def api_streaming_search(
    provider: str = Query(..., description="spotify or apple"),
    q: str = Query(..., min_length=1),
    limit: int = Query(15, ge=1, le=25),
) -> StreamingSearchResponse:
    """Search Spotify/Apple. Always returns 400 with a real message — never bare 500."""
    p = (provider or "").strip().lower()
    svc = get_streaming_service()
    if p == "spotify" and not svc.spotify.configured():
        raise ValueError(
            "Spotify not configured — copy config/streaming.env.example to ~/musync.env, "
            "set MUSYNC_SPOTIFY_CLIENT_ID + MUSYNC_SPOTIFY_CLIENT_SECRET, "
            "then restart ./scripts/run_pi.sh (see docs/spotify-apple.md)"
        )
    if p == "spotify" and not svc.spotify.authenticated():
        raise ValueError("Connect Spotify first (Streaming tab), then search from Queue")
    if p in {"apple", "apple_music", "music"} and not svc.apple.configured():
        raise ValueError(
            "Apple Music not configured — set MUSYNC_APPLE_TEAM_ID, "
            "MUSYNC_APPLE_KEY_ID, MUSYNC_APPLE_PRIVATE_KEY_PATH in ~/musync.env "
            "(search only; hub PCM needs AirPlay — docs/spotify-apple.md)"
        )
    try:
        return svc.search(provider, q, limit=limit)
    except ValueError:
        raise
    except Exception as exc:  # noqa: BLE001 — surface provider failures in the UI
        label = "Spotify" if p == "spotify" else "Apple Music" if p.startswith("apple") else "Streaming"
        raise ValueError(f"{label} search failed: {exc}") from exc


@app.post("/api/streaming/queue", response_model=QueueItem, status_code=201)
def api_streaming_queue_add(body: StreamingQueueAdd) -> QueueItem:
    """Add a searched track to the hub queue and optionally activate it."""
    source_type = SourceType.SPOTIFY if body.provider == "spotify" else SourceType.APPLE
    # Keep title clean; artist/album/cover live on dedicated fields for now-playing.
    title = body.title or body.uri
    item = backend.add_queue_item(
        QueueItemCreate(
            source_type=source_type,
            locator=body.uri,
            title=title,
            duration_ms=body.duration_ms,
            source_id=body.uri,
            artist=body.artist,
            album=body.album,
            cover_url=body.cover_url,
        )
    )
    if body.activate:
        backend.set_active_queue_item(item.id)
        # Re-fetch in case availability/note updated on activate path.
        items = {q.id: q for q in backend.list_queue()}
        item = items.get(item.id, item)
    return item


@app.get("/api/wifi", response_model=WifiStatus)
def api_wifi_status() -> WifiStatus:
    """Current Wi-Fi association and IPv4 addresses (for SPP / LAN access)."""
    return wifi_backend.status()


@app.get("/api/wifi/networks", response_model=list[WifiNetwork])
def api_wifi_list_networks() -> list[WifiNetwork]:
    """Saved SSIDs only — passwords are never returned."""
    return wifi_backend.list_networks()


@app.post("/api/wifi/networks", response_model=WifiNetwork)
def api_wifi_save_network(body: WifiSaveRequest) -> WifiNetwork:
    """Pre-save an SSID (+ optional password) without requiring an immediate join."""
    return wifi_backend.save_network(body.ssid, body.password)


@app.post("/api/wifi/connect", response_model=WifiStatus)
def api_wifi_connect(body: WifiConnectRequest) -> WifiStatus:
    """Join a network; omit password when the profile was pre-saved."""
    return wifi_backend.connect(body.ssid, body.password)


@app.delete("/api/wifi/networks/{ssid}", status_code=204)
def api_wifi_forget_network(ssid: str) -> Response:
    wifi_backend.forget_network(unquote(ssid))
    return Response(status_code=204)


def _index_html() -> Path:
    return web_root() / "index.html"


_SPA_PAGES = frozenset(
    {"outputs", "inputs", "streaming", "queue", "wifi", "profiles"}
)


def _spa_page_file(page: str) -> Path | None:
    """Resolve a tab route to a built HTML file (dir index or flat .html)."""
    root = web_root()
    for candidate in (
        root / page / "index.html",
        root / f"{page}.html",
        root / "index.html",
    ):
        if candidate.is_file():
            return candidate
    return None


@app.get("/")
def index() -> FileResponse:
    path = _index_html()
    if not path.is_file():
        raise HTTPException(
            status_code=503,
            detail="UI not built — run: cd web && npm ci && npm run build",
        )
    return FileResponse(path)


@app.get("/{page}")
@app.get("/{page}/")
def spa_tab(page: str) -> FileResponse:
    """Deep-link SvelteKit tabs when StaticFiles won't map /outputs → outputs.html."""
    if page.startswith("_") or page == "api":
        raise HTTPException(status_code=404)
    if page not in _SPA_PAGES:
        raise HTTPException(status_code=404)
    path = _spa_page_file(page)
    if path is None:
        raise HTTPException(
            status_code=503,
            detail="UI not built — run: cd web && npm ci && npm run build",
        )
    return FileResponse(path)


# Built SvelteKit assets (_app/*, favicon, SPA fallback) after API routes.
_web = web_root()
if _web.is_dir():
    app.mount("/", StaticFiles(directory=str(_web), html=True), name="web")


def create_app(
    audio_backend: AudioBackend | None = None,
    wifi: WifiBackend | None = None,
    profiles: VolumeProfileStore | None = None,
) -> FastAPI:
    """Test helper: swap the module-level backends used by route handlers."""
    global backend, wifi_backend, profile_store
    if audio_backend is not None:
        backend = audio_backend
    if wifi is not None:
        wifi_backend = wifi
    if profiles is not None:
        profile_store = profiles
    return app
