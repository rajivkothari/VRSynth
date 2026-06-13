"""Core data model for VR motion capture.

This module defines the durable, lossless representation of a captured VR dance
performance -- the source material that later becomes a Synth Riders beatmap (see
``docs/VR_CHOREOGRAPHY_CAPTURE.md``). It is deliberately runtime-agnostic: there
is no OpenXR/SteamVR/Quest code here. Higher layers feed already-sampled poses
into these dataclasses; this module only models, serializes, and validates them.

The first storage format is JSON. The on-disk schema is versioned via
``FORMAT_VERSION`` so the encoding can evolve (e.g. to a binary columnar format)
without breaking older files.

Conventions match the OpenXR tracking space:

* Positions are in **meters**, right-handed, Y-up.
* Rotations are **unit quaternions** in ``(x, y, z, w)`` order.
* Times are in **seconds** relative to song start (``time = 0`` at song t0).
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional, Union

__all__ = [
    "FORMAT_VERSION",
    "PoseSample",
    "FrameSample",
    "MotionRecording",
    "save_motion_recording",
    "load_motion_recording",
    "validate_motion_recording",
]

# Bump on any incompatible change to the on-disk JSON schema.
FORMAT_VERSION = "0.1.0"

# Tolerance used when checking that a rotation quaternion is unit-length.
_DEFAULT_QUATERNION_TOLERANCE = 1e-3

PathLike = Union[str, Path]


@dataclass
class PoseSample:
    """A single 6-DoF pose for one tracked device at one instant.

    Velocity and angular velocity are optional: some runtimes report them
    directly (OpenXR ``XR_SPACE_VELOCITY``), otherwise they are left ``None`` and
    may be derived later during transcription.
    """

    time_seconds: float

    position_x: float
    position_y: float
    position_z: float

    rotation_x: float
    rotation_y: float
    rotation_z: float
    rotation_w: float

    velocity_x: Optional[float] = None
    velocity_y: Optional[float] = None
    velocity_z: Optional[float] = None

    angular_velocity_x: Optional[float] = None
    angular_velocity_y: Optional[float] = None
    angular_velocity_z: Optional[float] = None

    @property
    def has_velocity(self) -> bool:
        """True when all three linear-velocity components are present."""
        return None not in (self.velocity_x, self.velocity_y, self.velocity_z)

    @property
    def has_angular_velocity(self) -> bool:
        """True when all three angular-velocity components are present."""
        return None not in (
            self.angular_velocity_x,
            self.angular_velocity_y,
            self.angular_velocity_z,
        )

    def quaternion_norm(self) -> float:
        """Euclidean norm of the rotation quaternion."""
        return math.sqrt(
            self.rotation_x * self.rotation_x
            + self.rotation_y * self.rotation_y
            + self.rotation_z * self.rotation_z
            + self.rotation_w * self.rotation_w
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PoseSample":
        return cls(
            time_seconds=data["time_seconds"],
            position_x=data["position_x"],
            position_y=data["position_y"],
            position_z=data["position_z"],
            rotation_x=data["rotation_x"],
            rotation_y=data["rotation_y"],
            rotation_z=data["rotation_z"],
            rotation_w=data["rotation_w"],
            velocity_x=data.get("velocity_x"),
            velocity_y=data.get("velocity_y"),
            velocity_z=data.get("velocity_z"),
            angular_velocity_x=data.get("angular_velocity_x"),
            angular_velocity_y=data.get("angular_velocity_y"),
            angular_velocity_z=data.get("angular_velocity_z"),
        )


@dataclass
class FrameSample:
    """One captured frame: synchronized poses for the headset and both hands."""

    time_seconds: float
    headset: PoseSample
    left_controller: PoseSample
    right_controller: PoseSample

    def to_dict(self) -> dict[str, Any]:
        return {
            "time_seconds": self.time_seconds,
            "headset": self.headset.to_dict(),
            "left_controller": self.left_controller.to_dict(),
            "right_controller": self.right_controller.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FrameSample":
        return cls(
            time_seconds=data["time_seconds"],
            headset=PoseSample.from_dict(data["headset"]),
            left_controller=PoseSample.from_dict(data["left_controller"]),
            right_controller=PoseSample.from_dict(data["right_controller"]),
        )


@dataclass
class MotionRecording:
    """A complete captured performance, time-locked to a song.

    ``frames`` is the lossless per-frame motion stream; everything else is
    context needed to transcribe it into a beatmap (the song it was danced to,
    its tempo/offset, and free-form ``metadata`` such as headset model or
    performer height).
    """

    song_path: str
    sample_rate: float
    frames: list[FrameSample] = field(default_factory=list)
    bpm: Optional[float] = None
    offset: Optional[float] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def duration_seconds(self) -> float:
        """Time span covered by the recording, or 0.0 if empty."""
        if not self.frames:
            return 0.0
        return self.frames[-1].time_seconds - self.frames[0].time_seconds

    def to_dict(self) -> dict[str, Any]:
        return {
            "format_version": FORMAT_VERSION,
            "song_path": self.song_path,
            "sample_rate": self.sample_rate,
            "bpm": self.bpm,
            "offset": self.offset,
            "metadata": self.metadata,
            "frames": [frame.to_dict() for frame in self.frames],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MotionRecording":
        return cls(
            song_path=data["song_path"],
            sample_rate=data["sample_rate"],
            frames=[FrameSample.from_dict(f) for f in data.get("frames", [])],
            bpm=data.get("bpm"),
            offset=data.get("offset"),
            metadata=dict(data.get("metadata", {})),
        )


def save_motion_recording(recording: MotionRecording, path: PathLike) -> None:
    """Serialize ``recording`` to ``path`` as JSON.

    The parent directory is created if needed. The write is not atomic; callers
    that need crash-safety should write to a temp file and rename.
    """
    out_path = Path(path)
    if out_path.parent and not out_path.parent.exists():
        out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as handle:
        json.dump(recording.to_dict(), handle, ensure_ascii=False, indent=2)


def load_motion_recording(path: PathLike) -> MotionRecording:
    """Load a :class:`MotionRecording` from a JSON file written by
    :func:`save_motion_recording`."""
    in_path = Path(path)
    with in_path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    return MotionRecording.from_dict(data)


def validate_motion_recording(
    recording: MotionRecording,
    *,
    quaternion_tolerance: float = _DEFAULT_QUATERNION_TOLERANCE,
) -> list[str]:
    """Check a recording for structural and physical sanity.

    Returns a list of human-readable error messages; an **empty list means the
    recording is valid**. This is intentionally non-raising so callers can
    surface every problem at once (e.g. in a capture-review UI).

    Checks performed:

    * ``song_path`` is a non-empty string.
    * ``sample_rate`` is a positive number.
    * ``bpm`` / ``offset``, when present, are sensible (bpm > 0).
    * every frame time is finite and non-negative.
    * frame times are monotonically non-decreasing.
    * every pose's rotation quaternion is finite and unit-length (within
      ``quaternion_tolerance``).
    """
    errors: list[str] = []

    if not isinstance(recording.song_path, str) or not recording.song_path.strip():
        errors.append("song_path must be a non-empty string")

    if not _is_finite_number(recording.sample_rate) or recording.sample_rate <= 0:
        errors.append(
            f"sample_rate must be a positive number (got {recording.sample_rate!r})"
        )

    if recording.bpm is not None:
        if not _is_finite_number(recording.bpm) or recording.bpm <= 0:
            errors.append(f"bpm must be a positive number when set (got {recording.bpm!r})")

    if recording.offset is not None and not _is_finite_number(recording.offset):
        errors.append(f"offset must be a finite number when set (got {recording.offset!r})")

    if not isinstance(recording.metadata, dict):
        errors.append("metadata must be a dict")

    previous_time: Optional[float] = None
    for index, frame in enumerate(recording.frames):
        if not _is_finite_number(frame.time_seconds) or frame.time_seconds < 0:
            errors.append(f"frame {index}: time_seconds must be finite and >= 0")
        elif previous_time is not None and frame.time_seconds < previous_time:
            errors.append(
                f"frame {index}: time_seconds {frame.time_seconds} is earlier than "
                f"previous frame ({previous_time}); frames must be non-decreasing"
            )
        if _is_finite_number(frame.time_seconds):
            previous_time = frame.time_seconds

        for device_name, pose in (
            ("headset", frame.headset),
            ("left_controller", frame.left_controller),
            ("right_controller", frame.right_controller),
        ):
            errors.extend(
                _validate_pose(index, device_name, pose, quaternion_tolerance)
            )

    return errors


def _validate_pose(
    frame_index: int,
    device_name: str,
    pose: PoseSample,
    quaternion_tolerance: float,
) -> list[str]:
    problems: list[str] = []
    prefix = f"frame {frame_index} {device_name}"

    for axis in ("position_x", "position_y", "position_z"):
        if not _is_finite_number(getattr(pose, axis)):
            problems.append(f"{prefix}: {axis} must be a finite number")

    quat_components = (
        pose.rotation_x,
        pose.rotation_y,
        pose.rotation_z,
        pose.rotation_w,
    )
    if not all(_is_finite_number(c) for c in quat_components):
        problems.append(f"{prefix}: rotation quaternion components must be finite")
    else:
        norm = pose.quaternion_norm()
        if abs(norm - 1.0) > quaternion_tolerance:
            problems.append(
                f"{prefix}: rotation quaternion is not unit-length "
                f"(norm={norm:.6f}, tolerance={quaternion_tolerance})"
            )

    return problems


def _is_finite_number(value: Any) -> bool:
    """True for real, finite numbers (rejects bool, NaN, inf, and non-numbers)."""
    if isinstance(value, bool):
        return False
    if not isinstance(value, (int, float)):
        return False
    return math.isfinite(value)
