"""Tests for motion analysis (``synthcopilot.motion_analysis``).

Exercises velocity computation, smoothing, and the segment detectors against
both the fake dance recording and small hand-crafted recordings with a known
shape (a held pose, a one-way sweep, a lift, an alternating-punch burst, and a
circle). These verify the analyzer extracts choreography intent -- it does not
test any Synth Riders note output, which this layer intentionally does not do.
"""

from __future__ import annotations

import math
import unittest

from synthcopilot.motion import (
    FrameSample,
    MotionRecording,
    PoseSample,
    validate_motion_recording,
)
from synthcopilot.motion_analysis import (
    ALTERNATING_PUNCHES,
    CIRCULAR_MOTION,
    DOWNWARD_DROP,
    LEFT_SWEEP,
    PRIMITIVES,
    RIGHT_SWEEP,
    STILL,
    TWO_HAND_CONTRACTION,
    TWO_HAND_EXPANSION,
    UPWARD_LIFT,
    compute_velocity,
    segment_motion,
    smooth_motion,
)
from tools.generate_fake_motion import generate_fake_motion_recording

_VALID_HANDS = {"left", "right", "both", "head"}
_NEUTRAL = {  # resting positions for devices we want to hold still
    "head": (0.0, 1.6, -0.10),
    "left": (-0.30, 1.20, -0.35),
    "right": (0.30, 1.20, -0.35),
}


def _pose(t: float, pos: tuple[float, float, float],
          quat: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 1.0)) -> PoseSample:
    return PoseSample(
        time_seconds=t,
        position_x=pos[0], position_y=pos[1], position_z=pos[2],
        rotation_x=quat[0], rotation_y=quat[1], rotation_z=quat[2], rotation_w=quat[3],
    )


def _build(positions, sample_rate: float = 60.0) -> MotionRecording:
    """positions: list of {"head"/"left"/"right": (x,y,z)} dicts (per frame)."""
    frames = []
    for i, p in enumerate(positions):
        t = i / sample_rate
        frames.append(
            FrameSample(
                time_seconds=t,
                headset=_pose(t, p.get("head", _NEUTRAL["head"])),
                left_controller=_pose(t, p.get("left", _NEUTRAL["left"])),
                right_controller=_pose(t, p.get("right", _NEUTRAL["right"])),
            )
        )
    return MotionRecording(song_path="x.mp3", sample_rate=sample_rate, frames=frames)


class ComputeVelocityTests(unittest.TestCase):
    def test_keys_and_lengths(self) -> None:
        rec = generate_fake_motion_recording(duration_seconds=2.0)
        vel = compute_velocity(rec)
        self.assertEqual(set(vel), {"head", "left", "right"})
        for series in vel.values():
            self.assertEqual(len(series), len(rec.frames))

    def test_constant_velocity_is_recovered(self) -> None:
        # Left hand drifts +x at 0.5 m/s; central differences are exact for this.
        sr = 60.0
        positions = [{"left": (-0.3 + 0.5 * (i / sr), 1.2, -0.35)} for i in range(60)]
        vel = compute_velocity(_build(positions, sr))
        interior = vel["left"][1:-1]
        for vx, vy, vz in interior:
            self.assertAlmostEqual(vx, 0.5, places=6)
            self.assertAlmostEqual(vy, 0.0, places=6)
            self.assertAlmostEqual(vz, 0.0, places=6)
        # A held device reads ~zero velocity.
        for vx, vy, vz in vel["right"][1:-1]:
            self.assertAlmostEqual(math.sqrt(vx * vx + vy * vy + vz * vz), 0.0, places=6)

    def test_single_frame_recording(self) -> None:
        rec = _build([{}])
        vel = compute_velocity(rec)
        self.assertEqual(vel["left"], [(0.0, 0.0, 0.0)])


class SmoothMotionTests(unittest.TestCase):
    def test_preserves_structure_and_validity(self) -> None:
        rec = generate_fake_motion_recording(duration_seconds=3.0)
        smoothed = smooth_motion(rec, window=5)
        self.assertEqual(len(smoothed.frames), len(rec.frames))
        self.assertEqual(smoothed.sample_rate, rec.sample_rate)
        for a, b in zip(smoothed.frames, rec.frames):
            self.assertEqual(a.time_seconds, b.time_seconds)
        self.assertEqual(smoothed.metadata["smoothed_window"], 5)
        # Rotations are untouched, so the result is still a valid recording.
        self.assertEqual(validate_motion_recording(smoothed), [])

    def test_reduces_high_frequency_noise(self) -> None:
        sr = 60.0
        # Steady position plus a sawtooth jitter on x.
        positions = [
            {"left": (-0.3 + (0.05 if i % 2 else -0.05), 1.2, -0.35)}
            for i in range(60)
        ]
        rec = _build(positions, sr)
        smoothed = smooth_motion(rec, window=5)

        def roughness(r: MotionRecording) -> float:
            xs = [f.left_controller.position_x for f in r.frames]
            return sum(abs(xs[i + 1] - xs[i]) for i in range(len(xs) - 1))

        self.assertLess(roughness(smoothed), roughness(rec))

    def test_window_one_is_identity_positions(self) -> None:
        rec = generate_fake_motion_recording(duration_seconds=1.0)
        smoothed = smooth_motion(rec, window=1)
        for a, b in zip(smoothed.frames, rec.frames):
            self.assertAlmostEqual(a.left_controller.position_x, b.left_controller.position_x)


class SegmentMotionFakeDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rec = generate_fake_motion_recording(duration_seconds=30.0)
        cls.segments = segment_motion(cls.rec)

    def test_produces_segments(self) -> None:
        self.assertGreater(len(self.segments), 0)

    def test_all_segments_well_formed(self) -> None:
        duration = self.rec.frames[-1].time_seconds
        for s in self.segments:
            self.assertIn(s.hand, _VALID_HANDS)
            self.assertIn(s.primitive_name, PRIMITIVES)
            self.assertGreaterEqual(s.confidence, 0.0)
            self.assertLessEqual(s.confidence, 1.0)
            self.assertLessEqual(s.start_time, s.end_time)
            self.assertGreaterEqual(s.start_time, 0.0)
            self.assertLessEqual(s.end_time, duration + 1e-6)
            self.assertIsInstance(s.features, dict)

    def test_detects_left_and_right_sweeps(self) -> None:
        prims = {s.primitive_name for s in self.segments}
        self.assertIn(LEFT_SWEEP, prims)
        self.assertIn(RIGHT_SWEEP, prims)

    def test_detects_two_hand_expansion_and_contraction(self) -> None:
        both = {s.primitive_name for s in self.segments if s.hand == "both"}
        self.assertIn(TWO_HAND_EXPANSION, both)
        self.assertIn(TWO_HAND_CONTRACTION, both)

    def test_detects_vertical_motion(self) -> None:
        prims = {s.primitive_name for s in self.segments}
        self.assertTrue({UPWARD_LIFT, DOWNWARD_DROP} & prims)

    def test_empty_and_single_frame_are_safe(self) -> None:
        self.assertEqual(segment_motion(_build([])), [])
        self.assertEqual(segment_motion(_build([{}])), [])


class SegmentMotionCraftedTests(unittest.TestCase):
    def test_held_pose_is_still(self) -> None:
        rec = _build([{} for _ in range(120)])  # everything at rest
        segs = segment_motion(rec)
        self.assertEqual({s.primitive_name for s in segs}, {STILL})
        left_still = [s for s in segs if s.hand == "left"]
        self.assertTrue(left_still)
        self.assertGreater(left_still[0].confidence, 0.8)

    def test_one_way_left_sweep(self) -> None:
        sr = 60.0
        positions = [{"left": (-0.3 - 0.5 * (i / sr), 1.2, -0.35)} for i in range(90)]
        segs = segment_motion(_build(positions, sr))
        left = [s for s in segs if s.hand == "left"]
        self.assertTrue(any(s.primitive_name == LEFT_SWEEP for s in left))
        # A purely leftward drift must not be misread as a right sweep.
        self.assertFalse(any(s.primitive_name == RIGHT_SWEEP for s in left))

    def test_upward_lift(self) -> None:
        sr = 60.0
        positions = [{"left": (-0.3, 1.2 + 0.5 * (i / sr), -0.35)} for i in range(90)]
        segs = segment_motion(_build(positions, sr))
        left = [s for s in segs if s.hand == "left"]
        self.assertTrue(any(s.primitive_name == UPWARD_LIFT for s in left))
        self.assertFalse(any(s.primitive_name == DOWNWARD_DROP for s in left))

    def test_alternating_punches(self) -> None:
        sr, n = 60.0, 180
        positions = []
        for i in range(n):
            t = i / sr
            lz = -0.35 + sum(-0.5 * math.exp(-0.5 * ((t - c) / 0.10) ** 2) for c in (0.5, 1.5))
            rz = -0.35 + sum(-0.5 * math.exp(-0.5 * ((t - c) / 0.10) ** 2) for c in (1.0, 2.0))
            positions.append({"left": (-0.3, 1.2, lz), "right": (0.3, 1.2, rz)})
        segs = segment_motion(_build(positions, sr))
        punches = [s for s in segs if s.primitive_name == ALTERNATING_PUNCHES]
        self.assertTrue(punches)
        seq = punches[0].features["hand_sequence"]
        # Hands must actually alternate.
        self.assertTrue(all(seq[i] != seq[i + 1] for i in range(len(seq) - 1)))
        self.assertEqual(punches[0].hand, "both")

    def test_circular_motion(self) -> None:
        sr, n = 60.0, 180
        positions = []
        for i in range(n):
            a = 2 * math.pi * 0.7 * (i / sr)
            positions.append(
                {"left": (-0.3 + 0.2 * math.cos(a), 1.3 + 0.2 * math.sin(a), -0.35)}
            )
        segs = segment_motion(_build(positions, sr))
        circ = [s for s in segs if s.primitive_name == CIRCULAR_MOTION and s.hand == "left"]
        self.assertTrue(circ)
        self.assertGreater(circ[0].features["revolutions"], 0.5)

    def test_back_and_forth_sweep_is_not_circular(self) -> None:
        sr, n = 60.0, 180
        positions = [
            {"left": (-0.3 + 0.25 * math.sin(2 * math.pi * 0.5 * (i / sr)), 1.2, -0.35)}
            for i in range(n)
        ]
        segs = segment_motion(_build(positions, sr))
        self.assertFalse(any(s.primitive_name == CIRCULAR_MOTION for s in segs))


if __name__ == "__main__":
    unittest.main()
