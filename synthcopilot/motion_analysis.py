"""Motion analysis: raw controller paths -> movement segments.

This is the L4 "feature extraction" layer from ``docs/VR_CHOREOGRAPHY_CAPTURE.md``.
It turns a :class:`~synthcopilot.motion.MotionRecording` (headset + two controller
trajectories) into a list of :class:`MovementSegment` candidates that describe
*what the body was doing* -- swept left, lifted up, both hands expanded, punched,
etc. It deliberately stops at **choreography intent**: it does NOT emit Synth
Riders notes, rails, or walls. That mapping (L5/L6) is a later, separate step.

Design notes:

* Pure standard library (``math`` only) -- no numpy -- so the core capture +
  analysis pipeline has zero third-party dependencies.
* Segments are *candidates* and may **overlap**: a moment where both hands sweep
  apart is legitimately a left-hand ``left_sweep`` + a right-hand ``right_sweep``
  + a two-hand ``two_hand_expansion`` at once. Downstream transcription chooses
  among them; this layer's job is recall, not commitment.
* Coordinate conventions follow the capture format: meters, right-handed, Y-up,
  Z forward = negative (away from the body).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Optional

from .motion import FrameSample, MotionRecording, PoseSample

__all__ = [
    "MovementSegment",
    "PRIMITIVES",
    "compute_velocity",
    "smooth_motion",
    "segment_motion",
]

Vec3 = tuple[float, float, float]

# Logical device name -> FrameSample attribute.
_DEVICES: dict[str, str] = {
    "head": "headset",
    "left": "left_controller",
    "right": "right_controller",
}

# Primitive vocabulary (choreography intent, not game notes).
STILL = "still"
LEFT_SWEEP = "left_sweep"
RIGHT_SWEEP = "right_sweep"
UPWARD_LIFT = "upward_lift"
DOWNWARD_DROP = "downward_drop"
TWO_HAND_EXPANSION = "two_hand_expansion"
TWO_HAND_CONTRACTION = "two_hand_contraction"
ALTERNATING_PUNCHES = "alternating_punches"
CIRCULAR_MOTION = "circular_motion"

PRIMITIVES = frozenset(
    {
        STILL,
        LEFT_SWEEP,
        RIGHT_SWEEP,
        UPWARD_LIFT,
        DOWNWARD_DROP,
        TWO_HAND_EXPANSION,
        TWO_HAND_CONTRACTION,
        ALTERNATING_PUNCHES,
        CIRCULAR_MOTION,
    }
)


@dataclass
class MovementSegment:
    """A detected span of coherent motion -- an early choreography primitive.

    ``confidence`` is a heuristic 0..1 score (1 = very clearly this primitive).
    ``features`` carries the numeric evidence behind the call (mean/peak speed,
    dominant axis, displacement, etc.) so later stages and debugging can inspect
    *why* a segment was labelled the way it was.
    """

    start_time: float
    end_time: float
    hand: str  # "left" | "right" | "both" | "head"
    primitive_name: str
    confidence: float
    features: dict[str, Any] = field(default_factory=dict)

    @property
    def duration(self) -> float:
        return self.end_time - self.start_time


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def compute_velocity(recording: MotionRecording) -> dict[str, list[Vec3]]:
    """Finite-difference linear velocity (m/s) for each device.

    Returns ``{"head": [...], "left": [...], "right": [...]}`` where each list has
    one ``(vx, vy, vz)`` per frame. Uses central differences in the interior and
    one-sided differences at the ends, based on the *actual* frame timestamps (so
    it is correct even if the sample rate jitters).
    """
    times = [f.time_seconds for f in recording.frames]
    out: dict[str, list[Vec3]] = {}
    for name, attr in _DEVICES.items():
        positions = [_position(getattr(f, attr)) for f in recording.frames]
        out[name] = _finite_difference(times, positions)
    return out


def smooth_motion(recording: MotionRecording, window: int = 5) -> MotionRecording:
    """Return a copy of ``recording`` with positions low-pass smoothed.

    A centered moving average of width ``window`` (clamped to odd, >= 1) is applied
    to each device's position track. Rotations are left untouched (averaging
    quaternions naively would break unit-length), and per-sample velocities are
    cleared (they become stale after smoothing -- recompute with
    :func:`compute_velocity`). Timestamps, ordering, and metadata are preserved.
    """
    if window < 1:
        window = 1
    if window % 2 == 0:
        window += 1
    half = window // 2

    tracks: dict[str, list[Vec3]] = {}
    for name, attr in _DEVICES.items():
        positions = [_position(getattr(f, attr)) for f in recording.frames]
        tracks[name] = _moving_average(positions, half)

    new_frames: list[FrameSample] = []
    for i, frame in enumerate(recording.frames):
        new_frames.append(
            FrameSample(
                time_seconds=frame.time_seconds,
                headset=_with_position(frame.headset, tracks["head"][i]),
                left_controller=_with_position(frame.left_controller, tracks["left"][i]),
                right_controller=_with_position(frame.right_controller, tracks["right"][i]),
            )
        )

    metadata = dict(recording.metadata)
    metadata["smoothed_window"] = window
    return MotionRecording(
        song_path=recording.song_path,
        sample_rate=recording.sample_rate,
        frames=new_frames,
        bpm=recording.bpm,
        offset=recording.offset,
        metadata=metadata,
    )


def segment_motion(
    recording: MotionRecording,
    *,
    smooth: bool = True,
    smooth_window: int = 5,
    still_speed: float = 0.15,
    min_duration: float = 0.12,
    expansion_rate: float = 0.25,
    punch_speed: float = 0.5,
    punch_max_gap: float = 1.2,
    circular_window_seconds: float = 1.0,
    circular_min_rotation: float = 1.25 * math.pi,
) -> list[MovementSegment]:
    """Segment a recording into candidate movement primitives.

    Runs several independent detectors and returns their union, sorted by start
    time (segments may overlap -- see module docstring). Detectors:

    * per-hand dominant-axis motion -> still / left|right sweep / up|down
      (run for left, right, and head);
    * two-hand separation -> expansion / contraction;
    * forward-thrust peaks that alternate hands -> alternating punches;
    * sustained rotation of the velocity vector -> circular motion.
    """
    rec = smooth_motion(recording, smooth_window) if smooth else recording
    if len(rec.frames) < 2:
        return []

    times = [f.time_seconds for f in rec.frames]
    velocity = compute_velocity(rec)
    positions = {
        name: [_position(getattr(f, attr)) for f in rec.frames]
        for name, attr in _DEVICES.items()
    }

    segments: list[MovementSegment] = []
    for hand in ("left", "right", "head"):
        segments += _segment_single_hand(
            times, velocity[hand], positions[hand], hand, still_speed, min_duration
        )
    segments += _segment_two_hand(
        times, positions["left"], positions["right"], expansion_rate, min_duration
    )
    segments += _detect_alternating_punches(
        times, velocity, punch_speed, punch_max_gap
    )
    for hand in ("left", "right"):
        segments += _detect_circular(
            times,
            velocity[hand],
            hand,
            still_speed,
            circular_window_seconds,
            circular_min_rotation,
        )

    segments.sort(key=lambda s: (s.start_time, s.end_time))
    return segments


# --------------------------------------------------------------------------- #
# Single-hand dominant-axis segmentation
# --------------------------------------------------------------------------- #
def _segment_single_hand(
    times: list[float],
    velocity: list[Vec3],
    positions: list[Vec3],
    hand: str,
    still_speed: float,
    min_duration: float,
) -> list[MovementSegment]:
    labels: list[Optional[str]] = []
    for vx, vy, vz in velocity:
        speed = math.sqrt(vx * vx + vy * vy + vz * vz)
        if speed < still_speed:
            labels.append(STILL)
            continue
        ax, ay, az = abs(vx), abs(vy), abs(vz)
        if ax >= ay and ax >= az:
            labels.append(LEFT_SWEEP if vx < 0 else RIGHT_SWEEP)
        elif ay >= ax and ay >= az:
            labels.append(UPWARD_LIFT if vy > 0 else DOWNWARD_DROP)
        else:
            # Depth-dominant motion is handled by the punch detector; break runs.
            labels.append(None)

    segments: list[MovementSegment] = []
    for start, end, label in _runs(labels):
        if times[end] - times[start] < min_duration:
            continue
        speeds = [_norm(velocity[i]) for i in range(start, end + 1)]
        mean_speed = sum(speeds) / len(speeds)
        peak_speed = max(speeds)
        disp = _sub(positions[end], positions[start])

        if label == STILL:
            # High confidence when very still relative to the threshold.
            conf = _clip(1.0 - mean_speed / still_speed, 0.0, 1.0)
            dominant_ratio = 0.0
            axis = "none"
        else:
            axis, dominant_ratio = _axis_purity(velocity, start, end, label)
            conf = _clip(dominant_ratio, 0.0, 1.0)

        segments.append(
            MovementSegment(
                start_time=times[start],
                end_time=times[end],
                hand=hand,
                primitive_name=label,
                confidence=conf,
                features={
                    "mean_speed": mean_speed,
                    "peak_speed": peak_speed,
                    "dominant_axis": axis,
                    "dominant_ratio": dominant_ratio,
                    "displacement": list(disp),
                    "frames": end - start + 1,
                },
            )
        )
    return segments


def _axis_purity(
    velocity: list[Vec3], start: int, end: int, label: str
) -> tuple[str, float]:
    """Mean fraction of speed carried by this primitive's axis over the run."""
    axis_index = {
        LEFT_SWEEP: 0,
        RIGHT_SWEEP: 0,
        UPWARD_LIFT: 1,
        DOWNWARD_DROP: 1,
    }[label]
    axis_name = {0: "x", 1: "y", 2: "z"}[axis_index]
    ratios = []
    for i in range(start, end + 1):
        speed = _norm(velocity[i])
        if speed > 1e-9:
            ratios.append(abs(velocity[i][axis_index]) / speed)
    purity = sum(ratios) / len(ratios) if ratios else 0.0
    return axis_name, purity


