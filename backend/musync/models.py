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
    # Headphone/IEM sensitivity calibration (silent-disco). Applied volume =
    # clamp(preferred_level × gain). Delay stays independent of profiles.
    gain: float = Field(default=1.0, ge=0.1, le=4.0)
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

    @field_validator("gain")
    @classmethod
    def round_gain(cls, v: float) -> float:
        return round(float(v), 3)


class DevicePatch(BaseModel):
    delay_ms: Optional[int] = Field(default=None, ge=0, le=2000)
    volume: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    gain: Optional[float] = Field(default=None, ge=0.1, le=4.0)
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

    @field_validator("gain")
    @classmethod
    def round_gain(cls, v: Optional[float]) -> Optional[float]:
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


class BluetoothAdapter(BaseModel):
    """One BlueZ HCI controller — onboard Pi Bluetooth or a USB dongle."""

    hci: str
    mac: str = ""
    name: str = ""
    powered: bool = False
    healthy: bool = True
    onboard: bool = False
    # "usb" | "uart" | "" — bus class from sysfs (generic; not chip-specific).
    bus: str = ""
    # USB idVendor:idProduct when bus=usb (e.g. 0b05:1d70); empty otherwise.
    usb_id: str = ""
    detail: str = ""


class DeviceDiscoverResponse(BaseModel):
    """Discovery payload for the device-setup UI (no manual MAC entry)."""

    bluetooth: list[DiscoveredBluetoothSink] = Field(default_factory=list)
    spp_default: Device
    adapters: list[BluetoothAdapter] = Field(default_factory=list)
    adapter_capacity: int = 0
    soft_warn: str = ""
    config_path: str = ""
    config_ok: bool = True
    config_detail: str = ""
    detail: str = ""


class BluetoothScanStatus(BaseModel):
    """Live BlueZ scan session + accumulated discovery rows for the web UI."""

    scanning: bool = False
    bluetooth: list[DiscoveredBluetoothSink] = Field(default_factory=list)
    adapters: list[BluetoothAdapter] = Field(default_factory=list)
    adapter_capacity: int = 0
    soft_warn: str = ""
    detail: str = ""


class BluetoothConnectRequest(BaseModel):
    """Connect (and optionally trust/pair) a discovered Bluetooth remote by MAC."""

    mac: str = Field(..., min_length=11, max_length=32)
    name: Optional[str] = None
    # Prefer this HCI when free/healthy (multi-adapter). Empty = auto-pick.
    hci: Optional[str] = None
    # When true, enable the device as an output sink after a successful connect.
    enable_as_output: bool = True

    @field_validator("mac")
    @classmethod
    def normalize_request_mac(cls, v: str) -> str:
        cleaned = v.strip().upper().replace("-", ":")
        if cleaned.count(":") != 5:
            raise ValueError("MAC must look like AA:BB:CC:DD:EE:FF")
        return cleaned


class BluetoothConnectResponse(BaseModel):
    ok: bool
    mac: str
    detail: str = ""
    device: Optional[Device] = None
    bluetooth: list[DiscoveredBluetoothSink] = Field(default_factory=list)
    adapters: list[BluetoothAdapter] = Field(default_factory=list)
    soft_warn: str = ""


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
    gain: Optional[float] = Field(default=None, ge=0.1, le=4.0)
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


class MediaKind(str, Enum):
    """Hub-local file kind — video audio still fans out via musync_feeder."""

    AUDIO = "audio"
    VIDEO = "video"


class CapturePath(str, Enum):
    """How a phone/PC / HDMI capture feeds the Pi."""

    BT_A2DP = "bt_a2dp"  # device as A2DP source → bluez_input → feeder
    USB = "usb"  # USB audio interface / UAC from PC → feeder
    HDMI = "hdmi"  # HDMI / capture-card ALSA → PipeWire → feeder
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
    # audio | video for hub-local library rows (ignored for stream/input stubs).
    media_kind: MediaKind = MediaKind.AUDIO
    artist: Optional[str] = None
    album: Optional[str] = None
    cover_url: Optional[str] = None


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
    media_kind: MediaKind = MediaKind.AUDIO
    artist: Optional[str] = None
    album: Optional[str] = None
    cover_url: Optional[str] = None


