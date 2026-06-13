"""Coordinate-system boundary: normalized capture space vs. final .synth units.

This module is the **seam** between two coordinate spaces:

* **Normalized space** (:class:`NormalizedPoint`) — the unit-ish ``[-1, 1]`` space
  that all motion capture, analysis, and map-object generation work in. It is
  hardware- and game-agnostic.
* **Synth space** (:class:`SynthPoint`) — the final Synth Riders / editor units
  that a real ``.synth`` file uses.

Everything upstream of export stays in normalized space. Conversion happens in
exactly one place — the writer/export step — by applying a
:class:`CoordinateMappingProfile`.

**We intentionally do NOT know the real Synth Riders coordinate range yet.** The
default profile (:data:`DEFAULT_NORMALIZED_PROFILE`) is identity/pass-through, so
nothing is silently scaled into invented units. A placeholder
:data:`SYNTH_RIDERS_UNVERIFIED_PROFILE` exists to mark the work that remains, but
its values are deliberately ``NaN`` so that :func:`validate_profile` rejects it
and it cannot be used by accident. Before real ``.synth`` emission we must verify
the scale/offset from a known editor-exported ``.synth`` file and fill in a
verified profile.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Union

__all__ = [
    "NORMALIZED_RANGE",
    "NormalizedPoint",
    "SynthPoint",
    "CoordinateMappingProfile",
    "DEFAULT_NORMALIZED_PROFILE",
    "SYNTH_RIDERS_UNVERIFIED_PROFILE",
    "normalized_to_synth",
    "synth_to_normalized",
    "clamp_normalized",
    "validate_profile",
]

# The expected (soft) range of normalized space. Used by clamp_normalized; values
# slightly outside are possible before clamping but should never be exported raw.
NORMALIZED_RANGE = (-1.0, 1.0)


@dataclass(frozen=True)
class NormalizedPoint:
    """A point in normalized capture/map space (each axis roughly ``[-1, 1]``)."""

    x: float
    y: float
    z: float = 0.0

    def to_dict(self) -> dict[str, float]:
        return asdict(self)


@dataclass(frozen=True)
class SynthPoint:
    """A point in final Synth Riders ``.synth``/editor units."""

    x: float
    y: float
    z: float = 0.0

    def to_dict(self) -> dict[str, float]:
        return asdict(self)


@dataclass(frozen=True)
class CoordinateMappingProfile:
    """An affine per-axis mapping from normalized space to synth space.

    ``synth = normalized * scale + offset`` (independently per axis). ``notes``
    documents where the numbers came from and how trustworthy they are -- crucial
    while the real Synth Riders units remain unverified.
    """

    name: str
    x_scale: float
    y_scale: float
    z_scale: float
    x_offset: float = 0.0
    y_offset: float = 0.0
    z_offset: float = 0.0
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CoordinateMappingProfile":
        return cls(
            name=data["name"],
            x_scale=data["x_scale"], y_scale=data["y_scale"], z_scale=data["z_scale"],
            x_offset=data.get("x_offset", 0.0),
            y_offset=data.get("y_offset", 0.0),
            z_offset=data.get("z_offset", 0.0),
            notes=data.get("notes", ""),
        )

    def save(self, path: "Union[str, Path]") -> None:
        """Write this profile to a JSON file."""
        out = Path(path)
        if out.parent and not out.parent.exists():
            out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", encoding="utf-8") as handle:
            json.dump(self.to_dict(), handle, ensure_ascii=False, indent=2)

    @classmethod
    def load(cls, path: "Union[str, Path]") -> "CoordinateMappingProfile":
        """Load a profile from a JSON file written by :meth:`save`."""
        with Path(path).open("r", encoding="utf-8") as handle:
            return cls.from_dict(json.load(handle))
# Synth Riders profile exists. This is the default everywhere.
DEFAULT_NORMALIZED_PROFILE = CoordinateMappingProfile(
    name="default_normalized",
    x_scale=1.0,
    y_scale=1.0,
    z_scale=1.0,
    x_offset=0.0,
    y_offset=0.0,
    z_offset=0.0,
    notes=(
        "Identity / pass-through. Output stays in normalized [-1, 1] space; no "
        "real Synth Riders units are applied. Safe default until a verified "
        "profile is measured from a known editor-exported .synth file."
    ),
)

# Placeholder marking the work that remains. Values are intentionally NaN so that
# validate_profile() rejects it -- it MUST NOT be used until real numbers are
# measured. Do not wire this into any pipeline by default.
SYNTH_RIDERS_UNVERIFIED_PROFILE = CoordinateMappingProfile(
    name="synth_riders_UNVERIFIED",
    x_scale=math.nan,
    y_scale=math.nan,
    z_scale=math.nan,
    x_offset=math.nan,
    y_offset=math.nan,
    z_offset=math.nan,
    notes=(
        "TODO/PLACEHOLDER -- UNVERIFIED. The real Synth Riders .synth coordinate "
        "scale and offset have NOT been determined. Values are NaN on purpose so "
        "validate_profile() fails and this profile cannot be used accidentally. "
        "To finish: export a known beatmap from the Synth Riders editor, read back "
        "note/rail/wall positions, and solve for per-axis scale/offset, then "
        "replace these NaNs with measured, verified values."
    ),
)


def normalized_to_synth(
    point: NormalizedPoint, profile: CoordinateMappingProfile
) -> SynthPoint:
    """Map a normalized point into synth units via ``profile``.

    Callers should ensure ``profile`` is valid (see :func:`validate_profile`);
    an unverified/NaN profile will propagate ``NaN`` rather than fail silently.
    """
    return SynthPoint(
        x=point.x * profile.x_scale + profile.x_offset,
        y=point.y * profile.y_scale + profile.y_offset,
        z=point.z * profile.z_scale + profile.z_offset,
    )


def synth_to_normalized(
    point: SynthPoint, profile: CoordinateMappingProfile
) -> NormalizedPoint:
    """Inverse of :func:`normalized_to_synth`.

    Raises :class:`ValueError` if any axis scale is zero (non-invertible).
    """
    if profile.x_scale == 0 or profile.y_scale == 0 or profile.z_scale == 0:
        raise ValueError(
            f"profile {profile.name!r} has a zero scale and cannot be inverted"
        )
    return NormalizedPoint(
        x=(point.x - profile.x_offset) / profile.x_scale,
        y=(point.y - profile.y_offset) / profile.y_scale,
        z=(point.z - profile.z_offset) / profile.z_scale,
    )


def clamp_normalized(point: NormalizedPoint) -> NormalizedPoint:
    """Clamp each axis of a normalized point into :data:`NORMALIZED_RANGE`."""
    lo, hi = NORMALIZED_RANGE
    return NormalizedPoint(
        x=_clamp(point.x, lo, hi),
        y=_clamp(point.y, lo, hi),
        z=_clamp(point.z, lo, hi),
    )


def validate_profile(profile: CoordinateMappingProfile) -> list[str]:
    """Check a profile for usability. Returns error strings (empty == valid).

    A profile is valid when it has a non-empty name, finite & non-zero per-axis
    scales (so the mapping is invertible), and finite offsets. The unverified
    placeholder profile fails these checks by design.
    """
    errors: list[str] = []

    if not isinstance(profile.name, str) or not profile.name.strip():
        errors.append("name must be a non-empty string")

    for axis in ("x_scale", "y_scale", "z_scale"):
        value = getattr(profile, axis)
        if not _is_finite_number(value):
            errors.append(f"{axis} must be a finite number (got {value!r})")
        elif value == 0:
            errors.append(f"{axis} must be non-zero (mapping must be invertible)")

    for axis in ("x_offset", "y_offset", "z_offset"):
        value = getattr(profile, axis)
        if not _is_finite_number(value):
            errors.append(f"{axis} must be a finite number (got {value!r})")

    if not isinstance(profile.notes, str):
        errors.append("notes must be a string")

    return errors


def _clamp(value: float, lo: float, hi: float) -> float:
    return lo if value < lo else hi if value > hi else value


def _is_finite_number(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if not isinstance(value, (int, float)):
        return False
    return math.isfinite(value)
