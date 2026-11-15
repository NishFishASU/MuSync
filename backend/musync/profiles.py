"""Named user volume preference profiles (silent-disco style).

Persisted under ``config/profiles.yaml`` (or ``MUSYNC_PROFILES_CONFIG``).
Applied sink volume = clamp(preferred_level × device.gain); delay is untouched.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import uuid
from pathlib import Path
from typing import Optional

import yaml

from .config import clamp_volume, env_path, repo_root
from .models import (
    Device,
    DevicePatch,
    VolumeProfile,
    VolumeProfileCreate,
    VolumeProfilePatch,
    VolumeProfilesState,
)

log = logging.getLogger(__name__)


def profiles_config_path() -> Path:
    return env_path("MUSYNC_PROFILES_CONFIG", repo_root() / "config" / "profiles.yaml")


def clamp_gain(value: float) -> float:
    return round(max(0.1, min(4.0, float(value))), 3)


def compute_output_volume(
    preferred_level: float,
    gain: float = 1.0,
    *,
    override: float | None = None,
) -> float:
    """preferred (or per-output override) × gain, clamped to 0..1."""
    level = float(override) if override is not None else float(preferred_level)
    return clamp_volume(level * clamp_gain(gain))


def volumes_for_devices(
    profile: VolumeProfile,
    devices: list[Device],
) -> dict[str, float]:
    """Map device_id → applied volume for an active profile."""
    out: dict[str, float] = {}
    for device in devices:
        override = profile.output_overrides.get(device.id)
        out[device.id] = compute_output_volume(
            profile.preferred_level,
            getattr(device, "gain", 1.0) or 1.0,
            override=override,
        )
    return out


def slug_profile_id(name: str, used: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-") or "user"
    base = base[:24]
    candidate = f"user-{base}"
    if candidate not in used:
        return candidate
    for i in range(2, 100):
        alt = f"{candidate}-{i}"
        if alt not in used:
            return alt
    return f"user-{uuid.uuid4().hex[:8]}"


def load_profiles_state() -> VolumeProfilesState:
    """Load profiles.yaml; missing/broken → empty state (API still boots)."""
    path = profiles_config_path()
    if not path.is_file():
        return VolumeProfilesState()

    try:
        with path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except (OSError, yaml.YAMLError) as exc:
        log.warning("profiles.yaml unreadable at %s: %s", path, exc)
        return VolumeProfilesState()

    if data is None:
        return VolumeProfilesState()
    if not isinstance(data, dict):
        log.warning("profiles.yaml invalid root at %s", path)
        return VolumeProfilesState()

    raw_profiles = data.get("profiles") or []
    profiles: list[VolumeProfile] = []
    if isinstance(raw_profiles, list):
        for item in raw_profiles:
            try:
                profiles.append(VolumeProfile.model_validate(item))
            except (ValueError, TypeError) as exc:
                log.warning("skipping invalid volume profile: %s", exc)

    active_id = data.get("active_id")
    if active_id is not None:
        active_id = str(active_id).strip() or None
    if active_id and active_id not in {p.id for p in profiles}:
        active_id = None

    return VolumeProfilesState(profiles=profiles, active_id=active_id)


def save_profiles_state(state: VolumeProfilesState) -> Path:
    """Atomically write profiles.yaml (temp file + replace)."""
    path = profiles_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "active_id": state.active_id,
        "profiles": [p.model_dump(mode="json") for p in state.profiles],
    }
    text = yaml.safe_dump(payload, sort_keys=False)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    return path


class VolumeProfileStore:
    """Thread-safe hub store for named listening preference profiles."""

    def __init__(self, *, path: Path | None = None) -> None:
        self._lock = threading.RLock()
        self._path_override = path
        self._state = self._load()

    def _config_path(self) -> Path:
        return self._path_override or profiles_config_path()

    def _load(self) -> VolumeProfilesState:
        if self._path_override is not None:
            # Temporarily point env-style load at the override via direct read.
            path = self._path_override
            if not path.is_file():
                return VolumeProfilesState()
            try:
                with path.open("r", encoding="utf-8") as fh:
                    data = yaml.safe_load(fh)
            except (OSError, yaml.YAMLError):
                return VolumeProfilesState()
            if not isinstance(data, dict):
                return VolumeProfilesState()
            profiles: list[VolumeProfile] = []
            for item in data.get("profiles") or []:
                try:
                    profiles.append(VolumeProfile.model_validate(item))
                except (ValueError, TypeError):
                    continue
            active_id = data.get("active_id")
            if active_id is not None:
                active_id = str(active_id).strip() or None
            if active_id and active_id not in {p.id for p in profiles}:
                active_id = None
            return VolumeProfilesState(profiles=profiles, active_id=active_id)
        return load_profiles_state()

    def _persist(self) -> None:
        path = self._config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "active_id": self._state.active_id,
            "profiles": [p.model_dump(mode="json") for p in self._state.profiles],
        }
        text = yaml.safe_dump(payload, sort_keys=False)
        tmp = path.with_suffix(path.suffix + ".tmp")
        try:
            with tmp.open("w", encoding="utf-8") as fh:
                fh.write(text)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
        except OSError as exc:
            log.warning("could not persist profiles to %s: %s", path, exc)
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass

    def get_state(self) -> VolumeProfilesState:
        with self._lock:
            return VolumeProfilesState(
                profiles=[p.model_copy(deep=True) for p in self._state.profiles],
                active_id=self._state.active_id,
            )

    def get(self, profile_id: str) -> VolumeProfile:
        with self._lock:
            for profile in self._state.profiles:
                if profile.id == profile_id:
                    return profile.model_copy(deep=True)
            raise KeyError(f"Unknown volume profile: {profile_id}")

    def create(self, body: VolumeProfileCreate) -> VolumeProfile:
        with self._lock:
            used = {p.id for p in self._state.profiles}
            profile = VolumeProfile(
                id=slug_profile_id(body.name, used),
                name=body.name,
                preferred_level=body.preferred_level,
                output_overrides=dict(body.output_overrides),
            )
            self._state.profiles.append(profile)
            self._persist()
            return profile.model_copy(deep=True)

    def patch(self, profile_id: str, body: VolumeProfilePatch) -> VolumeProfile:
        with self._lock:
            idx = next(
                (i for i, p in enumerate(self._state.profiles) if p.id == profile_id),
                None,
            )
            if idx is None:
                raise KeyError(f"Unknown volume profile: {profile_id}")
            current = self._state.profiles[idx]
            data = current.model_dump()
            updates = body.model_dump(exclude_unset=True)
            data.update(updates)
            updated = VolumeProfile.model_validate(data)
            self._state.profiles[idx] = updated
            self._persist()
            return updated.model_copy(deep=True)

    def delete(self, profile_id: str) -> None:
        with self._lock:
            before = len(self._state.profiles)
            self._state.profiles = [
                p for p in self._state.profiles if p.id != profile_id
            ]
            if len(self._state.profiles) == before:
                raise KeyError(f"Unknown volume profile: {profile_id}")
            if self._state.active_id == profile_id:
                self._state.active_id = None
            self._persist()

    def set_active(self, profile_id: Optional[str]) -> VolumeProfilesState:
        with self._lock:
            if profile_id is None:
                self._state.active_id = None
                self._persist()
                return self.get_state()
            if profile_id not in {p.id for p in self._state.profiles}:
                raise KeyError(f"Unknown volume profile: {profile_id}")
            self._state.active_id = profile_id
            self._persist()
            return self.get_state()

    def active_profile(self) -> Optional[VolumeProfile]:
        with self._lock:
            if not self._state.active_id:
                return None
            for profile in self._state.profiles:
                if profile.id == self._state.active_id:
                    return profile.model_copy(deep=True)
            return None


def apply_profile_to_backend(profile: VolumeProfile, backend) -> list[Device]:
    """Set each sink volume from preferred × gain; leave delay/mute/enabled alone."""
    devices = backend.list_devices()
    targets = volumes_for_devices(profile, devices)
    updated: list[Device] = []
    for device in devices:
        vol = targets[device.id]
        if abs(float(device.volume) - vol) < 0.0005:
            updated.append(device)
            continue
        updated.append(backend.patch_device(device.id, DevicePatch(volume=vol)))
    return updated
