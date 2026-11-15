"""Shared source catalog + song queue — feeder swaps keep the sink graph alive."""

from __future__ import annotations

import itertools
import threading
import time
from typing import Callable, Optional

from ..config import DEMO_DURATION_MS, PLACEHOLDER_SOURCES, discover_local_sources
from ..models import (
    PLAYABLE_SOURCE_TYPES,
    MediaKind,
    QueueItem,
    QueueItemCreate,
    QueueItemPatch,
    Source,
    SourceType,
    Transport,
    TransportPut,
    TransportState,
)
from ..streaming import get_streaming_service
from .input_registry import (
    InputRegistry,
    host_is_live,
    host_live_note,
)

STUB_NOTES: dict[SourceType, str] = {
    SourceType.SPOTIFY: "Stub — connect Spotify (docs/spotify-apple.md); sink graph stays up",
    SourceType.APPLE: "Stub — Apple limited on Linux; configure MusicKit/AirPlay or use Spotify",
    SourceType.INPUT: "Waiting for live capture — bind a bluez_input / USB / upload",
}


def _hub_stream_ready(source_type: SourceType) -> bool:
    try:
        return get_streaming_service().is_hub_stream_playable(source_type)
    except Exception:  # noqa: BLE001
        return False


def is_playable(
    source_type: SourceType,
    *,
    path: Optional[str] = None,
    input_id: Optional[str] = None,
    inputs: Optional[InputRegistry] = None,
    locator: Optional[str] = None,
) -> bool:
    if source_type == SourceType.INPUT:
        if not input_id or inputs is None:
            return False
        try:
            return host_is_live(inputs.get_host(input_id))
        except KeyError:
            return False
    if source_type == SourceType.SPOTIFY:
        if input_id:
            return False  # host-bound Spotify remains a phone-session stub
        if not _hub_stream_ready(SourceType.SPOTIFY):
            return False
        loc = (locator or "").strip()
        # Catalog hub row alone is not a track — needs a real URI to play.
        return bool(loc.startswith("spotify:track:") or loc.startswith("spotify:episode:"))
    if source_type == SourceType.APPLE:
        if input_id:
            return False
        if not _hub_stream_ready(SourceType.APPLE):
            return False
        loc = (locator or "").strip()
        return bool(loc.startswith("apple:song:") or loc.startswith("apple:track:"))
    if source_type not in PLAYABLE_SOURCE_TYPES:
        return False
    # Local demo tracks without a file path are playable on the mock clock.
    return True


def stub_note(source_type: SourceType) -> Optional[str]:
    if source_type == SourceType.SPOTIFY:
        svc = get_streaming_service()
        if svc.spotify_ready():
            return "Pick a Spotify track in Streaming — then Play into musync_feeder"
        return STUB_NOTES[SourceType.SPOTIFY]
    if source_type == SourceType.APPLE:
        svc = get_streaming_service()
        if svc.apple_ready() and not svc.apple_playable():
            return svc.apple_play_note()
        if svc.apple_ready():
            return "Pick an Apple song; AirPlay capture feeds musync_feeder"
        return STUB_NOTES[SourceType.APPLE]
    return STUB_NOTES.get(source_type)


