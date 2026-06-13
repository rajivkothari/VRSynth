"""Tests for musical timing helpers (``synthcopilot.timing``)."""

from __future__ import annotations

import unittest

from synthcopilot.timing import beat_to_seconds, seconds_to_beat, snap_time_to_grid


class ConversionTests(unittest.TestCase):
    def test_seconds_to_beat(self) -> None:
        # 120 bpm -> 2 beats per second.
        self.assertAlmostEqual(seconds_to_beat(0.0, 120), 0.0)
        self.assertAlmostEqual(seconds_to_beat(0.5, 120), 1.0)
        self.assertAlmostEqual(seconds_to_beat(1.0, 120), 2.0)

    def test_seconds_to_beat_with_offset(self) -> None:
        self.assertAlmostEqual(seconds_to_beat(0.25, 120, offset=0.25), 0.0)
        self.assertAlmostEqual(seconds_to_beat(0.75, 120, offset=0.25), 1.0)

    def test_beat_to_seconds_is_inverse(self) -> None:
        for bpm, offset in ((120, 0.0), (123, 0.1), (90, -0.05)):
            for beat in (0.0, 0.5, 1.0, 3.25, 16.0):
                t = beat_to_seconds(beat, bpm, offset)
                self.assertAlmostEqual(seconds_to_beat(t, bpm, offset), beat, places=9)

    def test_invalid_bpm_raises(self) -> None:
        for bad in (0, -10, None):
            with self.assertRaises(ValueError):
                seconds_to_beat(1.0, bad)
            with self.assertRaises(ValueError):
                beat_to_seconds(1.0, bad)


class SnapTests(unittest.TestCase):
    # At 120 bpm with subdivision 4 the grid cell is 0.125 s.
    def test_snaps_when_within_tolerance(self) -> None:
        self.assertAlmostEqual(
            snap_time_to_grid(0.13, 120, 0.0, 4, tolerance=0.01), 0.125
        )

    def test_keeps_time_when_outside_tolerance(self) -> None:
        # 0.13 is 0.005 from the grid; a 0.001 tolerance must not snap it.
        self.assertAlmostEqual(
            snap_time_to_grid(0.13, 120, 0.0, 4, tolerance=0.001), 0.13
        )

    def test_none_tolerance_always_snaps(self) -> None:
        self.assertAlmostEqual(
            snap_time_to_grid(0.20, 120, 0.0, 4, tolerance=None), 0.25
        )

    def test_exactly_on_grid_is_unchanged(self) -> None:
        self.assertAlmostEqual(
            snap_time_to_grid(0.25, 120, 0.0, 4, tolerance=0.001), 0.25
        )

    def test_respects_offset(self) -> None:
        # Grid lines shift by the offset.
        self.assertAlmostEqual(
            snap_time_to_grid(0.135, 120, 0.01, 4, tolerance=0.01), 0.135
        )

    def test_no_op_without_timing(self) -> None:
        self.assertEqual(snap_time_to_grid(0.13, None, 0.0, 4), 0.13)
        self.assertEqual(snap_time_to_grid(0.13, 0, 0.0, 4), 0.13)
        self.assertEqual(snap_time_to_grid(0.13, 120, 0.0, 0), 0.13)

    def test_finer_subdivision_snaps_to_a_closer_line(self) -> None:
        # At 120 bpm, t=0.07 s. Quarter grid (0.125 cell) nearest line is 0.125
        # (0.055 away); eighth grid (0.0625 cell) nearest line is 0.0625 (0.0075
        # away). With a tolerance that admits both, the finer grid lands closer.
        t = 0.07
        coarse = snap_time_to_grid(t, 120, 0.0, 4, tolerance=0.06)
        fine = snap_time_to_grid(t, 120, 0.0, 8, tolerance=0.06)
        self.assertAlmostEqual(coarse, 0.125)
        self.assertAlmostEqual(fine, 0.0625)
        self.assertLess(abs(fine - t), abs(coarse - t))

    def test_on_grid_value_unchanged_at_matching_subdivision(self) -> None:
        # 0.0625 s is exactly an eighth-note line at 120 bpm.
        self.assertAlmostEqual(
            snap_time_to_grid(0.0625, 120, 0.0, 8, tolerance=0.001), 0.0625
        )


if __name__ == "__main__":
    unittest.main()
