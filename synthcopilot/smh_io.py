"""Map export seam: TrackData container and writers.

This is the boundary where in-memory map objects (rails + notes, in **normalized**
space) become files. Two writers:

* :func:`write_normalized_json` -- always available. Writes the rails+notes map as
  JSON in normalized coordinates. This is the source-of-truth intermediate.
* :func:`write_synth` -- writes a real Synth Riders ``.synth`` file. This requires
  BOTH ``synth_mapping_helper`` to be installed AND a *verified*
  ``CoordinateMappingProfile`` (the real Synth Riders coordinate scale is not yet
  known -- see ``docs/VR_CHOREOGRAPHY_CAPTURE.md`` 8.7). Until then it raises
  :class:`SynthExportUnavailable`, and callers fall back to the normalized JSON.

Keeping the seam here means the rest of the pipeline never imports SMH or touches
``.synth`` units.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Union

from .coordinate_systems import (
    DEFAULT_NORMALIZED_PROFILE,
    CoordinateMappingProfile,
    normalized_to_synth,
    validate_profile,
)
from .motion_to_map import Note, Rail

__all__ = [
    "TrackData",
    "SynthExportUnavailable",
    "ExportProfileError",
    "ExportResult",
    "write_normalized_json",
    "write_synth_space_preview",
    "write_synth",
    "export_track",
    "MAP_FORMAT",
    "MAP_FORMAT_VERSION",
    "EXPORT_NORMALIZED_JSON_ONLY",
    "EXPORT_PROFILE_APPLIED_NO_REAL_WRITER",
    "EXPORT_REAL_SYNTH_WRITTEN",
]

MAP_FORMAT = "vrsynth_normalized_map"
MAP_FORMAT_VERSION = "0.1.0"

# Explicit export outcomes (no ambiguity about whether a real .synth was written).
EXPORT_NORMALIZED_JSON_ONLY = "normalized_json_only"
EXPORT_PROFILE_APPLIED_NO_REAL_WRITER = "profile_applied_no_real_writer"
EXPORT_REAL_SYNTH_WRITTEN = "real_synth_written"

PathLike = Union[str, Path]


class SynthExportUnavailable(RuntimeError):
    """Raised when a real ``.synth`` cannot be written (no SMH or no verified
    coordinate profile)."""


class ExportProfileError(ValueError):
    """Raised when a coordinate profile is supplied but fails validation."""


@dataclass
class TrackData:
    """A generated map's contents, in normalized coordinate space.

    Mirrors the pieces a Synth Riders track needs (audio, tempo, difficulty, and
    the playable objects) without committing to ``.synth`` units -- conversion is
    the writer's job, via a CoordinateMappingProfile.
    """

    audio: Optional[str]
    bpm: Optional[float]
    offset: Optional[float]
    difficulty: str
    rails: list[Rail] = field(default_factory=list)
    notes: list[Note] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": MAP_FORMAT,
            "format_version": MAP_FORMAT_VERSION,
            "coordinate_space": "normalized",
            "audio": self.audio,
            "bpm": self.bpm,
            "offset": self.offset,
            "difficulty": self.difficulty,
            "counts": {"rails": len(self.rails), "notes": len(self.notes)},
            "rails": [r.to_dict() for r in self.rails],
            "notes": [n.to_dict() for n in self.notes],
            "metadata": self.metadata,
        }


def write_normalized_json(track: TrackData, path: PathLike) -> Path:
    """Write the rails+notes map as normalized-coordinate JSON. Returns the path."""
    out = Path(path)
    if out.parent and not out.parent.exists():
        out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as handle:
        json.dump(track.to_dict(), handle, ensure_ascii=False, indent=2)
    return out


def write_synth(
    track: TrackData,
    path: PathLike,
    profile: CoordinateMappingProfile = DEFAULT_NORMALIZED_PROFILE,
) -> Path:
    """Write a real Synth Riders ``.synth`` file (editor-importable).

    Requires ``synth_mapping_helper`` installed and a **verified** coordinate
    ``profile`` mapping normalized space to real ``.synth`` units. Both are
    currently unavailable, so this raises :class:`SynthExportUnavailable` with a
    pointer to what's missing; the CLI falls back to :func:`write_normalized_json`.
    """
    try:
        import synth_mapping_helper  # noqa: F401
    except ImportError as exc:
        raise SynthExportUnavailable(
            "synth_mapping_helper is not installed; cannot write a real .synth. "
            "Install it, or use the normalized JSON output."
        ) from exc

    profile_errors = validate_profile(profile)
    if profile_errors or profile.name == DEFAULT_NORMALIZED_PROFILE.name:
        raise SynthExportUnavailable(
            "a verified normalized->.synth coordinate profile is required before "
            "real .synth export (the Synth Riders coordinate scale is not yet "
            "verified; see docs/VR_CHOREOGRAPHY_CAPTURE.md 8.7). "
            f"profile issues: {profile_errors or 'using identity default'}"
        )

    # Real .synth emission would happen here once a verified profile exists:
    # build a synth_mapping_helper SynthFile from notes/rails mapped through
    # normalized_to_synth(profile) and call SynthFile.save_as(path).
    raise SynthExportUnavailable(
        "real .synth writing is not implemented yet (pending a verified "
        "coordinate profile); falling back to normalized JSON."
    )


def _to_synth_xyz(point_dict_owner, profile: CoordinateMappingProfile) -> list[float]:
    """Convert an object's normalized point to synth-space [x, y, z] via the profile."""
    sp = normalized_to_synth(point_dict_owner.to_normalized_point(), profile)
    return [sp.x, sp.y, sp.z]


