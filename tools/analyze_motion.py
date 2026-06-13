"""Motion analysis report -- the Product B equivalent of an evaluator/debug report.

Loads a captured (or fake) ``MotionRecording``, runs the analysis pipeline
(smooth -> velocities -> segment), prints a human-readable summary, and writes a
JSON report. It is read-only with respect to the recording and emits **no** Synth
Riders notes -- it reports choreography intent only.

Usage::

    python3 tools/analyze_motion.py \
        --input debug/fake_motion_recording.json \
        --output debug/motion_report.json
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from synthcopilot.motion import MotionRecording, load_motion_recording  # noqa: E402
from synthcopilot.motion_analysis import (  # noqa: E402
    compute_velocity,
    segment_motion,
    smooth_motion,
)

_REPORT_VERSION = "0.1.0"


def _speed_stats(velocities: list[tuple[float, float, float]]) -> dict[str, float]:
    if not velocities:
        return {"average": 0.0, "peak": 0.0}
    speeds = [math.sqrt(vx * vx + vy * vy + vz * vz) for vx, vy, vz in velocities]
    return {"average": sum(speeds) / len(speeds), "peak": max(speeds)}


def build_report(
    recording: MotionRecording,
    *,
    smooth_window: int = 5,
    source: str | None = None,
) -> dict[str, Any]:
    """Run the analysis pipeline and assemble a JSON-serializable report dict."""
    smoothed = smooth_motion(recording, smooth_window)
    velocity = compute_velocity(smoothed)
    segments = segment_motion(recording, smooth_window=smooth_window)

    left = _speed_stats(velocity["left"])
    right = _speed_stats(velocity["right"])
    combined = _speed_stats(velocity["left"] + velocity["right"])

    primitive_counts = Counter(s.primitive_name for s in segments)
    hand_counts = Counter(s.hand for s in segments)

    timeline = [
        {
            "start_time": round(s.start_time, 4),
            "end_time": round(s.end_time, 4),
            "duration": round(s.duration, 4),
            "hand": s.hand,
            "primitive_name": s.primitive_name,
            "confidence": round(s.confidence, 4),
        }
        for s in segments
    ]

    return {
        "report_version": _REPORT_VERSION,
        "source": source,
        "song_path": recording.song_path,
        "duration_seconds": round(recording.duration_seconds, 4),
        "sample_rate": recording.sample_rate,
        "frame_count": len(recording.frames),
        "bpm": recording.bpm,
        "offset": recording.offset,
        "hand_speed": {
            "left": {k: round(v, 4) for k, v in left.items()},
            "right": {k: round(v, 4) for k, v in right.items()},
            "combined": {k: round(v, 4) for k, v in combined.items()},
        },
        "segment_count": len(segments),
        "primitive_counts": dict(sorted(primitive_counts.items())),
        "hand_counts": dict(sorted(hand_counts.items())),
        "timeline": timeline,
        "analysis_params": {"smooth_window": smooth_window},
    }


def format_summary(report: dict[str, Any], *, max_timeline_rows: int = 25) -> str:
    """Render a compact human-readable summary of a report dict."""
    lines: list[str] = []
    lines.append("=" * 60)
    lines.append("VRSynth motion analysis report")
    lines.append("=" * 60)
    lines.append(f"song            : {report['song_path']}")
    lines.append(f"duration        : {report['duration_seconds']:.2f} s")
    lines.append(f"sample rate     : {report['sample_rate']:g} Hz")
    lines.append(f"frames          : {report['frame_count']}")
    bpm = report.get("bpm")
    lines.append(f"bpm             : {bpm if bpm is not None else 'unknown'}")

    hs = report["hand_speed"]
    lines.append("")
    lines.append("hand speed (m/s)        average     peak")
    for name in ("left", "right", "combined"):
        lines.append(
            f"  {name:<10}        {hs[name]['average']:>8.3f} {hs[name]['peak']:>8.3f}"
        )

    lines.append("")
    lines.append(f"segments        : {report['segment_count']}")
    lines.append("primitive counts:")
    if report["primitive_counts"]:
        width = max(len(k) for k in report["primitive_counts"])
        for prim, count in report["primitive_counts"].items():
            lines.append(f"  {prim:<{width}} : {count}")
    else:
        lines.append("  (none)")

    lines.append("")
    lines.append("timeline:")
    timeline = report["timeline"]
    if not timeline:
        lines.append("  (no segments)")
    else:
        for row in timeline[:max_timeline_rows]:
            lines.append(
                f"  [{row['start_time']:7.2f} - {row['end_time']:7.2f}] "
                f"{row['hand']:<5} {row['primitive_name']:<20} "
                f"conf={row['confidence']:.2f}"
            )
        if len(timeline) > max_timeline_rows:
            lines.append(f"  ... and {len(timeline) - max_timeline_rows} more segments")
    lines.append("=" * 60)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", "-i", required=True, help="motion JSON path")
    parser.add_argument("--output", "-o", default="debug/motion_report.json")
    parser.add_argument("--smooth-window", type=int, default=5)
    parser.add_argument(
        "--quiet", "-q", action="store_true", help="do not print the summary"
    )
    args = parser.parse_args(argv)

    recording = load_motion_recording(args.input)
    report = build_report(
        recording, smooth_window=args.smooth_window, source=str(args.input)
    )

    if not args.quiet:
        print(format_summary(report))

    out_path = Path(args.output)
    if out_path.parent and not out_path.parent.exists():
        out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    print(f"\nWrote report to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
