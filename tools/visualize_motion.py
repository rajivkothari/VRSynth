"""Visualize a captured (or fake) VR :class:`MotionRecording`.

Renders the headset and both controller trajectories so a capture can be eyeballed
for correctness before any transcription work -- the "make it trustable" step from
``docs/VR_CHOREOGRAPHY_CAPTURE.md``. No VR hardware required; it reads the JSON
produced by ``synthcopilot.motion`` (e.g. ``debug/fake_motion_recording.json``).

Produces three PNGs:

* the ``--output`` path: a 3D plot of all three trajectories;
* ``<output>_topdown.png``: a top-down X/Z (floor-plane) view;
* ``<output>_frontview.png``: a front-facing X/Y (camera-facing) view.

Each trajectory is drawn left=blue, right=pink, head=green (matching Synth Riders
hand colors), optionally shaded light->dark over time, with start (circle) and
end (square) markers.

Usage::

    python3 tools/visualize_motion.py \
        --input debug/fake_motion_recording.json \
        --output debug/motion_plot.png
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from synthcopilot.motion import MotionRecording, load_motion_recording  # noqa: E402

try:
    import matplotlib
    matplotlib.use("Agg")  # headless: write files, never open a window
    import matplotlib.pyplot as plt
    from matplotlib.collections import LineCollection
    from matplotlib.lines import Line2D
    from mpl_toolkits.mplot3d.art3d import Line3DCollection
    import numpy as np
except ImportError as exc:  # pragma: no cover - exercised only without matplotlib
    raise SystemExit(
        "visualize_motion requires matplotlib and numpy. "
        "Install them with: pip install matplotlib"
    ) from exc


# Device -> (display label, solid color, time-gradient colormap).
_DEVICES = {
    "headset": ("Headset", "#2ca02c", "Greens"),
    "left_controller": ("Left controller", "#1f77b4", "Blues"),
    "right_controller": ("Right controller", "#e6194b", "RdPu"),
}


def _trajectories(recording: MotionRecording) -> dict[str, "np.ndarray"]:
    """Return {device: Nx3 array of positions} plus a shared time vector under
    the special key ``"_time"``."""
    out: dict[str, np.ndarray] = {}
    for device in _DEVICES:
        pts = []
        for frame in recording.frames:
            pose = getattr(frame, device)
            pts.append((pose.position_x, pose.position_y, pose.position_z))
        out[device] = np.asarray(pts, dtype=float).reshape(-1, 3)
    out["_time"] = np.asarray(
        [f.time_seconds for f in recording.frames], dtype=float
    )
    return out


def _segments(a: "np.ndarray", b: "np.ndarray") -> "np.ndarray":
    """Pair consecutive 2D points into (N-1, 2, 2) line segments."""
    xy = np.column_stack([a, b])
    pts = xy.reshape(-1, 1, 2)
    return np.concatenate([pts[:-1], pts[1:]], axis=1)


def _add_path_2d(ax, x, y, label, color, cmap, times, color_by_time) -> None:
    if color_by_time and len(x) > 1:
        segs = _segments(x, y)
        lc = LineCollection(segs, cmap=cmap, array=times[:-1], linewidths=1.6)
        ax.add_collection(lc)
    else:
        ax.plot(x, y, color=color, linewidth=1.6, alpha=0.9)
    # Start / end markers.
    ax.scatter([x[0]], [y[0]], color=color, marker="o", s=70,
               edgecolors="black", zorder=5)
    ax.scatter([x[-1]], [y[-1]], color=color, marker="s", s=70,
               edgecolors="black", zorder=5)


def _add_path_3d(ax, xyz, label, color, cmap, times, color_by_time) -> None:
    # Reorder data (x, y_up, z_fwd) -> plot axes (x, z_fwd, y_up) so the vertical
    # matplotlib axis shows real-world "up". All artists use this same order.
    disp = xyz[:, [0, 2, 1]]
    dx, dy, dz = disp[:, 0], disp[:, 1], disp[:, 2]
    if color_by_time and len(disp) > 1:
        pts = disp.reshape(-1, 1, 3)
        segs = np.concatenate([pts[:-1], pts[1:]], axis=1)
        lc = Line3DCollection(segs, cmap=cmap, linewidths=1.6)
        lc.set_array(times[:-1])
        ax.add_collection3d(lc)
    else:
        ax.plot(dx, dy, dz, color=color, linewidth=1.6, alpha=0.9)
    ax.scatter([dx[0]], [dy[0]], [dz[0]], color=color, marker="o", s=70,
               edgecolors="black")
    ax.scatter([dx[-1]], [dy[-1]], [dz[-1]], color=color, marker="s", s=70,
               edgecolors="black")


def _legend_handles() -> list:
    handles = [
        Line2D([0], [0], color=color, lw=2, label=label)
        for label, color, _ in _DEVICES.values()
    ]
    handles += [
        Line2D([0], [0], marker="o", color="0.4", lw=0, markeredgecolor="black",
               markersize=9, label="start"),
        Line2D([0], [0], marker="s", color="0.4", lw=0, markeredgecolor="black",
               markersize=9, label="end"),
    ]
    return handles


def _set_equal_2d(ax, traj, i, j) -> None:
    allpts = np.concatenate(
        [traj[d][:, (i, j)] for d in _DEVICES], axis=0
    )
    lo = allpts.min(axis=0)
    hi = allpts.max(axis=0)
    pad = 0.1 * max(hi - lo).max() if (hi - lo).max() > 0 else 0.1
    ax.set_xlim(lo[0] - pad, hi[0] + pad)
    ax.set_ylim(lo[1] - pad, hi[1] + pad)
    ax.set_aspect("equal", adjustable="box")


def render(recording: MotionRecording, output: Path, color_by_time: bool = True) -> list[Path]:
    """Render the 3D, top-down, and front-view PNGs. Returns the written paths."""
    traj = _trajectories(recording)
    times = traj["_time"]
    written: list[Path] = []

    title_suffix = f"{len(recording.frames)} frames @ {recording.sample_rate:g} Hz"

    # --- 3D trajectory ---
    fig = plt.figure(figsize=(9, 7))
    ax = fig.add_subplot(111, projection="3d")
    for device, (label, color, cmap) in _DEVICES.items():
        _add_path_3d(ax, traj[device], label, color, cmap, times, color_by_time)
    ax.set_xlabel("X (m, right)")
    ax.set_ylabel("Z (m, forward)")
    ax.set_zlabel("Y (m, up)")
    ax.set_title(f"Motion 3D trajectory — {title_suffix}")
    ax.legend(handles=_legend_handles(), loc="upper left", fontsize=8)
    _autoscale_3d(ax, traj)
    fig.tight_layout()
    fig.savefig(output, dpi=130)
    plt.close(fig)
    written.append(output)

    # --- Top-down X/Z (floor plane) ---
    topdown = _suffixed(output, "topdown")
    fig, ax = plt.subplots(figsize=(8, 7))
    for device, (label, color, cmap) in _DEVICES.items():
        xyz = traj[device]
        _add_path_2d(ax, xyz[:, 0], xyz[:, 2], label, color, cmap, times, color_by_time)
    ax.set_xlabel("X (m, right)")
    ax.set_ylabel("Z (m, forward / depth)")
    ax.set_title(f"Top-down (X/Z) — {title_suffix}")
    ax.grid(True, alpha=0.3)
    ax.legend(handles=_legend_handles(), loc="upper left", fontsize=8)
    _set_equal_2d(ax, traj, 0, 2)
    fig.tight_layout()
    fig.savefig(topdown, dpi=130)
    plt.close(fig)
    written.append(topdown)

    # --- Front view X/Y (camera facing) ---
    frontview = _suffixed(output, "frontview")
    fig, ax = plt.subplots(figsize=(8, 7))
    for device, (label, color, cmap) in _DEVICES.items():
        xyz = traj[device]
        _add_path_2d(ax, xyz[:, 0], xyz[:, 1], label, color, cmap, times, color_by_time)
    ax.set_xlabel("X (m, right)")
    ax.set_ylabel("Y (m, up)")
    ax.set_title(f"Front view (X/Y) — {title_suffix}")
    ax.grid(True, alpha=0.3)
    ax.legend(handles=_legend_handles(), loc="upper left", fontsize=8)
    _set_equal_2d(ax, traj, 0, 1)
    fig.tight_layout()
    fig.savefig(frontview, dpi=130)
    plt.close(fig)
    written.append(frontview)

    return written


def _autoscale_3d(ax, traj) -> None:
    allpts = np.concatenate([traj[d] for d in _DEVICES], axis=0)
    lo = allpts.min(axis=0)
    hi = allpts.max(axis=0)
    # data axes: x->x, z->depth(y-axis of plot), y->up(z-axis of plot)
    ax.set_xlim(lo[0], hi[0])
    ax.set_ylim(lo[2], hi[2])
    ax.set_zlim(lo[1], hi[1])


def _suffixed(path: Path, suffix: str) -> Path:
    return path.with_name(f"{path.stem}_{suffix}{path.suffix}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", "-i", required=True, help="motion JSON path")
    parser.add_argument("--output", "-o", default="debug/motion_plot.png")
    parser.add_argument(
        "--no-time-color", action="store_true",
        help="disable shading trajectories by time",
    )
    args = parser.parse_args(argv)

    recording = load_motion_recording(args.input)
    if not recording.frames:
        raise SystemExit(f"{args.input} contains no frames to plot")

    output = Path(args.output)
    if output.parent and not output.parent.exists():
        output.parent.mkdir(parents=True, exist_ok=True)

    written = render(recording, output, color_by_time=not args.no_time_color)
    print("Wrote:")
    for path in written:
        print(f"  {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
