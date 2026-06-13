"""Tests for the motion analysis report tool (``tools/analyze_motion.py``)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from synthcopilot.motion import MotionRecording, save_motion_recording
from tools.analyze_motion import build_report, format_summary, main
from tools.generate_fake_motion import generate_fake_motion_recording


class BuildReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.recording = generate_fake_motion_recording(duration_seconds=30.0)
        cls.report = build_report(cls.recording, source="fake")

    def test_required_keys_present(self) -> None:
        for key in (
            "duration_seconds", "sample_rate", "frame_count", "hand_speed",
            "segment_count", "primitive_counts", "hand_counts", "timeline",
        ):
            self.assertIn(key, self.report)

    def test_top_level_values(self) -> None:
        self.assertAlmostEqual(self.report["duration_seconds"], 29.9833, places=2)
        self.assertEqual(self.report["sample_rate"], 60.0)
        self.assertEqual(self.report["frame_count"], 1800)
        self.assertEqual(self.report["source"], "fake")

    def test_counts_are_internally_consistent(self) -> None:
        n = self.report["segment_count"]
        self.assertEqual(sum(self.report["primitive_counts"].values()), n)
        self.assertEqual(sum(self.report["hand_counts"].values()), n)
        self.assertEqual(len(self.report["timeline"]), n)

    def test_hand_speed_stats(self) -> None:
        for name in ("left", "right", "combined"):
            stats = self.report["hand_speed"][name]
            self.assertGreater(stats["average"], 0.0)
            self.assertGreaterEqual(stats["peak"], stats["average"])

    def test_timeline_rows_well_formed(self) -> None:
        for row in self.report["timeline"]:
            self.assertLessEqual(row["start_time"], row["end_time"])
            self.assertIn(row["hand"], {"left", "right", "both", "head"})
            self.assertGreaterEqual(row["confidence"], 0.0)
            self.assertLessEqual(row["confidence"], 1.0)
        starts = [r["start_time"] for r in self.report["timeline"]]
        self.assertEqual(starts, sorted(starts))  # timeline is time-ordered

    def test_empty_recording_is_safe(self) -> None:
        report = build_report(MotionRecording(song_path="x.mp3", sample_rate=60.0))
        self.assertEqual(report["segment_count"], 0)
        self.assertEqual(report["frame_count"], 0)
        self.assertEqual(report["timeline"], [])
        self.assertEqual(report["hand_speed"]["combined"], {"average": 0.0, "peak": 0.0})

    def test_format_summary_is_readable_text(self) -> None:
        text = format_summary(self.report)
        self.assertIn("motion analysis report", text)
        self.assertIn("primitive counts", text)
        self.assertIn("timeline", text)
        self.assertIsInstance(text, str)


class MainTests(unittest.TestCase):
    def test_main_writes_valid_json_report(self) -> None:
        recording = generate_fake_motion_recording(duration_seconds=5.0)
        with tempfile.TemporaryDirectory() as tmp:
            in_path = Path(tmp) / "motion.json"
            out_path = Path(tmp) / "out" / "report.json"  # parent created by tool
            save_motion_recording(recording, in_path)
            rc = main(["--input", str(in_path), "--output", str(out_path), "--quiet"])
            self.assertEqual(rc, 0)
            self.assertTrue(out_path.exists())
            loaded = json.loads(out_path.read_text(encoding="utf-8"))
        self.assertEqual(loaded["frame_count"], len(recording.frames))
        self.assertEqual(
            sum(loaded["primitive_counts"].values()), loaded["segment_count"]
        )


if __name__ == "__main__":
    unittest.main()
