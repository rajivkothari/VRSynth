"""Generate a fake VR dance :class:`MotionRecording` for development.

Real VR capture does not exist yet, so this tool synthesizes "realistic-enough"
motion -- a headset bobbing gently while two controllers sweep side to side,
occasionally expand outward with both hands, and throw the odd punch. The output
is a valid ``MotionRecording`` JSON file that downstream pipeline work (feature
extraction, visualization, transcription) can consume today.

Run as a script to (re)generate the default fixture::

    python3 tools/generate_fake_motion.py

which writes ``debug/fake_motion_recording.json``.

The motion is fully deterministic for a given seed so tests and fixtures are
reproducible.
"""

from __future__ import annotations

import argparse
import math
import os
import random
import sys
from pathlib import Path

# Allow running directly as a script (``python3 tools/generate_fake_motion.py``)
# by putting the repo root on the import path.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from synthcopilot.motion import (  # noqa: E402  (import after sys.path tweak)
    FrameSample,
    MotionRecording,
    PoseSample,
    save_motion_recording,
)

# --- Defaults ---------------------------------------------------------------

DEFAULT_DURATION_SECONDS = 30.0
DEFAULT_SAMPLE_RATE = 60.0
DEFAULT_BPM = 120.0
DEFAULT_SEED = 1234
DEFAULT_OUTPUT_PATH = "debug/fake_motion_recording.json"

# --- Synth Riders playfield bounds (meters, body-relative-ish) --------------
# Generous reach envelope around a standing player. Generated controller
# positions are clamped to stay inside; tests assert the same bounds.
PLAYFIELD_X = (-1.0, 1.0)   # left .. right
PLAYFIELD_Y = (0.0, 2.2)    # floor .. overhead
PLAYFIELD_Z = (-1.2, 0.6)   # forward (away from body) .. behind

# Neutral resting poses.
_HEAD_BASE = (0.0, 1.60, -0.10)
_LEFT_BASE = (-0.30, 1.20, -0.35)
_RIGHT_BASE = (0.30, 1.20, -0.35)


def _quat_from_euler(roll: float, pitch: float, yaw: float) -> tuple[float, float, float, float]:
    """Unit quaternion ``(x, y, z, w)`` from intrinsic roll/pitch/yaw radians."""
    cr, sr = math.cos(roll * 0.5), math.sin(roll * 0.5)
    cp, sp = math.cos(pitch * 0.5), math.sin(pitch * 0.5)
    cy, sy = math.cos(yaw * 0.5), math.sin(yaw * 0.5)
    w = cr * cp * cy + sr * sp * sy
    x = sr * cp * cy - cr * sp * sy
    y = cr * sp * cy + sr * cp * sy
    z = cr * cp * sy - sr * sp * cy
    return (x, y, z, w)


def _clamp(value: float, lo: float, hi: float) -> float:
    return lo if value < lo else hi if value > hi else value


def _clamp_to_playfield(p: tuple[float, float, float]) -> tuple[float, float, float]:
    return (
        _clamp(p[0], *PLAYFIELD_X),
        _clamp(p[1], *PLAYFIELD_Y),
        _clamp(p[2], *PLAYFIELD_Z),
    )


def _bell(t: float, center: float, width: float) -> float:
    """Gaussian-ish 0..1 envelope peaking at ``center`` with ~``width`` spread."""
    z = (t - center) / width
    return math.exp(-0.5 * z * z)


def _gesture_schedule(duration: float, rng: random.Random) -> tuple[list[float], list[tuple[float, str]]]:
    """Pick times for two-hand expansions and (time, hand) punches."""
    expansions: list[float] = []
    t = 4.0
    while t < duration - 2.0:
        expansions.append(t + rng.uniform(-0.4, 0.4))
        t += rng.uniform(5.5, 7.5)

    punches: list[tuple[float, str]] = []
    t = 2.5
    hand = "r"
    while t < duration - 1.0:
        punches.append((t + rng.uniform(-0.2, 0.2), hand))
        hand = "l" if hand == "r" else "r"
        t += rng.uniform(3.0, 4.5)

    return expansions, punches


