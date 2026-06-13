"""Tests for the profile-aware export boundary (``synthcopilot.smh_io.export_track``).

Verifies the clean conversion boundary where normalized map objects become export
objects -- without implementing real .synth writing. Also covers profile
load/save and the CLI/demo --profile plumbing.
"""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

import synthcopilot.smh_io as smh
from synthcopilot.coordinate_systems import (
    DEFAULT_NORMALIZED_PROFILE,
    SYNTH_RIDERS_UNVERIFIED_PROFILE,
    CoordinateMappingProfile,
)
from synthcopilot.cli import build_track_data
from synthcopilot.cli import main as cli_main
from synthcopilot.motion import save_motion_recording
from synthcopilot.smh_io import (
    EXPORT_NORMALIZED_JSON_ONLY,
    EXPORT_PROFILE_APPLIED_NO_REAL_WRITER,
    EXPORT_REAL_SYNTH_WRITTEN,
    ExportProfileError,
    export_track,
)
from tools.generate_fake_motion import generate_fake_motion_recording


def _track():
    rec = generate_fake_motion_recording(duration_seconds=8.0)
    return build_track_data(rec, audio="song.mp3", bpm=120, offset=0.0, difficulty="Master")


class ExportBoundaryTests(unittest.TestCase):
    def test_no_profile_writes_normalized_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = export_track(_track(), Path(tmp) / "m.synth", None)
            self.assertTrue(result.files[0].exists())
        self.assertEqual(result.status, EXPORT_NORMALIZED_JSON_ONLY)
        self.assertFalse(result.coordinates_converted)
        self.assertIsNone(result.profile_name)
        self.assertEqual(result.files[0].name, "m.normalized.json")

    def test_invalid_profile_hard_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ExportProfileError):
                export_track(_track(), Path(tmp) / "m.synth", SYNTH_RIDERS_UNVERIFIED_PROFILE)

    def test_valid_profile_runs_conversion_but_no_real_writer(self) -> None:
        profile = CoordinateMappingProfile(
            name="identity_test", x_scale=1.0, y_scale=1.0, z_scale=1.0, notes="id"
        )
        with tempfile.TemporaryDirectory() as tmp:
            result = export_track(_track(), Path(tmp) / "m.synth", profile)
            # Status must be the boundary status, NEVER real_synth_written.
            self.assertEqual(result.status, EXPORT_PROFILE_APPLIED_NO_REAL_WRITER)
            self.assertNotEqual(result.status, EXPORT_REAL_SYNTH_WRITTEN)
            self.assertTrue(result.coordinates_converted)
            self.assertEqual(result.profile_name, "identity_test")
            data = json.loads(result.files[0].read_text(encoding="utf-8"))
        self.assertEqual(result.files[0].name, "m.synth_space.json")
        self.assertIn("synth_units", data["coordinate_space"])
        self.assertTrue(data["notes"] and data["rails"])

    def test_identity_profile_preserves_coordinates(self) -> None:
        # An identity profile must convert without changing values (no hidden scale).
        track = _track()
        profile = CoordinateMappingProfile(
            name="identity_test", x_scale=1.0, y_scale=1.0, z_scale=1.0, notes="id"
        )
        with tempfile.TemporaryDirectory() as tmp:
            result = export_track(track, Path(tmp) / "m.synth", profile)
            data = json.loads(result.files[0].read_text(encoding="utf-8"))
        n0 = track.notes[0]
        self.assertAlmostEqual(data["notes"][0]["synth"][0], n0.x)
        self.assertAlmostEqual(data["notes"][0]["synth"][1], n0.y)
        self.assertAlmostEqual(data["notes"][0]["synth"][2], 0.0)

    def test_default_profile_is_treated_as_a_provided_profile(self) -> None:
        # Passing the identity DEFAULT explicitly still exercises the boundary.
        with tempfile.TemporaryDirectory() as tmp:
            result = export_track(_track(), Path(tmp) / "m.synth", DEFAULT_NORMALIZED_PROFILE)
        self.assertEqual(result.status, EXPORT_PROFILE_APPLIED_NO_REAL_WRITER)
        self.assertTrue(result.coordinates_converted)

    def test_no_hardcoded_synth_range_in_export(self) -> None:
        # The conversion uses only the profile; with an identity profile the synth
        # values equal the normalized values -> no editor constants are injected.
        src = Path(smh.__file__).read_text(encoding="utf-8")
        for needle in ("0.1365", "GRID_SCALE", "TIME_SCALE", "X_OFFSET"):
            self.assertNotIn(needle, src)


class ProfileSaveLoadTests(unittest.TestCase):
    def test_round_trip(self) -> None:
        profile = CoordinateMappingProfile(
            name="v1", x_scale=7.0, y_scale=5.0, z_scale=1.0,
            x_offset=0.1, y_offset=0.2, z_offset=0.0, notes="demo",
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "prof.json"
            profile.save(path)
            loaded = CoordinateMappingProfile.load(path)
        self.assertEqual(loaded, profile)


class CliProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        rec = generate_fake_motion_recording(duration_seconds=6.0)
        self.motion = Path(self.tmp.name) / "motion.json"
        save_motion_recording(rec, self.motion)

    def _run(self, argv: list[str]) -> int:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return cli_main(argv)

    def test_motion_new_with_valid_profile(self) -> None:
        prof = Path(self.tmp.name) / "p.json"
        CoordinateMappingProfile(
            name="v1", x_scale=7.0, y_scale=5.0, z_scale=1.0, notes="d"
        ).save(prof)
        out = Path(self.tmp.name) / "out.synth"
        rc = self._run([
            "motion-new", "--motion", str(self.motion), "--audio", "s.mp3",
            "--bpm", "120", "--profile", str(prof), "--output", str(out),
        ])
        self.assertEqual(rc, 0)
        self.assertTrue((out.with_suffix(".synth_space.json")).exists())

    def test_motion_new_with_invalid_profile_exits_nonzero(self) -> None:
        prof = Path(self.tmp.name) / "bad.json"
        SYNTH_RIDERS_UNVERIFIED_PROFILE.save(prof)
        out = Path(self.tmp.name) / "out.synth"
        rc = self._run([
            "motion-new", "--motion", str(self.motion), "--audio", "s.mp3",
            "--profile", str(prof), "--output", str(out),
        ])
        self.assertEqual(rc, 1)

    def test_motion_new_without_profile_is_normalized(self) -> None:
        out = Path(self.tmp.name) / "out.synth"
        rc = self._run([
            "motion-new", "--motion", str(self.motion), "--audio", "s.mp3",
            "--output", str(out),
        ])
        self.assertEqual(rc, 0)
        self.assertTrue((out.with_suffix(".normalized.json")).exists())


if __name__ == "__main__":
    unittest.main()
