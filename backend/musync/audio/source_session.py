"""Shared source catalog + song queue — feeder swaps keep the sink graph alive."""

from __future__ import annotations

import itertools
import threading
import time
from typing import Callable, Optional

from ..config import PLACEHOLDER_SOURCES, discover_local_sources
from ..models import (
    PLAYABLE_SOURCE_TYPES,
    QueueItem,
    QueueItemCreate,
    QueueItemPatch,
    Source,
    SourceType,
    Transport,
    TransportPut,
    TransportState,
)

STUB_NOTES: dict[SourceType, str] = {
    SourceType.SPOTIFY: "Stub — Spotify feeder not wired yet; sink graph stays up",
    SourceType.APPLE: "Stub — Apple Music feeder not wired yet; sink graph stays up",
    SourceType.INPUT: "Stub — phone/PC input not wired yet; sink graph stays up",
}


def is_playable(source_type: SourceType, *, path: Optional[str] = None) -> bool:
    if source_type not in PLAYABLE_SOURCE_TYPES:
        return False
    # Local demo tracks without a file path are playable on the mock clock.
    return True


def stub_note(source_type: SourceType) -> Optional[str]:
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
        on_feeder_stop: Optional[Callable[[], None]] = None,
        on_feeder_start: Optional[Callable[[], None]] = None,
        lock: Optional[threading.RLock] = None,
    ) -> None:
        self._lock = lock or threading.RLock()
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
            title=active.title if active else None,
        )
        self._play_started_wall: Optional[float] = None
        self._play_started_pos: int = 0

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
                    duration_ms=180_000,
                    note="Virtual demo track — drop files under media/ for real paths",
                )
            ]
        else:
            for src in local:
                if src.locator is None and src.path:
                    src.locator = src.path
        return local + list(PLACEHOLDER_SOURCES)

    def _seed_queue_from_locals(self) -> None:
        """One queue item per available local catalog entry (song-level switching)."""
        for src in self._sources:
            if src.type != SourceType.LOCAL or not src.available:
                continue
            self._queue.append(self._item_from_source(src))

    def _item_from_source(self, src: Source) -> QueueItem:
        playable = is_playable(src.type, path=src.path)
        return QueueItem(
            id=self._next_queue_id(),
            title=src.title,
            source_type=src.type,
            locator=src.locator or src.path or src.id,
            source_id=src.id,
            path=src.path,
            available=playable and src.available,
            note=None if playable else stub_note(src.type),
            duration_ms=src.duration_ms,
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
        placeholders = [s for s in self._sources if s.type != SourceType.LOCAL]
        local = discover_local_sources()
        if not local:
            local = [s for s in self._sources if s.type == SourceType.LOCAL]
        else:
            for src in local:
                if src.locator is None and src.path:
                    src.locator = src.path
        self._sources = local + placeholders

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
        self._transport.title = item.title
        self._transport.duration_ms = item.duration_ms or 0
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
        duration_ms = body.duration_ms
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

        if title is None:
            title = {
                SourceType.LOCAL: "Local track",
                SourceType.SPOTIFY: "Spotify track (stub)",
                SourceType.APPLE: "Apple Music track (stub)",
                SourceType.INPUT: "Input source (stub)",
            }[source_type]

        if locator is None:
            locator = {
                SourceType.LOCAL: path or "local:unset",
                SourceType.SPOTIFY: "spotify:track:stub",
                SourceType.APPLE: "apple:track:stub",
                SourceType.INPUT: "input:1",
            }[source_type]

        playable = is_playable(source_type, path=path)
        note = None if playable else stub_note(source_type)
        if (
            source_type == SourceType.LOCAL
            and path is None
            and source_id
            and source_id.startswith("local:demo")
        ):
            playable = True
            note = "Virtual demo track"

        return QueueItem(
            id=self._next_queue_id(),
            title=title,
            source_type=source_type,
            locator=locator,
            source_id=source_id,
            path=path,
            available=playable,
            note=note,
            duration_ms=duration_ms if duration_ms is not None else (180_000 if playable else None),
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
            if src.duration_ms is not None:
                data["duration_ms"] = src.duration_ms

        data.update({k: v for k, v in updates.items() if v is not None or k in {"locator", "path", "title"}})

        source_type = SourceType(data["source_type"])
        path = data.get("path")
        playable = is_playable(source_type, path=path)
        if source_type == SourceType.LOCAL and path is None and (data.get("source_id") or "").startswith("local:demo"):
            playable = True
        data["available"] = playable
        data["note"] = None if playable else stub_note(source_type)
        if source_type != SourceType.LOCAL:
            # Clear local path when switching away from local feeder.
            if "path" not in updates:
                data["path"] = None
            if "locator" not in updates or not data.get("locator"):
                data["locator"] = {
                    SourceType.SPOTIFY: "spotify:track:stub",
                    SourceType.APPLE: "apple:track:stub",
                    SourceType.INPUT: "input:1",
                }.get(source_type, data.get("locator"))

        return QueueItem.model_validate(data)

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
                    self._transport.title = None
                    self._transport.duration_ms = 0
            del item  # noqa: F841 — clarify we looked it up

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
        """Catalog picker: ensure a queue item for this source, then activate it."""
        with self._lock:
            self._refresh_catalog()
            src = self._source_by_id(source_id)
            if not src.available and src.type in PLAYABLE_SOURCE_TYPES:
                raise ValueError(f"Source not available: {source_id}")

            # Reuse an existing queue row for this catalog id when possible.
            existing = next((q for q in self._queue if q.source_id == source_id), None)
            if existing is None:
                item = self._item_from_source(src)
                # Stubs from catalog stay unavailable.
                if src.type not in PLAYABLE_SOURCE_TYPES:
                    item.available = False
                    item.note = stub_note(src.type) or src.note
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
                    if src.type not in PLAYABLE_SOURCE_TYPES:
                        item.available = False
                        item.note = stub_note(src.type) or src.note
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
