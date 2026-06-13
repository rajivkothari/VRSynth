"""Tests for the fake-motion fixture generator (``tools/generate_fake_motion.py``).

Verifies the synthesized recording is well-formed: correct frame count,
monotonic timestamps, controller positions inside the Synth Riders playfield
bounds, and a clean save/load round trip.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from synthcopilot.motion import (
    load_motion_recording,
    save_motion_recording,
    validate_motion_recording,
)
from tools.generate_fake_motion import (
    PLAYFIELD_X,
    PLAYFIELD_Y,
    PLAYFIELD_Z,
    generate_fake_motion_recording,
)


class FakeMotionGeneratorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.duration = 30.0
        self.sample_rate = 60.0
        self.recording = generate_fake_motion_recording(
            duration_seconds=self.duration, sample_rate=self.sample_rate
        )

    def test_frame_count_matches_duration_and_rate(self) -> None:
        expected = round(self.duration * self.sample_rate)
        self.assertEqual(len(self.recording.frames), expected)
        self.assertEqual(expected, 1800)  # 30s @ 60 Hz

    def test_metadata_and_song_fields(self) -> None:
        self.assertEqual(self.recording.sample_rate, self.sample_rate)
        self.assertTrue(self.recording.metadata.get("synthetic"))
        self.assertGreater(self.recording.metadata["gestures"]["expansions"], 0)
        self.assertGreater(self.recording.metadata["gestures"]["punches"], 0)

    def test_timestamps_are_monotonic_and_aligned(self) -> None:
        dt = 1.0 / self.sample_rate
        previous = None
        for i, frame in enumerate(self.recording.frames):
            # Frame and its three poses share one timestamp.
            self.assertAlmostEqual(frame.time_seconds, i * dt, places=9)
            for pose in (frame.headset, frame.left_controller, frame.right_controller):
                self.assertAlmostEqual(pose.time_seconds, frame.time_seconds, places=9)
            if previous is not None:
                self.assertGreater(frame.time_seconds, previous)
            previous = frame.time_seconds

    def test_controller_positions_within_playfield_bounds(self) -> None:
        for frame in self.recording.frames:
            for controller in (frame.left_controller, frame.right_controller):
                self.assertGreaterEqual(controller.position_x, PLAYFIELD_X[0])
                self.assertLessEqual(controller.position_x, PLAYFIELD_X[1])
                self.assertGreaterEqual(controller.position_y, PLAYFIELD_Y[0])
                self.assertLessEqual(controller.position_y, PLAYFIELD_Y[1])
                self.assertGreaterEqual(controller.position_z, PLAYFIELD_Z[0])
                self.assertLessEqual(controller.position_z, PLAYFIELD_Z[1])

    def test_left_and_right_actually_move(self) -> None:
        # Sanity: the dance isn't static -- hands sweep across a real range.
        left_x = [f.left_controller.position_x for f in self.recording.frames]
        right_x = [f.right_controller.position_x for f in self.recording.frames]
        self.assertGreater(max(left_x) - min(left_x), 0.2)
        self.assertGreater(max(right_x) - min(right_x), 0.2)

    def test_generated_recording_is_valid(self) -> None:
        self.assertEqual(validate_motion_recording(self.recording), [])

    def test_deterministic_for_a_seed(self) -> None:
        again = generate_fake_motion_recording(
            duration_seconds=self.duration, sample_rate=self.sample_rate
        )
        self.assertEqual(again, self.recording)

    def test_save_load_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fake.json"
            save_motion_recording(self.recording, path)
            loaded = load_motion_recording(path)
        self.assertEqual(loaded, self.recording)


if __name__ == "__main__":
    unittest.main()
