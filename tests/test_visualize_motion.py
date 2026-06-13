"""Tests for the motion visualizer (``tools/visualize_motion.py``).

These run headlessly (matplotlib Agg backend) and assert that the three PNGs are
produced from the fake recording. Skipped automatically if matplotlib is not
installed, so the core suite never hard-depends on it.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from synthcopilot.motion import save_motion_recording
from tools.generate_fake_motion import generate_fake_motion_recording

try:
    import matplotlib  # noqa: F401
    from tools import visualize_motion

    _HAS_MPL = True
except ImportError:
    _HAS_MPL = False


@unittest.skipUnless(_HAS_MPL, "matplotlib not installed")
class VisualizeMotionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.recording = generate_fake_motion_recording(
            duration_seconds=5.0, sample_rate=60.0
        )

    def test_render_writes_three_pngs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "plot.png"
            written = visualize_motion.render(self.recording, output)
            self.assertEqual(len(written), 3)
            for path in written:
                self.assertTrue(path.exists(), f"missing {path}")
                self.assertGreater(path.stat().st_size, 0)

    def test_output_filenames(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "plot.png"
            written = visualize_motion.render(self.recording, output)
            names = {p.name for p in written}
            self.assertEqual(
                names, {"plot.png", "plot_topdown.png", "plot_frontview.png"}
            )

    def test_render_without_time_color(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "plot.png"
            written = visualize_motion.render(
                self.recording, output, color_by_time=False
            )
            self.assertTrue(all(p.exists() for p in written))

    def test_main_end_to_end(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            in_path = Path(tmp) / "fake.json"
            out_path = Path(tmp) / "out" / "plot.png"  # parent does not exist yet
            save_motion_recording(self.recording, in_path)
            rc = visualize_motion.main(
                ["--input", str(in_path), "--output", str(out_path)]
            )
            self.assertEqual(rc, 0)
            self.assertTrue(out_path.exists())
            self.assertTrue((out_path.parent / "plot_topdown.png").exists())
            self.assertTrue((out_path.parent / "plot_frontview.png").exists())


if __name__ == "__main__":
    unittest.main()
