# Unity VR Motion Recorder — implementation spec

> Implementation spec for the **Unity + OpenXR** recorder (Option 1 from
> [`README.md`](README.md)). The companion first-draft script is
> [`unity/SynthCoPilotMotionRecorder.cs`](unity/SynthCoPilotMotionRecorder.cs).
>
> **Status:** spec + first-draft script. Not yet validated on hardware. The
> recorder's job is to produce a `MotionRecording` JSON that loads with
> `synthcopilot/motion.py`; it does **not** generate Synth Riders objects or
> invent `.synth` coordinate units (that is downstream — see §"Coordinate
> conversion").

---

## 1. Unity version

- **Recommended: Unity 2022.3 LTS.** Stable OpenXR + Input System support and
  broad Quest/PCVR compatibility. Unity 6 LTS also works; avoid non-LTS streams.
- Render pipeline: **URP** (lightweight, good on Quest). Built-in also fine.
- Scripting backend for Quest builds: **IL2CPP**, ARM64.

## 2. OpenXR packages

Install via **Window → Package Manager**:

| Package | Purpose |
|---------|---------|
| **XR Plugin Management** (`com.unity.xr.management`) | enable/select XR runtime |
| **OpenXR Plugin** (`com.unity.xr.openxr`) | the OpenXR runtime backend |
| **Input System** (`com.unity.inputsystem`) | controller buttons for start/stop |
| **XR Interaction Toolkit** (`com.unity.xr.interaction.toolkit`) *(optional)* | `XR Origin` rig + `TrackedPoseDriver`, ray UI |

**Project Settings → XR Plug-in Management → OpenXR:**
- Enable OpenXR for the target platform tab(s): **PC** and/or **Android** (Quest).
- Add an **Interaction Profile** matching your controllers (e.g. *Oculus Touch
  Controller Profile* / *Meta Quest Touch Pro*).
- Set the tracking origin / reference space to **Stage** (room-scale, floor
  origin) so positions are floor-referenced.

## 3. Scene layout

```
Scene
├── XR Origin (XR Rig)                    # TrackingOriginMode = Floor (Stage)
│   ├── Camera Offset
│   │   ├── Main Camera        (HMD)      # -> headset transform
│   │   ├── LeftController      GameObject# TrackedPoseDriver (Left hand, grip)
│   │   └── RightController     GameObject# TrackedPoseDriver (Right hand, grip)
├── Recorder                              # SynthCoPilotMotionRecorder.cs
│   └── (AudioSource)                     # the song clip
└── UI Canvas (world-space)               # status text + Start/Stop button
```

- Assign `headset`, `leftController`, `rightController` on the recorder to the
  three transforms above.
- The controller GameObjects use **TrackedPoseDriver** bound to the
  left/right-hand **grip pose** (matches the controller's physical position).

## 4. Audio playback

- One `AudioSource` holds the song `AudioClip`.
- **Song time** for each frame is `audioSource.time` (seconds into the clip),
  latched against the audio DSP clock. `t0` = the moment playback starts.
- **Latency caveat:** `audioSource.time` does not account for output latency
  (Bluetooth headphones can add 100–300 ms). A measured offset must be subtracted
  in a calibration step (§9) before captures are beat-accurate. Until then,
  treat song time as approximate and re-confirm via `tools/visualize_motion.py`.

## 5. Start / stop recording UX

- **Toggle to record.** Bind to a controller button (recommended:
  right-hand **primary/A** or the **menu** button) via the Input System, with a
  keyboard fallback (Space) for desktop testing.
- On **start**: brief 3-2-1 countdown (audio ticks + UI), then play the song and
  begin sampling. Show a red "● REC" indicator and elapsed time.
- On **stop** (same button, or song end): stop sampling, stop audio, write the
  JSON file, and show the saved path + frame count.
- **Re-take:** each start/stop is one take → one file (immutable; never appended).
- **Calibrate** button (e.g. left-hand **Y**) captures a neutral pose (§9).

## 6. Tracked objects

Three devices, each sampled every tick:

| Logical | Source | Notes |
|---------|--------|-------|
| **HMD** | `headset` transform (Main Camera) | defines body origin downstream |
| **Left controller** | `leftController` transform (grip pose) | left hand color |
| **Right controller** | `rightController` transform (grip pose) | right hand color |

Per device we record position, rotation, and a **finite-difference linear
velocity** (Unity's `Transform` has no velocity; we differentiate position
between samples). Angular velocity is left null for now.

> The current `MotionRecording` schema has **no tracking-validity field**.
> Inside-out tracking will drop controllers (hands behind back / at hips). This
> is a known gap (see §11) — add `*_tracked` to `motion.py` before trusting
> hardware captures.

## 7. JSON export format

Must match `synthcopilot/motion.py` exactly (loads with `load_motion_recording`,
passes `validate_motion_recording`). Conventions: **meters, right-handed, Y-up,
unit quaternions `(x, y, z, w)`, seconds** relative to song start.

```jsonc
{
  "format_version": "0.1.0",
  "song_path": "song.mp3",
  "sample_rate": 72.0,
  "bpm": 123,                 // null if unknown
  "offset": 0.0,              // null if unknown
  "metadata": {
    "recorder": "unity_openxr",
    "unity_version": "2022.3.x",
    "hmd": "Quest 3",
    "handedness_converted": true,
    "calibration": { "hmd_height_m": 1.62, "left_rest": [-0.3,1.2,-0.35], "right_rest": [0.3,1.2,-0.35] }
  },
  "frames": [
    {
      "time_seconds": 0.0,
      "headset":          { "time_seconds": 0.0, "position_x": 0.0, "position_y": 1.62, "position_z": -0.10,
                            "rotation_x": 0, "rotation_y": 0, "rotation_z": 0, "rotation_w": 1 },
      "left_controller":  { "time_seconds": 0.0, "position_x": -0.30, "position_y": 1.20, "position_z": -0.35,
                            "rotation_x": 0, "rotation_y": 0, "rotation_z": 0, "rotation_w": 1,
                            "velocity_x": 0.0, "velocity_y": 0.0, "velocity_z": 0.0 },
      "right_controller": { "...": "..." }
    }
  ]
}
```

- `velocity_*` / `angular_velocity_*` are optional; omit (or null) when unknown.
- Every per-pose `time_seconds` equals its frame's `time_seconds`.
- Floats serialized with `InvariantCulture` (decimal point, never comma).

## 8. File naming convention

```
{song}_{difficulty-or-take}_{yyyyMMdd_HHmmss}.motion.json
```

Examples: `example-track_take01_20260613_072300.motion.json`,
`my-song_Master_20260613_072300.motion.json`. Sanitize the song name (replace
non-alphanumerics with `-`). Files are written to `Application.persistentDataPath`
(per-platform app storage); on Quest, surface/export this path explicitly (see
§11). One file per take; never overwrite.

## 9. Calibration process

Run once before a take (stored in `metadata.calibration`):

1. **Neutral pose:** performer stands still, arms relaxed at sides. Press
   *Calibrate*. Capture `hmd_height_m` (HMD Y) and resting controller positions
   (`left_rest`/`right_rest`). These seed the downstream **body-relative**
   normalization (re-origin on HMD, scale by reach) in
   `synthcopilot/motion_to_map.py`.
2. **Forward facing (optional):** capture HMD yaw to define "forward" so the
   playfield aligns with where the player faces.
3. **Audio latency (Phase 2):** play a click on a known beat, have the performer
   tap; the measured delay becomes the `offset` subtracted from song time. Until
   implemented, `offset` is 0 and timing is approximate.

## 10. Coordinate conversion to the Synth Riders playfield

Two conversions, in **two different places** — keep them separate:

1. **In the recorder (this spec):** Unity is **left-handed, Y-up**; the
   `MotionRecording` format is **right-handed, Y-up** (OpenXR convention). The
   recorder converts each pose: negate Z on position, and mirror the rotation
   quaternion accordingly. This is implemented in the script behind
   `convertToRightHanded` and is marked **TODO: verify** — confirm orientation by
   replaying the output in `tools/visualize_motion.py` before trusting it.

2. **Downstream, NOT in the recorder:** mapping into the Synth Riders playfield
   is `synthcopilot`'s job. `normalize_motion_to_playfield` re-origins on the
   head and scales by arm reach into a normalized `[-1,1]×[-1,1]` space; the
   final mapping to **real `.synth` units is deliberately deferred and
   UNVERIFIED** (`synthcopilot/coordinate_systems.py`,
   `docs/VR_CHOREOGRAPHY_CAPTURE.md` §8.7). **The recorder must not bake in any
   Synth Riders coordinate range** — it emits plain meters in the format
   convention; everything game-specific happens later.

## 11. Known limitations

- **Handedness conversion is unverified** — must be confirmed against
  `visualize_motion.py`; orientation may be mirrored until then.
- **No tracking-validity flags** in the `MotionRecording` schema — dropped/
  occluded controllers are recorded as if valid. Needs a `motion.py` schema bump.
- **Audio latency is uncompensated** — song time can be offset by tens to
  hundreds of ms; beat alignment is approximate until calibrated.
- **Velocity is finite-differenced**, not read from OpenXR `XR_SPACE_VELOCITY`;
  noisier than native velocity. Angular velocity not recorded.
- **Sampling shares the main thread** — long takes should move JSON writing (and
  ideally buffering) off-thread; the first draft writes on stop.
- **Quest storage/export friction** — `persistentDataPath` is sandboxed; getting
  files off-device needs adb/MediaStore work (deferred).
- **Single difficulty / no in-headset review** in this first draft (review via
  `tools/visualize_motion.py` on desktop).
