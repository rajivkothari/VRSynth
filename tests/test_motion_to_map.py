"""Tests for the motion->rails bridge (``synthcopilot.motion_to_map``).

Verifies playfield normalization, single-segment rail building, and full rail
generation from the fake recording. This layer outputs rails only -- no notes or
walls -- so the tests assert rail structure, bounds, timing, and jitter handling,
not any Synth Riders note output.
"""

from __future__ import annotations

import math
import unittest

from synthcopilot.motion import FrameSample, MotionRecording, PoseSample
from synthcopilot.motion_analysis import MovementSegment, segment_motion
from synthcopilot.motion_to_map import (
    DIFFICULTIES,
    PLAYFIELD_X_RANGE,
    PLAYFIELD_Y_RANGE,
    Rail,
    generate_rails_from_motion,
    movement_segment_to_rail,
    normalize_motion_to_playfield,
)
from tools.generate_fake_motion import generate_fake_motion_recording


def _pose(t, pos):
    return PoseSample(
        time_seconds=t,
        position_x=pos[0], position_y=pos[1], position_z=pos[2],
        rotation_x=0.0, rotation_y=0.0, rotation_z=0.0, rotation_w=1.0,
    )


def _build(positions, sr=60.0, bpm=None, offset=None):
    frames = [
        FrameSample(
            time_seconds=i / sr,
            headset=_pose(i / sr, p.get("head", (0.0, 1.6, -0.1))),
            left_controller=_pose(i / sr, p.get("left", (-0.3, 1.2, -0.35))),
            right_controller=_pose(i / sr, p.get("right", (0.3, 1.2, -0.35))),
        )
        for i, p in enumerate(positions)
    ]
    return MotionRecording(
        song_path="x.mp3", sample_rate=sr, frames=frames, bpm=bpm, offset=offset
    )


def _in_bounds(node) -> bool:
    return (
        PLAYFIELD_X_RANGE[0] - 1e-9 <= node.x <= PLAYFIELD_X_RANGE[1] + 1e-9
        and PLAYFIELD_Y_RANGE[0] - 1e-9 <= node.y <= PLAYFIELD_Y_RANGE[1] + 1e-9
    )


class NormalizeTests(unittest.TestCase):
    def test_keys_and_alignment(self) -> None:
        rec = generate_fake_motion_recording(duration_seconds=2.0)
        norm = normalize_motion_to_playfield(rec)
        self.assertEqual(set(norm), {"head", "left", "right"})
        for series in norm.values():
            self.assertEqual(len(series), len(rec.frames))

    def test_values_within_playfield(self) -> None:
        rec = generate_fake_motion_recording(duration_seconds=30.0)
        norm = normalize_motion_to_playfield(rec)
        for hand in ("left", "right", "head"):
            for _t, x, y in norm[hand]:
                self.assertGreaterEqual(x, PLAYFIELD_X_RANGE[0] - 1e-9)
                self.assertLessEqual(x, PLAYFIELD_X_RANGE[1] + 1e-9)
                self.assertGreaterEqual(y, PLAYFIELD_Y_RANGE[0] - 1e-9)
                self.assertLessEqual(y, PLAYFIELD_Y_RANGE[1] + 1e-9)

    def test_head_is_horizontal_origin(self) -> None:
        # The head defines the horizontal origin, so its normalized x is ~0.
        rec = generate_fake_motion_recording(duration_seconds=2.0)
        norm = normalize_motion_to_playfield(rec)
        self.assertTrue(all(abs(x) < 1e-9 for _t, x, _y in norm["head"]))

    def test_left_maps_left_right_maps_right(self) -> None:
        rec = generate_fake_motion_recording(duration_seconds=4.0)
        norm = normalize_motion_to_playfield(rec)
        mean_left = sum(x for _t, x, _y in norm["left"]) / len(norm["left"])
        mean_right = sum(x for _t, x, _y in norm["right"]) / len(norm["right"])
        self.assertLess(mean_left, 0.0)
        self.assertGreater(mean_right, 0.0)