def generate_fake_motion_recording(
    duration_seconds: float = DEFAULT_DURATION_SECONDS,
    sample_rate: float = DEFAULT_SAMPLE_RATE,
    bpm: float = DEFAULT_BPM,
    seed: int = DEFAULT_SEED,
    song_path: str = "songs/fake_song_segment.mp3",
) -> MotionRecording:
    """Synthesize a deterministic fake dance recording.

    The number of frames is ``round(duration_seconds * sample_rate)`` and
    timestamps are exact multiples of ``1 / sample_rate`` (strictly increasing).
    """
    rng = random.Random(seed)
    num_frames = round(duration_seconds * sample_rate)
    dt = 1.0 / sample_rate

    expansions, punches = _gesture_schedule(duration_seconds, rng)

    # First pass: positions + rotations, so we can finite-difference velocity.
    head_p: list[tuple[float, float, float]] = []
    left_p: list[tuple[float, float, float]] = []
    right_p: list[tuple[float, float, float]] = []
    head_q: list[tuple[float, float, float, float]] = []
    left_q: list[tuple[float, float, float, float]] = []
    right_q: list[tuple[float, float, float, float]] = []

    for i in range(num_frames):
        t = i * dt

        # --- Head: gentle bob + sway + small nod/turn ---
        bob = 0.03 * math.sin(2 * math.pi * 1.1 * t)        # vertical bob
        sway = 0.04 * math.sin(2 * math.pi * 0.5 * t)       # weight shift
        head = (
            _HEAD_BASE[0] + sway,
            _HEAD_BASE[1] + bob,
            _HEAD_BASE[2] + 0.01 * math.sin(2 * math.pi * 0.5 * t),
        )
        head_p.append(_clamp_to_playfield(head))
        head_q.append(
            _quat_from_euler(
                roll=0.05 * math.sin(2 * math.pi * 0.5 * t),
                pitch=0.08 * math.sin(2 * math.pi * 1.1 * t),
                yaw=0.10 * math.sin(2 * math.pi * 0.33 * t),
            )
        )

        # --- Controllers: side-to-side sweeps around their resting pose ---
        sweep = math.sin(2 * math.pi * 0.5 * t)             # shared phrase phase
        lift = 0.06 * math.sin(2 * math.pi * 1.0 * t)
        left = [
            _LEFT_BASE[0] - 0.22 * sweep,
            _LEFT_BASE[1] + lift,
            _LEFT_BASE[2] + 0.05 * math.sin(2 * math.pi * 0.7 * t),
        ]
        right = [
            _RIGHT_BASE[0] + 0.22 * sweep,
            _RIGHT_BASE[1] - lift,
            _RIGHT_BASE[2] + 0.05 * math.sin(2 * math.pi * 0.7 * t + 1.0),
        ]

        # --- Two-hand expansions: both hands push outward and up ---
        for center in expansions:
            env = _bell(t, center, 0.45)
            if env < 1e-3:
                continue
            left[0] -= 0.35 * env
            left[1] += 0.30 * env
            left[2] += 0.10 * env
            right[0] += 0.35 * env
            right[1] += 0.30 * env
            right[2] += 0.10 * env

        # --- Punches: a quick forward thrust by one hand ---
        for center, p_hand in punches:
            env = _bell(t, center, 0.16)
            if env < 1e-3:
                continue
            target = left if p_hand == "l" else right
            target[2] -= 0.45 * env          # drive forward (more negative z)
            target[1] += 0.05 * env

        left_p.append(_clamp_to_playfield((left[0], left[1], left[2])))
        right_p.append(_clamp_to_playfield((right[0], right[1], right[2])))

        # Controllers roughly face forward with a little wrist motion.
        left_q.append(_quat_from_euler(0.0, 0.0, 0.15 * sweep))
        right_q.append(_quat_from_euler(0.0, 0.0, -0.15 * sweep))

    # Second pass: assemble frames with finite-difference linear velocity.
    def _velocity(seq: list[tuple[float, float, float]], i: int) -> tuple[float, float, float]:
        if i == 0:
            return (0.0, 0.0, 0.0)
        a, b = seq[i - 1], seq[i]
        return ((b[0] - a[0]) / dt, (b[1] - a[1]) / dt, (b[2] - a[2]) / dt)

    def _pose(
        i: int,
        pos: tuple[float, float, float],
        quat: tuple[float, float, float, float],
        vel: tuple[float, float, float],
    ) -> PoseSample:
        return PoseSample(
            time_seconds=i * dt,
            position_x=pos[0], position_y=pos[1], position_z=pos[2],
            rotation_x=quat[0], rotation_y=quat[1], rotation_z=quat[2], rotation_w=quat[3],
            velocity_x=vel[0], velocity_y=vel[1], velocity_z=vel[2],
        )

    frames: list[FrameSample] = []
    for i in range(num_frames):
        frames.append(
            FrameSample(
                time_seconds=i * dt,
                headset=_pose(i, head_p[i], head_q[i], _velocity(head_p, i)),
                left_controller=_pose(i, left_p[i], left_q[i], _velocity(left_p, i)),
                right_controller=_pose(i, right_p[i], right_q[i], _velocity(right_p, i)),
            )
        )

    return MotionRecording(
        song_path=song_path,
        sample_rate=sample_rate,
        frames=frames,
        bpm=bpm,
        offset=0.0,
        metadata={
            "generator": "tools/generate_fake_motion.py",
            "synthetic": True,
            "seed": seed,
            "duration_seconds": duration_seconds,
            "gestures": {
                "expansions": len(expansions),
                "punches": len(punches),
            },
        },
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", "-o", default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--duration", type=float, default=DEFAULT_DURATION_SECONDS)
    parser.add_argument("--sample-rate", type=float, default=DEFAULT_SAMPLE_RATE)
    parser.add_argument("--bpm", type=float, default=DEFAULT_BPM)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args(argv)

    recording = generate_fake_motion_recording(
        duration_seconds=args.duration,
        sample_rate=args.sample_rate,
        bpm=args.bpm,
        seed=args.seed,
    )
    save_motion_recording(recording, args.output)
    print(
        f"Wrote {len(recording.frames)} frames "
        f"({args.duration:g}s @ {args.sample_rate:g} Hz) to {Path(args.output)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
