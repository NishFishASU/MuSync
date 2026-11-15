"""FastAPI application — REST surface + static web UI."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .audio import AudioBackend, create_backend
from .config import web_root
from .models import (
    ActiveQueuePut,
    ActiveSourcePut,
    Device,
    DevicePatch,
    Health,
    InputHost,
    InputHostCreate,
    InputHostPatch,
    QueueItem,
    QueueItemCreate,
    QueueItemPatch,
    Source,
    Transport,
    TransportPut,
    WifiConnectRequest,
    WifiNetwork,
    WifiSaveRequest,
    WifiStatus,
)
from .wifi import WifiBackend, create_wifi_backend

backend: AudioBackend = create_backend()
wifi_backend: WifiBackend = create_wifi_backend()

app = FastAPI(
    title="MuSync",
    version=__version__,
    description="Multi-device Bluetooth audio sync hub — song-level source switching",
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


@app.get("/api/devices/{device_id}", response_model=Device)
def api_get_device(device_id: str) -> Device:
    return backend.get_device(device_id)


@app.patch("/api/devices/{device_id}", response_model=Device)
def api_patch_device(device_id: str, patch: DevicePatch) -> Device:
    return backend.patch_device(device_id, patch)


@app.get("/api/transport", response_model=Transport)
def api_get_transport() -> Transport:
    return backend.get_transport()


@app.put("/api/transport", response_model=Transport)
def api_put_transport(body: TransportPut) -> Transport:
    return backend.set_transport(body)


@app.get("/api/sources", response_model=list[Source])
def api_list_sources() -> list[Source]:
    return backend.list_sources()


@app.put("/api/sources/active", response_model=Transport)
def api_set_active_source(body: ActiveSourcePut) -> Transport:
    """Pick a catalog source; creates/reuses a queue item. Sinks stay up."""
    return backend.set_active_source(body.id)


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
    """Registered phone/PC hosts (≤4). Capture paths are stubs."""
    return backend.list_inputs()


@app.post("/api/inputs", response_model=InputHost, status_code=201)
def api_add_input(body: InputHostCreate) -> InputHost:
    return backend.add_input(body)


@app.patch("/api/inputs/{host_id}", response_model=InputHost)
def api_patch_input(host_id: str, patch: InputHostPatch) -> InputHost:
    return backend.patch_input(host_id, patch)


@app.delete("/api/inputs/{host_id}", status_code=204)
def api_delete_input(host_id: str) -> Response:
    backend.remove_input(host_id)
    return Response(status_code=204)


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


@app.get("/")
def index() -> FileResponse:
    path = _index_html()
    if not path.is_file():
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
) -> FastAPI:
    """Test helper: swap the module-level backends used by route handlers."""
    global backend, wifi_backend
    if audio_backend is not None:
        backend = audio_backend
    if wifi is not None:
        wifi_backend = wifi
    return app
