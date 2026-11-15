"""Queue turn-taking: rotate which output sink is unmuted after each track.

Rotation order (stable ``list_devices()`` order):

1. Prefer **enabled** sinks that are **ready**:
   - Bluetooth: ``enabled`` and ``connected``
   - SPP / Cast: ``enabled`` (armed listen / stream)
2. If any ready sinks have a **profile/user assignment**, rotate only among
   those assigned devices (paired users). Otherwise rotate among all ready
   sinks.
3. On each auto-advance (track end), move to the **next** id in that list
   (wrap). Solo = unmute the turn device, mute every other enabled sink.
"""

from __future__ import annotations

from typing import Optional

from ..models import Device, DeviceKind

ROTATION_NOTE = (
    "Eligible: enabled sinks that are connected (BT) or armed (SPP/Cast). "
    "If any have a profile/user assignment, only those participate. "
    "Order: stable list_devices() order. On track end: next queue item + next device. "
    "AVRCP Play on a connected BT headset promotes that sink to the active solo turn "
    "(and resumes hub transport if paused)."
)


def device_is_turn_ready(device: Device) -> bool:
    """Whether a sink can take a turn right now."""
    if not device.enabled:
        return False
    if device.kind == DeviceKind.BLUETOOTH:
        return bool(device.connected)
    # SPP / Cast: enabled means armed for listen/stream.
    return True


def eligible_turn_devices(
    devices: list[Device],
    *,
    assignments: Optional[dict[str, str]] = None,
) -> list[Device]:
    """Return ready sinks in rotation order (see module docstring)."""
    ready = [d for d in devices if device_is_turn_ready(d)]
    if not ready:
        return []
    assigned_ids = {
        device_id
        for device_id, profile_id in (assignments or {}).items()
        if profile_id
    }
    if assigned_ids:
        with_users = [d for d in ready if d.id in assigned_ids]
        if with_users:
            return with_users
    return ready


def eligible_detail(
    devices: list[Device],
    eligible: list[Device],
    *,
    assignments: Optional[dict[str, str]] = None,
) -> str:
    """Short Queue-tab explanation of who can take a turn (or why none)."""
    if eligible:
        return "Ready: " + ", ".join(d.name for d in eligible)
    enabled = [d for d in devices if d.enabled]
    if not enabled:
        return "No eligible sinks — enable a BT / SPP / Cast output on Outputs"
    assigned_ids = {
        device_id
        for device_id, profile_id in (assignments or {}).items()
        if profile_id
    }
    waiting: list[str] = []
    for device in enabled:
        if device_is_turn_ready(device):
            if assigned_ids and device.id not in assigned_ids:
                waiting.append(f"{device.name} (no profile)")
            continue
        if device.kind == DeviceKind.BLUETOOTH:
            waiting.append(f"{device.name} (BT not connected)")
        else:
            waiting.append(f"{device.name} (not armed)")
    if waiting:
        return "No ready sinks — " + "; ".join(waiting)
    return "No eligible sinks — connect BT or arm SPP/Cast"


def next_turn_device_id(
    eligible_ids: list[str],
    current_id: Optional[str],
    *,
    rotate: bool,
    promote_id: Optional[str] = None,
) -> Optional[str]:
    """Pick the turn device.

    ``promote_id`` (AVRCP Play on a known headset) wins when eligible.
    ``rotate=True`` advances; else keep current if valid.
    """
    if not eligible_ids:
        return None
    if promote_id and promote_id in eligible_ids:
        return promote_id
    if current_id in eligible_ids:
        if not rotate:
            return current_id
        idx = eligible_ids.index(current_id)
        return eligible_ids[(idx + 1) % len(eligible_ids)]
    return eligible_ids[0]


def mute_targets_for_turn(
    devices: list[Device],
    *,
    turn_device_id: Optional[str],
    turn_taking: bool,
) -> list[tuple[str, bool]]:
    """``(device_id, mute)`` pairs to apply for the current turn policy.

    When turn-taking is on: unmute ``turn_device_id``, mute every other
    **enabled** sink. Disabled sinks are left unchanged.
    When turn-taking is off: unmute all enabled sinks (restore fan-out).
    """
    out: list[tuple[str, bool]] = []
    for device in devices:
        if not device.enabled:
            continue
        if turn_taking:
            want_mute = device.id != turn_device_id
        else:
            want_mute = False
        if device.mute != want_mute:
            out.append((device.id, want_mute))
    return out
