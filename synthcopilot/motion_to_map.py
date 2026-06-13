"""First bridge: captured motion -> Synth Riders objects (rails only).

This is the start of the L5/L6 layer from ``docs/VR_CHOREOGRAPHY_CAPTURE.md``: it
turns a dancer's actual controller paths into **rails** -- continuous traced
hand paths -- placed in a normalized Synth Riders-style playfield. It does NOT
yet emit single notes or walls, and it does NOT write a final ``.synth`` file.

Scope of this first pass:

* Only **rails** are produced (notes and walls are later work).
* Rails come from per-hand continuous motion (sweeps, lifts/drops, circles).
  ``still`` produces nothing and ``alternating_punches`` is left for future note
  generation; two-hand expansion/contraction overlaps the per-hand sweeps, so it
  is not separately railed here.

### Playfield coordinates

Synth Riders places notes/rails on a plane facing the player (X = left..right,
Y = down..up); the approach toward the player is the *time* axis, so depth (Z) is
dropped for placement. We normalize body-relative hand positions into a
``[-1, 1]`` x ``[-1, 1]`` playfield. The exact mapping to real ``.synth`` units is
deliberately deferred to the future ``.synth`` writer -- this module's output is a
clean, unit-normalized intermediate.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from .motion import MotionRecording, PoseSample
from .motion_analysis import (
    CIRCULAR_MOTION,
    DOWNWARD_DROP,
    LEFT_SWEEP,
    RIGHT_SWEEP,
    UPWARD_LIFT,
    MovementSegment,
)

__all__ = [
    "RailNode",
    "Rail",
    "PLAYFIELD_X_RANGE",
    "PLAYFIELD_Y_RANGE",
    "DIFFICULTIES",
    "normalize_motion_to_playfield",
    "movement_segment_to_rail",
    "generate_rails_from_motion",
]

# Normalized Synth Riders-style playfield bounds (see module docstring).
PLAYFIELD_X_RANGE = (-1.0, 1.0)
PLAYFIELD_Y_RANGE = (-1.0, 1.0)

# Body-relative normalization constants.
_ARM_REACH_M = 0.65          # horizontal/vertical reach that maps to a playfield edge
_VERTICAL_ORIGIN_DROP_M = 0.30  # playfield Y=0 sits this far below the headset (≈shoulders)

# Primitives that represent a continuous traced path -> candidate rails.
# (punches -> notes later; still -> nothing; two-hand expand/contract overlaps sweeps.)
_RAIL_PRIMITIVES = frozenset(
    {LEFT_SWEEP, RIGHT_SWEEP, UPWARD_LIFT, DOWNWARD_DROP, CIRCULAR_MOTION}
)

# Per-difficulty knobs: finer/longer rails for higher difficulties.
DIFFICULTIES: dict[str, dict[str, float]] = {
    "Easy":   {"node_interval": 0.25, "min_duration": 0.50, "min_extent": 0.45,
               "smooth_nodes": 3, "merge_gap": 0.30, "max_duration": 6.0, "snap_subdiv": 2},
    "Normal": {"node_interval": 0.20, "min_duration": 0.40, "min_extent": 0.40,
               "smooth_nodes": 3, "merge_gap": 0.30, "max_duration": 5.0, "snap_subdiv": 4},
    "Hard":   {"node_interval": 0.16, "min_duration": 0.32, "min_extent": 0.32,
               "smooth_nodes": 3, "merge_gap": 0.25, "max_duration": 4.0, "snap_subdiv": 4},
    "Expert": {"node_interval": 0.13, "min_duration": 0.26, "min_extent": 0.28,
               "smooth_nodes": 1, "merge_gap": 0.22, "max_duration": 3.5, "snap_subdiv": 8},
    "Master": {"node_interval": 0.10, "min_duration": 0.22, "min_extent": 0.25,
               "smooth_nodes": 1, "merge_gap": 0.20, "max_duration": 3.0, "snap_subdiv": 8},
}

# Drop consecutive rail nodes closer than this (normalized) to kill jitter.
_MIN_NODE_SPACING = 0.04


@dataclass
class RailNode:
    """One point along a rail: a time and a normalized playfield position."""

    time_seconds: float
    x: float
    y: float
    beat: Optional[float] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Rail:
    """A continuous traced hand path, ready to become a Synth Riders rail.

    ``hand`` is ``"left"`` or ``"right"`` (the two Synth Riders hand colors).
    ``primitives`` records which detected movement primitive(s) the rail came
    from, for traceability/debugging.
    """

    hand: str
    difficulty: str
    nodes: list[RailNode]
    confidence: float
    primitives: list[str] = field(default_factory=list)

    @property
    def start_time(self) -> float:
        return self.nodes[0].time_seconds

    @property
    def end_time(self) -> float:
        return self.nodes[-1].time_seconds

    @property
    def duration(self) -> float:
        return self.end_time - self.start_time

    def to_dict(self) -> dict[str, Any]:
        return {
            "hand": self.hand,
            "difficulty": self.difficulty,
            "confidence": self.confidence,
            "primitives": list(self.primitives),
            "start_time": self.start_time,
            "end_time": self.end_time,
            "nodes": [n.to_dict() for n in self.nodes],
        }


# --------------------------------------------------------------------------- #
# Normalization
# --------------------------------------------------------------------------- #
def normalize_motion_to_playfield(
    recording: MotionRecording,
) -> dict[str, list[tuple[float, float, float]]]:
    """Map each device's path into normalized playfield coordinates.

    Returns ``{"head"/"left"/"right": [(time, x, y), ...]}`` where ``x``/``y`` are
    body-relative, scaled by arm reach, and clamped to the playfield. Horizontal
    is taken relative to the headset (so the dance re-centers on the player) and
    vertical relative to a point ~30 cm below the headset (≈ shoulder height).
    """
    out: dict[str, list[tuple[float, float, float]]] = {"head": [], "left": [], "right": []}
    for frame in recording.frames:
        head = frame.headset
        origin_x = head.position_x
        origin_y = head.position_y - _VERTICAL_ORIGIN_DROP_M
        for name, pose in (
            ("head", frame.headset),
            ("left", frame.left_controller),
            ("right", frame.right_controller),
        ):
            x = _clamp((pose.position_x - origin_x) / _ARM_REACH_M, *PLAYFIELD_X_RANGE)
            y = _clamp((pose.position_y - origin_y) / _ARM_REACH_M, *PLAYFIELD_Y_RANGE)
            out[name].append((frame.time_seconds, x, y))
    return out


# --------------------------------------------------------------------------- #
# Single segment -> rail
# --------------------------------------------------------------------------- #
def movement_segment_to_rail(
    segment: MovementSegment,
    recording: MotionRecording,
    hand: str,
    *,
    difficulty: str = "Master",
) -> Optional[Rail]:
    """Build a rail from one movement segment's span of a hand's path.

    Returns ``None`` if the gesture is too short, too small (jitter), or yields
    fewer than two distinct nodes.
    """
    if hand not in ("left", "right"):
        raise ValueError(f"hand must be 'left' or 'right', got {hand!r}")
    cfg = _difficulty_config(difficulty)
    normalized = normalize_motion_to_playfield(recording)
    rail = _path_to_rail(
        normalized[hand], segment.start_time, segment.end_time,
        hand, difficulty, cfg, recording,
        primitives=[segment.primitive_name], confidence=segment.confidence,
    )
    return rail


# --------------------------------------------------------------------------- #
# Recording + segments -> rails
# --------------------------------------------------------------------------- #
def generate_rails_from_motion(
    recording: MotionRecording,
    segments: list[MovementSegment],
    difficulty: str = "Master",
) -> list[Rail]:
    """Produce rails that follow the dancer's actual hand paths.

    Per hand, rail-eligible continuous-motion segments are merged across small
    gaps into expressive spans, then each span is sampled into a smoothed,
    clamped, jitter-filtered rail. Returns rails sorted by start time.
    """
    cfg = _difficulty_config(difficulty)
    normalized = normalize_motion_to_playfield(recording)
    rails: list[Rail] = []

    for hand in ("left", "right"):
        hand_segments = sorted(
            (s for s in segments if s.hand == hand and s.primitive_name in _RAIL_PRIMITIVES),
            key=lambda s: s.start_time,
        )
        for span in _merge_spans(hand_segments, cfg["merge_gap"], cfg["max_duration"]):
            for sub_start, sub_end in _split_span(
                span["start"], span["end"], cfg["max_duration"]
            ):
                rail = _path_to_rail(
                    normalized[hand], sub_start, sub_end,
                    hand, difficulty, cfg, recording,
                    primitives=span["primitives"], confidence=span["confidence"],
                )
                if rail is not None:
                    rails.append(rail)

    rails.sort(key=lambda r: (r.start_time, r.hand))
    return rails


def _merge_spans(
    segments: list[MovementSegment], merge_gap: float, max_duration: float
) -> list[dict[str, Any]]:
    """Merge time-adjacent segments into continuous spans.

    Two segments join when the gap between them is <= ``merge_gap`` *and* the
    resulting span would not exceed ``max_duration`` -- so continuous dancing is
    chopped into expressive but bounded rails rather than one song-long rail.
    """
    spans: list[dict[str, Any]] = []
    for seg in segments:
        if (
            spans
            and seg.start_time - spans[-1]["end"] <= merge_gap
            and seg.end_time - spans[-1]["start"] <= max_duration
        ):
            cur = spans[-1]
            cur["end"] = max(cur["end"], seg.end_time)
            cur["primitives"].append(seg.primitive_name)
            cur["confidences"].append(seg.confidence)
        else:
            spans.append({
                "start": seg.start_time,
                "end": seg.end_time,
                "primitives": [seg.primitive_name],
                "confidences": [seg.confidence],
            })
    for span in spans:
        confs = span.pop("confidences")
        span["confidence"] = sum(confs) / len(confs)
        # de-duplicate primitive names while preserving order
        seen: list[str] = []
        for p in span["primitives"]:
            if p not in seen:
                seen.append(p)
        span["primitives"] = seen
    return spans


# --------------------------------------------------------------------------- #
# Core path -> rail builder
# --------------------------------------------------------------------------- #
def _path_to_rail(
    path: list[tuple[float, float, float]],
    start_time: float,
    end_time: float,
    hand: str,
    difficulty: str,
    cfg: dict[str, float],
    recording: MotionRecording,
    *,
    primitives: list[str],
    confidence: float,
) -> Optional[Rail]:
    if end_time - start_time < cfg["min_duration"]:
        return None

    # 1. Resample the path at a fixed node interval (linear interpolation).
    interval = cfg["node_interval"]
    targets: list[float] = []
    t = start_time
    while t < end_time - 1e-9:
        targets.append(t)
        t += interval
    targets.append(end_time)
    sampled = [(_qt, *_interp_xy(path, _qt)) for _qt in targets]

    # 2. Smooth node positions (small moving average), then re-clamp.
    xs = _smooth([p[1] for p in sampled], int(cfg["smooth_nodes"]))
    ys = _smooth([p[2] for p in sampled], int(cfg["smooth_nodes"]))
    points = [
        (
            sampled[i][0],
            _clamp(xs[i], *PLAYFIELD_X_RANGE),
            _clamp(ys[i], *PLAYFIELD_Y_RANGE),
        )
        for i in range(len(sampled))
    ]

    # 3. Drop jitter: remove consecutive near-duplicate nodes.
    deduped = [points[0]]
    for p in points[1:]:
        if math.hypot(p[1] - deduped[-1][1], p[2] - deduped[-1][2]) >= _MIN_NODE_SPACING:
            deduped.append(p)
    # Always represent the true endpoint: append it if the hand moved since the
    # last kept node, otherwise just carry the endpoint's (later) time.
    end = points[-1]
    if math.hypot(end[1] - deduped[-1][1], end[2] - deduped[-1][2]) >= 1e-6:
        deduped.append(end)
    elif end[0] > deduped[-1][0]:
        deduped[-1] = end

    # 4. Reject tiny/jitter rails by overall extent.
    if len(deduped) < 2 or _extent(deduped) < cfg["min_extent"]:
        return None

    # 5. Optional beat snapping; build nodes.
    nodes = _build_nodes(deduped, recording, int(cfg["snap_subdiv"]))
    if len(nodes) < 2:
        return None

    return Rail(
        hand=hand,
        difficulty=difficulty,
        nodes=nodes,
        confidence=confidence,
        primitives=primitives,
    )


def _build_nodes(
    points: list[tuple[float, float, float]],
    recording: MotionRecording,
    snap_subdiv: int,
) -> list[RailNode]:
    bpm = recording.bpm
    offset = recording.offset or 0.0
    nodes: list[RailNode] = []
    last_time: Optional[float] = None
    for t, x, y in points:
        beat: Optional[float] = None
        if bpm and bpm > 0:
            raw_beat = (t - offset) * bpm / 60.0
            if snap_subdiv > 0:
                snapped_beat = round(raw_beat * snap_subdiv) / snap_subdiv
            else:
                snapped_beat = raw_beat
            beat = snapped_beat
            t = snapped_beat * 60.0 / bpm + offset
        # Keep times strictly increasing after snapping.
        if last_time is not None and t <= last_time:
            continue
        last_time = t
        nodes.append(RailNode(time_seconds=t, x=x, y=y, beat=beat))
    return nodes


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _split_span(start: float, end: float, max_duration: float) -> list[tuple[float, float]]:
    """Split a span into roughly equal chunks no longer than ``max_duration``."""
    total = end - start
    if total <= max_duration or max_duration <= 0:
        return [(start, end)]
    chunks = math.ceil(total / max_duration)
    width = total / chunks
    return [(start + i * width, start + (i + 1) * width) for i in range(chunks)]


def _difficulty_config(difficulty: str) -> dict[str, float]:
    if difficulty not in DIFFICULTIES:
        raise ValueError(
            f"unknown difficulty {difficulty!r}; choose from {sorted(DIFFICULTIES)}"
        )
    return DIFFICULTIES[difficulty]


def _interp_xy(path: list[tuple[float, float, float]], qt: float) -> tuple[float, float]:
    """Linear-interpolate (x, y) at time ``qt`` along a time-sorted path."""
    if not path:
        return (0.0, 0.0)
    if qt <= path[0][0]:
        return (path[0][1], path[0][2])
    if qt >= path[-1][0]:
        return (path[-1][1], path[-1][2])
    # Linear scan (paths/spans are short); find bracketing samples.
    for i in range(1, len(path)):
        t1, x1, y1 = path[i]
        if t1 >= qt:
            t0, x0, y0 = path[i - 1]
            span = t1 - t0
            f = 0.0 if span <= 0 else (qt - t0) / span
            return (x0 + f * (x1 - x0), y0 + f * (y1 - y0))
    return (path[-1][1], path[-1][2])


def _smooth(values: list[float], window: int) -> list[float]:
    if window <= 1 or len(values) <= 2:
        return list(values)
    half = window // 2
    n = len(values)
    out: list[float] = []
    for i in range(n):
        lo, hi = max(0, i - half), min(n, i + half + 1)
        out.append(sum(values[lo:hi]) / (hi - lo))
    return out


def _extent(points: list[tuple[float, float, float]]) -> float:
    xs = [p[1] for p in points]
    ys = [p[2] for p in points]
    return math.hypot(max(xs) - min(xs), max(ys) - min(ys))


def _clamp(value: float, lo: float, hi: float) -> float:
    return lo if value < lo else hi if value > hi else value
