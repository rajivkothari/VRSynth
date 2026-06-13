"""Smoke test for the end-to-end Product B demo (``tools/demo_motion_pipeline``)."""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from tools.demo_motion_pipeline import main, run_demo


def _run(**kwargs):
    with contextlib.redirect_stdout(io.StringIO()):
        return run_demo(**kwargs)


class DemoPipelineTests(unittest.TestCase):
    def test_with_audio_exports_a_map(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            created = _run(
                audio="song.mp3", bpm=123.0, offset=0.0,
                difficulty="Master", duration=8.0, output_dir=out,
            )
            for path in created:
                self.assertTrue(path.exists(), f"missing {path}")
            names = {p.name for p in created}
            self.assertIn("fake_motion_recording.json", names)
            self.assertIn("motion_report.json", names)
            # A map file is exported when audio is provided.
            map_files = [p for p in created if "captured_dance" in p.name]
            self.assertTrue(map_files)
            data = json.loads(map_files[0].read_text(encoding="utf-8"))
            self.assertIn("rails", data)
            self.assertIn("notes", data)

    def test_without_audio_skips_export(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            created = _run(
                audio=None, bpm=None, offset=0.0,
                difficulty="Master", duration=8.0, output_dir=out,
            )
            self.assertTrue(all(p.exists() for p in created))
            # No map file without audio.
            self.assertFalse(any("captured_dance" in p.name for p in created))
            self.assertIn("fake_motion_recording.json", {p.name for p in created})

    def test_main_returns_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with contextlib.redirect_stdout(io.StringIO()):
                rc = main(["--duration", "5", "--output-dir", tmp])
            self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
