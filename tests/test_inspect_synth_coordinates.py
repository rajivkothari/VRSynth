"""Tests for the .synth coordinate inspection harness.

No real editor .synth fixtures are available, so these build synthetic ``.synth``
files in the genuine editor format (a ZIP whose ``beatmap.meta.bin`` member is
JSON) and verify the measurements, per-hand ranges, Z classification, and the
derived (unverified) recommended profile -- plus that bad inputs fail clearly.
"""

from __future__ import annotations

import codecs
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from tools.inspect_synth_coordinates import (
    SynthReadError,
    analyze,
    build_report,
    classify_z,
    iter_objects,
    main,
    read_synth_beatmap,
    recommend_profile,
)

_NOTE_TYPE = {"right": 0, "left": 1, "single": 2, "both": 3}


def _write_synth(path: Path, notes: list[dict], bpm: float = 120.0) -> Path:
    """notes: [{tick, hand, pos:[x,y,z], segments:[[x,y,z],...] | None}]"""
    track = {d: {} for d in ("Easy", "Normal", "Hard", "Expert", "Master", "Custom")}
    for n in notes:
        entry = {
            "Position": n["pos"],
            "Segments": n.get("segments"),
            "Type": _NOTE_TYPE[n["hand"]],
        }
        track["Master"].setdefault(str(n["tick"]), []).append(entry)
    beatmap = {"BPM": bpm, "AudioName": "song.ogg", "Track": track,
               "Slides": {d: [] for d in track}}
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("beatmap.meta.bin", codecs.BOM_UTF8 + json.dumps(beatmap).encode("utf-8"))
        zf.writestr("song.ogg", b"FAKEAUDIO")
    return path


