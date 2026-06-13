"""The recording loop: PoseSource -> MotionRecording.

Samples a :class:`~vr_recorder.python_recorder.pose_source.PoseSource` at a fixed
rate, time-locked to the song, and assembles a ``synthcopilot.motion``
``MotionRecording`` (so the output is identical to what transcription consumes).

The loop logic is real and reusable. With the synthetic backend it runs anywhere;
with a hardware backend it would pace to wall-clock time (``realtime=True``).
"""

from __future__ import annotations

import os
import sys
import time
from typing import Callable, Optional

# Make the synthcopilot package importable when run from the repo root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from synthcopilot.motion import FrameSample, MotionRecording, PoseSample  # noqa: E402

from .audio import AudioPlayer  # noqa: E402
from .pose_source import DevicePose, PoseFrame, PoseSource  # noqa: E402


def _pose_sample(time_seconds: float, dp: DevicePose) -> PoseSample:
    vx = vy = vz = None
    if dp.velocity is not None:
        vx, vy, vz = dp.velocity
    ax = ay = az = None
    if dp.angular_velocity is not None:
        ax, ay, az = dp.angular_velocity
    return PoseSample(
        time_seconds=time_seconds,
        position_x=dp.position[0], position_y=dp.position[1], position_z=dp.position[2],
        rotation_x=dp.rotation[0], rotation_y=dp.rotation[1],
        rotation_z=dp.rotation[2], rotation_w=dp.rotation[3],
        velocity_x=vx, velocity_y=vy, velocity_z=vz,
        angular_velocity_x=ax, angular_velocity_y=ay, angular_velocity_z=az,
    )


def _frame_sample(time_seconds: float, pf: PoseFrame) -> FrameSample:
    return FrameSample(
        time_seconds=time_seconds,
        headset=_pose_sample(time_seconds, pf.headset),
        left_controller=_pose_sample(time_seconds, pf.left),
        right_controller=_pose_sample(time_seconds, pf.right),
    )


def record_session(
    source: PoseSource,
    *,
    song_path: str,
    duration_seconds: float,
    sample_rate: float = 72.0,
    bpm: Optional[float] = None,
    offset: Optional[float] = None,
    audio: Optional[AudioPlayer] = None,
    realtime: bool = False,
    clock: Callable[[], float] = time.perf_counter,
    sleep: Callable[[float], None] = time.sleep,
) -> MotionRecording:
    """Record a session into a ``MotionRecording``.

    Samples ``source`` at ``sample_rate`` for ``duration_seconds`` (frame ``i`` is
    stamped at ``i / sample_rate`` song-seconds). With ``realtime=True`` the loop
    paces each poll to wall-clock time (for live hardware capture); otherwise it
    samples on a logical clock (deterministic, used by the synthetic backend).
    """
    if sample_rate <= 0:
        raise ValueError("sample_rate must be positive")
    n_frames = round(duration_seconds * sample_rate)
    dt = 1.0 / sample_rate

    source.start()
    if audio is not None:
        audio.play(song_path)
    try:
        frames: list[FrameSample] = []
        start = clock()
        for i in range(n_frames):
            t = i * dt
            if realtime:
                target = start + t
                while True:
                    remaining = target - clock()
                    if remaining <= 0:
                        break
                    sleep(min(0.001, remaining))
            frames.append(_frame_sample(t, source.poll(t)))
    finally:
        source.stop()
        if audio is not None:
            audio.stop()

    return MotionRecording(
        song_path=song_path,
        sample_rate=sample_rate,
        frames=frames,
        bpm=bpm,
        offset=offset,
        metadata={
            "recorder": "vr_recorder.python_recorder",
            "backend": source.name,
            "synthetic": bool(getattr(source, "synthetic", False)),
        },
    )
