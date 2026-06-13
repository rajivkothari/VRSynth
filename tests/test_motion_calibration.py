"""Tests for room->canonical calibration (``synthcopilot.motion_calibration``)."""

from __future__ import annotations

import math
import statistics
import tempfile
import unittest
from pathlib import Path

from synthcopilot.motion import (
    FrameSample,
    MotionRecording,
    PoseSample,
    validate_motion_recording,
)
from synthcopilot.motion_calibration import (
    CalibrationProfile,
    apply_calibration,
    estimate_calibration,
    load_calibration_profile,
    save_calibration_profile,
)
from synthcopilot.motion_calibration import _rotate_vec_by_quat  # for assertions
from tools.generate_fake_motion import generate_fake_motion_recording


def _pose(t, pos, quat=(0.0, 0.0, 0.0, 1.0)):
    return PoseSample(
        time_seconds=t,
        position_x=pos[0], position_y=pos[1], position_z=pos[2],
        rotation_x=quat[0], rotation_y=quat[1], rotation_z=quat[2], rotation_w=quat[3],
    )


def _single_frame(left_pos, quat=(0.0, 0.0, 0.0, 1.0)):
    f = FrameSample(
        time_seconds=0.0,
        headset=_pose(0.0, (0.0, 1.6, 0.0), quat),
        left_controller=_pose(0.0, left_pos),
        right_controller=_pose(0.0, (0.3, 1.2, -0.35)),
    )
    return MotionRecording(song_path="x.mp3", sample_rate=60.0, frames=[f])


class EstimateTests(unittest.TestCase):
    def test_recovers_room_center(self) -> None:
        rec = generate_fake_motion_recording(duration_seconds=10.0, origin_offset=(1.5, -0.8))
        prof = estimate_calibration(rec)
        # Head base z is -0.1, so center_z ≈ offset_z + base_z.
        self.assertAlmostEqual(prof.center_x, 1.5, places=2)
        self.assertAlmostEqual(prof.center_z, -0.9, places=2)
        self.assertEqual(prof.floor_y, 0.0)
        self.assertEqual(prof.scale, 1.0)
        self.assertFalse(prof.flip_z)

    def test_forward_axis_from_neutral_facing(self) -> None:
        # Head facing +X: rotate (0,0,-1) -> (1,0,0) is a -90° yaw about +Y.
        a = -math.pi / 2
        q = (0.0, math.sin(a / 2), 0.0, math.cos(a / 2))
        rec = _single_frame((-0.3, 1.2, -0.35), quat=q)
        prof = estimate_calibration(rec)
        self.assertAlmostEqual(prof.forward_axis[0], 1.0, places=3)
        self.assertAlmostEqual(prof.forward_axis[1], 0.0, places=3)

    def test_empty_recording_is_identity_like(self) -> None:
        prof = estimate_calibration(MotionRecording(song_path="x.mp3", sample_rate=60.0))
        self.assertEqual(prof.center_x, 0.0)
        self.assertEqual(apply_calibration(
            MotionRecording(song_path="x.mp3", sample_rate=60.0), prof).frames, [])


class ApplyTests(unittest.TestCase):
    def test_recenters_neutral_stance(self) -> None:
        rec = generate_fake_motion_recording(duration_seconds=10.0, origin_offset=(1.5, -0.8))
        cal = apply_calibration(rec, estimate_calibration(rec))
        neutral = [f for f in cal.frames if f.time_seconds <= 3.0]
        nx = statistics.mean(f.headset.position_x for f in neutral)
        nz = statistics.mean(f.headset.position_z for f in neutral)
        self.assertAlmostEqual(nx, 0.0, places=3)
        self.assertAlmostEqual(nz, 0.0, places=3)

    def test_result_is_valid_and_structure_preserved(self) -> None:
        rec = generate_fake_motion_recording(duration_seconds=6.0, origin_offset=(0.5, 0.5))
        cal = apply_calibration(rec, estimate_calibration(rec))
        self.assertEqual(validate_motion_recording(cal), [])
        self.assertEqual(len(cal.frames), len(rec.frames))
        self.assertEqual(cal.sample_rate, rec.sample_rate)
        self.assertEqual(cal.bpm, rec.bpm)
        self.assertEqual([f.time_seconds for f in cal.frames],
                         [f.time_seconds for f in rec.frames])
        self.assertIn("calibration", cal.metadata)

    def test_forward_alignment_faces_canonical_forward(self) -> None:
        a = -math.pi / 2  # head facing +X
        q = (0.0, math.sin(a / 2), 0.0, math.cos(a / 2))
        rec = _single_frame((-0.3, 1.2, -0.35), quat=q)
        cal = apply_calibration(rec, estimate_calibration(rec))
        head = cal.frames[0].headset
        fwd = _rotate_vec_by_quat(
            (head.rotation_x, head.rotation_y, head.rotation_z, head.rotation_w),
            (0.0, 0.0, -1.0),
        )
        # After calibration the player should face canonical forward (-Z).
        self.assertAlmostEqual(fwd[0], 0.0, places=3)
        self.assertAlmostEqual(fwd[2], -1.0, places=3)

    def test_scale_applied(self) -> None:
        rec = _single_frame((0.4, 1.0, -0.2))
        prof = CalibrationProfile(scale=2.0)  # identity center/forward, no flip
        cal = apply_calibration(rec, prof)
        left = cal.frames[0].left_controller
        self.assertAlmostEqual(left.position_x, 0.8)
        self.assertAlmostEqual(left.position_y, 2.0)
        self.assertAlmostEqual(left.position_z, -0.4)

    def test_flip_z_negates_z(self) -> None:
        rec = _single_frame((0.4, 1.0, -0.2))
        prof = CalibrationProfile(flip_z=True)  # only handedness flip
        cal = apply_calibration(rec, prof)
        left = cal.frames[0].left_controller
        self.assertAlmostEqual(left.position_x, 0.4)
        self.assertAlmostEqual(left.position_z, 0.2)  # negated


class SaveLoadTests(unittest.TestCase):
    def test_round_trip(self) -> None:
        prof = CalibrationProfile(
            floor_y=0.1, center_x=1.2, center_z=-0.3, scale=1.5,
            forward_axis=(0.6, -0.8), flip_z=True, name="test", notes="hi",
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cal.json"
            save_calibration_profile(prof, path)
            loaded = load_calibration_profile(path)
        self.assertEqual(loaded.center_x, 1.2)
        self.assertEqual(loaded.forward_axis, (0.6, -0.8))
        self.assertTrue(loaded.flip_z)
        self.assertEqual(loaded.name, "test")


if __name__ == "__main__":
    unittest.main()
