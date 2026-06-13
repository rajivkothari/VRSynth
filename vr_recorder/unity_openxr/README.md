# Unity + OpenXR recorder (Option 1) — scaffolding

> **Status: SCAFFOLDING.** The C# files in `scripts/` show the intended structure
> and emit the `MotionRecording` JSON schema, but this is **not** a complete Unity
> project (no engine, packages, or scenes are vendored here) and it has **not**
> been run on hardware. Drop the scripts into a Unity OpenXR project to build it.

## Why Unity for the first real capture

Best Quest + PCVR coverage, mature pose/input APIs, a built-in renderer for
in-headset review, and a well-trodden OpenXR plugin. See
[`../README.md`](../README.md) and [`docs/VR_CHOREOGRAPHY_CAPTURE.md`](../../docs/VR_CHOREOGRAPHY_CAPTURE.md) §3.3.

## Project setup (on a machine with Unity)

1. Create a Unity project (Unity 2022 LTS+; URP fine).
2. **Package Manager** → install **XR Plugin Management** and **OpenXR Plugin**.
3. **Project Settings → XR Plug-in Management → OpenXR**: enable for your target
   (PC and/or Android/Quest); add the interaction profile for your controllers
   (e.g. *Oculus Touch* / *Meta Quest Touch Pro*).
4. Use the **STAGE** reference space (room-scale, floor origin) when available.
5. Add the scripts from `scripts/` to a GameObject in the scene.
6. Assign an `AudioSource` (the song) to `PoseRecorder`.

## Scripts

| File | Role |
|------|------|
| `SongClock.cs` | Single source of truth for song time; `t0` when audio starts. |
| `PoseRecorder.cs` | Each frame, reads HMD + L/R controller pose (and velocity) via `InputDevices`, buffers a frame stamped with song time. |
| `MotionRecordingWriter.cs` | Serializes the buffer to the `MotionRecording` JSON schema and writes the file. |

## Data contract

The writer MUST emit the schema from `synthcopilot/motion.py` (meters, Y-up, unit
quaternions `(x,y,z,w)`, seconds). Validate the output with:

```bash
python -c "from synthcopilot.motion import load_motion_recording, validate_motion_recording as v; print(v(load_motion_recording('capture.json')))"
```

An empty list means it's a valid `MotionRecording`.

## Coordinate note

Unity is **left-handed, Y-up**; OpenXR/our format is **right-handed, Y-up**. The
writer must convert (negate Z on positions and adjust the quaternion) so the
exported data matches the format convention. This conversion is marked `TODO` in
`MotionRecordingWriter.cs` and must be verified against
`tools/visualize_motion.py` before trusting captures.

## Build plan (Unity-specific)

- **Phase 1:** poses only, no audio. Confirm a recorded file replays in
  `tools/visualize_motion.py`.
- **Phase 2:** wire `SongClock` to the `AudioSource`; calibrate output latency.
- **Phase 3:** decouple sampling from disk I/O; add tracking-validity flags
  (needs a `motion.py` schema bump); crash-safe writes.
- **Phase 4:** Quest standalone build (storage/export, thermal budget).