class QueueItemCreate(BaseModel):
    """Add a queue item from a catalog source and/or explicit type + host."""

    title: Optional[str] = None
    source_type: SourceType = SourceType.LOCAL
    locator: Optional[str] = None
    source_id: Optional[str] = None
    path: Optional[str] = None
    input_id: Optional[str] = None
    duration_ms: Optional[int] = None
    media_kind: Optional[MediaKind] = None
    artist: Optional[str] = None
    album: Optional[str] = None
    cover_url: Optional[str] = None


class QueueItemPatch(BaseModel):
    """Switch source type / host / locator on a song without rebuilding sinks."""

    title: Optional[str] = None
    source_type: Optional[SourceType] = None
    locator: Optional[str] = None
    source_id: Optional[str] = None
    path: Optional[str] = None
    input_id: Optional[str] = None
    media_kind: Optional[MediaKind] = None
    artist: Optional[str] = None
    album: Optional[str] = None
    cover_url: Optional[str] = None


class Transport(BaseModel):
    state: TransportState = TransportState.STOPPED
    position_ms: int = Field(default=0, ge=0)
    duration_ms: int = Field(default=0, ge=0)
    source_id: Optional[str] = None
    queue_item_id: Optional[str] = None
    source_type: Optional[SourceType] = None
    input_id: Optional[str] = None
    title: Optional[str] = None
    artist: Optional[str] = None
    album: Optional[str] = None
    cover_url: Optional[str] = None
    media_kind: Optional[MediaKind] = None


class NowPlaying(BaseModel):
    """Unified now-playing metadata for local / streaming / live input."""

    title: Optional[str] = None
    artist: Optional[str] = None
    album: Optional[str] = None
    cover_url: Optional[str] = None
    has_cover: bool = False
    source_type: Optional[SourceType] = None
    input_id: Optional[str] = None
    queue_item_id: Optional[str] = None
    locator: Optional[str] = None
    state: TransportState = TransportState.STOPPED
    position_ms: int = 0
    duration_ms: int = 0
    note: Optional[str] = None


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
    adapters: list[BluetoothAdapter] = Field(default_factory=list)
    adapter_count: int = 0
    healthy_adapter_count: int = 0
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
    cover_url: Optional[str] = None


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
    album: Optional[str] = None
    cover_url: Optional[str] = None
    duration_ms: Optional[int] = None
    activate: bool = True


class VolumeProfile(BaseModel):
    """Named listening preference for silent-disco style headphone outputs.

    Explicit apply (#27 UI): sink volume = clamp(preferred_level × Device.gain).
    Optional ``output_overrides`` replace preferred_level for specific device
    ids (still × Device.gain). Delay is never touched.

    Learned latents (not UI controls):
    - ``sensitivity`` S_u — per-person loudness preference
    - device gains G_d live on ``VolumeProfilesState.device_gains``
    Suggested volume ≈ clamp(S_u × G_d); cross-infers across users/devices.
    """

    id: str
    name: str
    preferred_level: float = Field(default=0.8, ge=0.0, le=1.0)
    output_overrides: dict[str, float] = Field(default_factory=dict)
    # Learned S_u (1.0 = average). Updated by the hub from volume observations.
    sensitivity: float = Field(default=1.0, ge=0.05, le=20.0)

    @field_validator("preferred_level")
    @classmethod
    def round_preferred(cls, v: float) -> float:
        return round(float(v), 3)

    @field_validator("sensitivity")
    @classmethod
    def round_sensitivity(cls, v: float) -> float:
        return round(float(v), 3)

    @field_validator("output_overrides")
    @classmethod
    def round_overrides(cls, v: dict[str, float]) -> dict[str, float]:
        out: dict[str, float] = {}
        for key, raw in (v or {}).items():
            device_id = str(key).strip()
            if not device_id:
                continue
            level = round(max(0.0, min(1.0, float(raw))), 3)
            out[device_id] = level
        return out


class VolumeProfileCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    preferred_level: float = Field(default=0.8, ge=0.0, le=1.0)
    output_overrides: dict[str, float] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def strip_name(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("Profile name must be non-empty")
        return cleaned

    @field_validator("preferred_level")
    @classmethod
    def round_preferred(cls, v: float) -> float:
        return round(float(v), 3)

    @field_validator("output_overrides")
    @classmethod
    def round_overrides(cls, v: dict[str, float]) -> dict[str, float]:
        out: dict[str, float] = {}
        for key, raw in (v or {}).items():
            device_id = str(key).strip()
            if not device_id:
                continue
            out[device_id] = round(max(0.0, min(1.0, float(raw))), 3)
        return out


class VolumeProfilePatch(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=64)
    preferred_level: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    output_overrides: Optional[dict[str, float]] = None

    @field_validator("name")
    @classmethod
    def strip_name(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("Profile name must be non-empty")
        return cleaned

    @field_validator("preferred_level")
    @classmethod
    def round_preferred(cls, v: Optional[float]) -> Optional[float]:
        if v is None:
            return None
        return round(float(v), 3)

    @field_validator("output_overrides")
    @classmethod
    def round_overrides(
        cls, v: Optional[dict[str, float]]
    ) -> Optional[dict[str, float]]:
        if v is None:
            return None
        out: dict[str, float] = {}
        for key, raw in v.items():
            device_id = str(key).strip()
            if not device_id:
                continue
            out[device_id] = round(max(0.0, min(1.0, float(raw))), 3)
        return out


class VolumeProfilesState(BaseModel):
    """Hub-persisted user volume preference profiles + active selection.

    ``assignments`` maps output device id → profile (user) id currently on that
    headphone/IEM. ``volumes`` is the latest observed volume per user×device.
    ``device_gains`` is the learned per-device relative loudness G_d (latent;
    distinct from the explicit headphone-calibration ``Device.gain`` slider).
    """

    profiles: list[VolumeProfile] = Field(default_factory=list)
    active_id: Optional[str] = None
    assignments: dict[str, str] = Field(default_factory=dict)
    volumes: list["UserDeviceVolume"] = Field(default_factory=list)
    device_gains: dict[str, float] = Field(default_factory=dict)


class VolumeProfileActivePut(BaseModel):
    """Select a profile (null clears). Selecting applies volumes to outputs."""

    id: Optional[str] = None


class DeviceUserAssign(BaseModel):
    """Assign (or clear) which listening profile/user owns an output device."""

    device_id: str = Field(..., min_length=1, max_length=64)
    profile_id: Optional[str] = None
    # When true, apply a sensitivity-suggested volume if this user has no
    # observation for the device yet (or always re-suggest when forced).
    apply_suggested: bool = True

    @field_validator("device_id")
    @classmethod
    def strip_device(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("device_id must be non-empty")
        return cleaned


class DeviceUserAssignResult(BaseModel):
    """Result of assigning a user to an output (optional volume apply)."""

    state: VolumeProfilesState
    device: Optional[Device] = None
    suggested_volume: Optional[float] = None
    applied: bool = False
    detail: str = ""


class UserDeviceVolume(BaseModel):
    """One persisted (user, device) → volume observation."""

    user_id: str
    device_id: str
    volume: float = Field(ge=0.0, le=1.0)
    updated_at: str = ""

    @field_validator("volume")
    @classmethod
    def round_volume(cls, v: float) -> float:
        return round(float(v), 3)


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