class SingleSegmentRailTests(unittest.TestCase):
    def test_sweep_segment_becomes_rail(self) -> None:
        sr = 60.0
        positions = [{"left": (-0.2 - 0.5 * (i / sr), 1.2, -0.35)} for i in range(90)]
        rec = _build(positions, sr)
        seg = MovementSegment(
            start_time=0.1, end_time=1.4, hand="left",
            primitive_name="left_sweep", confidence=0.9, features={},
        )
        rail = movement_segment_to_rail(seg, rec, "left")
        self.assertIsInstance(rail, Rail)
        self.assertEqual(rail.hand, "left")
        self.assertGreaterEqual(len(rail.nodes), 2)
        self.assertIn("left_sweep", rail.primitives)
        for node in rail.nodes:
            self.assertTrue(_in_bounds(node))

    def test_tiny_jitter_segment_is_rejected(self) -> None:
        sr = 60.0
        # Hand essentially still with sub-millimeter wobble -> no rail.
        positions = [
            {"left": (-0.3 + 0.001 * math.sin(i), 1.2, -0.35)} for i in range(120)
        ]
        rec = _build(positions, sr)
        seg = MovementSegment(
            start_time=0.0, end_time=1.9, hand="left",
            primitive_name="left_sweep", confidence=0.5, features={},
        )
        self.assertIsNone(movement_segment_to_rail(seg, rec, "left"))

    def test_too_short_segment_is_rejected(self) -> None:
        rec = generate_fake_motion_recording(duration_seconds=2.0)
        seg = MovementSegment(
            start_time=0.0, end_time=0.05, hand="left",
            primitive_name="left_sweep", confidence=0.9, features={},
        )
        self.assertIsNone(movement_segment_to_rail(seg, rec, "left"))

    def test_invalid_hand_raises(self) -> None:
        rec = generate_fake_motion_recording(duration_seconds=1.0)
        seg = MovementSegment(0.0, 0.5, "head", "left_sweep", 0.9, {})
        with self.assertRaises(ValueError):
            movement_segment_to_rail(seg, rec, "head")


class GenerateRailsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rec = generate_fake_motion_recording(duration_seconds=30.0)
        cls.segments = segment_motion(cls.rec)
        cls.rails = generate_rails_from_motion(cls.rec, cls.segments, "Master")

    def test_produces_rails(self) -> None:
        self.assertGreater(len(self.rails), 0)

    def test_only_left_right_hands(self) -> None:
        self.assertTrue(all(r.hand in ("left", "right") for r in self.rails))

    def test_rails_sorted_by_start_time(self) -> None:
        starts = [r.start_time for r in self.rails]
        self.assertEqual(starts, sorted(starts))

    def test_nodes_in_bounds_and_time_monotonic(self) -> None:
        for rail in self.rails:
            self.assertGreaterEqual(len(rail.nodes), 2)
            times = [n.time_seconds for n in rail.nodes]
            self.assertEqual(times, sorted(times))
            self.assertEqual(len(times), len(set(times)))  # strictly increasing
            for node in rail.nodes:
                self.assertTrue(_in_bounds(node))

    def test_rails_respect_difficulty_duration_cap(self) -> None:
        cap = DIFFICULTIES["Master"]["max_duration"]
        for rail in self.rails:
            self.assertLessEqual(rail.duration, cap + 1e-6)

    def test_beats_present_when_bpm_known(self) -> None:
        # The fake recording has bpm set, so nodes should carry beat positions.
        self.assertIsNotNone(self.rec.bpm)
        for rail in self.rails:
            for node in rail.nodes:
                self.assertIsNotNone(node.beat)

    def test_no_beats_when_bpm_absent(self) -> None:
        rec = generate_fake_motion_recording(duration_seconds=10.0)
        rec.bpm = None
        rails = generate_rails_from_motion(rec, segment_motion(rec), "Master")
        self.assertTrue(rails)
        for rail in rails:
            self.assertTrue(all(n.beat is None for n in rail.nodes))

    def test_higher_difficulty_yields_more_rails(self) -> None:
        easy = generate_rails_from_motion(self.rec, self.segments, "Easy")
        master = generate_rails_from_motion(self.rec, self.segments, "Master")
        self.assertGreater(len(master), len(easy))

    def test_unknown_difficulty_raises(self) -> None:
        with self.assertRaises(ValueError):
            generate_rails_from_motion(self.rec, self.segments, "Nightmare")

    def test_to_dict_is_serializable(self) -> None:
        import json

        payload = [r.to_dict() for r in self.rails]
        text = json.dumps(payload)  # must not raise
        self.assertIn("nodes", text)
        first = payload[0]
        self.assertEqual(
            set(first),
            {"hand", "difficulty", "confidence", "primitives", "start_time",
             "end_time", "nodes"},
        )

    def test_empty_segments_yield_no_rails(self) -> None:
        self.assertEqual(generate_rails_from_motion(self.rec, [], "Master"), [])


if __name__ == "__main__":
    unittest.main()
