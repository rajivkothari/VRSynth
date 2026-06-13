"""Command-line interface: ``python -m synthcopilot <command>``.

Commands:

* ``motion-new`` -- Product B: build a map (rails + notes) from a VR motion
  capture recording and export it.
* ``new`` -- Product A (automatic MP3-to-map generation) placeholder. Product A
  has been superseded by the VR-choreography-capture pivot and is not part of
  this build; the command is kept so it isn't silently removed.
"""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
from typing import Optional

from .motion import MotionRecording, load_motion_recording
from .motion_analysis import segment_motion
from .motion_to_map import generate_notes_from_motion, generate_rails_from_motion
from .smh_io import SynthExportUnavailable, TrackData, write_normalized_json, write_synth

DEFAULT_DIFFICULTY = "Master"


def build_track_data(
    recording: MotionRecording,
    *,
    audio: Optional[str],
    bpm: Optional[float],
    offset: Optional[float],
    difficulty: str,
) -> TrackData:
    """Run the motion->map pipeline and assemble a TrackData (rails + notes)."""
    segments = segment_motion(recording)
    rails = generate_rails_from_motion(recording, segments, difficulty)
    notes = generate_notes_from_motion(recording, segments, bpm, offset, difficulty)
    return TrackData(
        audio=audio,
        bpm=bpm,
        offset=offset,
        difficulty=difficulty,
        rails=rails,
        notes=notes,
        metadata={
            "source": "motion-capture",
            "song_path": recording.song_path,
            "frame_count": len(recording.frames),
            "sample_rate": recording.sample_rate,
            "segment_count": len(segments),
        },
    )


def _print_summary(recording: MotionRecording, track: TrackData) -> None:
    note_sources = Counter(n.source for n in track.notes)
    print("=" * 56)
    print("VRSynth motion-new — map summary")
    print("=" * 56)
    print(f"song            : {recording.song_path}")
    print(f"audio           : {track.audio}")
    print(f"duration        : {recording.duration_seconds:.2f} s")
    print(f"bpm / offset    : {track.bpm} / {track.offset}")
    print(f"difficulty      : {track.difficulty}")
    print(f"frames          : {len(recording.frames)}")
    print(f"segments        : {track.metadata.get('segment_count')}")
    print(f"rails           : {len(track.rails)}")
    print(f"notes           : {len(track.notes)}")
    if note_sources:
        for source, count in sorted(note_sources.items()):
            print(f"  - {source:<16}: {count}")
    print("=" * 56)


def cmd_motion_new(args: argparse.Namespace) -> int:
    recording = load_motion_recording(args.motion)
    # CLI flags override values stored in the recording.
    bpm = args.bpm if args.bpm is not None else recording.bpm
    offset = args.offset if args.offset is not None else recording.offset

    track = build_track_data(
        recording,
        audio=args.audio,
        bpm=bpm,
        offset=offset,
        difficulty=args.difficulty,
    )
    _print_summary(recording, track)

    output = Path(args.output)
    try:
        written = write_synth(track, output)
        print(f"\nWrote Synth Riders map to {written}")
    except SynthExportUnavailable as exc:
        fallback = output.with_suffix(".normalized.json")
        written = write_normalized_json(track, fallback)
        print(f"\n.synth export unavailable: {exc}")
        print(f"Wrote normalized map (rails + notes) to {written}")
        print(
            "This JSON is in normalized coordinate space; converting to real "
            ".synth units needs a verified coordinate profile "
            "(see docs/VR_CHOREOGRAPHY_CAPTURE.md 8.7)."
        )
    return 0


def cmd_new(args: argparse.Namespace) -> int:
    print(
        "The 'new' command (Product A: automatic MP3-to-map generation) is not "
        "part of this build. SynthCoPilot has pivoted to VR choreography capture "
        "(Product B). Use 'motion-new' to build a map from a motion recording."
    )
    return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="synthcopilot", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    mn = sub.add_parser(
        "motion-new",
        help="build a rail+note map from a VR motion capture recording",
    )
    mn.add_argument("--motion", required=True, help="motion recording JSON path")
    mn.add_argument("--audio", default=None, help="audio file name for the track")
    mn.add_argument("--bpm", type=float, default=None, help="override BPM")
    mn.add_argument("--offset", type=float, default=None, help="override song offset (s)")
    mn.add_argument("--difficulty", default=DEFAULT_DIFFICULTY)
    mn.add_argument("--output", default="captured_dance.synth", help="output path")
    mn.set_defaults(func=cmd_motion_new)

    nw = sub.add_parser(
        "new",
        help="(Product A, deprecated) automatic MP3-to-map generation",
    )
    nw.set_defaults(func=cmd_new)

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)
