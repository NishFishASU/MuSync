"""SPA poll cadence + Page Visibility pause (MDAS-70).

Cadence helpers live in web/src/lib/api.js — mirrored here so backend CI
guards the contract without a Node test runner.
"""

from __future__ import annotations


# Keep in sync with web/src/lib/api.js (MDAS-71 SSE + fallback poll).
SPA_POLL_TRANSPORT_PLAYING_MS = 2000
SPA_POLL_TRANSPORT_IDLE_MS = 8000
SPA_POLL_NOW_PLAYING_PLAYING_MS = 4000
SPA_POLL_NOW_PLAYING_IDLE_MS = 12000
SPA_POLL_TRANSPORT_SSE_MS = 15000


def spa_poll_cadence(
    *, playing: bool = False, hidden: bool = False, sse_connected: bool = False
) -> dict:
    if hidden:
        return {"transportMs": 0, "nowPlayingMs": 0, "paused": True, "push": False}
    if sse_connected:
        return {
            "transportMs": SPA_POLL_TRANSPORT_SSE_MS,
            "nowPlayingMs": 0,
            "paused": False,
            "push": True,
        }
    if playing:
        return {
            "transportMs": SPA_POLL_TRANSPORT_PLAYING_MS,
            "nowPlayingMs": SPA_POLL_NOW_PLAYING_PLAYING_MS,
            "paused": False,
            "push": False,
        }
    return {
        "transportMs": SPA_POLL_TRANSPORT_IDLE_MS,
        "nowPlayingMs": SPA_POLL_NOW_PLAYING_IDLE_MS,
        "paused": False,
        "push": False,
    }


def spa_poll_requests_per_second(cadence: dict) -> float:
    if not cadence or cadence.get("paused"):
        return 0.0
    t = 1000 / cadence["transportMs"] if cadence.get("transportMs", 0) > 0 else 0.0
    n = 1000 / cadence["nowPlayingMs"] if cadence.get("nowPlayingMs", 0) > 0 else 0.0
    return t + n


def test_hidden_tab_pauses_all_polls() -> None:
    cadence = spa_poll_cadence(playing=True, hidden=True)
    assert cadence["paused"] is True
    assert cadence["transportMs"] == 0
    assert cadence["nowPlayingMs"] == 0
    assert spa_poll_requests_per_second(cadence) == 0.0


def test_sse_connected_skips_now_playing_poll() -> None:
    cadence = spa_poll_cadence(playing=True, sse_connected=True)
    assert cadence["push"] is True
    assert cadence["nowPlayingMs"] == 0
    assert cadence["transportMs"] == 15000
    assert spa_poll_requests_per_second(cadence) < 0.1


def test_playing_vs_idle_cadence() -> None:
    playing = spa_poll_cadence(playing=True, hidden=False)
    idle = spa_poll_cadence(playing=False, hidden=False)
    assert playing["transportMs"] == 2000
    assert idle["transportMs"] == 8000
    assert idle["nowPlayingMs"] == 12000
    assert spa_poll_requests_per_second(idle) < spa_poll_requests_per_second(playing)
    assert spa_poll_requests_per_second(idle) < 0.25
