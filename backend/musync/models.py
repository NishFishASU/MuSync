"""Shared API / domain models for MuSync."""

from __future__ import annotations

from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator


class TransportState(str, Enum):
    STOPPED = "stopped"
    PLAYING = "playing"
    PAUSED = "paused"


class DeviceKind(str, Enum):
    """Output sink kinds. Bluetooth A2DP (≤6) or SPP TCP network listen."""

    BLUETOOTH = "bluetooth"
    NETWORK_SPP = "network_spp"


MAX_BT_SINKS = 6
MAX_SPP_SINKS = 4


class Device(BaseModel):
    """One output sink: BT A2DP speaker or SPP TCP network listen."""

    id: str
    name: str
    kind: DeviceKind = DeviceKind.BLUETOOTH
    mac: str = ""
    hci: str = ""
    connected: bool = False
    delay_ms: int = Field(default=0, ge=0, le=2000)
    volume: float = Field(default=0.8, ge=0.0, le=1.0)
    mute: bool = False
    enabled: bool = True
    # Optional WirePlumber/PipeWire node id or node.name override after connect.
    pw_node: str = ""
    # Simple Protocol Player TCP listen (kind=network_spp).
    listen_host: str = "0.0.0.0"
    listen_port: int = Field(default=1234, ge=1, le=65535)
    sample_rate: int = Field(default=48000, ge=8000, le=192000)
    channels: int = Field(default=1, ge=1, le=2)

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
    kind: Optional[DeviceKind] = None
    # MAC/hci are filled by discovery — UI should not require typing them.
    mac: Optional[str] = None
    hci: Optional[str] = None
    listen_host: Optional[str] = None
    listen_port: Optional[int] = Field(default=None, ge=1, le=65535)
    sample_rate: Optional[int] = Field(default=None, ge=8000, le=192000)
    channels: Optional[int] = Field(default=None, ge=1, le=2)

    @field_validator("volume")
    @classmethod
    def round_volume(cls, v: Optional[float]) -> Optional[float]:
        if v is None:
            return None
        return round(float(v), 3)


class DiscoveredBluetoothSink(BaseModel):
    """A bluez / PipeWire sink found on the hub (matched by friendly name)."""

    name: str
    mac: str = ""
    pw_node: str = ""
    pw_name: str = ""
    hci: str = ""
    paired: bool = False
    connected: bool = False
    configured_id: Optional[str] = None


class DeviceDiscoverResponse(BaseModel):
    """Discovery payload for the device-setup UI (no manual MAC entry)."""

    bluetooth: list[DiscoveredBluetoothSink] = Field(default_factory=list)
    spp_default: Device
    config_path: str = ""
    config_ok: bool = True
    config_detail: str = ""
    detail: str = ""


class DeviceConfigEntry(BaseModel):
    """One sink the UI wants to keep — type + SPP details; MAC filled server-side."""

    id: Optional[str] = None
    name: str
    kind: DeviceKind = DeviceKind.BLUETOOTH
    enabled: bool = True
    # Optional: discovery hint (server fills mac/pw_node from name when omitted).
    mac: Optional[str] = None
    hci: Optional[str] = None
    pw_node: Optional[str] = None
    delay_ms: Optional[int] = Field(default=None, ge=0, le=2000)
    volume: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    mute: Optional[bool] = None
    listen_host: Optional[str] = None
    listen_port: Optional[int] = Field(default=None, ge=1, le=65535)
    sample_rate: Optional[int] = Field(default=None, ge=8000, le=192000)
    channels: Optional[int] = Field(default=None, ge=1, le=2)


class DevicesConfigPut(BaseModel):
    """Replace configured sinks from the web UI (discovered BT + SPP)."""

    devices: list[DeviceConfigEntry]


class SourceType(str, Enum):
    """Song-level feeder kinds. Streaming/input are stubs until later phases."""

    LOCAL = "local"
    SPOTIFY = "spotify"
    APPLE = "apple"
    INPUT = "input"


class CapturePath(str, Enum):
    """How a phone/PC feeds the Pi."""

    BT_A2DP = "bt_a2dp"  # device as A2DP source → bluez_input → feeder
    USB = "usb"  # USB audio interface / UAC from PC → feeder
    NETWORK = "network"  # uploaded file / network path played into feeder


class HostKind(str, Enum):
    PHONE = "phone"
    PC = "pc"
    OTHER = "other"


MAX_INPUT_HOSTS = 4

# Source types that can feed PCM when their prerequisites are met.
# INPUT is playable when the bound host is live (connected capture endpoint).
# SPOTIFY / APPLE become playable when streaming credentials (+ player) are ready.
PLAYABLE_SOURCE_TYPES = frozenset(
    {SourceType.LOCAL, SourceType.INPUT, SourceType.SPOTIFY, SourceType.APPLE}
)


class Source(BaseModel):
    """Catalog entry: hub local file, or a per-host contribution stub."""

    id: str
    title: str
    type: SourceType = SourceType.LOCAL
    path: Optional[str] = None
    locator: Optional[str] = None
    available: bool = True
    note: Optional[str] = None
    duration_ms: Optional[int] = None
    # Which registered phone/PC this catalog row belongs to (None = hub library).
    input_id: Optional[str] = None


class InputHost(BaseModel):
    """A phone or PC that can feed the hub (≤4)."""

    id: str
    name: str
    kind: HostKind = HostKind.PHONE
    capture_path: CapturePath = CapturePath.NETWORK
    armed: bool = False
    connected: bool = False
    endpoint: Optional[str] = None  # PipeWire source name/id or upload file path
    # What this host can contribute into the queue (stubs OK).
    offers_local: bool = True
    offers_spotify: bool = True
    offers_apple: bool = True
    offers_live_input: bool = True
    note: Optional[str] = None


