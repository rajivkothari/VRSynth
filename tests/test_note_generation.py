"""Tests for note generation (``generate_notes_from_motion``).

Notes are checkpoints on motion, so these assert the *rules* -- hand identity,
bounds, beat presence, minimum per-hand spacing, reachability, and not fighting
rails -- plus that real checkpoints (punches, expansions) are detected.
"""

from __future__ import annotations

import math
import unittest

import synthcopilot.motion_to_map as mtm
from synthcopilot.motion import FrameSample, MotionRecording, PoseSample
from synthcopilot.motion_analysis import segment_motion
from synthcopilot.motion_to_map import (
    DIFFICULTIES,
    Note,
    generate_notes_from_motion,
    generate_rails_from_motion,
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


class NoteRulesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rec = generate_fake_motion_recording(duration_seconds=30.0)
        cls.segments = segment_motion(cls.rec)
        cls.notes = generate_notes_from_motion(
            cls.rec, cls.segments, cls.rec.bpm, cls.rec.offset, "Master"
        )

    def test_produces_notes(self) -> None:
        self.assertGreater(len(self.notes), 0)

    def test_left_right_identity_and_bounds(self) -> None:
        for note in self.notes:
            self.assertIn(note.hand, ("left", "right"))
            self.assertGreaterEqual(note.x, -1.0 - 1e-9)
            self.assertLessEqual(note.x, 1.0 + 1e-9)
            self.assertGreaterEqual(note.y, -1.0 - 1e-9)
            self.assertLessEqual(note.y, 1.0 + 1e-9)

    def test_notes_sorted_by_time(self) -> None:
        times = [n.time_seconds for n in self.notes]
        self.assertEqual(times, sorted(times))

    def test_minimum_per_hand_spacing(self) -> None:
        min_gap = DIFFICULTIES["Master"]["note_min_gap"]
        for hand in ("left", "right"):
            hand_times = sorted(n.time_seconds for n in self.notes if n.hand == hand)
            for a, b in zip(hand_times, hand_times[1:]):
                self.assertGreaterEqual(b - a, min_gap - 1e-9)

    def test_reachability_no_impossible_jumps(self) -> None:
        for hand in ("left", "right"):
            seq = sorted(
                ((n.time_seconds, n.x, n.y) for n in self.notes if n.hand == hand)
            )
            for (t0, x0, y0), (t1, x1, y1) in zip(seq, seq[1:]):
                gap = t1 - t0
                if gap > 0:
                    speed = math.hypot(x1 - x0, y1 - y0) / gap
                    self.assertLessEqual(speed, mtm._MAX_REACH_SPEED + 1e-6)

    def test_path_following_notes_do_not_fight_rails(self) -> None:
        rails = generate_rails_from_motion(self.rec, self.segments, "Master")
        spans = {"left": [], "right": []}
        for rail in rails:
            spans[rail.hand].append((rail.start_time, rail.end_time))
        for note in self.notes:
            if note.source in ("gesture_extreme", "sweep_beat"):
                for s, e in spans[note.hand]:
                    self.assertFalse(
                        s + mtm._RAIL_EDGE_EPS < note.time_seconds < e - mtm._RAIL_EDGE_EPS,
                        f"{note.source} note at {note.time_seconds} is inside a rail",
                    )

    def test_punches_detected(self) -> None:
        sources = {n.source for n in self.notes}
        self.assertIn("punch", sources)
        # The fake dance throws 8 punches; most should survive as notes.
        punch_count = sum(1 for n in self.notes if n.source == "punch")
        self.assertGreaterEqual(punch_count, 4)

    def test_beats_present_with_bpm(self) -> None:
        self.assertTrue(all(n.beat is not None for n in self.notes))

    def test_no_beats_without_bpm(self) -> None:
        notes = generate_notes_from_motion(self.rec, self.segments, None, None, "Master")
        self.assertTrue(notes)
        self.assertTrue(all(n.beat is None for n in notes))

    def test_difficulty_spacing_respected(self) -> None:
        for difficulty, cfg in DIFFICULTIES.items():
            notes = generate_notes_from_motion(
                self.rec, self.segments, self.rec.bpm, self.rec.offset, difficulty
            )
            for hand in ("left", "right"):
                ts = sorted(n.time_seconds for n in notes if n.hand == hand)
                for a, b in zip(ts, ts[1:]):
                    self.assertGreaterEqual(b - a, cfg["note_min_gap"] - 1e-9)


class NoteCraftedTests(unittest.TestCase):
    def test_punch_endpoint_detected_for_correct_hand(self) -> None:
        sr, n = 60.0, 180
        positions = []
        for i in range(n):
            t = i / sr
            # Right hand thrusts forward (-z) around t=1.0 and t=2.0.
            rz = -0.35 + sum(
                -0.5 * math.exp(-0.5 * ((t - c) / 0.10) ** 2) for c in (1.0, 2.0)
            )
            positions.append({"right": (0.3, 1.2, rz)})
        rec = _build(positions, sr, bpm=120, offset=0.0)
        notes = generate_notes_from_motion(rec, segment_motion(rec), 120, 0.0, "Master")
        punches = [n for n in notes if n.source == "punch"]
        self.assertTrue(punches)
        self.assertTrue(all(p.hand == "right" for p in punches))

    def test_empty_and_short_recordings_are_safe(self) -> None:
        self.assertEqual(generate_notes_from_motion(_build([]), [], 120, 0.0), [])
        self.assertEqual(generate_notes_from_motion(_build([{}]), [], 120, 0.0), [])

    def test_returns_note_objects(self) -> None:
        rec = generate_fake_motion_recording(duration_seconds=8.0)
        notes = generate_notes_from_motion(rec, segment_motion(rec), rec.bpm, 0.0)
        self.assertTrue(all(isinstance(n, Note) for n in notes))
        self.assertIn("hand", notes[0].to_dict())


if __name__ == "__main__":
    unittest.main()
