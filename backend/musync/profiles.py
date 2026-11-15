"""Named user volume preference profiles (silent-disco style).

Persisted under ``config/profiles.yaml`` (or ``MUSYNC_PROFILES_CONFIG``).
Per-user×device volumes + learned device gains live in SQLite
(``config/user_volumes.sqlite`` / ``MUSYNC_VOLUME_DB``).

**Explicit apply (#27 UI):** ``volume = clamp(preferred_level × Device.gain)``.
Delay / mute / enabled are never touched by profile apply.

**Learned latents** (not UI sliders):

- ``S_u`` — per-person sensitivity (``VolumeProfile.sensitivity``)
- ``G_d`` — per-device relative loudness (``VolumeProfilesState.device_gains``;
  distinct from the headphone-calibration ``Device.gain`` slider)

Model: ``v_{u,d} ≈ S_u · G_d`` (clamped to 0..1).

Fit in log space from observed volumes (ALS with geo-mean(S)=1 for scale).
When a user changes volume after a suggestion, re-fit S and G from all
observations so both person and device latents track.

**Cross-infer:** if A set device1+2 and B set device1, then
``v̂_{B,2} = S_B · G_2`` with ``S_B ≈ v_{B,1}/G_1`` and ``G_2`` from A's data.
"""

from __future__ import annotations

import logging
import math
import os
import re
import sqlite3
import threading
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml

from .config import clamp_volume, env_path, repo_root
from .models import (
    Device,
    DevicePatch,
    DeviceUserAssign,
    DeviceUserAssignResult,
    UserDeviceVolume,
    VolumeProfile,
    VolumeProfileCreate,
    VolumeProfilePatch,
    VolumeProfilesState,
)

log = logging.getLogger(__name__)

_EPS = 1e-6
_ALS_ITERS = 24


def profiles_config_path() -> Path:
    return env_path("MUSYNC_PROFILES_CONFIG", repo_root() / "config" / "profiles.yaml")


def volume_db_path() -> Path:
    return env_path(
        "MUSYNC_VOLUME_DB", repo_root() / "config" / "user_volumes.sqlite"
    )


def clamp_gain(value: float) -> float:
    return round(max(0.1, min(4.0, float(value))), 3)


def clamp_sensitivity(value: float) -> float:
    return round(max(0.05, min(20.0, float(value))), 3)


def clamp_device_gain(value: float) -> float:
    """Learned G_d — relative loudness latent (wider than UI calibration)."""
    return round(max(0.05, min(4.0, float(value))), 3)


def compute_output_volume(
    preferred_level: float,
    gain: float = 1.0,
    *,
    override: float | None = None,
) -> float:
    """preferred (or per-output override) × Device.gain, clamped to 0..1."""
    level = float(override) if override is not None else float(preferred_level)
    return clamp_volume(level * clamp_gain(gain))


def volumes_for_devices(
    profile: VolumeProfile,
    devices: list[Device],
) -> dict[str, float]:
    """Map device_id → applied volume for preferred × Device.gain."""
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


def fit_sensitivity_and_device_gains(
    observations: dict[tuple[str, str], float],
    *,
    user_ids: list[str] | set[str] | None = None,
    device_ids: list[str] | set[str] | None = None,
) -> tuple[dict[str, float], dict[str, float]]:
    """Fit ``v ≈ S_u · G_d`` from observations via log-space ALS.

    Scale is pinned by geo-mean(S_u)=1 over users with data. Returns
    ``(sensitivities, device_gains)``; users/devices with no usable data stay
    at 1.0 / omitted respectively.
    """
    obs: list[tuple[str, str, float]] = []
    for (user_id, device_id), volume in observations.items():
        if volume is None:
            continue
        v = float(volume)
        if v <= 0:
            continue
        obs.append((str(user_id), str(device_id), v))

    users = list(
        dict.fromkeys(
            [*(str(u) for u in (user_ids or [])), *(u for u, _, _ in obs)]
        )
    )
    devices = list(
        dict.fromkeys(
            [*(str(d) for d in (device_ids or [])), *(d for _, d, _ in obs)]
        )
    )

    sens: dict[str, float] = {u: 1.0 for u in users}
    gains: dict[str, float] = {d: 1.0 for d in devices}
    if not obs:
        return sens, {}

    observed_users = {u for u, _, _ in obs}
    observed_devices = {d for _, d, _ in obs}

    # Seed: per-user geo-mean volume / global geo-mean → relative S; G from residuals.
    global_logs = [math.log(v) for _, _, v in obs]
    global_mean = sum(global_logs) / len(global_logs)
    user_logs: dict[str, list[float]] = defaultdict(list)
    for u, _, v in obs:
        user_logs[u].append(math.log(v))
    for u, logs in user_logs.items():
        sens[u] = math.exp(sum(logs) / len(logs) - global_mean)

    for _ in range(_ALS_ITERS):
        # Update G_d: geo-mean_u (v / S_u)
        for d in observed_devices:
            ratios: list[float] = []
            for u, dd, v in obs:
                if dd != d:
                    continue
                s = max(sens.get(u, 1.0), _EPS)
                ratios.append(v / s)
            if ratios:
                gains[d] = math.exp(sum(math.log(r) for r in ratios) / len(ratios))

        # Update S_u: geo-mean_d (v / G_d)
        for u in observed_users:
            ratios = []
            for uu, d, v in obs:
                if uu != u:
                    continue
                g = max(gains.get(d, 1.0), _EPS)
                ratios.append(v / g)
            if ratios:
                sens[u] = math.exp(sum(math.log(r) for r in ratios) / len(ratios))

        # Pin scale: geo-mean(S)=1 over observed users
        if observed_users:
            geo = math.exp(
                sum(math.log(max(sens[u], _EPS)) for u in observed_users)
                / len(observed_users)
            )
            if geo > 0:
                for u in observed_users:
                    sens[u] /= geo
                for d in observed_devices:
                    gains[d] *= geo

    for u in users:
        sens[u] = clamp_sensitivity(sens.get(u, 1.0))
    out_gains = {
        d: clamp_device_gain(gains[d]) for d in observed_devices
    }
    return sens, out_gains