class SourceSession:
    """Catalog + queue + transport clock shared by Mock and PipeWire backends.

    Source / queue switches only change the active feeder identity. Callers
    (PipeWireBackend) stop/start the player process; they must not tear down
    the N-sink delay/volume graph.
    """

    def __init__(
        self,
        *,
        inputs: Optional[InputRegistry] = None,
        on_feeder_stop: Optional[Callable[[], None]] = None,
        on_feeder_start: Optional[Callable[[], None]] = None,
        lock: Optional[threading.RLock] = None,
    ) -> None:
        self._lock = lock or threading.RLock()
        self._inputs = inputs or InputRegistry(lock=self._lock)
        self._on_feeder_stop = on_feeder_stop
        self._on_feeder_start = on_feeder_start
        self._id_seq = itertools.count(1)
        self._sources = self._build_sources()
        self._queue: list[QueueItem] = []
        self._seed_queue_from_locals()

        active = self._first_playable_queue_item()
        self._transport = Transport(
            state=TransportState.STOPPED,
            position_ms=0,
            duration_ms=(active.duration_ms or 0) if active else 0,
            source_id=active.source_id if active else None,
            queue_item_id=active.id if active else None,
            source_type=active.source_type if active else None,
            input_id=active.input_id if active else None,
            title=active.title if active else None,
        )
        self._play_started_wall: Optional[float] = None
        self._play_started_pos: int = 0

    @property
    def inputs(self) -> InputRegistry:
        return self._inputs

    def _next_queue_id(self) -> str:
        return f"q-{next(self._id_seq)}"

    def _build_sources(self) -> list[Source]:
        local = discover_local_sources()
        if not local:
            local = [
                Source(
                    id="local:demo/sample-tone",
                    title="Sample Tone (demo)",
                    type=SourceType.LOCAL,
                    path=None,
                    locator="demo/sample-tone",
                    available=True,
                    duration_ms=DEMO_DURATION_MS,
                    note="Virtual demo track — drop files under media/ for real paths",
                )
            ]
        else:
            for src in local:
                if src.locator is None and src.path:
                    src.locator = src.path
        # Hub stream rows + per-host contributions (local/spotify/apple/live).
        return local + self._streaming_placeholders() + self._inputs.catalog_sources()

    def _streaming_placeholders(self) -> list[Source]:
        out: list[Source] = []
        for src in PLACEHOLDER_SOURCES:
            if src.type == SourceType.SPOTIFY:
                out.append(
                    src.model_copy(
                        update={
                            "available": False,
                            "note": (
                                "Connected — search & pick a track in Streaming"
                                if get_streaming_service().spotify_ready()
                                else stub_note(SourceType.SPOTIFY)
                            ),
                        }
                    )
                )
            elif src.type == SourceType.APPLE:
                out.append(
                    src.model_copy(
                        update={
                            "available": False,
                            "note": stub_note(SourceType.APPLE),
                        }
                    )
                )
            else:
                out.append(src)
        return out

    def _seed_queue_from_locals(self) -> None:
        """One queue item per available hub-local catalog entry."""
        for src in self._sources:
            # Only seed playable hub library rows (not per-host local stubs).
            if src.type != SourceType.LOCAL or not src.available or src.input_id:
                continue
            self._queue.append(self._item_from_source(src))

    def _item_from_source(self, src: Source) -> QueueItem:
        locator = src.locator or src.path or src.id
        if src.type == SourceType.INPUT and src.input_id:
            playable = is_playable(
                src.type,
                path=src.path,
                input_id=src.input_id,
                inputs=self._inputs,
                locator=locator,
            )
        elif src.type in {SourceType.SPOTIFY, SourceType.APPLE} and not src.input_id:
            playable = is_playable(src.type, locator=locator, path=src.path)
        else:
            playable = (
                is_playable(src.type, path=src.path, locator=locator)
                and src.available
                and not src.input_id
            )
        note = None if playable else (src.note or stub_note(src.type))
        return QueueItem(
            id=self._next_queue_id(),
            title=src.title,
            source_type=src.type,
            locator=locator,
            source_id=src.id,
            path=src.path,
            input_id=src.input_id,
            available=playable,
            note=note,
            duration_ms=src.duration_ms,
            media_kind=src.media_kind if src.type == SourceType.LOCAL else MediaKind.AUDIO,
        )

    def _require_input(self, input_id: Optional[str]) -> None:
        if input_id:
            self._inputs.get_host(input_id)

    def _note_for_host_item(
        self, source_type: SourceType, input_id: Optional[str]
    ) -> Optional[str]:
        if not input_id:
            return stub_note(source_type)
        host = self._inputs.get_host(input_id)
        path_note = host_live_note(host)
        if source_type == SourceType.INPUT:
            if host_is_live(host):
                return None
            return f"{host.name} via {host.capture_path.value} — {path_note}"
        if source_type == SourceType.SPOTIFY:
            return f"{host.name} Spotify session — {path_note}"
        if source_type == SourceType.APPLE:
            return f"{host.name} Apple Music session — {path_note}"
        if source_type == SourceType.LOCAL:
            return f"{host.name} local files — upload stub; {path_note}"
        return path_note

    def _live_input_playable(self, input_id: Optional[str]) -> bool:
        return is_playable(
            SourceType.INPUT, input_id=input_id, inputs=self._inputs
        )

    def _first_playable_queue_item(self) -> Optional[QueueItem]:
        for item in self._queue:
            if item.available and item.source_type in PLAYABLE_SOURCE_TYPES:
                return item
        return self._queue[0] if self._queue else None

    def _source_by_id(self, source_id: str) -> Source:
        for src in self._sources:
            if src.id == source_id:
                return src
        raise KeyError(f"Unknown source: {source_id}")

    def _queue_by_id(self, item_id: str) -> QueueItem:
        for item in self._queue:
            if item.id == item_id:
                return item
        raise KeyError(f"Unknown queue item: {item_id}")

    def _refresh_catalog(self) -> None:
        local = discover_local_sources()
        if not local:
            local = [
                s
                for s in self._sources
                if s.type == SourceType.LOCAL and not s.input_id
            ]
            if not local:
                local = [
                    Source(
                        id="local:demo/sample-tone",
                        title="Sample Tone (demo)",
                        type=SourceType.LOCAL,
                        path=None,
                        locator="demo/sample-tone",
                        available=True,
                        duration_ms=DEMO_DURATION_MS,
                        note="Virtual demo track — drop files under media/ for real paths",
                    )
                ]
        else:
            for src in local:
                if src.locator is None and src.path:
                    src.locator = src.path
        self._sources = local + self._streaming_placeholders() + self._inputs.catalog_sources()

    def _tick_position(self) -> None:
        if self._transport.state != TransportState.PLAYING:
            return
        if self._play_started_wall is None:
            return
        elapsed = int((time.monotonic() - self._play_started_wall) * 1000)
        pos = self._play_started_pos + elapsed
        duration = self._transport.duration_ms or 0
        if duration and pos >= duration:
            self._transport.position_ms = duration
            self._transport.state = TransportState.STOPPED
            self._play_started_wall = None
            self._play_started_pos = 0
            if self._on_feeder_stop:
                self._on_feeder_stop()
        else:
            self._transport.position_ms = pos

    def _bind_transport_to_item(self, item: QueueItem, *, reset_position: bool) -> None:
        self._transport.queue_item_id = item.id
        self._transport.source_id = item.source_id
        self._transport.source_type = item.source_type
        self._transport.input_id = item.input_id
        self._transport.title = item.title
        self._transport.duration_ms = item.duration_ms or 0
        self._transport.media_kind = (
            item.media_kind
            if item.source_type == SourceType.LOCAL and not item.input_id
            else MediaKind.AUDIO
        )
        if reset_position:
            self._transport.position_ms = 0
            self._play_started_wall = None
            self._play_started_pos = 0

    def _apply_state(self, state: TransportState) -> None:
        current = self._transport.state
        if state == TransportState.PLAYING:
            item_id = self._transport.queue_item_id
            if item_id:
                item = self._queue_by_id(item_id)
                if not item.available:
                    raise ValueError(
                        f"Cannot play stub source ({item.source_type.value}): {item.note or item.id}"
                    )
            if current == TransportState.PLAYING:
                return
            self._play_started_wall = time.monotonic()
            self._play_started_pos = self._transport.position_ms
            self._transport.state = TransportState.PLAYING
            if self._on_feeder_start:
                self._on_feeder_start()
        elif state == TransportState.PAUSED:
            self._tick_position()
            self._play_started_wall = None
            self._transport.state = TransportState.PAUSED
            if self._on_feeder_stop:
                self._on_feeder_stop()
        elif state == TransportState.STOPPED:
            self._play_started_wall = None
            self._play_started_pos = 0
            self._transport.position_ms = 0
            self._transport.state = TransportState.STOPPED
            if self._on_feeder_stop:
                self._on_feeder_stop()

    def _resolve_create(self, body: QueueItemCreate) -> QueueItem:
        src: Optional[Source] = None
        if body.source_id:
            src = self._source_by_id(body.source_id)

        # Explicit source_type / locator / path win; otherwise inherit catalog.
        source_type = body.source_type
        title = body.title
        locator = body.locator
        path = body.path
        source_id = body.source_id
        input_id = body.input_id
        duration_ms = body.duration_ms
        media_kind = body.media_kind
        type_explicit = "source_type" in body.model_fields_set

        if src is not None:
            if not type_explicit:
                source_type = src.type
            if title is None:
                title = src.title
            if locator is None:
                locator = src.locator or src.path or src.id
            if path is None:
                path = src.path
            if source_id is None:
                source_id = src.id
            if duration_ms is None:
                duration_ms = src.duration_ms
            if input_id is None:
                input_id = src.input_id
            if media_kind is None:
                media_kind = src.media_kind

        # Live input / host-bound streaming must name a registered phone/PC.
        if source_type == SourceType.INPUT and not input_id:
            hosts = self._inputs.list_hosts()
            if not hosts:
                raise ValueError("Register a phone/PC input host before selecting live input")
            input_id = next((h.id for h in hosts if h.armed), hosts[0].id)

        self._require_input(input_id)

        if title is None:
            title = {
                SourceType.LOCAL: "Local track",
                SourceType.SPOTIFY: "Spotify track",
                SourceType.APPLE: "Apple Music track",
                SourceType.INPUT: "Live input",
            }[source_type]

        if locator is None:
            if input_id and source_type == SourceType.INPUT:
                host = self._inputs.get_host(input_id)
                locator = f"input:{host.id}:{host.capture_path.value}"
            else:
                locator = {
                    SourceType.LOCAL: path or "local:unset",
                    SourceType.SPOTIFY: "spotify:hub",
                    SourceType.APPLE: "apple:hub",
                    SourceType.INPUT: "input:unset",
                }[source_type]

        if source_type == SourceType.INPUT:
            playable = self._live_input_playable(input_id)
        elif source_type in {SourceType.SPOTIFY, SourceType.APPLE} and not input_id:
            playable = is_playable(source_type, path=path, locator=locator)
        else:
            # Hub-local files; host-bound local/spotify/apple remain stubs.
            playable = (
                is_playable(source_type, path=path, locator=locator)
                and not input_id
                and (path is not None or (source_id or "").startswith("local:demo"))
            )
        note = None if playable else self._note_for_host_item(source_type, input_id)
        if (
            source_type == SourceType.LOCAL
            and path is None
            and source_id
            and source_id.startswith("local:demo")
            and not input_id
        ):
            playable = True
            note = "Virtual demo track"

        if media_kind is None:
            media_kind = MediaKind.AUDIO
        if source_type != SourceType.LOCAL or input_id:
            media_kind = MediaKind.AUDIO

        return QueueItem(
            id=self._next_queue_id(),
            title=title,
            source_type=source_type,
            locator=locator,
            source_id=source_id,
            path=path,
            input_id=input_id,
            available=playable,
            note=note,
            media_kind=media_kind,
            # Live capture is open-ended (duration 0). Virtual demos invent a length.
            duration_ms=(
                0
                if playable and source_type == SourceType.INPUT
                else (
                    duration_ms
                    if duration_ms is not None
                    else (
                        DEMO_DURATION_MS
                        if playable
                        and path is None
                        and (source_id or "").startswith("local:demo")
                        else None
                    )
                )
            ),
        )

    def _apply_patch(self, item: QueueItem, patch: QueueItemPatch) -> QueueItem:
        data = item.model_dump()
        updates = patch.model_dump(exclude_unset=True)

        if "source_id" in updates and updates["source_id"]:
            src = self._source_by_id(updates["source_id"])
            data["source_id"] = src.id
            if "source_type" not in updates:
                data["source_type"] = src.type
            if "title" not in updates:
                data["title"] = src.title
            if "locator" not in updates:
                data["locator"] = src.locator or src.path or src.id
            if "path" not in updates:
                data["path"] = src.path
            if "input_id" not in updates:
                data["input_id"] = src.input_id
            if src.duration_ms is not None:
                data["duration_ms"] = src.duration_ms
            if "media_kind" not in updates:
                data["media_kind"] = src.media_kind

        data.update(
            {
                k: v
                for k, v in updates.items()
                if v is not None or k in {"locator", "path", "title", "input_id"}
            }
        )

        source_type = SourceType(data["source_type"])
        input_id = data.get("input_id")
        if source_type != SourceType.LOCAL or input_id:
            data["media_kind"] = MediaKind.AUDIO
        if source_type == SourceType.INPUT and not input_id:
            hosts = self._inputs.list_hosts()
            if not hosts:
                raise ValueError("Register a phone/PC input host before selecting live input")
            input_id = next((h.id for h in hosts if h.armed), hosts[0].id)
            data["input_id"] = input_id

        self._require_input(input_id)

        path = data.get("path")
        locator = data.get("locator")
        if source_type == SourceType.INPUT:
            playable = self._live_input_playable(input_id)
            data["duration_ms"] = 0 if playable else data.get("duration_ms")
        elif source_type in {SourceType.SPOTIFY, SourceType.APPLE} and not input_id:
            playable = is_playable(source_type, path=path, locator=locator)
        else:
            playable = (
                is_playable(source_type, path=path, locator=locator)
                and not input_id
                and (
                    path is not None
                    or (data.get("source_id") or "").startswith("local:demo")
                )
            )
            if (
                source_type == SourceType.LOCAL
                and path is None
                and (data.get("source_id") or "").startswith("local:demo")
                and not input_id
            ):
                playable = True
        data["available"] = playable
        data["note"] = None if playable else self._note_for_host_item(source_type, input_id)
        if source_type != SourceType.LOCAL or input_id:
            if "path" not in updates:
                data["path"] = None
            if "locator" not in updates or not data.get("locator"):
                if input_id and source_type == SourceType.INPUT:
                    host = self._inputs.get_host(input_id)
                    data["locator"] = f"input:{host.id}:{host.capture_path.value}"
                else:
                    data["locator"] = {
                        SourceType.SPOTIFY: "spotify:hub",
                        SourceType.APPLE: "apple:hub",
                        SourceType.INPUT: "input:unset",
                        SourceType.LOCAL: data.get("locator") or "local:host-stub",
                    }.get(source_type, data.get("locator"))

        return QueueItem.model_validate(data)

    def refresh_live_availability(self) -> None:
        """Recompute queue/catalog availability after capture / streaming updates."""
        with self._lock:
            for i, item in enumerate(self._queue):
                if item.source_type == SourceType.INPUT:
                    playable = self._live_input_playable(item.input_id)
                    note = None if playable else self._note_for_host_item(
                        item.source_type, item.input_id
                    )
                    self._queue[i] = item.model_copy(
                        update={
                            "available": playable,
                            "note": note,
                            "duration_ms": 0 if playable else item.duration_ms,
                        }
                    )
                    if self._transport.queue_item_id == item.id:
                        self._transport.input_id = item.input_id
                        self._transport.source_type = item.source_type
                        if playable:
                            self._transport.duration_ms = 0
                elif item.source_type in {SourceType.SPOTIFY, SourceType.APPLE} and not item.input_id:
                    playable = is_playable(
                        item.source_type, path=item.path, locator=item.locator
                    )
                    note = None if playable else stub_note(item.source_type)
                    self._queue[i] = item.model_copy(
                        update={"available": playable, "note": note}
                    )
            self._refresh_catalog()

    def active_locator(self) -> Optional[str]:
        with self._lock:
            qid = self._transport.queue_item_id
            if not qid:
                return None
            try:
                return self._queue_by_id(qid).locator
            except KeyError:
                return None

    def add_streaming_track(
        self,
        *,
        source_type: SourceType,
        uri: str,
        title: str,
        duration_ms: Optional[int] = None,
        activate: bool = True,
    ) -> tuple[QueueItem, Transport]:
        """Queue a hub streaming URI (spotify:track:… / apple:song:…)."""
        with self._lock:
            item = self._resolve_create(
                QueueItemCreate(
                    source_type=source_type,
                    locator=uri,
                    title=title,
                    duration_ms=duration_ms,
                    source_id=uri,
                )
            )
            self._queue.append(item)
            if activate:
                transport = self.set_active_queue_item(item.id)
            else:
                transport = self.get_transport()
            return item.model_copy(deep=True), transport

    def active_input_host(self):
        """Return the InputHost for the active cue when source_type=input."""
        with self._lock:
            if self._transport.source_type != SourceType.INPUT:
                return None
            if not self._transport.input_id:
                return None
            try:
                return self._inputs.get_host(self._transport.input_id)
            except KeyError:
                return None

    # --- public API used by backends ---

    def list_sources(self) -> list[Source]:
        with self._lock:
            self._refresh_catalog()
            return [s.model_copy(deep=True) for s in self._sources]

    def list_queue(self) -> list[QueueItem]:
        with self._lock:
            return [q.model_copy(deep=True) for q in self._queue]

    def add_queue_item(self, body: QueueItemCreate) -> QueueItem:
        with self._lock:
            self._refresh_catalog()
            item = self._resolve_create(body)
            self._queue.append(item)
            return item.model_copy(deep=True)

    def patch_queue_item(self, item_id: str, patch: QueueItemPatch) -> QueueItem:
        """Switch source type on a song. Does not tear down sinks — feeder only."""
        with self._lock:
            self._refresh_catalog()
            item = self._queue_by_id(item_id)
            updated = self._apply_patch(item, patch)
            for i, existing in enumerate(self._queue):
                if existing.id == item_id:
                    self._queue[i] = updated
                    break

            # If this is the active song, rebind transport (feeder swap only).
            if self._transport.queue_item_id == item_id:
                was_playing = self._transport.state == TransportState.PLAYING
                if was_playing and self._on_feeder_stop:
                    self._on_feeder_stop()
                self._bind_transport_to_item(updated, reset_position=True)
                if was_playing and updated.available:
                    self._transport.state = TransportState.PLAYING
                    self._play_started_wall = time.monotonic()
                    self._play_started_pos = 0
                    if self._on_feeder_start:
                        self._on_feeder_start()
                else:
                    self._transport.state = TransportState.STOPPED
                    self._play_started_wall = None
                    self._play_started_pos = 0

            return updated.model_copy(deep=True)

    def remove_queue_item(self, item_id: str) -> None:
        with self._lock:
            item = self._queue_by_id(item_id)
            self._queue = [q for q in self._queue if q.id != item_id]
            if self._transport.queue_item_id == item_id:
                if self._on_feeder_stop:
                    self._on_feeder_stop()
                self._transport.state = TransportState.STOPPED
                self._transport.position_ms = 0
                self._play_started_wall = None
                self._play_started_pos = 0
                nxt = self._first_playable_queue_item()
                if nxt:
                    self._bind_transport_to_item(nxt, reset_position=True)
                else:
                    self._transport.queue_item_id = None
                    self._transport.source_id = None
                    self._transport.source_type = None
                    self._transport.input_id = None
                    self._transport.title = None
                    self._transport.duration_ms = 0
                    self._transport.media_kind = None
            del item  # noqa: F841 — clarify we looked it up

    def clear_input_bindings(self, host_id: str) -> None:
        """When a host is removed, detach queue rows that pointed at it."""
        with self._lock:
            for i, item in enumerate(self._queue):
                if item.input_id != host_id:
                    continue
                updated = item.model_copy(
                    update={
                        "input_id": None,
                        "available": False,
                        "note": f"Host {host_id} removed — re-bind a phone/PC",
                    }
                )
                if updated.source_type == SourceType.INPUT:
                    updated.locator = "input:unset"
                self._queue[i] = updated
                if self._transport.queue_item_id == item.id:
                    self._bind_transport_to_item(updated, reset_position=False)

    def set_active_queue_item(self, item_id: str) -> Transport:
        """Activate a queue item — swap feeder only; sinks stay linked."""
        with self._lock:
            item = self._queue_by_id(item_id)
            was_playing = self._transport.state == TransportState.PLAYING
            if self._on_feeder_stop:
                self._on_feeder_stop()
            self._bind_transport_to_item(item, reset_position=True)
            if was_playing and item.available:
                self._transport.state = TransportState.PLAYING
                self._play_started_wall = time.monotonic()
                self._play_started_pos = 0
                if self._on_feeder_start:
                    self._on_feeder_start()
            else:
                self._transport.state = TransportState.STOPPED
                self._play_started_wall = None
                self._play_started_pos = 0
            return self._transport.model_copy(deep=True)

    def set_active_source(self, source_id: str) -> Transport:
        """Catalog picker: ensure a queue item for this source, then activate it.

        Unavailable catalog rows (host-local upload stubs, Spotify/Apple/input)
        still activate as stubs — Play is what rejects them. Previously
        ``type=local`` + ``available=false`` (host:*:local) returned HTTP 400
        while other stubs succeeded, which broke the Local catalog UX.
        """
        with self._lock:
            self._refresh_catalog()
            src = self._source_by_id(source_id)

            # Reuse an existing queue row for this catalog id when possible.
            existing = next((q for q in self._queue if q.source_id == source_id), None)
            if existing is None:
                item = self._item_from_source(src)
                self._queue.append(item)
            else:
                item = existing

            return self.set_active_queue_item(item.id)

    def get_transport(self) -> Transport:
        with self._lock:
            self._tick_position()
            return self._transport.model_copy(deep=True)

    def set_transport(self, body: TransportPut) -> Transport:
        with self._lock:
            self._tick_position()
            if body.queue_item_id is not None:
                # Nested call already locks; use internal path.
                item = self._queue_by_id(body.queue_item_id)
                was_playing = self._transport.state == TransportState.PLAYING
                if self._on_feeder_stop:
                    self._on_feeder_stop()
                self._bind_transport_to_item(item, reset_position=False)
                if was_playing and item.available:
                    self._transport.state = TransportState.PLAYING
                    self._play_started_wall = time.monotonic()
                    self._play_started_pos = self._transport.position_ms
                    if self._on_feeder_start:
                        self._on_feeder_start()
                elif was_playing and not item.available:
                    self._transport.state = TransportState.STOPPED
                    self._play_started_wall = None
            elif body.source_id is not None:
                # Avoid re-entrant lock: inline catalog activate without was_playing restart quirks.
                self._refresh_catalog()
                src = self._source_by_id(body.source_id)
                existing = next((q for q in self._queue if q.source_id == body.source_id), None)
                if existing is None:
                    item = self._item_from_source(src)
                    self._queue.append(item)
                else:
                    item = existing
                was_playing = self._transport.state == TransportState.PLAYING
                if self._on_feeder_stop:
                    self._on_feeder_stop()
                self._bind_transport_to_item(item, reset_position=False)
                if was_playing and item.available:
                    self._transport.state = TransportState.PLAYING
                    self._play_started_wall = time.monotonic()
                    self._play_started_pos = self._transport.position_ms
                    if self._on_feeder_start:
                        self._on_feeder_start()
                elif was_playing and not item.available:
                    self._transport.state = TransportState.STOPPED
                    self._play_started_wall = None

            if body.position_ms is not None:
                duration = self._transport.duration_ms or 0
                pos = max(0, body.position_ms)
                if duration:
                    pos = min(pos, duration)
                self._transport.position_ms = pos
                if self._transport.state == TransportState.PLAYING:
                    self._play_started_wall = time.monotonic()
                    self._play_started_pos = pos
                    if self._on_feeder_start:
                        self._on_feeder_start()

            if body.state is not None:
                self._apply_state(body.state)

            return self._transport.model_copy(deep=True)

    def active_file_path(self) -> Optional[str]:
        with self._lock:
            qid = self._transport.queue_item_id
            if not qid:
                return None
            try:
                item = self._queue_by_id(qid)
            except KeyError:
                return None
            return item.path
