"""Bridge: captured motion -> Synth Riders objects (rails + notes).

This is the L5/L6 layer from ``docs/VR_CHOREOGRAPHY_CAPTURE.md``: it turns a
dancer's actual controller paths into **rails** (continuous traced hand paths)
and **notes** (discrete hits) placed in a normalized Synth Riders-style playfield.
It does NOT yet emit walls, and it does NOT write a final ``.synth`` file.

Scope of this pass:

* **Rails** come from per-hand continuous motion (sweeps, lifts/drops, circles).
* **Notes** are *checkpoints on the motion*, not independent beat detections:
  gesture extremes / strong direction changes, punch endpoints, two-hand
  expansion peaks, and beat-aligned points along long sweeps. Notes are filtered
  so they don't fight the rails, stay reachable, keep left/right identity, and
  respect a minimum per-hand spacing.

### Playfield coordinates

Synth Riders places notes/rails on a plane facing the player (X = left..right,
Y = down..up); the approach toward the player is the *time* axis, so depth (Z) is
dropped for placement. We normalize body-relative hand positions into a
``[-1, 1]`` x ``[-1, 1]`` playfield (``NORMALIZED_RANGE`` from
:mod:`synthcopilot.coordinate_systems`).

**All objects this module emits stay in normalized space** -- every
:class:`RailNode` is a normalized point (see :meth:`RailNode.to_normalized_point`).
The exact mapping to real ``.synth`` units is deliberately deferred to the export
step, which is the *only* place a :class:`~synthcopilot.coordinate_systems.\
CoordinateMappingProfile` is applied. Nothing here invents Synth Riders units.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from .coordinate_systems import NORMALIZED_RANGE, NormalizedPoint
from .motion import MotionRecording, PoseSample
from .timing import beat_to_seconds, seconds_to_beat, snap_time_to_grid
from .motion_analysis import (
    CIRCULAR_MOTION,
    DOWNWARD_DROP,
    LEFT_SWEEP,
    RIGHT_SWEEP,
    TWO_HAND_EXPANSION,
    UPWARD_LIFT,
    MovementSegment,
)

__all__ = [
    "RailNode",
    "Rail",
    "Note",
    "PLAYFIELD_X_RANGE",
    "PLAYFIELD_Y_RANGE",
    "DIFFICULTIES",
    "normalize_motion_to_playfield",
    "movement_segment_to_rail",
    "generate_rails_from_motion",
    "generate_notes_from_motion",
]

# Normalized playfield bounds. Single-sourced from the coordinate-system boundary
# layer so motion->map never invents its own (or any real Synth Riders) range.
PLAYFIELD_X_RANGE = NORMALIZED_RANGE
PLAYFIELD_Y_RANGE = NORMALIZED_RANGE

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
               "smooth_nodes": 3, "merge_gap": 0.30, "max_duration": 6.0,
               "snap_subdiv": 2, "note_min_gap": 0.55},
    "Normal": {"node_interval": 0.20, "min_duration": 0.40, "min_extent": 0.40,
               "smooth_nodes": 3, "merge_gap": 0.30, "max_duration": 5.0,
               "snap_subdiv": 4, "note_min_gap": 0.42},
    "Hard":   {"node_interval": 0.16, "min_duration": 0.32, "min_extent": 0.32,
               "smooth_nodes": 3, "merge_gap": 0.25, "max_duration": 4.0,
               "snap_subdiv": 4, "note_min_gap": 0.34},
    "Expert": {"node_interval": 0.13, "min_duration": 0.26, "min_extent": 0.28,
               "smooth_nodes": 1, "merge_gap": 0.22, "max_duration": 3.5,
               "snap_subdiv": 8, "note_min_gap": 0.27},
    "Master": {"node_interval": 0.10, "min_duration": 0.22, "min_extent": 0.25,
               "smooth_nodes": 1, "merge_gap": 0.20, "max_duration": 3.0,
               "snap_subdiv": 8, "note_min_gap": 0.22},
}

# Drop consecutive rail nodes closer than this (normalized) to kill jitter.
_MIN_NODE_SPACING = 0.04

# Beat-snap tolerance as a fraction of one grid cell. Start/end anchors snap to
# the nearest grid line (half a cell => effectively always), while internal nodes
# only snap when already very close, so expressive off-grid motion is preserved.
_ANCHOR_SNAP_FRACTION = 0.5
_INTERNAL_SNAP_FRACTION = 0.15

# --- Note-generation tuning (normalized units unless noted) ---
# Minimum prominence for a position extreme to count as a gesture checkpoint.
_NOTE_EXTREME_AMPLITUDE = 0.15
# How far forward (meters) past the hand's mean depth marks a punch endpoint.
_PUNCH_EXTENSION_M = 0.20
# Notes snap to the nearest grid line within half a cell (clean discrete hits).
_NOTE_SNAP_FRACTION = 0.5
# Reject a later note if reaching it from the previous one for that hand would
# need more than this normalized speed (catches impossible hand jumps).
_MAX_REACH_SPEED = 8.0
# Allow notes within this many seconds of a rail's start/end (endpoints are fine);
# notes strictly inside a rail are suppressed so they don't fight the rail.
_RAIL_EDGE_EPS = 0.05


@dataclass
class RailNode:
    """One point along a rail: a time and a normalized playfield position."""

    time_seconds: float
    x: float
    y: float
    beat: Optional[float] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_normalized_point(self) -> NormalizedPoint:
        """This node's position as a normalized point (depth is the time axis).

        Rails live in normalized space; the export step is responsible for mapping
        the result into ``.synth`` units via a CoordinateMappingProfile.
        """
        return NormalizedPoint(x=self.x, y=self.y, z=0.0)


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


@dataclass
class Note:
    """A discrete hit: a checkpoint on the dancer's motion.

    ``hand`` is ``"left"`` or ``"right"`` (preserved Synth Riders hand identity).
    ``source`` records which checkpoint produced it (gesture extreme, punch,
    expansion, sweep beat) for traceability. Position is normalized playfield.
    """

    time_seconds: float
    hand: str
    x: float
    y: float
    beat: Optional[float] = None
    source: str = ""
    confidence: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_normalized_point(self) -> NormalizedPoint:
        """This note's position as a normalized point (export maps to .synth)."""
        return NormalizedPoint(x=self.x, y=self.y, z=0.0)


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


