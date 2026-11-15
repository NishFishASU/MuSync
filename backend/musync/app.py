"""FastAPI application — REST surface + static web UI."""

from __future__ import annotations

import os
import re
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import unquote

from fastapi import FastAPI, File, HTTPException, Query, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .audio import AudioBackend, create_backend
from .config import media_library_path, resolve_media_file, web_root
from .lan_url import enrich_wifi_status, lan_ui_url, svg_qr
from .mdns import get_mdns_status, start_hub_mdns, stop_hub_mdns
from .streaming.https_tunnel import (
    bash_https_tunnel_script,
    build_https_tunnel_info,
)
from .streaming.spotify import default_redirect_uri
from .streaming.ssh_tunnel import (
    bash_tunnel_script,
    build_ssh_tunnel_info,
    powershell_tunnel_script,
)
from .models import (
    ActiveQueuePut,
    ActiveSourcePut,
    AvrcpCommandPut,
    AvrcpStatus,
    AvrcpVolumePut,
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
    QueueSettings,
    QueueSettingsPut,
    Source,
    SourceType,
    SpotifyHttpsTunnelInfo,
    SpotifySshTunnelInfo,
    StreamingCredentialsPut,
    StreamingCredentialsStatus,
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
    MdnsStatus,
    WifiConnectRequest,
    WifiNetwork,
    WifiSaveRequest,
    WifiStatus,
)
from .nowplaying import resolve_cover_bytes
from .profiles import VolumeProfileStore, apply_profile_to_backend
from .streaming import get_streaming_service
from .streaming.credentials import (
    credentials_status,
    load_streaming_env_files,
    save_streaming_credentials,
)
from .wifi import WifiBackend, create_wifi_backend

# Client ID/secret from ~/musync.env or config/streaming.local.env before backends boot.
load_streaming_env_files()