def suggest_volume_for_user(
    user_id: str,
    device_id: str,
    *,
    observations: dict[tuple[str, str], float],
    sensitivities: dict[str, float],
    device_gains: dict[str, float],
    preferred_level: float = 0.8,
    ui_gain: float = 1.0,
    override: float | None = None,
) -> tuple[float, str]:
    """Suggest volume for ``user_id`` on ``device_id``.

    Priority:
    1. Direct observation for this user×device
    2. ``S_u · G_d`` when both latents are known (cross-infer / preset)
    3. Infer missing S_u from another device (or G_d from another user) then ×
    4. Fallback preferred × Device.gain (#27 explicit path)
    """
    key = (user_id, device_id)
    if key in observations and observations[key] > 0:
        return clamp_volume(observations[key]), "observed"

    sens_u = float(sensitivities.get(user_id, 1.0) or 1.0)
    g_d = device_gains.get(device_id)

    # Cross-infer S_u from this user's other devices if we have those G's.
    if user_id not in sensitivities or abs(sens_u - 1.0) < 1e-9:
        ratios: list[float] = []
        for (ou, od), vol in observations.items():
            if ou != user_id or vol <= 0:
                continue
            g_other = device_gains.get(od)
            if g_other and g_other > 0:
                ratios.append(vol / g_other)
        if ratios:
            sens_u = math.exp(sum(math.log(r) for r in ratios) / len(ratios))
            sens_u = clamp_sensitivity(sens_u)

    # Cross-infer G_d from other users on this device if missing.
    if g_d is None:
        ratios = []
        for (ou, od), vol in observations.items():
            if od != device_id or vol <= 0 or ou == user_id:
                continue
            s_o = float(sensitivities.get(ou, 1.0) or 1.0)
            if s_o > 0:
                ratios.append(vol / s_o)
        if ratios:
            g_d = clamp_device_gain(
                math.exp(sum(math.log(r) for r in ratios) / len(ratios))
            )

    if g_d is not None and g_d > 0 and sens_u > 0:
        # Also accept default sens=1.0 if G_d known (preset new user on known device)
        # or known S_u on known G_d (preset known user on new/shared device).
        has_user_obs = any(ou == user_id for ou, _ in observations)
        has_device_obs = any(od == device_id for _, od in observations)
        if has_user_obs or has_device_obs or device_id in device_gains:
            return clamp_volume(sens_u * float(g_d)), "s_u_x_g_d"

    return (
        compute_output_volume(preferred_level, ui_gain, override=override),
        "preferred_x_gain",
    )