# --------------------------------------------------------------------------- #
# Two-hand expansion / contraction
# --------------------------------------------------------------------------- #
def _segment_two_hand(
    times: list[float],
    left: list[Vec3],
    right: list[Vec3],
    expansion_rate: float,
    min_duration: float,
) -> list[MovementSegment]:
    separation = [_dist(left[i], right[i]) for i in range(len(times))]
    rate = _scalar_finite_difference(times, separation)

    labels: list[Optional[str]] = []
    for r in rate:
        if r > expansion_rate:
            labels.append(TWO_HAND_EXPANSION)
        elif r < -expansion_rate:
            labels.append(TWO_HAND_CONTRACTION)
        else:
            labels.append(None)

    segments: list[MovementSegment] = []
    for start, end, label in _runs(labels):
        if times[end] - times[start] < min_duration:
            continue
        rates = [abs(rate[i]) for i in range(start, end + 1)]
        mean_rate = sum(rates) / len(rates)
        peak_rate = max(rates)
        conf = _clip(mean_rate / 1.0, 0.0, 1.0)  # ~1 m/s of opening = full confidence
        segments.append(
            MovementSegment(
                start_time=times[start],
                end_time=times[end],
                hand="both",
                primitive_name=label,
                confidence=conf,
                features={
                    "mean_separation_rate": mean_rate,
                    "peak_separation_rate": peak_rate,
                    "start_separation": separation[start],
                    "end_separation": separation[end],
                    "frames": end - start + 1,
                },
            )
        )
    return segments