backend: AudioBackend = create_backend()
wifi_backend: WifiBackend = create_wifi_backend()
profile_store: VolumeProfileStore = VolumeProfileStore()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Advertise ``musync.local`` for UI LAN discovery (not Spotify OAuth)."""
    from .streaming.spotify import api_listen_port

    port = api_listen_port()
    start_hub_mdns(port=port)
    try:
        yield
    finally:
        stop_hub_mdns()


app = FastAPI(
    title="MuSync",
    version=__version__,
    description="Multi-device Bluetooth audio sync hub — song-level source switching",
    lifespan=lifespan,
)

# Vite (:5173) / preview (:4173) and alternate mock ports (:8081) call the API
# cross-origin; OAuth itself is a top-level redirect, but status/search need CORS.
# musync.local is UI-only (Spotify forbids .local / localhost redirects).
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
        "http://musync.local:8080",
        "http://musync.local:8081",
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
    """Auto-discover bluez + Cast (Nest Mini) + always-available SPP defaults.

    UI sets sink type, SPP host/port, and Cast enable — no manual MAC entry.
    """
    return backend.discover_device_setup()


@app.put("/api/devices/config", response_model=list[Device])
def api_put_devices_config(body: DevicesConfigPut) -> list[Device]:
    """Save enabled sinks from the web UI (MAC/Cast UUID filled from discovery)."""
    if not body.devices:
        raise ValueError(
            "At least one sink is required (enable Phone SPP, Cast, or a BT device)"
        )
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


@app.get("/api/avrcp", response_model=AvrcpStatus)
def api_avrcp_status() -> AvrcpStatus:
    """BT Absolute Volume + headset transport relay status (soft-fail when unsupported)."""
    try:
        return backend.get_avrcp_status()
    except Exception as exc:  # noqa: BLE001
        return AvrcpStatus(
            supported=False,
            watching=False,
            detail=f"AVRCP soft-fail: {exc}",
            last_error=str(exc),
        )


@app.post("/api/avrcp/command", response_model=Transport)
def api_avrcp_command(body: AvrcpCommandPut) -> Transport:
    """Relay play/pause/skip from a headset (or test client) to the active source."""
    try:
        return backend.relay_avrcp_command(body.action)
    except NotImplementedError as exc:
        raise HTTPException(
            status_code=501,
            detail=f"AVRCP transport relay unsupported: {exc}",
        ) from exc
    except ValueError as exc:
        # Soft-fail unavailable play / bad action — keep hub up; surface reason.
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/avrcp/volume", response_model=Device)
def api_avrcp_volume(body: AvrcpVolumePut) -> Device:
    """Simulate Absolute Volume → MuSync slider (mock/tests; Pi uses BlueZ poll)."""
    try:
        return backend.inject_avrcp_volume(body.device_id, body.volume)
    except NotImplementedError as exc:
        raise HTTPException(
            status_code=501,
            detail=f"AVRCP volume inject unsupported: {exc}",
        ) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


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


def _profile_assignments() -> dict[str, str]:
    """device_id → profile/user id for turn-taking eligibility."""
    try:
        state = profile_store.state()
        return {
            device_id: profile_id
            for device_id, profile_id in (state.assignments or {}).items()
            if profile_id
        }
    except Exception:  # noqa: BLE001
        return {}


@app.get("/api/queue/settings", response_model=QueueSettings)
def api_get_queue_settings() -> QueueSettings:
    """Loop (repeat-all) + turn-taking rotation status / order."""
    return backend.get_queue_settings(assignments=_profile_assignments())


@app.put("/api/queue/settings", response_model=QueueSettings)
def api_put_queue_settings(body: QueueSettingsPut) -> QueueSettings:
    """Toggle queue loop and/or turn-taking solo rotation."""
    return backend.set_queue_settings(body, assignments=_profile_assignments())


@app.put("/api/queue/active", response_model=Transport)
def api_set_active_queue_item(body: ActiveQueuePut) -> Transport:
    """Activate a queue item (feeder swap only)."""
    return backend.set_active_queue_item(body.id)


@app.patch("/api/queue/{item_id}", response_model=QueueItem)
def api_patch_queue_item(item_id: str, patch: QueueItemPatch) -> QueueItem:
    """Switch source_type on a song without tearing down the sink graph."""
    return backend.patch_queue_item(item_id, patch)


@app.delete("/api/queue/{item_id}", status_code=204)
def api_delete_queue_item(item_id: str) -> Response:
    backend.remove_queue_item(item_id)
    return Response(status_code=204)


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


def _wifi_ips_for_tunnel() -> list[str]:
    """Best-effort LAN IPs for the SSH tunnel helper (soft-empty on failure)."""
    try:
        return list(wifi_backend.status().ips or [])
    except Exception:  # noqa: BLE001
        return []


def _ssh_tunnel_info(host: str | None = None) -> SpotifySshTunnelInfo:
    """Build tunnel command / script links from Wi‑Fi LAN IP + MUSYNC_SSH_USER."""
    return SpotifySshTunnelInfo.model_validate(
        build_ssh_tunnel_info(_wifi_ips_for_tunnel(), host=host)
    )


def _https_tunnel_info() -> SpotifyHttpsTunnelInfo:
    """Cloudflare / HTTPS tunnel helper for phone Spotify Connect."""
    return SpotifyHttpsTunnelInfo.model_validate(build_https_tunnel_info())


def _streaming_status_with_airplay() -> StreamingStatus:
    """Spotify + AirPlay status; enrich AirPlay from live input hosts when possible."""
    from .audio.input_registry import host_is_live
    from .lan_url import hub_port, pick_lan_ip
    from .models import CapturePath

    discovered = None
    live = False
    host_name = None
    try:
        for host in backend.list_inputs():
            if host.capture_path != CapturePath.AIRPLAY:
                continue
            if host_is_live(host):
                discovered = host.endpoint
                live = True
                host_name = host.name
                break
            if host.endpoint and not discovered:
                discovered = host.endpoint
                host_name = host.name
    except Exception:  # noqa: BLE001
        pass
    status = get_streaming_service().status(
        airplay_discovered=discovered,
        airplay_live=live,
        airplay_host_name=host_name,
    )
    status.ssh_tunnel = _ssh_tunnel_info()
    status.https_tunnel = _https_tunnel_info()
    ips = _wifi_ips_for_tunnel()
    lan = pick_lan_ip(ips)
    status.lan_ip = lan or None
    status.lan_ui_url = lan_ui_url(ips, port=hub_port()) or None
    return status


@app.get("/api/streaming", response_model=StreamingStatus)
def api_streaming_status() -> StreamingStatus:
    """Spotify auth + AirPlay receive status (secrets never returned)."""
    return _streaming_status_with_airplay()


@app.get("/api/streaming/credentials", response_model=StreamingCredentialsStatus)
def api_streaming_credentials_get() -> StreamingCredentialsStatus:
    """Masked developer credentials (Client Secret / YouTube key never raw)."""
    return StreamingCredentialsStatus.model_validate(credentials_status())


@app.put("/api/streaming/credentials", response_model=StreamingCredentialsStatus)
def api_streaming_credentials_put(
    body: StreamingCredentialsPut,
) -> StreamingCredentialsStatus:
    """Persist Spotify / YouTube keys to ``~/musync.env`` (never commit/log).

    Soft-fails on empty body (no write). Applies to process env immediately
    so Connect / YouTube search work without a restart when possible.
    Rejects Spotify-illegal ``http://<LAN-IP>`` redirects (Dashboard Invalid URI).
    """
    from .streaming.service import reset_streaming_service

    try:
        result = save_streaming_credentials(
            spotify_client_id=body.spotify_client_id,
            spotify_client_secret=body.spotify_client_secret,
            spotify_redirect_uri=body.spotify_redirect_uri,
            youtube_api_key=body.youtube_api_key,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # Rebuild clients so configured()/auth_url pick up new env.
    reset_streaming_service()
    return StreamingCredentialsStatus.model_validate(result)


def _spotify_loopback_requested(request: Request) -> bool:
    """UI Connect defaults to loopback; ``?loopback=0`` uses env / request base."""
    raw = (request.query_params.get("loopback") or "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _spotify_redirect_uri(request: Request) -> str:
    """Resolve the OAuth redirect URI Spotify will call back to.

    - ``MUSYNC_SPOTIFY_REDIRECT_URI`` that is Spotify-legal always wins:
      ``https://…`` (phone / Cloudflare Tunnel) or ``http://127.0.0.1:…``.
    - ``?loopback=1`` (default Connect): otherwise ``http://127.0.0.1:<port>/…``.
    - ``?loopback=0``: env if set, else request Host rewritten (LAN http → loopback;
      Spotify rejects plain ``http://<LAN-IP>``).

    Never returns ``http://host:/path`` (empty port) or ``http://<LAN-IP>``.
    """
    import os

    from .streaming.spotify import (
        effective_oauth_redirect_uri,
        redirect_uri_for_request_base,
    )

    configured = (os.environ.get("MUSYNC_SPOTIFY_REDIRECT_URI") or "").strip()
    if configured:
        # HTTPS tunnel or loopback env — honor for Connect (phone + laptop).
        return effective_oauth_redirect_uri(
            prefer_loopback=_spotify_loopback_requested(request)
        )

    if _spotify_loopback_requested(request):
        return default_redirect_uri()

    return redirect_uri_for_request_base(str(request.base_url))


@app.get("/api/streaming/spotify/login")
def api_spotify_login(request: Request) -> RedirectResponse:
    """Link-style OAuth: redirect browser to Spotify authorize → callback → tokens.

    Uses ``MUSYNC_SPOTIFY_REDIRECT_URI`` when it is Spotify-legal (``https://…`` or
    ``http://127.0.0.1:…``). Otherwise loopback. Never uses ``http://<LAN-IP>`` —
    Spotify rejects that as Invalid redirect URI. Pass ``?loopback=0`` only to
    derive from request Host (still rewritten off forbidden / LAN http hosts).
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

    Defaults to loopback redirect (``?loopback=1``). Pass ``loopback=0`` for LAN Host.
    """
    svc = get_streaming_service().spotify
    redirect_uri = _spotify_redirect_uri(request)
    if not svc.configured():
        return {
            "ok": False,
            "error": (
                "Spotify app credentials missing — set MUSYNC_SPOTIFY_CLIENT_ID and "
                "MUSYNC_SPOTIFY_CLIENT_SECRET in ~/musync.env "
                "(Windows: %USERPROFILE%\\musync.env), restart, then Connect again"
            ),
            "redirect_uri": redirect_uri,
            "loopback": _spotify_loopback_requested(request),
        }
    try:
        return {
            "ok": True,
            "url": svc.auth_url(redirect_uri=redirect_uri),
            "redirect_uri": redirect_uri,
            "loopback": _spotify_loopback_requested(request),
        }
    except ValueError as exc:
        return {
            "ok": False,
            "error": str(exc),
            "redirect_uri": redirect_uri,
            "loopback": _spotify_loopback_requested(request),
        }


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
    return _streaming_status_with_airplay()


@app.get("/api/streaming/spotify/ssh-tunnel", response_model=SpotifySshTunnelInfo)
def api_spotify_ssh_tunnel(
    host: str | None = Query(
        None,
        description="Override Pi host/IP (default: first LAN IPv4 from Wi‑Fi)",
    ),
) -> SpotifySshTunnelInfo:
    """JSON helper: copyable ``ssh -L`` command + short Connect-from-laptop steps."""
    return _ssh_tunnel_info(host=host)


@app.get("/api/streaming/spotify/ssh-tunnel.sh")
def api_spotify_ssh_tunnel_sh(
    host: str | None = Query(
        None,
        description="Override Pi host/IP baked into the script",
    ),
) -> Response:
    """Downloadable bash script that opens the Spotify OAuth loopback tunnel."""
    info = _ssh_tunnel_info(host=host)
    body = bash_tunnel_script(host=info.host or "", port=info.port, user=info.user)
    return Response(
        content=body,
        media_type="text/x-shellscript",
        headers={
            "Content-Disposition": 'attachment; filename="musync-spotify-ssh-tunnel.sh"',
            "Cache-Control": "no-store",
        },
    )


@app.get("/api/streaming/spotify/ssh-tunnel.ps1")
def api_spotify_ssh_tunnel_ps1(
    host: str | None = Query(
        None,
        description="Override Pi host/IP baked into the script",
    ),
) -> Response:
    """Downloadable PowerShell script (Windows OpenSSH) for the same tunnel."""
    info = _ssh_tunnel_info(host=host)
    body = powershell_tunnel_script(host=info.host or "", port=info.port, user=info.user)
    return Response(
        content=body,
        media_type="text/plain",
        headers={
            "Content-Disposition": 'attachment; filename="musync-spotify-ssh-tunnel.ps1"',
            "Cache-Control": "no-store",
        },
    )


@app.get("/api/streaming/spotify/https-tunnel", response_model=SpotifyHttpsTunnelInfo)
def api_spotify_https_tunnel() -> SpotifyHttpsTunnelInfo:
    """JSON helper: cloudflared steps for phone Spotify Connect (HTTPS redirect)."""
    return _https_tunnel_info()


@app.get("/api/streaming/spotify/https-tunnel.sh")
def api_spotify_https_tunnel_sh() -> Response:
    """Downloadable bash script that starts a Cloudflare quick tunnel to the hub."""
    info = _https_tunnel_info()
    body = bash_https_tunnel_script(port=info.port)
    return Response(
        content=body,
        media_type="text/x-shellscript",
        headers={
            "Content-Disposition": 'attachment; filename="musync-spotify-https-tunnel.sh"',
            "Cache-Control": "no-store",
        },
    )


@app.get("/api/streaming/search", response_model=StreamingSearchResponse)
def api_streaming_search(
    request: Request,
    provider: str = Query(..., description="spotify or youtube"),
    q: str = Query(..., min_length=1),
    # Accept raw query string — FastAPI may pass duplicates as odd shapes; coerce below.
    limit: str | None = Query("10"),
) -> StreamingSearchResponse:
    """Search Spotify/YouTube. Always returns 400 with a real message — never bare 500."""
    from .streaming.spotify import _clamp_search_limit, _coerce_query_int

    p = (provider or "").strip().lower()
    svc = get_streaming_service()
    # Prefer first query value if duplicated (?limit=15&limit=10); never leave as list.
    raw_limit = request.query_params.get("limit", limit)
    # Spotify Dev Mode: /search max limit is 10 (15+ → misleading "Invalid limit").
    # YouTube can use a higher cap; spotify.search_tracks re-clamps via _clamp_search_limit.
    safe_limit = _coerce_query_int(raw_limit, default=10)
    safe_limit = max(1, min(safe_limit, 50))
    if p == "spotify":
        safe_limit = int(_clamp_search_limit(safe_limit))
    if p == "spotify" and not svc.spotify.configured():
        raise ValueError(
            "Spotify not configured — copy config/streaming.env.example to ~/musync.env, "
            "set MUSYNC_SPOTIFY_CLIENT_ID + MUSYNC_SPOTIFY_CLIENT_SECRET, "
            "then restart ./scripts/run_pi.sh (see docs/spotify-apple.md)"
        )
    if p == "spotify" and not svc.spotify.authenticated():
        raise ValueError("Connect Spotify first (Streaming tab), then search from Queue")
    if p in {"youtube", "yt", "ytb"} and not svc.youtube.configured():
        raise ValueError(
            "YouTube not configured — set MUSYNC_YOUTUBE_API_KEY in ~/musync.env "
            "(YouTube Data API v3), install yt-dlp + ffmpeg, then restart "
            "./scripts/run_pi.sh (see docs/youtube.md)"
        )
    try:
        return svc.search(provider, q, limit=safe_limit)
    except ValueError:
        raise
    except Exception as exc:  # noqa: BLE001 — surface provider failures in the UI
        if p == "spotify":
            label = "Spotify"
        elif p in {"youtube", "yt", "ytb"}:
            label = "YouTube"
        else:
            label = "Streaming"
        raise ValueError(f"{label} search failed: {exc}") from exc


@app.post("/api/streaming/queue", response_model=QueueItem, status_code=201)
def api_streaming_queue_add(body: StreamingQueueAdd) -> QueueItem:
    """Add a searched Spotify/YouTube track to the hub queue and optionally activate it."""
    if body.provider == "spotify":
        source_type = SourceType.SPOTIFY
    elif body.provider == "youtube":
        source_type = SourceType.YOUTUBE
    else:
        raise ValueError(
            "Only Spotify/YouTube search queue is supported — use AirPlay live input "
            "for Apple devices (docs/airplay-receive.md)"
        )
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


@app.get("/api/mdns", response_model=MdnsStatus)
def api_mdns_status() -> MdnsStatus:
    """Hub UI mDNS (``musync.local``) — discovery only; not for Spotify redirects."""
    return MdnsStatus.model_validate(get_mdns_status().as_dict())


def _wifi_status_enriched() -> WifiStatus:
    """Wi‑Fi status plus ``lan_url`` / ``port`` / mDNS hostname hint."""
    status = wifi_backend.status()
    mdns = get_mdns_status()
    if mdns.hostname:
        status.mdns_hostname = mdns.hostname
    return enrich_wifi_status(status)


@app.get("/api/wifi", response_model=WifiStatus)
def api_wifi_status() -> WifiStatus:
    """Current Wi-Fi association and IPv4 addresses (for SPP / LAN access)."""
    return _wifi_status_enriched()


@app.get("/api/wifi/lan-qr.svg")
def api_wifi_lan_qr() -> Response:
    """SVG QR for ``http://PI_IP:PORT`` — same payload as the Wi‑Fi URL card / HDMI banner."""
    status = _wifi_status_enriched()
    url = status.lan_url or lan_ui_url(status.ips)
    if not url:
        raise HTTPException(
            status_code=404,
            detail="No LAN IP yet — join Wi‑Fi first, then reload the QR",
        )
    try:
        markup = svg_qr(url)
    except ImportError as exc:
        raise HTTPException(
            status_code=503,
            detail="QR support missing — pip install qrcode",
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(
        content=markup,
        media_type="image/svg+xml",
        headers={"Cache-Control": "no-store"},
    )


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
    status = wifi_backend.connect(body.ssid, body.password)
    mdns = get_mdns_status()
    if mdns.hostname:
        status.mdns_hostname = mdns.hostname
    return enrich_wifi_status(status)


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
