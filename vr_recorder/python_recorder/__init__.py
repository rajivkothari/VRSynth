"""Python VR recorder (Option 2).

A lean, engine-free recorder. The recording loop and JSON writer are real and
reusable; pose backends are pluggable. Only the synthetic backend is implemented;
the OpenVR/OpenXR hardware backends are honest stubs (see ``pose_source.py``).
"""

from .pose_source import (
    DevicePose,
    OpenVRPoseSource,
    PoseFrame,
    PoseSource,
    PyOpenXRPoseSource,
    RecorderBackendUnavailable,
    SyntheticPoseSource,
    make_pose_source,
)
from .recorder import record_session

__all__ = [
    "DevicePose",
    "PoseFrame",
    "PoseSource",
    "SyntheticPoseSource",
    "OpenVRPoseSource",
    "PyOpenXRPoseSource",
    "RecorderBackendUnavailable",
    "make_pose_source",
    "record_session",
]
