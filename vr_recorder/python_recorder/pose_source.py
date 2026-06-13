"""Pose backends for the Python recorder.

A ``PoseSource`` yields, per polled frame, the 6-DoF pose of the headset and both
controllers. Backends:

* :class:`SyntheticPoseSource` -- **implemented**. Deterministic fake motion for
  exercising the pipeline with no hardware. NOT a real capture.
* :class:`OpenVRPoseSource` -- **stub**. SteamVR via the ``openvr`` pip package.
* :class:`PyOpenXRPoseSource` -- **stub**. OpenXR via ``pyopenxr`` (``xr``).

The hardware backends document the exact runtime calls to make but raise
:class:`RecorderBackendUnavailable` / ``NotImplementedError`` until built and
tested on a real machine with a headset. Nothing here pretends capture works.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

Vec3 = tuple[float, float, float]
Quat = tuple[float, float, float, float]  # (x, y, z, w)


class RecorderBackendUnavailable(RuntimeError):
    """Raised when a hardware backend's runtime/bindings/device are not present."""


@dataclass
class DevicePose:
    """One device's pose for one frame, in OpenXR conventions (meters, Y-up)."""

    position: Vec3
    rotation: Quat = (0.0, 0.0, 0.0, 1.0)
    velocity: Optional[Vec3] = None
    angular_velocity: Optional[Vec3] = None
    tracked: bool = True  # NOTE: not yet represented in MotionRecording JSON


@dataclass
class PoseFrame:
    """Synchronized poses for the three tracked devices at one instant."""

    headset: DevicePose
    left: DevicePose
    right: DevicePose


class PoseSource:
    """Backend interface. Subclasses implement ``start``/``poll``/``stop``."""

    name: str = "abstract"
    synthetic: bool = False

    def start(self) -> None:  # pragma: no cover - trivial
        """Acquire the runtime/session. Override as needed."""

    def poll(self, time_seconds: float) -> PoseFrame:
        """Return the current poses. ``time_seconds`` is song time for this frame."""
        raise NotImplementedError

    def stop(self) -> None:  # pragma: no cover - trivial
        """Release the runtime/session. Override as needed."""


# --------------------------------------------------------------------------- #
# Synthetic backend (implemented) -- FAKE data, for pipeline testing only.
# --------------------------------------------------------------------------- #
class SyntheticPoseSource(PoseSource):
    """Deterministic fake motion. **Not a hardware capture.**

    Produces a gently bobbing head and two controllers sweeping side to side, so
    the recorder loop and JSON output can be tested without any VR runtime.
    """

    name = "synthetic"
    synthetic = True

    def poll(self, time_seconds: float) -> PoseFrame:
        t = time_seconds
        head = DevicePose(
            position=(0.03 * math.sin(2 * math.pi * 0.5 * t),
                      1.60 + 0.03 * math.sin(2 * math.pi * 1.1 * t),
                      -0.10),
        )
        sweep = math.sin(2 * math.pi * 0.5 * t)
        left = DevicePose(position=(-0.30 - 0.22 * sweep, 1.20, -0.35))
        right = DevicePose(position=(0.30 + 0.22 * sweep, 1.20, -0.35))
        return PoseFrame(headset=head, left=left, right=right)


# --------------------------------------------------------------------------- #
# OpenVR / SteamVR backend (STUB).
# --------------------------------------------------------------------------- #
class OpenVRPoseSource(PoseSource):
    """SteamVR backend via the ``openvr`` pip package. **Not implemented.**

    Intended implementation (on a machine with SteamVR + a headset):

    * ``start``: ``self._vr = openvr.init(openvr.VRApplication_Background)``.
    * ``poll``: ``poses = self._vr.getDeviceToAbsoluteTrackingPose(
      openvr.TrackingUniverseStanding, 0, openvr.k_unMaxTrackedDeviceCount)``;
      take HMD index ``openvr.k_unTrackedDeviceIndex_Hmd`` and the two controller
      indices (``getTrackedDeviceClass == TrackedDeviceClass_Controller``);
      convert each ``mDeviceToAbsoluteTracking`` 3x4 matrix to position +
      quaternion, and read ``vVelocity`` for linear velocity.
    * ``stop``: ``openvr.shutdown()``.
    """

    name = "openvr"

    def __init__(self) -> None:
        try:
            import openvr  # noqa: F401
        except ImportError as exc:
            raise RecorderBackendUnavailable(
                "openvr is not installed. Install SteamVR and `pip install openvr`, "
                "then run with a headset connected."
            ) from exc
        # A real SteamVR runtime + headset is still required even if the package
        # imports; do not claim capture works until tested on hardware.
        raise NotImplementedError(
            "OpenVRPoseSource is a stub. See the class docstring for the intended "
            "SteamVR implementation; build and test it on a machine with a headset."
        )

    def poll(self, time_seconds: float) -> PoseFrame:  # pragma: no cover - stub
        raise NotImplementedError


# --------------------------------------------------------------------------- #
# OpenXR backend (STUB).
# --------------------------------------------------------------------------- #
class PyOpenXRPoseSource(PoseSource):
    """OpenXR backend via ``pyopenxr`` (imported as ``xr``). **Not implemented.**

    Intended implementation: create an ``xr`` instance + session, a STAGE
    reference space, and action spaces bound to ``/user/hand/{left,right}/input/
    grip/pose``; each frame ``xrLocateSpace`` the view space (HMD) and the two
    grip action spaces against the predicted display time, reading position,
    orientation, and (when available) ``XR_SPACE_VELOCITY``, plus the
    ``*_TRACKED_BIT`` validity flags.
    """

    name = "openxr"

    def __init__(self) -> None:
        try:
            import xr  # noqa: F401  (pyopenxr)
        except ImportError as exc:
            raise RecorderBackendUnavailable(
                "pyopenxr is not installed. `pip install pyopenxr` and run with an "
                "OpenXR runtime (SteamVR/Monado/Oculus) + headset."
            ) from exc
        raise NotImplementedError(
            "PyOpenXRPoseSource is a stub. See the class docstring for the intended "
            "OpenXR implementation; build and test it on a machine with a headset."
        )

    def poll(self, time_seconds: float) -> PoseFrame:  # pragma: no cover - stub
        raise NotImplementedError


_BACKENDS = {
    "synthetic": SyntheticPoseSource,
    "openvr": OpenVRPoseSource,
    "openxr": PyOpenXRPoseSource,
}


def make_pose_source(name: str) -> PoseSource:
    """Instantiate a backend by name (``synthetic`` | ``openvr`` | ``openxr``)."""
    try:
        cls = _BACKENDS[name]
    except KeyError:
        raise ValueError(
            f"unknown backend {name!r}; choose from {sorted(_BACKENDS)}"
        ) from None
    return cls()
