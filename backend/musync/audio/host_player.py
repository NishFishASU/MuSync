"""Host default-speaker playback for MUSYNC_BACKEND=mock (Windows / macOS / Linux).

Tries optional ``sounddevice`` first, then CLI players (``ffplay``, ``mpv``,
``afplay``, ``paplay``, ``pw-play``, ``aplay``). Soft-fails when no player or
no audio device is available — the mock transport clock still advances.

Disable with ``MUSYNC_HOST_SPEAKERS=0``.
"""

from __future__ import annotations

import logging
import math
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path
from typing import Callable, Optional

log = logging.getLogger(__name__)


def _ffplay_argv(path: str, start: float) -> list[str]:
    return [
        "ffplay",
        "-nodisp",
        "-autoexit",
        "-loglevel",
        "quiet",
        "-ss",
        f"{start:.3f}",
        path,
    ]


def _mpv_argv(path: str, start: float) -> list[str]:
    return [
        "mpv",
        "--no-video",
        "--really-quiet",
        f"--start={start:.3f}",
        path,
    ]


def _afplay_argv(path: str, _start: float) -> list[str]:
    # macOS afplay has no reliable seek; play from the start.
    return ["afplay", path]


def _paplay_argv(path: str, _start: float) -> list[str]:
    return ["paplay", path]


def _pw_play_argv(path: str, _start: float) -> list[str]:
    return ["pw-play", path]


def _aplay_argv(path: str, _start: float) -> list[str]:
    return ["aplay", "-q", path]


# External players: (name, argv builder).
_CLI_CANDIDATES: list[tuple[str, Callable[[str, float], list[str]]]] = [
    ("ffplay", _ffplay_argv),
    ("mpv", _mpv_argv),
    ("afplay", _afplay_argv),
    ("paplay", _paplay_argv),
    ("pw-play", _pw_play_argv),
    ("aplay", _aplay_argv),
]


