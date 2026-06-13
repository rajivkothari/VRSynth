"""Unit tests for the VR motion-capture data model (``synthcopilot.motion``).

Covers round-trip save/load fidelity and the validation rules. Uses stdlib
``unittest`` so it runs with no third-party dependencies (also discoverable by
pytest).
"""

from __future__ import annotations

import math
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from synthcopilot.motion import (
    FORMAT_VERSION,
    FrameSample,
    MotionRecording,
    PoseSample,
    load_motion_recording,
    save_motion_recording,
    validate_motion_recording,
)


def _identity_pose(time_seconds: float, *, with_velocity: bool = False) -> PoseSample:
    """A valid pose at the origin with an identity (unit) rotation."""
    return PoseSample(
        time_seconds=time_seconds,
        position_x=0.1,
        position_y=1.6,
        position_z=-0.3,
        rotation_x=0.0,
        rotation_y=0.0,
        rotation_z=0.0,
        rotation_w=1.0,
        velocity_x=0.5 if with_velocity else None,
        velocity_y=-0.2 if with_velocity else None,
        velocity_z=0.1 if with_velocity else None,
        angular_velocity_x=0.01 if with_velocity else None,
        angular_velocity_y=0.0 if with_velocity else None,
        angular_velocity_z=-0.02 if with_velocity else None,
    )


def _frame(time_seconds: float, *, with_velocity: bool = False) -> FrameSample:
    return FrameSample(
        time_seconds=time_seconds,
        headset=_identity_pose(time_seconds),
        left_controller=_identity_pose(time_seconds, with_velocity=with_velocity),
        right_controller=_identity_pose(time_seconds),
    )


def _recording(num_frames: int = 3, sample_rate: float = 90.0) -> MotionRecording:
    frames = [
        _frame(i / sample_rate, with_velocity=(i % 2 == 0)) for i in range(num_frames)
    ]
    return MotionRecording(
        song_path="songs/example.mp3",
        sample_rate=sample_rate,
        frames=frames,
        bpm=128.0,
        offset=0.042,
        metadata={"performer": "anon", "hmd": "Quest 3", "height_cm": 178},
    )


class RoundTripTests(unittest.TestCase):
    def test_save_then_load_is_equal(self) -> None:
        recording = _recording(num_frames=5)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "take.json"
            save_motion_recording(recording, path)
            self.assertTrue(path.exists())
            loaded = load_motion_recording(path)
        # Dataclasses implement structural equality, so this checks every field
        # including optional velocities and nested poses.
        self.assertEqual(loaded, recording)

    def test_optional_velocity_none_survives_round_trip(self) -> None:
        recording = _recording(num_frames=2)
        # headset pose has no velocity; ensure None is preserved (not dropped/0).
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "take.json"
            save_motion_recording(recording, path)
            loaded = load_motion_recording(path)
        head = loaded.frames[0].headset
        self.assertIsNone(head.velocity_x)
        self.assertIsNone(head.angular_velocity_z)
        self.assertFalse(head.has_velocity)
        left = loaded.frames[0].left_controller
        self.assertTrue(left.has_velocity)
        self.assertTrue(left.has_angular_velocity)

    def test_string_path_is_accepted(self) -> None:
        recording = _recording()
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "take.json")
            save_motion_recording(recording, path)
            loaded = load_motion_recording(path)
        self.assertEqual(loaded, recording)

    def test_save_creates_missing_parent_directories(self) -> None:
        recording = _recording()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "dir" / "take.json"
            save_motion_recording(recording, path)
            self.assertTrue(path.exists())

    def test_serialized_file_records_format_version(self) -> None:
        recording = _recording()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "take.json"
            save_motion_recording(recording, path)
            text = path.read_text(encoding="utf-8")
        self.assertIn(FORMAT_VERSION, text)

    def test_empty_recording_round_trips(self) -> None:
        recording = MotionRecording(song_path="songs/x.mp3", sample_rate=72.0)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "empty.json"
            save_motion_recording(recording, path)
            loaded = load_motion_recording(path)
        self.assertEqual(loaded, recording)
        self.assertEqual(loaded.frames, [])
        self.assertEqual(loaded.duration_seconds, 0.0)

    def test_duration_seconds(self) -> None:
        recording = _recording(num_frames=10, sample_rate=100.0)
        self.assertAlmostEqual(recording.duration_seconds, 9 / 100.0)


class ValidationTests(unittest.TestCase):
    def test_valid_recording_has_no_errors(self) -> None:
        self.assertEqual(validate_motion_recording(_recording()), [])

    def test_non_positive_sample_rate_is_invalid(self) -> None:
        rec = replace(_recording(), sample_rate=0.0)
        errors = validate_motion_recording(rec)
        self.assertTrue(any("sample_rate" in e for e in errors))

    def test_empty_song_path_is_invalid(self) -> None:
        rec = replace(_recording(), song_path="   ")
        errors = validate_motion_recording(rec)
        self.assertTrue(any("song_path" in e for e in errors))

    def test_non_positive_bpm_is_invalid(self) -> None:
        rec = replace(_recording(), bpm=-1.0)
        errors = validate_motion_recording(rec)
        self.assertTrue(any("bpm" in e for e in errors))

    def test_bpm_none_is_allowed(self) -> None:
        rec = replace(_recording(), bpm=None, offset=None)
        self.assertEqual(validate_motion_recording(rec), [])

    def test_negative_frame_time_is_invalid(self) -> None:
        rec = _recording(num_frames=2)
        rec.frames[0] = _frame(-0.5)
        errors = validate_motion_recording(rec)
        self.assertTrue(any("time_seconds" in e for e in errors))

    def test_non_monotonic_frame_times_are_invalid(self) -> None:
        rec = _recording(num_frames=3)
        rec.frames[2] = _frame(0.001)  # earlier than frame 1
        errors = validate_motion_recording(rec)
        self.assertTrue(any("non-decreasing" in e for e in errors))

    def test_non_unit_quaternion_is_invalid(self) -> None:
        rec = _recording(num_frames=1)
        bad = replace(rec.frames[0].headset, rotation_w=5.0)
        rec.frames[0] = replace(rec.frames[0], headset=bad)
        errors = validate_motion_recording(rec)
        self.assertTrue(any("unit-length" in e for e in errors))

    def test_nan_position_is_invalid(self) -> None:
        rec = _recording(num_frames=1)
        bad = replace(rec.frames[0].left_controller, position_x=math.nan)
        rec.frames[0] = replace(rec.frames[0], left_controller=bad)
        errors = validate_motion_recording(rec)
        self.assertTrue(any("position_x" in e for e in errors))

    def test_quaternion_tolerance_is_respected(self) -> None:
        rec = _recording(num_frames=1)
        slightly_off = replace(rec.frames[0].headset, rotation_w=1.0005)
        rec.frames[0] = replace(rec.frames[0], headset=slightly_off)
        # Strict tolerance rejects it; loose tolerance accepts it.
        self.assertTrue(validate_motion_recording(rec, quaternion_tolerance=1e-5))
        self.assertEqual(validate_motion_recording(rec, quaternion_tolerance=1e-2), [])

    def test_loaded_recording_validates(self) -> None:
        rec = _recording(num_frames=4)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "take.json"
            save_motion_recording(rec, path)
            loaded = load_motion_recording(path)
        self.assertEqual(validate_motion_recording(loaded), [])


if __name__ == "__main__":
    unittest.main()
