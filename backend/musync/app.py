"""FastAPI application — REST surface + static web UI."""

from __future__ import annotations

from pathlib import Path

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
    QueueItem,
    QueueItemCreate,
    QueueItemPatch,
    Source,
    Transport,
    TransportPut,
)

backend: AudioBackend = create_backend()

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


def create_app(audio_backend: AudioBackend | None = None) -> FastAPI:
    """Test helper: swap the module-level backend used by route handlers."""
    global backend
    if audio_backend is not None:
        backend = audio_backend
    return app