def host_speakers_enabled() -> bool:
    raw = os.environ.get("MUSYNC_HOST_SPEAKERS", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _sounddevice_available() -> bool:
    try:
        import numpy  # noqa: F401
        import sounddevice  # noqa: F401

        return True
    except Exception:  # noqa: BLE001
        return False


def discover_host_player() -> tuple[str, str]:
    """Return ``(backend_id, human_detail)`` for the preferred host output path.

    ``backend_id`` is ``sounddevice``, a CLI name, or ``none``.
    """
    if not host_speakers_enabled():
        return "none", "host speakers disabled (MUSYNC_HOST_SPEAKERS=0)"
    if _sounddevice_available():
        return "sounddevice", "sounddevice → default output device"
    for name, _builder in _CLI_CANDIDATES:
        if shutil.which(name):
            return name, f"{name} → host default speakers"
    return (
        "none",
        "no host player (install ffplay/mpv or pip install sounddevice) — soft-fail",
    )


def write_demo_tone_wav(
    path: Path,
    *,
    duration_s: float = 2.0,
    freq_hz: float = 440.0,
    sample_rate: int = 44100,
    amplitude: float = 0.2,
) -> Path:
    """Write a short mono PCM tone (stdlib only) for pathless demo cues."""
    path.parent.mkdir(parents=True, exist_ok=True)
    nframes = max(1, int(duration_s * sample_rate))
    with wave.open(str(path), "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        frames = bytearray()
        for i in range(nframes):
            sample = int(
                amplitude * 32767.0 * math.sin(2.0 * math.pi * freq_hz * i / sample_rate)
            )
            frames.extend(struct.pack("<h", sample))
        wf.writeframes(frames)
    return path


class HostSpeakerPlayer:
    """Start/stop local file playback on the host default speakers (soft-fail)."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._proc: Optional[subprocess.Popen[str]] = None
        self._sd_thread: Optional[threading.Thread] = None
        self._sd_stop = threading.Event()
        self._backend, self._detail = discover_host_player()
        self._temp_demo: Optional[Path] = None
        self._last_error = ""

    @property
    def backend(self) -> str:
        return self._backend

    @property
    def detail(self) -> str:
        if self._last_error:
            return f"{self._detail}; last soft-fail: {self._last_error}"
        return self._detail

    @property
    def available(self) -> bool:
        return self._backend != "none"

    def refresh(self) -> None:
        with self._lock:
            self._backend, self._detail = discover_host_player()

    def stop(self) -> None:
        with self._lock:
            self._sd_stop.set()
            thread = self._sd_thread
            self._sd_thread = None
            proc = self._proc
            self._proc = None
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                try:
                    proc.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    proc.kill()
            except OSError as exc:
                log.debug("host player stop: %s", exc)

    def play(self, path: Optional[str], *, position_ms: int = 0) -> bool:
        """Play ``path`` from ``position_ms``. Returns True if a player started.

        Soft-fails (returns False, never raises) when disabled, missing tools,
        missing file, or OS/audio errors.
        """
        self.stop()
        if not host_speakers_enabled():
            self._last_error = "disabled"
            return False
        self.refresh()
        if self._backend == "none":
            self._last_error = "no player"
            log.info("Host speakers soft-fail: %s", self._detail)
            return False

        resolved = (path or "").strip()
        if not resolved or not Path(resolved).is_file():
            self._last_error = "no local file"
            log.info(
                "Host speakers soft-fail: no playable file (%r) — software clock only",
                path,
            )
            return False

        start_s = max(0.0, position_ms / 1000.0)
        try:
            if self._backend == "sounddevice":
                return self._play_sounddevice(resolved, start_s=start_s)
            return self._play_cli(resolved, start_s=start_s)
        except Exception as exc:  # noqa: BLE001 — never break mock transport
            self._last_error = str(exc)
            log.warning("Host speakers soft-fail: %s", exc)
            self.stop()
            return False

    def play_demo_tone(self, *, position_ms: int = 0) -> bool:
        """Play a short generated tone for pathless local demo cues."""
        try:
            if self._temp_demo is None or not self._temp_demo.is_file():
                fd, name = tempfile.mkstemp(prefix="musync-demo-", suffix=".wav")
                os.close(fd)
                self._temp_demo = write_demo_tone_wav(Path(name), duration_s=2.0)
            return self.play(str(self._temp_demo), position_ms=position_ms)
        except Exception as exc:  # noqa: BLE001
            self._last_error = str(exc)
            log.warning("Host speakers demo tone soft-fail: %s", exc)
            return False

    def _play_cli(self, path: str, *, start_s: float) -> bool:
        builder: Optional[Callable[[str, float], list[str]]] = None
        for name, build in _CLI_CANDIDATES:
            if name == self._backend:
                builder = build
                break
        if builder is None:
            self._last_error = f"unknown backend {self._backend}"
            return False
        argv = builder(path, start_s)
        if not shutil.which(argv[0]):
            self._last_error = f"{argv[0]} missing"
            return False
        creationflags = 0
        if sys.platform == "win32":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self._proc = subprocess.Popen(
            argv,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
            creationflags=creationflags,
        )
        self._last_error = ""
        log.info(
            "Host speakers: %s playing %s (start=%.3fs)", self._backend, path, start_s
        )
        return True

    def _play_sounddevice(self, path: str, *, start_s: float) -> bool:
        import sounddevice as sd

        data, rate = self._load_pcm(path)
        if data is None or rate is None:
            # Decode failed — try a CLI player instead.
            for name, _build in _CLI_CANDIDATES:
                if shutil.which(name):
                    self._backend = name
                    self._detail = f"{name} → host default speakers"
                    return self._play_cli(path, start_s=start_s)
            self._last_error = "sounddevice could not decode file"
            return False

        start_frame = int(start_s * rate)
        if start_frame >= len(data):
            start_frame = 0
        chunk = data[start_frame:]
        duration_s = float(len(chunk)) / float(rate) if rate else 0.0
        self._sd_stop.clear()

        def _run() -> None:
            try:
                sd.play(chunk, rate)
                end = time.monotonic() + duration_s + 0.25
                while time.monotonic() < end and not self._sd_stop.is_set():
                    time.sleep(0.05)
                if self._sd_stop.is_set():
                    sd.stop()
            except Exception as exc:  # noqa: BLE001
                log.warning("sounddevice play soft-fail: %s", exc)

        self._sd_thread = threading.Thread(
            target=_run, name="musync-host-sd", daemon=True
        )
        self._sd_thread.start()
        self._last_error = ""
        log.info(
            "Host speakers: sounddevice playing %s (start=%.3fs)", path, start_s
        )
        return True

    @staticmethod
    def _load_pcm(path: str):
        """Return ``(float32 ndarray, sample_rate)`` or ``(None, None)``."""
        suffix = Path(path).suffix.lower()
        if suffix == ".wav":
            try:
                import numpy as np

                with wave.open(path, "rb") as wf:
                    channels = wf.getnchannels()
                    width = wf.getsampwidth()
                    rate = wf.getframerate()
                    raw = wf.readframes(wf.getnframes())
                if width == 2:
                    ints = np.frombuffer(raw, dtype="<i2").astype("float32") / 32768.0
                elif width == 1:
                    ints = (
                        np.frombuffer(raw, dtype="u1").astype("float32") - 128.0
                    ) / 128.0
                else:
                    return None, None
                if channels > 1:
                    ints = ints.reshape(-1, channels)
                return ints, rate
            except Exception:  # noqa: BLE001
                return None, None
        try:
            import soundfile as sf

            data, rate = sf.read(path, dtype="float32")
            return data, rate
        except Exception:  # noqa: BLE001
            return None, None

    def cleanup(self) -> None:
        self.stop()
        if self._temp_demo is not None:
            try:
                self._temp_demo.unlink(missing_ok=True)
            except OSError:
                pass
            self._temp_demo = None
