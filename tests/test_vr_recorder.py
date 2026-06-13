"""Tests for the experimental VR recorder prototype (``vr_recorder``).

Exercises the recording loop with the synthetic backend (the only one that runs
without hardware) and confirms the output is a valid MotionRecording. Also checks
that the hardware backends fail honestly rather than pretending to capture.
"""

from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
import warnings
from pathlib import Path

from synthcopilot.motion import load_motion_recording, validate_motion_recording
from vr_recorder.python_recorder import (
    RecorderBackendUnavailable,
    SyntheticPoseSource,
    make_pose_source,
    record_session,
)
from vr_recorder.python_recorder.__main__ import main


def _record(**kwargs):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return record_session(SyntheticPoseSource(), **kwargs)


class RecordingLoopTests(unittest.TestCase):
    def test_produces_valid_motion_recording(self) -> None:
        rec = _record(
            song_path="song.mp3", duration_seconds=4.0, sample_rate=72.0,
            bpm=123.0, offset=0.0,
        )
        self.assertEqual(validate_motion_recording(rec), [])

    def test_frame_count_and_fields(self) -> None:
        rec = _record(song_path="s.mp3", duration_seconds=5.0, sample_rate=60.0)
        self.assertEqual(len(rec.frames), 300)
        self.assertEqual(rec.sample_rate, 60.0)
        self.assertEqual(rec.song_path, "s.mp3")
        self.assertTrue(rec.metadata["synthetic"])
        self.assertEqual(rec.metadata["backend"], "synthetic")

    def test_timestamps_monotonic_and_aligned(self) -> None:
        rec = _record(song_path="s.mp3", duration_seconds=2.0, sample_rate=72.0)
        dt = 1.0 / 72.0
        prev = None
        for i, frame in enumerate(rec.frames):
            self.assertAlmostEqual(frame.time_seconds, i * dt, places=9)
            self.assertAlmostEqual(frame.headset.time_seconds, frame.time_seconds)
            if prev is not None:
                self.assertGreater(frame.time_seconds, prev)
            prev = frame.time_seconds

    def test_bpm_offset_propagated(self) -> None:
        rec = _record(song_path="s.mp3", duration_seconds=1.0, sample_rate=60.0,
                      bpm=128.0, offset=0.05)
        self.assertEqual(rec.bpm, 128.0)
        self.assertEqual(rec.offset, 0.05)

    def test_invalid_sample_rate_raises(self) -> None:
        with self.assertRaises(ValueError):
            _record(song_path="s.mp3", duration_seconds=1.0, sample_rate=0.0)


class BackendTests(unittest.TestCase):
    def test_synthetic_backend_is_marked_synthetic(self) -> None:
        source = make_pose_source("synthetic")
        self.assertTrue(source.synthetic)
        self.assertEqual(source.name, "synthetic")

    def test_hardware_backends_unavailable_without_bindings(self) -> None:
        # openvr / pyopenxr are not installed here; they must fail loudly.
        for name in ("openvr", "openxr"):
            with self.assertRaises((RecorderBackendUnavailable, NotImplementedError)):
                make_pose_source(name)

    def test_unknown_backend_raises(self) -> None:
        with self.assertRaises(ValueError):
            make_pose_source("nope")


class CliTests(unittest.TestCase):
    def test_cli_synthetic_writes_valid_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "session.json"
            with warnings.catch_warnings(), contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                warnings.simplefilter("ignore")
                rc = main([
                    "--backend", "synthetic", "--song", "song.mp3",
                    "--duration", "2", "--sample-rate", "60", "--no-audio",
                    "--output", str(out),
                ])
            self.assertEqual(rc, 0)
            self.assertTrue(out.exists())
            rec = load_motion_recording(out)
        self.assertEqual(len(rec.frames), 120)
        self.assertEqual(validate_motion_recording(rec), [])

    def test_cli_hardware_backend_returns_error_code(self) -> None:
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            rc = main(["--backend", "openvr", "--duration", "1"])
        self.assertEqual(rc, 3)


if __name__ == "__main__":
    unittest.main()
