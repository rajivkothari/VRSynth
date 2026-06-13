"""Musical timing helpers: seconds <-> beats and light grid snapping.

Shared by the motion->map bridge (for rails today, notes later). The snapping
here is deliberately *gentle*: it nudges a time onto the beat grid only when it is
already within a tolerance window, so a dancer's expressive off-grid timing is
preserved rather than quantized away.

Conventions:

* ``bpm`` is beats per minute; one beat = a quarter note in Synth Riders terms.
* ``offset`` is the song time (seconds) at beat 0.
* ``subdivision`` is the number of grid lines per beat (e.g. 4 = sixteenth-note
  grid).
"""

from __future__ import annotations

from typing import Optional

__all__ = ["seconds_to_beat", "beat_to_seconds", "snap_time_to_grid"]


def seconds_to_beat(time: float, bpm: float, offset: float = 0.0) -> float:
    """Convert a time in seconds to a (possibly fractional) beat position."""
    if bpm is None or bpm <= 0:
        raise ValueError(f"bpm must be positive to compute beats (got {bpm!r})")
    return (time - offset) * bpm / 60.0


def beat_to_seconds(beat: float, bpm: float, offset: float = 0.0) -> float:
    """Convert a beat position to a time in seconds. Inverse of
    :func:`seconds_to_beat`."""
    if bpm is None or bpm <= 0:
        raise ValueError(f"bpm must be positive to convert beats (got {bpm!r})")
    return beat * 60.0 / bpm + offset


def snap_time_to_grid(
    time: float,
    bpm: float,
    offset: float = 0.0,
    subdivision: int = 4,
    tolerance: Optional[float] = None,
) -> float:
    """Snap ``time`` to the nearest beat-grid line, but only if it is close.

    The grid has ``subdivision`` lines per beat. The nearest grid time is returned
    when it is within ``tolerance`` seconds of ``time``; otherwise ``time`` is
    returned unchanged (preserving off-grid expression). ``tolerance=None`` means
    always snap to the nearest grid line.

    No-ops (returns ``time``) when timing is undefined: ``bpm`` missing/<=0 or
    ``subdivision`` <= 0.
    """
    if bpm is None or bpm <= 0 or subdivision <= 0:
        return time
    beat = (time - offset) * bpm / 60.0
    nearest_beat = round(beat * subdivision) / subdivision
    snapped = nearest_beat * 60.0 / bpm + offset
    if tolerance is None or abs(snapped - time) <= tolerance:
        return snapped
    return time
