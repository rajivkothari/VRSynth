"""Tests for the coordinate-system boundary (``synthcopilot.coordinate_systems``).

Covers identity round-trip, clamping, profile validation, and -- importantly --
that the motion analysis/mapping layers keep everything in normalized space and
never reach for a (still unverified) hardcoded Synth Riders coordinate range.
"""

from __future__ import annotations

import math
import unittest
from pathlib import Path

from synthcopilot import coordinate_systems as cs
from synthcopilot.coordinate_systems import (
    DEFAULT_NORMALIZED_PROFILE,
    NORMALIZED_RANGE,
    SYNTH_RIDERS_UNVERIFIED_PROFILE,
    CoordinateMappingProfile,
    NormalizedPoint,
    SynthPoint,
    clamp_normalized,
    normalized_to_synth,
    synth_to_normalized,
    validate_profile,
)


class IdentityAndRoundTripTests(unittest.TestCase):
    def test_default_profile_is_pass_through(self) -> None:
        p = NormalizedPoint(0.5, -0.3, 0.1)
        s = normalized_to_synth(p, DEFAULT_NORMALIZED_PROFILE)
        self.assertEqual((s.x, s.y, s.z), (p.x, p.y, p.z))

    def test_identity_round_trip(self) -> None:
        p = NormalizedPoint(0.5, -0.3, 0.1)
        s = normalized_to_synth(p, DEFAULT_NORMALIZED_PROFILE)
        back = synth_to_normalized(s, DEFAULT_NORMALIZED_PROFILE)
        for a, b in zip((back.x, back.y, back.z), (p.x, p.y, p.z)):
            self.assertAlmostEqual(a, b, places=12)

    def test_affine_round_trip(self) -> None:
        profile = CoordinateMappingProfile(
            name="t", x_scale=2.0, y_scale=3.0, z_scale=0.5,
            x_offset=5.0, y_offset=-1.0, z_offset=0.25, notes="test",
        )
        p = NormalizedPoint(0.4, -0.9, 0.2)
        s = normalized_to_synth(p, profile)
        # Spot-check the forward mapping, not just the round trip.
        self.assertAlmostEqual(s.x, 0.4 * 2.0 + 5.0)
        self.assertAlmostEqual(s.y, -0.9 * 3.0 - 1.0)
        back = synth_to_normalized(s, profile)
        for a, b in zip((back.x, back.y, back.z), (p.x, p.y, p.z)):
            self.assertAlmostEqual(a, b, places=12)

    def test_zero_scale_cannot_be_inverted(self) -> None:
        profile = CoordinateMappingProfile("z", 0.0, 1.0, 1.0, notes="")
        with self.assertRaises(ValueError):
            synth_to_normalized(SynthPoint(1.0, 1.0, 1.0), profile)


class ClampTests(unittest.TestCase):
    def test_clamps_each_axis(self) -> None:
        c = clamp_normalized(NormalizedPoint(1.5, -2.0, 0.5))
        self.assertEqual((c.x, c.y, c.z), (1.0, -1.0, 0.5))

    def test_in_range_unchanged(self) -> None:
        p = NormalizedPoint(0.2, -0.8, 0.0)
        c = clamp_normalized(p)
        self.assertEqual((c.x, c.y, c.z), (p.x, p.y, p.z))

    def test_uses_module_range(self) -> None:
        lo, hi = NORMALIZED_RANGE
        c = clamp_normalized(NormalizedPoint(hi + 10, lo - 10, 0.0))
        self.assertEqual(c.x, hi)
        self.assertEqual(c.y, lo)


class ProfileValidationTests(unittest.TestCase):
    def test_default_profile_is_valid(self) -> None:
        self.assertEqual(validate_profile(DEFAULT_NORMALIZED_PROFILE), [])

    def test_unverified_profile_is_rejected(self) -> None:
        # Placeholder NaNs must make this profile unusable.
        errors = validate_profile(SYNTH_RIDERS_UNVERIFIED_PROFILE)
        self.assertTrue(errors)
        self.assertTrue(any("scale" in e for e in errors))

    def test_unverified_profile_clearly_marked(self) -> None:
        self.assertIn("UNVERIFIED", SYNTH_RIDERS_UNVERIFIED_PROFILE.name)
        self.assertTrue(math.isnan(SYNTH_RIDERS_UNVERIFIED_PROFILE.x_scale))
        self.assertIn("PLACEHOLDER", SYNTH_RIDERS_UNVERIFIED_PROFILE.notes.upper())

    def test_empty_name_invalid(self) -> None:
        profile = CoordinateMappingProfile("  ", 1.0, 1.0, 1.0, notes="")
        self.assertTrue(any("name" in e for e in validate_profile(profile)))

    def test_zero_scale_invalid(self) -> None:
        profile = CoordinateMappingProfile("z", 1.0, 0.0, 1.0, notes="")
        self.assertTrue(any("y_scale" in e for e in validate_profile(profile)))

    def test_non_finite_offset_invalid(self) -> None:
        profile = CoordinateMappingProfile(
            "o", 1.0, 1.0, 1.0, x_offset=math.inf, notes=""
        )
        self.assertTrue(any("x_offset" in e for e in validate_profile(profile)))


class BoundarySeparationTests(unittest.TestCase):
    """Guard rails: capture/analysis must stay in normalized space."""

    def _source(self, module_filename: str) -> str:
        path = Path(cs.__file__).parent / module_filename
        return path.read_text(encoding="utf-8")

    def test_analysis_does_not_reference_synth_units(self) -> None:
        # Motion analysis works in raw meters; it must not touch synth coordinates
        # or the unverified profile at all.
        for module in ("motion.py", "motion_analysis.py"):
            src = self._source(module)
            self.assertNotIn("SYNTH_RIDERS", src, f"{module} references synth units")

    def test_motion_to_map_does_not_use_unverified_profile(self) -> None:
        src = self._source("motion_to_map.py")
        self.assertNotIn("SYNTH_RIDERS_UNVERIFIED_PROFILE", src)
        # And it must not hardcode a numeric playfield range -- it single-sources
        # NORMALIZED_RANGE from the boundary layer.
        self.assertIn("NORMALIZED_RANGE", src)

    def test_generated_rails_stay_in_normalized_range(self) -> None:
        from tools.generate_fake_motion import generate_fake_motion_recording
        from synthcopilot.motion_analysis import segment_motion
        from synthcopilot.motion_to_map import generate_rails_from_motion

        rec = generate_fake_motion_recording(duration_seconds=15.0)
        rails = generate_rails_from_motion(rec, segment_motion(rec), "Master")
        self.assertTrue(rails)
        lo, hi = NORMALIZED_RANGE
        for rail in rails:
            for node in rail.nodes:
                np = node.to_normalized_point()
                self.assertGreaterEqual(np.x, lo - 1e-9)
                self.assertLessEqual(np.x, hi + 1e-9)
                self.assertGreaterEqual(np.y, lo - 1e-9)
                self.assertLessEqual(np.y, hi + 1e-9)


if __name__ == "__main__":
    unittest.main()
