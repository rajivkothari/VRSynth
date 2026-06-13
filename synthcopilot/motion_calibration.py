"""Calibration: raw VR room coordinates -> a canonical centered frame.

Raw captures live wherever the player happened to stand and face in their room.
Before transcription it helps to put every recording in a consistent frame:
floor at Y=0, the player centered at the XZ origin, facing canonical forward
(-Z), at a consistent scale. This module estimates that transform from the
performer's **neutral stance** (assumed to be the first few seconds of the take)
and applies it.

This is upstream of, and separate from, the playfield normalization in
``motion_to_map.py`` (which re-origins on the head per frame and scales by arm
reach). Calibration here cleans the *world* frame; the playfield mapping and the
still-deferred ``.synth`` coordinate units come later (see
``docs/VR_CHOREOGRAPHY_CAPTURE.md`` §8.7).

Honest scope notes:

* **Floor** is best supplied by the recorder's stage/tracking origin (Stage space
  puts the floor at 0). Without feet tracking it cannot be reliably inferred from
  hand/head motion, so ``estimate_calibration`` assumes a floor-referenced input
  (``floor_y = 0``) and leaves it overridable.
* **Scale** is assumed to be meters (OpenXR convention) -> ``scale = 1.0``;
  per-player size normalization is handled later by arm-reach scaling.
* What the neutral stance *does* give robustly is the **center** (where the player
  stands) and the **forward** direction (where they face).
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Union

from .motion import FrameSample, MotionRecording, PoseSample

__all__ = [
    "CalibrationProfile",
    "estimate_calibration",
    "apply_calibration",
    "save_calibration_profile",
    "load_calibration_profile",
]

PathLike = Union[str, Path]

# Default neutral-stance window (seconds from the start of the take).
DEFAULT_CALIBRATION_SECONDS = 3.0


@dataclass
class CalibrationProfile:
    """An affine room->canonical transform estimated from a neutral stance.

    Applied per pose as: optional Z-flip (handedness) -> translate by
    (center_x, floor_y, center_z) -> yaw so ``forward_axis`` points to canonical
    forward (-Z) -> uniform ``scale``.
    """

    floor_y: float = 0.0
    center_x: float = 0.0
    center_z: float = 0.0
    scale: float = 1.0
    # Player's facing direction in the horizontal (XZ) plane, as a unit vector.
    forward_axis: tuple[float, float] = (0.0, -1.0)
    # Handedness adjustment: negate Z (and mirror the quaternion) for a
    # left-handed source. OpenXR-convention input needs no flip.
    flip_z: bool = False
    name: str = "estimated"
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["forward_axis"] = list(self.forward_axis)
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CalibrationProfile":
        fa = data.get("forward_axis", [0.0, -1.0])
        return cls(
            floor_y=data.get("floor_y", 0.0),
            center_x=data.get("center_x", 0.0),
            center_z=data.get("center_z", 0.0),
            scale=data.get("scale", 1.0),
            forward_axis=(float(fa[0]), float(fa[1])),
            flip_z=bool(data.get("flip_z", False)),
            name=data.get("name", "estimated"),
            notes=data.get("notes", ""),
        )


def estimate_calibration(
    recording: MotionRecording,
    calibration_seconds: float = DEFAULT_CALIBRATION_SECONDS,
) -> CalibrationProfile:
    """Estimate a :class:`CalibrationProfile` from the neutral stance.

    Uses the frames in the first ``calibration_seconds`` of the take: the mean
    headset XZ becomes the center, and the mean headset facing becomes
    ``forward_axis``. ``floor_y`` defaults to 0 (assume floor-referenced input)
    and ``scale`` to 1 (assume meters) -- see the module docstring.
    """
    if not recording.frames:
        return CalibrationProfile(notes="empty recording; identity calibration")

    t0 = recording.frames[0].time_seconds
    neutral = [f for f in recording.frames if f.time_seconds - t0 <= calibration_seconds]
    if not neutral:
        neutral = recording.frames[:1]

    cx = sum(f.headset.position_x for f in neutral) / len(neutral)
    cz = sum(f.headset.position_z for f in neutral) / len(neutral)

    fx = fz = 0.0
    for f in neutral:
        q = (f.headset.rotation_x, f.headset.rotation_y,
             f.headset.rotation_z, f.headset.rotation_w)
        fwd = _rotate_vec_by_quat(q, (0.0, 0.0, -1.0))  # device forward in world
        fx += fwd[0]
        fz += fwd[2]
    norm = math.hypot(fx, fz)
    forward = (fx / norm, fz / norm) if norm > 1e-6 else (0.0, -1.0)

    return CalibrationProfile(
        floor_y=0.0,
        center_x=cx,
        center_z=cz,
        scale=1.0,
        forward_axis=forward,
        flip_z=False,
        name="estimated",
        notes=(
            f"estimated from first {calibration_seconds:g}s neutral stance "
            f"({len(neutral)} frames); floor assumed at 0 (floor-referenced "
            "input), scale assumed meters."
        ),
    )


def apply_calibration(
    recording: MotionRecording, profile: CalibrationProfile
) -> MotionRecording:
    """Return a new recording with ``profile`` applied to every pose.

    Timestamps, sample rate, song, and tempo are preserved; rotations are
    re-normalized so the result stays a valid recording. The applied profile is
    recorded under ``metadata["calibration"]``.
    """
    # Yaw that rotates forward_axis onto canonical forward (-Z), about +Y.
    fx, fz = profile.forward_axis
    phi = math.atan2(fz, fx) + math.pi / 2.0
    cos_p, sin_p = math.cos(phi), math.sin(phi)
    yaw_q = (0.0, math.sin(phi / 2.0), 0.0, math.cos(phi / 2.0))

    def transform_position(x: float, y: float, z: float) -> tuple[float, float, float]:
        if profile.flip_z:
            z = -z
        x -= profile.center_x
        y -= profile.floor_y
        z -= profile.center_z
        # Rotate about +Y by phi (matches yaw_q): x'=x cos + z sin; z'=-x sin + z cos.
        xr = x * cos_p + z * sin_p
        zr = -x * sin_p + z * cos_p
        s = profile.scale
        return (xr * s, y * s, zr * s)

    def transform_direction(vx: float, vy: float, vz: float) -> tuple[float, float, float]:
        if profile.flip_z:
            vz = -vz
        xr = vx * cos_p + vz * sin_p
        zr = -vx * sin_p + vz * cos_p
        s = profile.scale
        return (xr * s, vy * s, zr * s)

    def transform_quat(q: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
        if profile.flip_z:
            q = (-q[0], -q[1], q[2], q[3])
        return _normalize_quat(_quat_mul(yaw_q, q))

    def transform_pose(p: PoseSample) -> PoseSample:
        px, py, pz = transform_position(p.position_x, p.position_y, p.position_z)
        qx, qy, qz, qw = transform_quat(
            (p.rotation_x, p.rotation_y, p.rotation_z, p.rotation_w)
        )
        vx = vy = vz = None
        if p.velocity_x is not None and p.velocity_y is not None and p.velocity_z is not None:
            vx, vy, vz = transform_direction(p.velocity_x, p.velocity_y, p.velocity_z)
        ax = ay = az = None
        if (p.angular_velocity_x is not None and p.angular_velocity_y is not None
                and p.angular_velocity_z is not None):
            # Angular velocity is an axial vector: rotate (and Z-flip) but don't scale.
            ax, ay, az = transform_direction(
                p.angular_velocity_x, p.angular_velocity_y, p.angular_velocity_z
            )
            if profile.scale not in (0.0,):
                ax, ay, az = ax / profile.scale, ay / profile.scale, az / profile.scale
        return PoseSample(
            time_seconds=p.time_seconds,
            position_x=px, position_y=py, position_z=pz,
            rotation_x=qx, rotation_y=qy, rotation_z=qz, rotation_w=qw,
            velocity_x=vx, velocity_y=vy, velocity_z=vz,
            angular_velocity_x=ax, angular_velocity_y=ay, angular_velocity_z=az,
        )

    new_frames = [
        FrameSample(
            time_seconds=f.time_seconds,
            headset=transform_pose(f.headset),
            left_controller=transform_pose(f.left_controller),
            right_controller=transform_pose(f.right_controller),
        )
        for f in recording.frames
    ]

    metadata = dict(recording.metadata)
    metadata["calibration"] = profile.to_dict()
    return MotionRecording(
        song_path=recording.song_path,
        sample_rate=recording.sample_rate,
        frames=new_frames,
        bpm=recording.bpm,
        offset=recording.offset,
        metadata=metadata,
    )


def save_calibration_profile(profile: CalibrationProfile, path: PathLike) -> None:
    """Write a calibration profile to JSON."""
    out = Path(path)
    if out.parent and not out.parent.exists():
        out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as handle:
        json.dump(profile.to_dict(), handle, ensure_ascii=False, indent=2)


def load_calibration_profile(path: PathLike) -> CalibrationProfile:
    """Load a calibration profile from JSON."""
    with Path(path).open("r", encoding="utf-8") as handle:
        return CalibrationProfile.from_dict(json.load(handle))


# --------------------------------------------------------------------------- #
# Quaternion helpers
# --------------------------------------------------------------------------- #
def _quat_mul(a: tuple[float, float, float, float],
              b: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
        aw * bw - ax * bx - ay * by - az * bz,
    )


def _normalize_quat(q: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    n = math.sqrt(q[0] * q[0] + q[1] * q[1] + q[2] * q[2] + q[3] * q[3])
    if n < 1e-12:
        return (0.0, 0.0, 0.0, 1.0)
    return (q[0] / n, q[1] / n, q[2] / n, q[3] / n)


def _rotate_vec_by_quat(q: tuple[float, float, float, float],
                        v: tuple[float, float, float]) -> tuple[float, float, float]:
    """Rotate vector ``v`` by quaternion ``q`` (x, y, z, w)."""
    x, y, z, w = q
    vx, vy, vz = v
    # t = 2 * cross(q.xyz, v)
    tx = 2.0 * (y * vz - z * vy)
    ty = 2.0 * (z * vx - x * vz)
    tz = 2.0 * (x * vy - y * vx)
    # v' = v + w*t + cross(q.xyz, t)
    return (
        vx + w * tx + (y * tz - z * ty),
        vy + w * ty + (z * tx - x * tz),
        vz + w * tz + (x * ty - y * tx),
    )
