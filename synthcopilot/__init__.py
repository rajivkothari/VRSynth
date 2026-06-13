"""SynthCoPilot: VR choreography capture for Synth Riders.

This package currently exposes the core motion-capture data model. VR runtime
capture (reading live poses from OpenXR/SteamVR/Quest) is intentionally not
implemented yet -- see ``docs/VR_CHOREOGRAPHY_CAPTURE.md``.
"""

from .motion import (
    FrameSample,
    MotionRecording,
    PoseSample,
    load_motion_recording,
    save_motion_recording,
    validate_motion_recording,
)

__all__ = [
    "PoseSample",
    "FrameSample",
    "MotionRecording",
    "save_motion_recording",
    "load_motion_recording",
    "validate_motion_recording",
]