# --------------------------------------------------------------------------- #
# Recording + segments -> notes (checkpoints on motion)
# --------------------------------------------------------------------------- #
def generate_notes_from_motion(
    recording: MotionRecording,
    segments: list[MovementSegment],
    bpm: Optional[float],
    offset: Optional[float],
    difficulty: str = "Master",
) -> list[Note]:
    """Generate notes as checkpoints on the dancer's motion (not beat detections).

    Per hand, candidate hits are gathered from gesture extremes / strong direction
    changes, punch endpoints, two-hand expansion peaks, and beat-aligned points
    along long sweeps. Candidates are then lightly beat-snapped and filtered:
    notes strictly inside a rail (for the same hand) are dropped so they don't
    fight the rail, a minimum per-hand spacing is enforced (keeping the
    higher-confidence note on conflict), unreachable hand jumps are removed, and
    positions are clamped to the playfield. Left/right identity is preserved.

    Returns notes sorted by time then hand.
    """
    cfg = _difficulty_config(difficulty)
    offset = offset or 0.0
    normalized = normalize_motion_to_playfield(recording)
    times = [t for t, _, _ in normalized["left"]]
    if len(times) < 3:
        return []
    dt = (times[-1] - times[0]) / (len(times) - 1)

    # Rails are generated so notes can avoid fighting them.
    rail_spans: dict[str, list[tuple[float, float]]] = {"left": [], "right": []}
    for rail in generate_rails_from_motion(recording, segments, difficulty):
        rail_spans[rail.hand].append((rail.start_time, rail.end_time))

    notes: list[Note] = []
    for hand in ("left", "right"):
        path = normalized[hand]
        candidates: list[tuple[float, float, float, str, float]] = []
        candidates += _gesture_extreme_candidates(path, dt)
        candidates += _punch_candidates(recording, path, hand)
        candidates += _expansion_candidates(path, segments)
        candidates += _sweep_beat_candidates(path, segments, hand, bpm, offset)
        notes += _finalize_notes(
            candidates, hand, cfg, bpm, offset, rail_spans[hand]
        )

    notes.sort(key=lambda n: (n.time_seconds, n.hand))
    return notes