class UserVolumeDB:
    """SQLite store of (user, device) volumes + learned device gains G_d."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path
        self._lock = threading.RLock()
        self._ensure_schema()

    def _db_path(self) -> Path:
        return self._path or volume_db_path()

    def _connect(self) -> sqlite3.Connection:
        path = self._db_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), timeout=5.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._lock:
            try:
                with self._connect() as conn:
                    conn.execute(
                        """
                        CREATE TABLE IF NOT EXISTS user_device_volumes (
                            user_id TEXT NOT NULL,
                            device_id TEXT NOT NULL,
                            volume REAL NOT NULL,
                            updated_at TEXT NOT NULL,
                            PRIMARY KEY (user_id, device_id)
                        )
                        """
                    )
                    conn.execute(
                        """
                        CREATE TABLE IF NOT EXISTS device_gains (
                            device_id TEXT PRIMARY KEY,
                            gain REAL NOT NULL,
                            updated_at TEXT NOT NULL
                        )
                        """
                    )
                    conn.commit()
            except sqlite3.Error as exc:
                log.warning("volume DB schema init failed: %s", exc)

    def list_all(self) -> list[UserDeviceVolume]:
        with self._lock:
            try:
                with self._connect() as conn:
                    rows = conn.execute(
                        "SELECT user_id, device_id, volume, updated_at "
                        "FROM user_device_volumes ORDER BY user_id, device_id"
                    ).fetchall()
            except sqlite3.Error as exc:
                log.warning("volume DB list failed: %s", exc)
                return []
        return [
            UserDeviceVolume(
                user_id=str(r["user_id"]),
                device_id=str(r["device_id"]),
                volume=float(r["volume"]),
                updated_at=str(r["updated_at"] or ""),
            )
            for r in rows
        ]

    def as_map(self) -> dict[tuple[str, str], float]:
        return {(v.user_id, v.device_id): v.volume for v in self.list_all()}

    def list_device_gains(self) -> dict[str, float]:
        with self._lock:
            try:
                with self._connect() as conn:
                    rows = conn.execute(
                        "SELECT device_id, gain FROM device_gains ORDER BY device_id"
                    ).fetchall()
            except sqlite3.Error as exc:
                log.warning("device_gains list failed: %s", exc)
                return {}
        return {
            str(r["device_id"]): clamp_device_gain(float(r["gain"])) for r in rows
        }

    def upsert(self, user_id: str, device_id: str, volume: float) -> UserDeviceVolume:
        stamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        entry = UserDeviceVolume(
            user_id=user_id,
            device_id=device_id,
            volume=clamp_volume(volume),
            updated_at=stamp,
        )
        with self._lock:
            try:
                with self._connect() as conn:
                    conn.execute(
                        """
                        INSERT INTO user_device_volumes
                            (user_id, device_id, volume, updated_at)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(user_id, device_id) DO UPDATE SET
                            volume = excluded.volume,
                            updated_at = excluded.updated_at
                        """,
                        (entry.user_id, entry.device_id, entry.volume, entry.updated_at),
                    )
                    conn.commit()
            except sqlite3.Error as exc:
                log.warning("volume DB upsert failed: %s", exc)
        return entry

    def save_device_gains(self, gains: dict[str, float]) -> None:
        stamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        with self._lock:
            try:
                with self._connect() as conn:
                    conn.execute("DELETE FROM device_gains")
                    for device_id, gain in gains.items():
                        conn.execute(
                            """
                            INSERT INTO device_gains (device_id, gain, updated_at)
                            VALUES (?, ?, ?)
                            """,
                            (device_id, clamp_device_gain(gain), stamp),
                        )
                    conn.commit()
            except sqlite3.Error as exc:
                log.warning("device_gains save failed: %s", exc)

    def delete_user(self, user_id: str) -> None:
        with self._lock:
            try:
                with self._connect() as conn:
                    conn.execute(
                        "DELETE FROM user_device_volumes WHERE user_id = ?",
                        (user_id,),
                    )
                    conn.commit()
            except sqlite3.Error as exc:
                log.warning("volume DB delete_user failed: %s", exc)


def _parse_assignments(raw: object) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for key, value in raw.items():
        device_id = str(key).strip()
        profile_id = str(value).strip() if value is not None else ""
        if device_id and profile_id:
            out[device_id] = profile_id
    return out


def _state_from_yaml_dict(data: dict) -> VolumeProfilesState:
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
    profile_ids = {p.id for p in profiles}
    if active_id and active_id not in profile_ids:
        active_id = None

    assignments = {
        d: u
        for d, u in _parse_assignments(data.get("assignments")).items()
        if u in profile_ids
    }

    return VolumeProfilesState(
        profiles=profiles,
        active_id=active_id,
        assignments=assignments,
    )


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

    return _state_from_yaml_dict(data)


def save_profiles_state(state: VolumeProfilesState) -> Path:
    """Atomically write profiles.yaml (temp file + replace)."""
    path = profiles_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "active_id": state.active_id,
        "assignments": dict(state.assignments),
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
    """Thread-safe hub store for named listening profiles + S_u/G_d learning."""

    def __init__(
        self,
        *,
        path: Path | None = None,
        volume_db: Path | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._path_override = path
        self._volumes = UserVolumeDB(path=volume_db)
        self._state = self._load()
        self._device_gains: dict[str, float] = self._volumes.list_device_gains()
        self._refresh_latents_locked()

    def _config_path(self) -> Path:
        return self._path_override or profiles_config_path()

    def _load(self) -> VolumeProfilesState:
        if self._path_override is not None:
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
            return _state_from_yaml_dict(data)
        return load_profiles_state()

    def _persist(self) -> None:
        path = self._config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "active_id": self._state.active_id,
            "assignments": dict(self._state.assignments),
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

    def _refresh_latents_locked(self) -> bool:
        """Re-fit S_u and G_d from the volume DB. Returns True if profiles changed."""
        obs = self._volumes.as_map()
        user_ids = [p.id for p in self._state.profiles]
        sens, gains = fit_sensitivity_and_device_gains(obs, user_ids=user_ids)
        self._device_gains = gains
        self._volumes.save_device_gains(gains)

        changed = False
        updated: list[VolumeProfile] = []
        for profile in self._state.profiles:
            new_sens = sens.get(profile.id, 1.0)
            if abs(float(profile.sensitivity) - new_sens) > 0.0005:
                changed = True
                updated.append(profile.model_copy(update={"sensitivity": new_sens}))
            else:
                updated.append(profile)
        if changed:
            self._state.profiles = updated
        return changed

    def get_state(self) -> VolumeProfilesState:
        with self._lock:
            return VolumeProfilesState(
                profiles=[p.model_copy(deep=True) for p in self._state.profiles],
                active_id=self._state.active_id,
                assignments=dict(self._state.assignments),
                volumes=self._volumes.list_all(),
                device_gains=dict(self._device_gains),
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
                sensitivity=1.0,
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
            updates.pop("sensitivity", None)
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
            self._state.assignments = {
                d: u for d, u in self._state.assignments.items() if u != profile_id
            }
            self._volumes.delete_user(profile_id)
            self._refresh_latents_locked()
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

    def assigned_user(self, device_id: str) -> Optional[str]:
        with self._lock:
            return self._state.assignments.get(device_id)

    def record_volume(
        self, user_id: str, device_id: str, volume: float
    ) -> VolumeProfilesState:
        """Persist observation and re-fit S_u / G_d (user adjusted from suggestion)."""
        with self._lock:
            if user_id not in {p.id for p in self._state.profiles}:
                raise KeyError(f"Unknown volume profile: {user_id}")
            self._volumes.upsert(user_id, device_id, volume)
            self._refresh_latents_locked()
            self._persist()
            return self.get_state()

    def assign_device(
        self,
        body: DeviceUserAssign,
        *,
        backend=None,
        device: Device | None = None,
    ) -> DeviceUserAssignResult:
        """Assign a user to an output; preset volume via S_u × G_d when possible."""
        with self._lock:
            device_id = body.device_id
            profile_id = body.profile_id

            if profile_id is not None and profile_id not in {
                p.id for p in self._state.profiles
            }:
                raise KeyError(f"Unknown volume profile: {profile_id}")

            if profile_id is None:
                self._state.assignments.pop(device_id, None)
                self._persist()
                return DeviceUserAssignResult(
                    state=self.get_state(),
                    device=device,
                    detail="cleared assignment",
                )

            self._state.assignments[device_id] = profile_id
            profile = next(p for p in self._state.profiles if p.id == profile_id)
            obs = self._volumes.as_map()
            sens = {p.id: p.sensitivity for p in self._state.profiles}
            ui_gain = float(getattr(device, "gain", 1.0) or 1.0) if device else 1.0
            override = profile.output_overrides.get(device_id)
            suggested, reason = suggest_volume_for_user(
                profile_id,
                device_id,
                observations=obs,
                sensitivities=sens,
                device_gains=self._device_gains,
                preferred_level=profile.preferred_level,
                ui_gain=ui_gain,
                override=override,
            )

            applied = False
            updated_device = device
            detail = f"assigned {profile.name} ({reason})"
            if body.apply_suggested and backend is not None:
                has_obs = (profile_id, device_id) in obs
                if not has_obs:
                    updated_device = backend.patch_device(
                        device_id, DevicePatch(volume=suggested)
                    )
                    # Preset only — do not treat suggestion as a hard observation
                    # until the user confirms/adjusts (record_volume on slider).
                    applied = True
                    detail = (
                        f"assigned {profile.name}; preset {suggested:.0%} via {reason}"
                    )
                else:
                    detail = f"assigned {profile.name}; kept observed volume"

            self._persist()
            return DeviceUserAssignResult(
                state=self.get_state(),
                device=updated_device,
                suggested_volume=suggested,
                applied=applied,
                detail=detail,
            )


def apply_profile_to_backend(profile: VolumeProfile, backend) -> list[Device]:
    """Set each sink volume from preferred × Device.gain; leave delay alone."""
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