# --------------------------------------------------------------------------- #
# Alternating punches
# --------------------------------------------------------------------------- #
def _detect_alternating_punches(
    times: list[float],
    velocity: dict[str, list[Vec3]],
    punch_speed: float,
    max_gap: float,
) -> list[MovementSegment]:
    events: list[tuple[float, str, float]] = []  # (time, hand, peak forward speed)
    for hand in ("left", "right"):
        events += _forward_thrust_peaks(times, velocity[hand], hand, punch_speed)
    events.sort(key=lambda e: e[0])

    segments: list[MovementSegment] = []
    i = 0
    n = len(events)
    while i < n:
        group = [events[i]]
        j = i + 1
        while (
            j < n
            and events[j][1] != group[-1][1]  # alternating hand
            and events[j][0] - group[-1][0] <= max_gap
        ):
            group.append(events[j])
            j += 1
        if len(group) >= 2:
            peaks = [g[2] for g in group]
            conf = _clip(0.4 + 0.15 * (len(group) - 2), 0.0, 1.0)
            segments.append(
                MovementSegment(
                    start_time=group[0][0],
                    end_time=group[-1][0],
                    hand="both",
                    primitive_name=ALTERNATING_PUNCHES,
                    confidence=conf,
                    features={
                        "num_punches": len(group),
                        "hand_sequence": [g[1] for g in group],
                        "peak_speeds": peaks,
                        "mean_peak_speed": sum(peaks) / len(peaks),
                    },
                )
            )
            i = j
        else:
            i += 1
    return segments


