"""End-to-end demo / smoke test for the Product B pipeline.

Runs the whole motion-capture-to-map flow on fake motion and reports exactly
which files it created:

  1. generate fake motion        -> fake_motion_recording.json
  2. analyze motion              -> motion_report.json (+ printed summary)
  3. create visualizations       -> motion_plot*.png (if matplotlib present)
  4. convert to map objects      -> rails + notes (in memory)
  5. export a map file           -> only if --audio is given
  6. print every file created

CLI:
    python tools/demo_motion_pipeline.py --audio song.mp3 --bpm 123

With no --audio, every step runs except the map/`.synth` export.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from synthcopilot.coordinate_systems import CoordinateMappingProfile  # noqa: E402
from synthcopilot.motion import save_motion_recording  # noqa: E402
from synthcopilot.motion_analysis import segment_motion  # noqa: E402
from synthcopilot.smh_io import ExportProfileError, export_track  # noqa: E402
from synthcopilot.cli import build_track_data  # noqa: E402
from tools.analyze_motion import build_report, format_summary  # noqa: E402
from tools.generate_fake_motion import generate_fake_motion_recording  # noqa: E402


def run_demo(
    *,
    audio: str | None,
    bpm: float | None,
    offset: float,
    difficulty: str,
    duration: float,
    output_dir: Path,
    profile: CoordinateMappingProfile | None = None,
) -> list[Path]:
    """Run the pipeline; return the list of files created (in order)."""
    output_dir.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []

    # 1. Generate fake motion ------------------------------------------------
    print("[1/6] Generating fake motion...")
    recording = generate_fake_motion_recording(
        duration_seconds=duration,
        bpm=bpm if bpm is not None else 120.0,
        song_path=audio or "fake_song_segment.mp3",
    )
    motion_path = output_dir / "fake_motion_recording.json"
    save_motion_recording(recording, motion_path)
    created.append(motion_path)
    print(f"      {len(recording.frames)} frames @ {recording.sample_rate:g} Hz")

    # 2. Analyze motion ------------------------------------------------------
    print("[2/6] Analyzing motion...")
    segments = segment_motion(recording)
    report = build_report(recording, source=str(motion_path))
    report_path = output_dir / "motion_report.json"
    with report_path.open("w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    created.append(report_path)
    print(format_summary(report))

    # 3. Create visualizations ----------------------------------------------
    print("[3/6] Creating visualizations...")
    plot_path = output_dir / "motion_plot.png"
    try:
        # Imported lazily: visualize_motion raises SystemExit if matplotlib is
        # missing, so the smoke test still runs the rest without it.
        from tools import visualize_motion

        created += visualize_motion.render(recording, plot_path)
    except SystemExit as exc:
        print(f"      (skipped — {exc})")

    # 4. Convert to map objects ---------------------------------------------
    print("[4/6] Converting motion to map objects (rails + notes)...")
    track = build_track_data(
        recording, audio=audio, bpm=recording.bpm, offset=offset, difficulty=difficulty
    )
    print(f"      rails={len(track.rails)} notes={len(track.notes)} "
          f"difficulty={difficulty}")

    # 5. Export a map file (only if audio was provided) ----------------------
    print("[5/6] Exporting map...")
    print(f"      coordinate profile : {profile.name if profile else 'none (normalized)'}")
    if audio is None:
        print("      (skipped — no --audio provided; map objects built in memory only)")
    else:
        synth_path = output_dir / "captured_dance.synth"
        result = export_track(track, synth_path, profile)
        print(f"      coordinates converted: {result.coordinates_converted}")
        print(f"      export status      : {result.status}")
        if result.status != "real_synth_written":
            print("      (real .synth NOT written — see docs/COORDINATE_VERIFICATION.md)")
        for path in result.files:
            print(f"      wrote: {path}")
            created.append(path)

    # 6. Report files created ------------------------------------------------
    print("[6/6] Done. Files created:")
    for path in created:
        print(f"      - {path}")
    return created


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio", default=None, help="audio file path; enables map export")
    parser.add_argument("--bpm", type=float, default=None)
    parser.add_argument("--offset", type=float, default=0.0)
    parser.add_argument("--difficulty", default="Master")
    parser.add_argument("--duration", type=float, default=30.0)
    parser.add_argument("--output-dir", default="debug/demo")
    parser.add_argument("--profile", default=None,
                        help="JSON CoordinateMappingProfile applied at export")
    args = parser.parse_args(argv)

    profile = CoordinateMappingProfile.load(args.profile) if args.profile else None
    try:
        run_demo(
            audio=args.audio,
            bpm=args.bpm,
            offset=args.offset,
            difficulty=args.difficulty,
            duration=args.duration,
            output_dir=Path(args.output_dir),
            profile=profile,
        )
    except ExportProfileError as exc:
        print(f"ERROR: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
