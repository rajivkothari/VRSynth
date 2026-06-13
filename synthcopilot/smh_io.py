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
    validate_profile,
)
from .motion_to_map import Note, Rail

__all__ = [
    "TrackData",
    "SynthExportUnavailable",
    "write_normalized_json",
    "write_synth",
    "MAP_FORMAT",
    "MAP_FORMAT_VERSION",
]

MAP_FORMAT = "vrsynth_normalized_map"
MAP_FORMAT_VERSION = "0.1.0"

PathLike = Union[str, Path]


class SynthExportUnavailable(RuntimeError):
    """Raised when a real ``.synth`` cannot be written (no SMH or no verified
    coordinate profile)."""


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