def _gesture_extreme_candidates(
    path: list[tuple[float, float, float]], dt: float
) -> list[tuple[float, float, float, str, float]]:
    """Windowed local extrema of x and y -> gesture extremes / direction changes."""
    times = [p[0] for p in path]
    xs = _smooth([p[1] for p in path], 3)
    ys = _smooth([p[2] for p in path], 3)
    half = max(2, int(round(0.12 / dt))) if dt > 0 else 2
    out: list[tuple[float, float, float, str, float]] = []
    for axis in (xs, ys):
        for i in range(half, len(axis) - half):
            window = axis[i - half : i + half + 1]
            center = axis[i]
            is_peak = center >= max(window) and window.index(max(window)) == half
            is_valley = center <= min(window) and window.index(min(window)) == half
            if is_peak:
                prominence = center - min(window)
            elif is_valley:
                prominence = max(window) - center
            else:
                continue
            if prominence < _NOTE_EXTREME_AMPLITUDE:
                continue
            conf = _clamp(0.55 + prominence, 0.0, 1.0)
            out.append((times[i], path[i][1], path[i][2], "gesture_extreme", conf))
    return out


def _punch_candidates(
    recording: MotionRecording,
    path: list[tuple[float, float, float]],
    hand: str,
) -> list[tuple[float, float, float, str, float]]:
    """Forward-extension endpoints (deepest -Z) of punches -> high-confidence hits."""
    attr = {"left": "left_controller", "right": "right_controller"}[hand]
    zs = [getattr(f, attr).position_z for f in recording.frames]
    times = [p[0] for p in path]
    n = len(zs)
    if n < 5:
        return []
    mean_z = sum(zs) / n
    half = 3
    out: list[tuple[float, float, float, str, float]] = []
    for i in range(half, n - half):
        window = zs[i - half : i + half + 1]
        # Most-forward point = local minimum of z (forward is -Z).
        if zs[i] <= min(window) and window.index(min(window)) == half:
            if mean_z - zs[i] >= _PUNCH_EXTENSION_M:
                out.append((times[i], path[i][1], path[i][2], "punch", 0.95))
    return out


def _expansion_candidates(
    path: list[tuple[float, float, float]],
    segments: list[MovementSegment],
) -> list[tuple[float, float, float, str, float]]:
    """Two-hand expansion peaks (max separation ~ segment end) -> a hit per hand."""
    out: list[tuple[float, float, float, str, float]] = []
    for seg in segments:
        if seg.hand == "both" and seg.primitive_name == TWO_HAND_EXPANSION:
            t = seg.end_time
            x, y = _interp_xy(path, t)
            out.append((t, x, y, "expansion", _clamp(0.6 + 0.3 * seg.confidence, 0.0, 1.0)))
    return out


def _sweep_beat_candidates(
    path: list[tuple[float, float, float]],
    segments: list[MovementSegment],
    hand: str,
    bpm: Optional[float],
    offset: float,
) -> list[tuple[float, float, float, str, float]]:
    """Beat-aligned points along long sweeps (>= 1 beat) for this hand."""
    if not bpm or bpm <= 0:
        return []
    beat_seconds = 60.0 / bpm
    out: list[tuple[float, float, float, str, float]] = []
    for seg in segments:
        if seg.hand != hand or seg.primitive_name not in (LEFT_SWEEP, RIGHT_SWEEP):
            continue
        if seg.end_time - seg.start_time < beat_seconds:
            continue
        first = math.ceil(seconds_to_beat(seg.start_time, bpm, offset))
        last = math.floor(seconds_to_beat(seg.end_time, bpm, offset))
        for beat in range(int(first), int(last) + 1):
            t = beat_to_seconds(beat, bpm, offset)
            x, y = _interp_xy(path, t)
            out.append((t, x, y, "sweep_beat", 0.6))
    return out


