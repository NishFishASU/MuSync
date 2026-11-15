"""Shared API / domain models for MuSync."""

from __future__ import annotations

from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator


class TransportState(str, Enum):
    STOPPED = "stopped"
    PLAYING = "playing"
    PAUSED = "paused"


class Device(BaseModel):
    """One Bluetooth A2DP sink (one BT600 adapter → one speaker)."""

    id: str
    name: str
    mac: str = ""
    hci: str = ""
    connected: bool = False
    delay_ms: int = Field(default=0, ge=0, le=2000)
    volume: float = Field(default=0.8, ge=0.0, le=1.0)
    mute: bool = False
    enabled: bool = True
    # Optional WirePlumber/PipeWire node id or node.name override after connect.
    pw_node: str = ""

    @field_validator("volume")
    @classmethod
    def round_volume(cls, v: float) -> float:
        return round(float(v), 3)


class DevicePatch(BaseModel):
    delay_ms: Optional[int] = Field(default=None, ge=0, le=2000)
    volume: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    mute: Optional[bool] = None
    enabled: Optional[bool] = None
    name: Optional[str] = None
    pw_node: Optional[str] = None
    connected: Optional[bool] = None

    @field_validator("volume")
    @classmethod
    def round_volume(cls, v: Optional[float]) -> Optional[float]:
        if v is None:
            return None
        return round(float(v), 3)


class SourceType(str, Enum):
    """Song-level feeder kinds. Streaming/input are stubs until later phases."""

    LOCAL = "local"
    SPOTIFY = "spotify"
    APPLE = "apple"
    INPUT = "input"


# Source types that can actually feed PCM today.
PLAYABLE_SOURCE_TYPES = frozenset({SourceType.LOCAL})


class Source(BaseModel):
    """Catalog entry: discovered local file or streaming/input stub."""

    id: str
    title: str
    type: SourceType = SourceType.LOCAL
    path: Optional[str] = None
    locator: Optional[str] = None
    available: bool = True
    note: Optional[str] = None
    duration_ms: Optional[int] = None


class QueueItem(BaseModel):
    """One song/cue in the play queue — source type can switch per item."""

    id: str
    title: str
    source_type: SourceType = SourceType.LOCAL
    locator: Optional[str] = None
    source_id: Optional[str] = None
    path: Optional[str] = None
    available: bool = True
    note: Optional[str] = None
    duration_ms: Optional[int] = None


class QueueItemCreate(BaseModel):
    """Add a queue item from a catalog source and/or explicit type + locator."""

    title: Optional[str] = None
    source_type: SourceType = SourceType.LOCAL
    locator: Optional[str] = None
    source_id: Optional[str] = None
    path: Optional[str] = None
    duration_ms: Optional[int] = None


class QueueItemPatch(BaseModel):
    """Switch source type / locator on a song without rebuilding sinks."""

    title: Optional[str] = None
    source_type: Optional[SourceType] = None
    locator: Optional[str] = None
    source_id: Optional[str] = None
    path: Optional[str] = None


class Transport(BaseModel):
    state: TransportState = TransportState.STOPPED
    position_ms: int = Field(default=0, ge=0)
    duration_ms: int = Field(default=0, ge=0)
    source_id: Optional[str] = None
    queue_item_id: Optional[str] = None
    source_type: Optional[SourceType] = None
    title: Optional[str] = None


class TransportPut(BaseModel):
    """Client can set state and/or scrub position; source_id optional."""

    state: Optional[TransportState] = None
    position_ms: Optional[int] = Field(default=None, ge=0)
    source_id: Optional[str] = None
    queue_item_id: Optional[str] = None


class ActiveSourcePut(BaseModel):
    id: str


class ActiveQueuePut(BaseModel):
    id: str


class Health(BaseModel):
    ok: bool
    backend: Literal["mock", "pipewire"]
    pipewire_up: bool
    sink_count: int
    enabled_count: int
    hci_map: dict[str, str] = Field(default_factory=dict)
    detail: str = ""
    mapped_sinks: int = 0
    graph_dry_run: bool = True
