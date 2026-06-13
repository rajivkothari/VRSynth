# vr_recorder — experimental VR motion capture prototype

> **Status: SCAFFOLDING. No real hardware capture is implemented yet.**
> This folder is an isolated prototype, separate from the `synthcopilot/`
> package. The only thing that runs end-to-end today is a **synthetic** pose
> source (clearly labelled fake) used to exercise the recording pipeline. The
> actual OpenXR / SteamVR hardware backends are **stubs** that raise a clear
> "backend unavailable / not implemented" error until built on a real machine
> with a headset.

## Target

A small app that:

1. Plays a song (with a known, synced clock).
2. Records 6-DoF poses of the **headset** and **both controllers** at a fixed
   sample rate, time-locked to the song.
3. Exports a **`MotionRecording` JSON** that is byte-for-byte loadable by
   `synthcopilot/motion.py` (`load_motion_recording`) and passes
   `validate_motion_recording`.

This is the M1–M3 capture milestone from
[`docs/VR_CHOREOGRAPHY_CAPTURE.md`](../docs/VR_CHOREOGRAPHY_CAPTURE.md): get a
lossless, song-synced capture onto disk. Transcription (rails/notes) already
lives in `synthcopilot/` and consumes exactly this format.

## The data contract (the important part)

Whatever language/runtime the recorder is written in, it must emit JSON matching
the schema in `synthcopilot/motion.py`. Minimal shape:

```jsonc
{
  "format_version": "0.1.0",
  "song_path": "song.mp3",
  "sample_rate": 72.0,
  "bpm": 123,            // nullable
  "offset": 0.0,         // nullable
  "metadata": { "recorder": "...", "hmd": "..." },
  "frames": [
    {
      "time_seconds": 0.0,
      "headset":          { "time_seconds": 0.0, "position_x": 0.0, "position_y": 1.6, "position_z": -0.1,
                            "rotation_x": 0, "rotation_y": 0, "rotation_z": 0, "rotation_w": 1 },
      "left_controller":  { "...": "same fields; velocity_*/angular_velocity_* optional" },
      "right_controller": { "..." : "..." }
    }
  ]
}
```

Conventions (match OpenXR): **meters**, right-handed, **Y up**, rotations are
**unit quaternions** `(x, y, z, w)`, times in **seconds** relative to song start.

> Note: the current `MotionRecording` schema has **no per-device tracking-validity
> flag**. Inside-out tracking *will* drop controllers (hands behind back / at
> hips). Recording validity is a known gap to add to `motion.py` before relying
> on hardware captures — see "Open gaps" below.

## Local feasibility (what's installed here)

Researched against this environment's installed dependencies only:

| Need | Found? | Notes |
|------|--------|-------|
| `openvr` (SteamVR Python) | ❌ | not installed |
| `pyopenxr` / `xr` (OpenXR Python) | ❌ | not installed |
| audio playback (`sounddevice`, `simpleaudio`, `ffplay`, `aplay`, …) | ❌ | none available |
| `numpy` | ✅ | available |
| VR runtime / headset | ❌ | headless container |

**Conclusion:** a real capture cannot run in this environment. The Python
recorder is therefore structured as a backend interface with: a working
**synthetic** backend (for testing the loop and the JSON output) and **stub**
hardware backends that fail loudly with install/setup guidance.

---

## Two implementation options

### Option 1 — Unity + OpenXR recorder (recommended for first real capture)

See [`unity_openxr/README.md`](unity_openxr/README.md). Unity gives the best
Quest + PCVR coverage, mature pose/input APIs, easy audio playback, and a free
in-headset visualizer. The C# scripts in `unity_openxr/scripts/` are
**scaffolding** that show the intended structure and emit the exact JSON schema
above; they are not a complete Unity project (no engine is vendored here).

- **Pros:** broadest hardware reach (standalone Quest *and* PCVR), built-in
  rendering for review, well-trodden OpenXR plugin.
- **Cons:** needs the Unity editor + a project; heavier than a script.

### Option 2 — Python OpenXR / SteamVR recorder

See [`python_recorder/`](python_recorder/). A lean recorder that runs alongside
SteamVR with no game engine. The recording loop, clock, and JSON writer are
**real** and reusable; the pose backends are pluggable:

- `SyntheticPoseSource` — **implemented**, deterministic fake motion. Lets us run
  and test the whole pipeline with no hardware. *Explicitly not a capture.*
- `OpenVRPoseSource` — **stub** for SteamVR via the `openvr` pip package.
- `PyOpenXRPoseSource` — **stub** for OpenXR via `pyopenxr`.

Run it (synthetic only here):

```bash
python -m vr_recorder.python_recorder \
    --backend synthetic --song song.mp3 --duration 10 --sample-rate 72 \
    --bpm 123 --output debug/recorded_session.json
```

- **Pros:** tiny, scriptable, no engine; good for a "record in the background"
  companion on PCVR.
- **Cons:** SteamVR/PCVR-centric; standalone Quest is harder; you build your own
  visualization (we already have `tools/visualize_motion.py`).

---

## Build plan

**Phase 0 — contract & plumbing (done in this prototype).**
Backend interface (`PoseSource`), recording loop, and a `MotionRecording` JSON
writer validated against `synthcopilot/motion.py`, exercised with the synthetic
backend. ✅

**Phase 1 — one real backend, poses only (no audio).**
Pick Option 1 *or* 2. Implement `start()/poll()/stop()` against the real runtime:
- *Python/SteamVR:* `openvr.init(VRApplication_Background)`, then per frame
  `getDeviceToAbsoluteTrackingPose(...)`; map HMD + the two controller indices to
  `DevicePose` (extract position + quaternion from the 3×4 matrix; grab velocity
  if present).
- *Unity/OpenXR:* `InputDevices`/`TrackedPoseDriver` or `XR ... .TryGetFeatureValue`
  for device position/rotation/velocity each `Update()`.
Exit: a recorded `MotionRecording` replays correctly in
`tools/visualize_motion.py`.

**Phase 2 — song sync.**
Add audio playback and establish `song t0` + audio-latency calibration so motion
and audio share one timeline (see doc §6/§5.3). Stamp song time on every frame.

**Phase 3 — robustness.**
Fixed-rate sampling decoupled from I/O, tracking-validity flags (requires a
`motion.py` schema bump), crash-safe incremental writes, take management.

**Phase 4 — platform.**
PCVR first (or Quest-via-Link), then standalone Quest (storage/export + thermal
budget per doc §5.4).

## Open gaps (must address before trusting hardware captures)

- **Tracking-validity flags** are not in the `MotionRecording` schema yet.
- **Audio↔motion latency** calibration is unimplemented.
- The hardware backends are **untested** (no device available here).

## Layout

```
vr_recorder/
├── README.md                     # this file
├── python_recorder/              # Option 2 (Python)
│   ├── pose_source.py            # PoseSource interface + Synthetic (real) + HW stubs
│   ├── audio.py                  # audio player abstraction (best-effort/no-op)
│   ├── recorder.py               # recording loop -> MotionRecording
│   └── __main__.py               # CLI
└── unity_openxr/                 # Option 1 (Unity)
    ├── README.md                 # project setup + packages
    └── scripts/                  # C# scaffolding (drop into a Unity project)
        ├── PoseRecorder.cs
        ├── SongClock.cs
        └── MotionRecordingWriter.cs
```