def _forward_thrust_peaks(
    times: list[float], velocity: list[Vec3], hand: str, punch_speed: float
) -> list[tuple[float, str, float]]:
    """Local maxima of forward (-Z) speed where depth motion dominates."""
    forward = []
    for vx, vy, vz in velocity:
        depth_dominant = abs(vz) >= abs(vx) and abs(vz) >= abs(vy)
        forward.append(-vz if (vz < 0 and depth_dominant) else 0.0)

    peaks: list[tuple[float, str, float]] = []
    for i in range(1, len(forward) - 1):
        if (
            forward[i] >= punch_speed
            and forward[i] >= forward[i - 1]
            and forward[i] > forward[i + 1]
        ):
            peaks.append((times[i], hand, forward[i]))
    return peaks


# --------------------------------------------------------------------------- #
# Circular / wave-like motion
# --------------------------------------------------------------------------- #
def _detect_circular(
    times: list[float],
    velocity: list[Vec3],
    hand: str,
    still_speed: float,
    window_seconds: float,
    min_rotation: float,
) -> list[MovementSegment]:
    """Flag spans where the XY velocity vector rotates steadily (looping motion).

    Computes the frame-to-frame turning angle of the velocity in the X/Y plane and
    looks for windows whose accumulated rotation approaches a full revolution with
    a consistent sign -- the signature of circular (and, loosely, wave-like) hand
    motion.
    """
    n = len(times)
    if n < 3:
        return []
    dt = _median_dt(times)
    win = max(3, int(round(window_seconds / dt)))
    if win >= n:
        win = n - 1

    # Per-step signed turn angle of the XY velocity (0 where too slow to be reliable).
    turn = [0.0] * n
    for i in range(1, n):
        a, b = velocity[i - 1], velocity[i]
        if math.hypot(a[0], a[1]) < still_speed or math.hypot(b[0], b[1]) < still_speed:
            continue
        turn[i] = _signed_angle_xy(a, b)

    flagged = [False] * n
    rotations = [0.0] * n
    for start in range(0, n - win):
        window = turn[start + 1 : start + win + 1]
        total = sum(window)
        if abs(total) < min_rotation:
            continue
        same_sign = sum(1 for t in window if t != 0.0 and _sign(t) == _sign(total))
        nonzero = sum(1 for t in window if t != 0.0)
        if nonzero == 0 or same_sign / nonzero < 0.8:
            continue
        for k in range(start, start + win + 1):
            flagged[k] = True
            rotations[k] = max(rotations[k], abs(total))

    segments: list[MovementSegment] = []
    for s, e, _ in _runs([CIRCULAR_MOTION if f else None for f in flagged]):
        revolutions = max(rotations[s : e + 1]) / (2 * math.pi)
        segments.append(
            MovementSegment(
                start_time=times[s],
                end_time=times[e],
                hand=hand,
                primitive_name=CIRCULAR_MOTION,
                confidence=_clip(revolutions, 0.0, 1.0),
                features={
                    "revolutions": revolutions,
                    "plane": "xy",
                    "frames": e - s + 1,
                },
            )
        )
    return segments


