"""CLI: ``python -m vr_recorder.python_recorder``.

Records a session and writes a MotionRecording JSON. Only ``--backend synthetic``
works without hardware; the openvr/openxr backends are stubs (see pose_source).
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from synthcopilot.motion import save_motion_recording, validate_motion_recording  # noqa: E402

from .audio import NullAudioPlayer, best_available_player  # noqa: E402
from .pose_source import RecorderBackendUnavailable, make_pose_source  # noqa: E402
from .recorder import record_session  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--backend", required=True, choices=("synthetic", "openvr", "openxr"),
        help="pose source; only 'synthetic' runs without VR hardware",
    )
    parser.add_argument("--song", default="song.mp3", help="audio file path (song_path)")
    parser.add_argument("--duration", type=float, default=10.0, help="seconds to record")
    parser.add_argument("--sample-rate", type=float, default=72.0)
    parser.add_argument("--bpm", type=float, default=None)
    parser.add_argument("--offset", type=float, default=None)
    parser.add_argument("--output", "-o", default="debug/recorded_session.json")
    parser.add_argument("--no-audio", action="store_true", help="do not play audio")
    parser.add_argument(
        "--realtime", action="store_true",
        help="pace sampling to wall-clock time (for live capture)",
    )
    args = parser.parse_args(argv)

    try:
        source = make_pose_source(args.backend)
    except (RecorderBackendUnavailable, NotImplementedError) as exc:
        print(f"Backend '{args.backend}' is not available: {exc}", file=sys.stderr)
        return 3

    if source.synthetic:
        print("WARNING: 'synthetic' backend produces FAKE motion, not a real "
              "VR capture. Use it only to exercise the pipeline.", file=sys.stderr)

    audio = NullAudioPlayer() if args.no_audio else best_available_player()

    recording = record_session(
        source,
        song_path=args.song,
        duration_seconds=args.duration,
        sample_rate=args.sample_rate,
        bpm=args.bpm,
        offset=args.offset,
        audio=audio,
        realtime=args.realtime,
    )

    errors = validate_motion_recording(recording)
    if errors:
        print("Recording failed validation:", file=sys.stderr)
        for e in errors[:10]:
            print(f"  - {e}", file=sys.stderr)
        return 1

    save_motion_recording(recording, args.output)
    print(f"Wrote {len(recording.frames)} frames "
          f"({args.duration:g}s @ {args.sample_rate:g} Hz, backend={source.name}) "
          f"to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
