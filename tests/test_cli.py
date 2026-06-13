"""Tests for the CLI (``synthcopilot.cli``) and the export seam (``smh_io``)."""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from synthcopilot.cli import build_track_data, main
from synthcopilot.motion import save_motion_recording
from synthcopilot.smh_io import (
    SynthExportUnavailable,
    TrackData,
    write_normalized_json,
    write_synth,
)
from tools.generate_fake_motion import generate_fake_motion_recording


def _run(argv: list[str]) -> int:
    with contextlib.redirect_stdout(io.StringIO()):
        return main(argv)


class BuildTrackDataTests(unittest.TestCase):
    def test_contains_rails_and_notes(self) -> None:
        rec = generate_fake_motion_recording(duration_seconds=15.0)
        track = build_track_data(
            rec, audio="song.mp3", bpm=120, offset=0.0, difficulty="Master"
        )
        self.assertIsInstance(track, TrackData)
        self.assertGreater(len(track.rails), 0)
        self.assertGreater(len(track.notes), 0)
        self.assertEqual(track.audio, "song.mp3")
        self.assertEqual(track.difficulty, "Master")


class SmhIoTests(unittest.TestCase):
    def setUp(self) -> None:
        rec = generate_fake_motion_recording(duration_seconds=10.0)
        self.track = build_track_data(
            rec, audio="song.mp3", bpm=120, offset=0.0, difficulty="Master"
        )

    def test_normalized_json_round_trips(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = write_normalized_json(self.track, Path(tmp) / "map.json")
            self.assertTrue(path.exists())
            data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["coordinate_space"], "normalized")
        self.assertEqual(data["counts"]["rails"], len(self.track.rails))
        self.assertEqual(data["counts"]["notes"], len(self.track.notes))
        self.assertIn("rails", data)
        self.assertIn("notes", data)

    def test_write_synth_unavailable_without_smh(self) -> None:
        # synth_mapping_helper isn't installed here, so a real .synth can't be made.
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SynthExportUnavailable):
                write_synth(self.track, Path(tmp) / "map.synth")


class MotionNewCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        rec = generate_fake_motion_recording(duration_seconds=12.0)
        self.motion_path = Path(self.tmp.name) / "motion.json"
        save_motion_recording(rec, self.motion_path)

    def test_motion_new_exports_rails_and_notes(self) -> None:
        out = Path(self.tmp.name) / "out" / "captured_dance.synth"
        rc = _run([
            "motion-new", "--motion", str(self.motion_path),
            "--audio", "song.mp3", "--bpm", "123", "--output", str(out),
        ])
        self.assertEqual(rc, 0)
        # Real .synth is unavailable -> normalized JSON fallback is written.
        fallback = out.with_suffix(".normalized.json")
        self.assertTrue(fallback.exists())
        data = json.loads(fallback.read_text(encoding="utf-8"))
        self.assertEqual(data["bpm"], 123.0)
        self.assertEqual(data["coordinate_space"], "normalized")
        self.assertGreater(data["counts"]["rails"], 0)
        self.assertGreater(data["counts"]["notes"], 0)
        # Both object kinds are present in the export.
        self.assertTrue(data["rails"] and data["notes"])

    def test_bpm_override(self) -> None:
        out = Path(self.tmp.name) / "map.synth"
        _run(["motion-new", "--motion", str(self.motion_path), "--bpm", "100",
              "--output", str(out)])
        data = json.loads(out.with_suffix(".normalized.json").read_text("utf-8"))
        self.assertEqual(data["bpm"], 100.0)

    def test_new_command_is_deprecated_stub(self) -> None:
        self.assertEqual(_run(["new"]), 2)


if __name__ == "__main__":
    unittest.main()