# --------------------------------------------------------------------------- #
# Small numeric helpers (stdlib only)
# --------------------------------------------------------------------------- #
def _position(pose: PoseSample) -> Vec3:
    return (pose.position_x, pose.position_y, pose.position_z)


def _with_position(pose: PoseSample, position: Vec3) -> PoseSample:
    return PoseSample(
        time_seconds=pose.time_seconds,
        position_x=position[0],
        position_y=position[1],
        position_z=position[2],
        rotation_x=pose.rotation_x,
        rotation_y=pose.rotation_y,
        rotation_z=pose.rotation_z,
        rotation_w=pose.rotation_w,
    )


def _finite_difference(times: list[float], points: list[Vec3]) -> list[Vec3]:
    n = len(points)
    if n == 1:
        return [(0.0, 0.0, 0.0)]
    out: list[Vec3] = []
    for i in range(n):
        if i == 0:
            a, b, dt = points[0], points[1], times[1] - times[0]
        elif i == n - 1:
            a, b, dt = points[i - 1], points[i], times[i] - times[i - 1]
        else:
            a, b, dt = points[i - 1], points[i + 1], times[i + 1] - times[i - 1]
        if dt <= 0:
            out.append((0.0, 0.0, 0.0))
        else:
            out.append(((b[0] - a[0]) / dt, (b[1] - a[1]) / dt, (b[2] - a[2]) / dt))
    return out


def _scalar_finite_difference(times: list[float], values: list[float]) -> list[float]:
    n = len(values)
    if n == 1:
        return [0.0]
    out: list[float] = []
    for i in range(n):
        if i == 0:
            a, b, dt = values[0], values[1], times[1] - times[0]
        elif i == n - 1:
            a, b, dt = values[i - 1], values[i], times[i] - times[i - 1]
        else:
            a, b, dt = values[i - 1], values[i + 1], times[i + 1] - times[i - 1]
        out.append((b - a) / dt if dt > 0 else 0.0)
    return out


def _moving_average(points: list[Vec3], half: int) -> list[Vec3]:
    if half == 0:
        return list(points)
    n = len(points)
    out: list[Vec3] = []
    for i in range(n):
        lo, hi = max(0, i - half), min(n, i + half + 1)
        count = hi - lo
        sx = sum(points[k][0] for k in range(lo, hi))
        sy = sum(points[k][1] for k in range(lo, hi))
        sz = sum(points[k][2] for k in range(lo, hi))
        out.append((sx / count, sy / count, sz / count))
    return out


def _runs(labels: list[Optional[str]]):
    """Yield (start, end_inclusive, label) for maximal runs of equal non-None labels."""
    start = 0
    n = len(labels)
    while start < n:
        label = labels[start]
        if label is None:
            start += 1
            continue
        end = start
        while end + 1 < n and labels[end + 1] == label:
            end += 1
        yield start, end, label
        start = end + 1


def _norm(v: Vec3) -> float:
    return math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])


def _sub(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dist(a: Vec3, b: Vec3) -> float:
    return _norm(_sub(a, b))


def _clip(value: float, lo: float, hi: float) -> float:
    return lo if value < lo else hi if value > hi else value


def _sign(value: float) -> int:
    return 1 if value > 0 else -1 if value < 0 else 0


def _median_dt(times: list[float]) -> float:
    diffs = sorted(times[i + 1] - times[i] for i in range(len(times) - 1))
    if not diffs:
        return 1.0
    mid = len(diffs) // 2
    return diffs[mid] if diffs[mid] > 0 else 1.0


def _signed_angle_xy(a: Vec3, b: Vec3) -> float:
    """Signed angle (radians) from vector a to b in the XY plane."""
    cross = a[0] * b[1] - a[1] * b[0]
    dot = a[0] * b[0] + a[1] * b[1]
    return math.atan2(cross, dot)
