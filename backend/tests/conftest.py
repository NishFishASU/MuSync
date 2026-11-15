from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


@pytest.fixture(autouse=True)
def _disable_host_speakers_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep unit tests quiet; host-speaker coverage opts in via MUSYNC_HOST_SPEAKERS=1."""
    monkeypatch.setenv("MUSYNC_HOST_SPEAKERS", "0")