def _finalize_notes(
    candidates: list[tuple[float, float, float, str, float]],
    hand: str,
    cfg: dict[str, float],
    bpm: Optional[float],
    offset: float,
    rail_spans: list[tuple[float, float]],
) -> list[Note]:
    snap_enabled = bool(bpm and bpm > 0)
    cell = 60.0 / (bpm * cfg["snap_subdiv"]) if snap_enabled else 0.0

    # 1. Light beat snapping for clean discrete hits.
    snapped: list[list[float | str]] = []
    for t, x, y, source, conf in candidates:
        if snap_enabled:
            t = snap_time_to_grid(
                t, bpm, offset, int(cfg["snap_subdiv"]),
                tolerance=cell * _NOTE_SNAP_FRACTION,
            )
        snapped.append([t, x, y, source, conf])
    snapped.sort(key=lambda c: c[0])

    # 2. Don't fight rails. Punches (forward strikes) and expansion accents are
    #    distinct hits that don't conflict with a 2D rail trace, so they pass
    #    through. Path-following sources (gesture extremes, beat points along
    #    sweeps) lie *on* the traced rail, so drop those that fall strictly inside
    #    a rail for this hand -- they would clutter/fight the rail.
    def inside_rail(t: float) -> bool:
        return any(s + _RAIL_EDGE_EPS < t < e - _RAIL_EDGE_EPS for s, e in rail_spans)

    _SUPPRESSED_INSIDE_RAILS = ("gesture_extreme", "sweep_beat")
    filtered = [
        c for c in snapped
        if not (c[3] in _SUPPRESSED_INSIDE_RAILS and inside_rail(c[0]))
    ]

    # 3. Minimum per-hand spacing; on conflict keep the higher-confidence note.
    min_gap = cfg["note_min_gap"]
    kept: list[list[float | str]] = []
    for c in filtered:
        if kept and c[0] - kept[-1][0] < min_gap:
            if c[4] > kept[-1][4]:
                kept[-1] = c
            continue
        kept.append(c)

    # 4. Reject impossible hand jumps (too far, too fast).
    reachable: list[list[float | str]] = []
    for c in kept:
        if reachable:
            prev = reachable[-1]
            gap = c[0] - prev[0]
            if gap > 0:
                speed = math.hypot(c[1] - prev[1], c[2] - prev[2]) / gap
                if speed > _MAX_REACH_SPEED:
                    continue
        reachable.append(c)

    # 5. Build Notes (clamp defensively; attach beat).
    notes: list[Note] = []
    for t, x, y, source, conf in reachable:
        beat = seconds_to_beat(t, bpm, offset) if snap_enabled else None
        notes.append(
            Note(
                time_seconds=t,
                hand=hand,
                x=_clamp(x, *PLAYFIELD_X_RANGE),
                y=_clamp(y, *PLAYFIELD_Y_RANGE),
                beat=beat,
                source=source,
                confidence=conf,
            )
        )
    return notes


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
    """Turn (time, x, y) samples into rail nodes with light beat snapping.

    Start/end anchors snap onto the grid (cleaner downbeats), while internal nodes
    snap only within a tight tolerance -- preserving the dancer's expressive
    timing through the body of the rail. Times are kept strictly increasing.
    """
    bpm = recording.bpm
    offset = recording.offset or 0.0
    snap_enabled = bool(bpm and bpm > 0 and snap_subdiv > 0)
    cell = 60.0 / (bpm * snap_subdiv) if snap_enabled else 0.0

    nodes: list[RailNode] = []
    last_time: Optional[float] = None
    n = len(points)
    for i, (t, x, y) in enumerate(points):
        beat: Optional[float] = None
        if snap_enabled:
            is_anchor = i == 0 or i == n - 1
            frac = _ANCHOR_SNAP_FRACTION if is_anchor else _INTERNAL_SNAP_FRACTION
            t = snap_time_to_grid(t, bpm, offset, snap_subdiv, tolerance=cell * frac)
            beat = seconds_to_beat(t, bpm, offset)
        # Keep times strictly increasing (a snapped anchor could otherwise collide).
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
