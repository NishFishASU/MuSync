from __future__ import annotations

import pytest

from musync.config import validate_devices
from musync.models import Device


def _dev(i: int, **kwargs) -> Device:
    base = dict(
        id=f"sink-{i}",
        name=f"S{i}",
        mac=f"AA:BB:CC:DD:EE:0{i}",
        hci=f"hci{i-1}",
    )
    base.update(kwargs)
    return Device(**base)


def test_validate_accepts_two_to_six() -> None:
    devices = [_dev(i) for i in range(1, 3)]
    assert len(validate_devices(devices)) == 2
    devices6 = [_dev(i) for i in range(1, 7)]
    assert len(validate_devices(devices6)) == 6


def test_validate_rejects_seventh() -> None:
    devices = [_dev(i) for i in range(1, 8)]
    # MAC/hci for 7 would be hci6 / EE:07 — still invalid by BT count
    with pytest.raises(ValueError, match="at most 6 Bluetooth"):
        validate_devices(devices)


def test_validate_rejects_duplicate_mac_or_hci() -> None:
    with pytest.raises(ValueError, match="Duplicate speaker MAC"):
        validate_devices(
            [
                _dev(1),
                _dev(2, mac="AA:BB:CC:DD:EE:01"),
            ]
        )
    with pytest.raises(ValueError, match="Duplicate HCI"):
        validate_devices(
            [
                _dev(1),
                _dev(2, hci="hci0"),
            ]
        )