def track_to_synth_space(track: TrackData, profile: CoordinateMappingProfile) -> dict[str, Any]:
    """Convert a normalized track's note/rail points into synth-space coordinates.

    This is the conversion boundary: every :class:`Note`/:class:`RailNode` is
    pushed through ``normalized_to_synth(point, profile)``. The result is a preview
    structure in synth units -- it is explicitly NOT a real ``.synth`` file.
    """
    notes = [
        {
            "hand": n.hand, "time_seconds": n.time_seconds, "beat": n.beat,
            "source": n.source, "confidence": n.confidence,
            "synth": _to_synth_xyz(n, profile),
        }
        for n in track.notes
    ]
    rails = [
        {
            "hand": r.hand, "difficulty": r.difficulty, "confidence": r.confidence,
            "primitives": list(r.primitives),
            "nodes": [
                {"time_seconds": node.time_seconds, "beat": node.beat,
                 "synth": _to_synth_xyz(node, profile)}
                for node in r.nodes
            ],
        }
        for r in track.rails
    ]
    return {
        "format": "vrsynth_synth_space_preview",
        "coordinate_space": "synth_units (UNVERIFIED preview; NOT a real .synth file)",
        "profile": profile.to_dict(),
        "audio": track.audio,
        "bpm": track.bpm,
        "offset": track.offset,
        "difficulty": track.difficulty,
        "counts": {"rails": len(rails), "notes": len(notes)},
        "rails": rails,
        "notes": notes,
    }


def write_synth_space_preview(
    track: TrackData, path: PathLike, profile: CoordinateMappingProfile
) -> Path:
    """Write the profile-converted (synth-space) coordinates as a JSON preview.

    Used when a valid profile is applied but no real ``.synth`` writer exists yet.
    The file is a preview of converted coordinates, not an importable ``.synth``.
    """
    out = Path(path)
    if out.parent and not out.parent.exists():
        out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as handle:
        json.dump(track_to_synth_space(track, profile), handle, ensure_ascii=False, indent=2)
    return out


@dataclass
class ExportResult:
    """Outcome of :func:`export_track`."""

    status: str                       # one of the EXPORT_* constants
    files: list[Path]                 # files written
    profile_name: Optional[str]       # profile applied, if any
    coordinates_converted: bool       # were points pushed through the profile?


def export_track(
    track: TrackData,
    output_path: PathLike,
    profile: Optional[CoordinateMappingProfile] = None,
) -> ExportResult:
    """Export a track, applying a coordinate profile at the boundary if given.

    Behavior:

    * **No profile** -> write the normalized-JSON draft only
      (``EXPORT_NORMALIZED_JSON_ONLY``); no conversion.
    * **Profile that fails validation** -> raise :class:`ExportProfileError`
      (hard fail; we never export with a broken profile).
    * **Valid profile** -> convert all points through ``normalized_to_synth`` and
      attempt the real writer. The real writer is still gated, so today this
      writes a synth-space *preview* and reports
      ``EXPORT_PROFILE_APPLIED_NO_REAL_WRITER`` (never ``EXPORT_REAL_SYNTH_WRITTEN``
      until a real ``.synth`` writer lands).
    """
    out = Path(output_path)

    if profile is None:
        written = write_normalized_json(track, out.with_suffix(".normalized.json"))
        return ExportResult(EXPORT_NORMALIZED_JSON_ONLY, [written], None, False)

    errors = validate_profile(profile)
    if errors:
        raise ExportProfileError(
            f"coordinate profile {profile.name!r} is invalid; refusing to export: "
            f"{errors}"
        )

    try:
        written = write_synth(track, out, profile)
        return ExportResult(EXPORT_REAL_SYNTH_WRITTEN, [written], profile.name, True)
    except SynthExportUnavailable:
        # Profile is valid and was applied (coordinates converted), but there is
        # no real .synth writer yet -> emit the converted synth-space preview.
        preview = write_synth_space_preview(track, out.with_suffix(".synth_space.json"), profile)
        return ExportResult(
            EXPORT_PROFILE_APPLIED_NO_REAL_WRITER, [preview], profile.name, True
        )
