"""FastAPI application — REST surface + static web UI."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .audio import AudioBackend, create_backend
from .config import web_root
from .models import (
    ActiveSourcePut,
    Device,
    DevicePatch,
    Health,
    Source,
    Transport,
    TransportPut,
)

backend: AudioBackend = create_backend()

app = FastAPI(
    title="MuSync",
    version=__version__,
    description="Multi-device Bluetooth audio sync hub (Phase 0 MVP)",
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
    return backend.set_active_source(body.id)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(web_root() / "index.html")


# Static assets (CSS/JS) after API routes so /api/* stays exclusive.
_web = web_root()
if _web.is_dir():
    app.mount("/", StaticFiles(directory=str(_web), html=True), name="web")


def create_app(audio_backend: AudioBackend | None = None) -> FastAPI:
    """Test helper: swap the module-level backend used by route handlers."""
    global backend
    if audio_backend is not None:
        backend = audio_backend
    return app