class InputHostCreate(BaseModel):
    name: str
    kind: HostKind = HostKind.PHONE
    capture_path: CapturePath = CapturePath.NETWORK
    armed: bool = False
    connected: bool = False
    endpoint: Optional[str] = None
    offers_local: bool = True
    offers_spotify: bool = True
    offers_apple: bool = True
    offers_live_input: bool = True
    note: Optional[str] = None


class InputHostPatch(BaseModel):
    name: Optional[str] = None
    kind: Optional[HostKind] = None
    capture_path: Optional[CapturePath] = None
    armed: Optional[bool] = None
    connected: Optional[bool] = None
    endpoint: Optional[str] = None
    offers_local: Optional[bool] = None
    offers_spotify: Optional[bool] = None
    offers_apple: Optional[bool] = None
    offers_live_input: Optional[bool] = None
    note: Optional[str] = None


class DiscoveredCaptureSource(BaseModel):
    """A PipeWire Audio/Source usable as phone/PC live input (e.g. bluez_input)."""

    name: str
    pw_node: str = ""
    pw_name: str = ""
    mac: str = ""
    capture_path: CapturePath = CapturePath.BT_A2DP
    connected: bool = True
    configured_id: Optional[str] = None


class CaptureDiscoverResponse(BaseModel):
    """Discovery payload for binding a host to a live PipeWire source."""

    sources: list[DiscoveredCaptureSource] = Field(default_factory=list)
    detail: str = ""


class QueueItem(BaseModel):
    """One song/cue — source type + optional phone/PC host binding."""

    id: str
    title: str
    source_type: SourceType = SourceType.LOCAL
    locator: Optional[str] = None
    source_id: Optional[str] = None
    path: Optional[str] = None
    # When set: this cue is contributed by / captured from that host.
    input_id: Optional[str] = None
    available: bool = True
    note: Optional[str] = None
    duration_ms: Optional[int] = None


class QueueItemCreate(BaseModel):
    """Add a queue item from a catalog source and/or explicit type + host."""

    title: Optional[str] = None
    source_type: SourceType = SourceType.LOCAL
    locator: Optional[str] = None
    source_id: Optional[str] = None
    path: Optional[str] = None
    input_id: Optional[str] = None
    duration_ms: Optional[int] = None


class QueueItemPatch(BaseModel):
    """Switch source type / host / locator on a song without rebuilding sinks."""

    title: Optional[str] = None
    source_type: Optional[SourceType] = None
    locator: Optional[str] = None
    source_id: Optional[str] = None
    path: Optional[str] = None
    input_id: Optional[str] = None


class Transport(BaseModel):
    state: TransportState = TransportState.STOPPED
    position_ms: int = Field(default=0, ge=0)
    duration_ms: int = Field(default=0, ge=0)
    source_id: Optional[str] = None
    queue_item_id: Optional[str] = None
    source_type: Optional[SourceType] = None
    input_id: Optional[str] = None
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
    spotify_ready: bool = False
    apple_ready: bool = False


class StreamingProviderStatus(BaseModel):
    provider: Literal["spotify", "apple"]
    configured: bool = False
    authenticated: bool = False
    display_name: Optional[str] = None
    librespot_available: Optional[bool] = None
    librespot_running: Optional[bool] = None
    librespot_name: Optional[str] = None
    feeder: Optional[str] = None
    redirect_uri: Optional[str] = None
    premium_required: Optional[bool] = None
    user_token_set: Optional[bool] = None
    airplay_source: Optional[str] = None
    playback_mode: Optional[str] = None
    limits: list[str] = Field(default_factory=list)
    note: str = ""


class StreamingStatus(BaseModel):
    spotify: StreamingProviderStatus
    apple: StreamingProviderStatus
    mock_playback: bool = False


class StreamingSearchHit(BaseModel):
    provider: Literal["spotify", "apple"]
    id: str
    uri: str
    title: str
    artist: str
    duration_ms: Optional[int] = None
    album: Optional[str] = None


class StreamingSearchResponse(BaseModel):
    provider: Literal["spotify", "apple"]
    query: str
    hits: list[StreamingSearchHit] = Field(default_factory=list)


class StreamingQueueAdd(BaseModel):
    """Add a searched streaming track to the song queue (hub, no host binding)."""

    provider: Literal["spotify", "apple"]
    uri: str
    title: Optional[str] = None
    artist: Optional[str] = None
    duration_ms: Optional[int] = None
    activate: bool = True


class WifiNetwork(BaseModel):
    """Saved Wi-Fi profile metadata (never includes the password)."""

    ssid: str
    saved: bool = True
    active: bool = False
    has_password: bool = False


class WifiStatus(BaseModel):
    """Current association + IPv4 addresses for SPP / LAN UI."""

    backend: Literal["mock", "nmcli"]
    connected: bool = False
    ssid: Optional[str] = None
    ips: list[str] = Field(default_factory=list)
    interface: Optional[str] = None
    detail: str = ""


class WifiSaveRequest(BaseModel):
    ssid: str = Field(..., min_length=1, max_length=32)
    password: Optional[str] = Field(default=None, max_length=63)

    @field_validator("ssid")
    @classmethod
    def strip_ssid(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("SSID must be non-empty")
        return cleaned


class WifiConnectRequest(BaseModel):
    ssid: str = Field(..., min_length=1, max_length=32)
    password: Optional[str] = Field(default=None, max_length=63)

    @field_validator("ssid")
    @classmethod
    def strip_ssid(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("SSID must be non-empty")
        return cleaned