class ReadAndExtractTests(unittest.TestCase):
    def test_counts_notes_rails_and_nodes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_synth(Path(tmp) / "m.synth", [
                {"tick": 0, "hand": "right", "pos": [0.5, 0.3, 0.0]},
                {"tick": 16, "hand": "left", "pos": [-0.5, 0.4, 1.0]},
                {"tick": 32, "hand": "right", "pos": [0.6, 0.2, 2.0],
                 "segments": [[0.65, 0.25, 2.1], [0.7, 0.3, 2.2]]},  # rail w/ 3 nodes
            ])
            beatmap = read_synth_beatmap(path)
            records = list(iter_objects(beatmap, str(path)))
        stats = analyze(records)
        self.assertEqual(stats["total_notes"], 2)
        self.assertEqual(stats["total_rails"], 1)
        self.assertEqual(stats["total_rail_nodes"], 3)
        self.assertEqual(stats["total_nodes"], 5)

    def test_axis_stats_exact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_synth(Path(tmp) / "m.synth", [
                {"tick": 0, "hand": "right", "pos": [-1.0, 0.0, 0.0]},
                {"tick": 16, "hand": "left", "pos": [3.0, 2.0, 10.0]},
            ])
            records = list(iter_objects(read_synth_beatmap(path), str(path)))
        stats = analyze(records)
        self.assertEqual(stats["x"]["min"], -1.0)
        self.assertEqual(stats["x"]["max"], 3.0)
        self.assertEqual(stats["x"]["mean"], 1.0)
        self.assertEqual(stats["y"]["max"], 2.0)

    def test_per_hand_ranges(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_synth(Path(tmp) / "m.synth", [
                {"tick": 0, "hand": "right", "pos": [0.5, 0.3, 0.0]},
                {"tick": 16, "hand": "right", "pos": [0.9, 0.3, 1.0]},
                {"tick": 32, "hand": "left", "pos": [-0.8, 0.3, 2.0]},
            ])
            records = list(iter_objects(read_synth_beatmap(path), str(path)))
        per_hand = analyze(records)["per_hand"]
        self.assertEqual(per_hand["right"]["x"]["min"], 0.5)
        self.assertEqual(per_hand["right"]["x"]["max"], 0.9)
        self.assertEqual(per_hand["left"]["x"]["max"], -0.8)


class ZClassificationTests(unittest.TestCase):
    def _records(self, zs: list[float]) -> list[dict]:
        return [
            {"time_key": float(i * 16), "is_rail": False, "hand": "right",
             "nodes": [(0.5, 0.3, z)]}
            for i, z in enumerate(zs)
        ]

    def test_time_like_z(self) -> None:
        # Z increases monotonically with time -> time/depth axis.
        recs = self._records([i * 1.5 for i in range(20)])
        info = classify_z(recs, bpm=120.0)
        self.assertTrue(info["classification"].startswith("time/depth"))
        self.assertGreater(abs(info["time_key_z_correlation"]), 0.95)

    def test_spatial_like_z(self) -> None:
        # Z oscillates in a bounded range, uncorrelated with time.
        import math
        recs = self._records([0.3 * math.sin(i) for i in range(20)])
        info = classify_z(recs, bpm=120.0)
        self.assertTrue(info["classification"].startswith("spatial"))


class RecommendProfileTests(unittest.TestCase):
    def test_maps_observed_extent_to_unit_range(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_synth(Path(tmp) / "m.synth", [
                {"tick": 0, "hand": "right", "pos": [2.0, 0.0, 0.0]},
                {"tick": 16, "hand": "left", "pos": [-2.0, 4.0, 1.0]},
            ])
            records = list(iter_objects(read_synth_beatmap(path), str(path)))
        stats = analyze(records)
        z_info = classify_z(records, 120.0)
        rec = recommend_profile(stats, z_info)
        p = rec["profile"]
        # x in [-2, 2] -> scale 2, offset 0; y in [0,4] -> scale 2, offset 2.
        self.assertAlmostEqual(p["x_scale"], 2.0)
        self.assertAlmostEqual(p["x_offset"], 0.0)
        self.assertAlmostEqual(p["y_scale"], 2.0)
        self.assertAlmostEqual(p["y_offset"], 2.0)
        self.assertIn("UNVERIFIED", p["name"])

    def test_profile_is_explicitly_unverified(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_synth(Path(tmp) / "m.synth", [
                {"tick": 0, "hand": "right", "pos": [1.0, 1.0, 0.0]},
                {"tick": 16, "hand": "left", "pos": [-1.0, -1.0, 1.0]},
            ])
            records = list(iter_objects(read_synth_beatmap(path), str(path)))
        rec = recommend_profile(analyze(records), classify_z(records, 120.0))
        self.assertIn("UNVERIFIED", rec["profile"]["notes"])


class FailureModeTests(unittest.TestCase):
    def test_non_synth_file_fails_clearly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "notes.txt"
            bad.write_text("hello", encoding="utf-8")
            with self.assertRaises(SynthReadError):
                read_synth_beatmap(bad)

    def test_normalized_draft_detected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            draft = Path(tmp) / "draft.normalized.json"
            draft.write_text(json.dumps({"format": "vrsynth_normalized_map"}), encoding="utf-8")
            with self.assertRaises(SynthReadError) as ctx:
                read_synth_beatmap(draft)
            self.assertIn("normalized draft", str(ctx.exception))

    def test_main_returns_2_when_nothing_readable(self) -> None:
        import contextlib
        import io
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad.synth"
            bad.write_text("not a zip", encoding="utf-8")
            out = Path(tmp) / "report.json"
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                rc = main(["--input", str(bad), "--output", str(out)])
            self.assertEqual(rc, 2)
            self.assertTrue(out.exists())  # a report is still written


class EndToEndTests(unittest.TestCase):
    def test_build_report_aggregates_multiple_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p1 = _write_synth(Path(tmp) / "a.synth", [
                {"tick": 0, "hand": "right", "pos": [0.5, 0.3, 0.0]},
            ])
            p2 = _write_synth(Path(tmp) / "b.synth", [
                {"tick": 0, "hand": "left", "pos": [-0.5, 0.3, 0.0]},
            ])
            report, errors = build_report([p1, p2])
        self.assertEqual(errors, [])
        self.assertEqual(report["total_notes"], 2)
        self.assertEqual(len(report["files"]), 2)
        self.assertIn("recommended_profile", report)


if __name__ == "__main__":
    unittest.main()
