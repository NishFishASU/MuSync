"""Hub → SPA playback push (SSE).

Song start / pause / skip / queue switch publish a ``playback`` event so the
SPA does not need to poll ``/api/now-playing`` (MDAS-71). Position scrub stays
client-side; optional SSE keepalive carries a light transport snapshot for drift.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

_SUB_QUEUE_SIZE = 16


@dataclass
class HubEvent:
    """One SSE payload."""

    type: str  # "playback" | "ping"
    data: dict[str, Any]


class HubEventBus:
    """Thread-safe fan-out to subscriber queues (SSE generators)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subs: list[queue.Queue[HubEvent]] = []

    def subscribe(self) -> queue.Queue[HubEvent]:
        q: queue.Queue[HubEvent] = queue.Queue(maxsize=_SUB_QUEUE_SIZE)
        with self._lock:
            self._subs.append(q)
        return q

    def unsubscribe(self, q: queue.Queue[HubEvent]) -> None:
        with self._lock:
            try:
                self._subs.remove(q)
            except ValueError:
                pass

    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subs)

    def publish(self, event_type: str, data: dict[str, Any]) -> None:
        """Publish from any thread (API handlers, AVRCP, track-end)."""
        event = HubEvent(type=event_type, data=data)
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(event)
            except queue.Full:
                try:
                    q.get_nowait()
                except queue.Empty:
                    pass
                try:
                    q.put_nowait(event)
                except queue.Full:
                    log.debug("hub event dropped (subscriber backlog)")


bus = HubEventBus()


def format_sse(event: HubEvent) -> str:
    """Encode one Server-Sent Event block."""
    payload = json.dumps(event.data, default=str, separators=(",", ":"))
    return f"event: {event.type}\ndata: {payload}\n\n"


def publish_playback(
    *,
    transport: Any,
    now_playing: Any,
    reason: str = "change",
) -> None:
    """Push transport + now-playing snapshot to all SSE subscribers."""
    try:
        t = (
            transport.model_dump(mode="json")
            if hasattr(transport, "model_dump")
            else dict(transport)
        )
        np = (
            now_playing.model_dump(mode="json")
            if hasattr(now_playing, "model_dump")
            else dict(now_playing)
        )
    except Exception as exc:  # noqa: BLE001
        log.debug("hub playback serialize: %s", exc)
        return
    bus.publish(
        "playback",
        {
            "reason": reason,
            "transport": t,
            "now_playing": np,
        },
    )
