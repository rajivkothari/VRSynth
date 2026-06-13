# VR Choreography Capture for Synth Riders

> **Status:** Planning / architecture only. No generator or runtime code is
> implemented or modified by this document.
> **Date:** 2026-06-13
> **Owner:** SynthCoPilot

---

## 0. Context: Why We Are Pivoting

SynthCoPilot's original direction (**Product A**) was *automatic MP3-to-map
generation*: ingest an audio file, run music analysis (onset detection, beat
tracking, spectral features), and emit a Synth Riders beatmap directly from the
signal.

Product A did not work as a product. The core failure is conceptual, not a bug:

> **Music analysis ≠ choreography.**

Knowing *where the beats are* tells you almost nothing about *what a body should
do*. A good Synth Riders map is a dance — it has phrasing, weight shifts,
left/right call-and-response, build-ups, rests, and spatial flow that a human
choreographer feels but an onset detector cannot infer. Audio-derived maps are
metronomically "correct" and yet feel lifeless, mechanical, and physically
awkward.

**Product B** inverts the data flow. Instead of *predicting* movement from
audio, we **capture real human movement** and treat that motion as the
authoritative source of the choreography. The human is the choreographer; the
software is the recorder, editor, and transcriber.

This document is the planning and architecture pass for Product B.

### What is preserved

- **Product A is not deleted.** Its audio-analysis and map-emission code remain
  in the tree. Product B will eventually *reuse* the back half of Product A —
  the part that writes a valid `.synth` beatmap file — as a transcription
  target. Audio analysis becomes a *supporting* signal (alignment, snapping,
  BPM context), not the *source* of choreography.
- The Synth Riders file-format knowledge encoded in Product A is an asset we
  carry forward.

---

## 1. Product Concept

**One sentence:** Put on a VR headset, play a song, dance with the controllers,
and SynthCoPilot turns your performance into a Synth Riders beatmap.

### The user story

1. A creator opens SynthCoPilot in VR (or launches a lightweight capture
   companion alongside a media player).
2. They pick a song. The song plays with a clear, synced clock.
3. They dance naturally — hands trace the melody, arms punch the beat, the head
   bobs and leans. They are *performing the map they wish existed*.
4. SynthCoPilot records the 6-DoF motion of the headset (HMD) and both
   controllers for the full duration, time-locked to the song.
5. After the take, the creator reviews a **visualization** of their movement
   (a "ghost" replay, motion trails, a timeline).
6. They can re-record sections, keep the best take, and trim.
7. The captured motion is later **transcribed** into Synth Riders primitives —
   notes, rails, and walls — producing a playable, human-feeling map.

### Why this is better than Product A

- The choreography is *authored by intent*, not inferred from audio.
- Spatial flow, handedness, and phrasing come for free — they are literally what
  the person did.
- It is fun. Recording is a creative act, not a settings dialog.
- It scales the content library through community performance, not just through
  an algorithm's guesses.

### What it is **not**

- Not a real-time "auto play." We are recording a *performance intended to
  become a map*, not someone playing an existing map.
- Not full-body mocap (initially). Three tracked points (head + two hands) is
  the Synth Riders-native input surface and our MVP target.

---

## 2. MVP Scope

The MVP proves the **capture → store → visualize** loop end to end, with a
*manual or semi-automatic* path to a beatmap. We deliberately do **not** try to
nail automatic transcription in the MVP.

### In scope (MVP)

1. **Capture runtime**
   - Read 6-DoF pose (position + orientation) of HMD and both controllers at a
     fixed sample rate (target 72–90 Hz, matching headset refresh).
   - Read controller button/trigger/grip state and timestamps (used later as
     explicit "this is a hit" markers).
   - Time-lock every sample to a single monotonic session clock and to song
     playback time.
2. **Song sync**
   - Load a local audio file, play it, and expose `songTimeMs` to the recorder.
   - Record an explicit `t0` (song start) so motion and audio share one
     timeline.
3. **Recording session management**
   - Start / stop / re-take. Each take is a discrete file.
   - Basic metadata (song title, BPM if known, performer, headset model).
4. **Persisted capture format**
   - Write the raw capture to disk in the documented JSON-Lines + manifest
     format (see §4). This is the *durable source of truth*.
5. **Playback / visualization**
   - Replay a recorded take: three moving objects + motion trails on a timeline
     scrubber, synced to the audio.
   - This is what makes the data *trustable* — you can see what was captured.
6. **A manual transcription bridge (thin)**
   - Export the capture in a form that a human (or a later automated pass) can
     turn into notes. MVP target: emit *candidate hit events* derived from
     simple heuristics (velocity peaks, trigger presses) into an intermediate
     "events" file — **not** a final `.synth` yet.

### Out of scope for MVP (but on the roadmap)

- Fully automatic, high-quality note/rail/wall transcription.
- In-headset authoring UI / map editing.
- Multiplayer or cloud sync.
- Full-body / hip / feet tracking.
- Direct write into a polished, ship-ready `.synth` (the *plumbing* to write
  `.synth` may be stubbed, but quality is not an MVP gate).

### MVP definition of done

> A creator can record a 60–90s dance to a song, close the app, reopen the file,
> watch an accurate synced replay of their three tracked points, and export an
> intermediate events file. No motion data is lost, and audio/motion stay in
> sync to within one frame.

---

## 3. Technical Architecture

### 3.1 High-level component map

```
                ┌──────────────────────────────────────────────────────┐
                │                   SynthCoPilot (Product B)            │
                │                                                       │
  VR Hardware   │   ┌───────────────┐      ┌────────────────────────┐  │
  ┌──────────┐  │   │  Capture      │      │  Session / Clock       │  │
  │ HMD      │──┼──▶│  Runtime      │◀────▶│  - monotonic clock     │  │
  │ Ctrl L/R │  │   │  (OpenXR)     │      │  - song time (t0..tN)  │  │
  └──────────┘  │   └──────┬────────┘      └───────────┬────────────┘  │
                │          │ pose samples              │ songTimeMs     │
  Audio file ───┼──────────┼───────────────────────────┘                │
                │          ▼                                            │
                │   ┌───────────────┐      ┌────────────────────────┐  │
                │   │  Capture      │──────│  Capture Store          │  │
                │   │  Buffer       │ write│  (.scp capture bundle)  │  │
                │   └───────────────┘      └───────────┬────────────┘  │
                │                                       │ read           │
                │   ┌───────────────┐      ┌────────────▼────────────┐  │
                │   │ Visualization │◀─────│  Playback / Replay      │  │
                │   │ (trails, ghost│      │  engine                 │  │
                │   │  timeline)    │      └─────────────────────────┘  │
                │   └───────────────┘                                   │
                │                                                       │
                │   ┌──────────────────────────────────────────────┐   │
                │   │  Transcription pipeline (offline / async)     │   │
                │   │  capture → features → events → notes/rails/   │   │
                │   │  walls → .synth   (reuses Product A writer)    │   │
                │   └──────────────────────────────────────────────┘   │
                └──────────────────────────────────────────────────────┘
```

### 3.2 Layered design

We keep a hard separation between **capture** (must be real-time, lossless,
hardware-coupled) and **transcription** (offline, iterative, swappable). The
durable capture file is the contract between them.

| Layer | Responsibility | Real-time? | Swappable? |
|-------|----------------|------------|-----------|
| **L0 Hardware abstraction** | OpenXR/SteamVR/Quest pose & input | Yes | Vendor-specific backends behind one interface |
| **L1 Capture runtime** | Sample, timestamp, buffer, persist | Yes (hot path) | No — stability-critical |
| **L2 Capture store** | Durable serialization (the `.scp` bundle) | No | Versioned format |
| **L3 Replay / visualization** | Render captured motion for review | Soft real-time | Yes |
| **L4 Feature extraction** | Velocity, acceleration, jerk, hand crossings, dwell | No (offline) | Yes |
| **L5 Transcription** | Map features → notes / rails / walls | No (offline) | **Yes — the iteration surface** |
| **L6 Beatmap writer** | Emit valid `.synth` (reuses Product A) | No | Versioned to game format |

The thesis of the whole architecture: **L0–L3 must be rock solid and rarely
change; L4–L5 are where we will iterate for a long time.** Decoupling them via
the capture file means we can re-transcribe a year-old recording with a better
algorithm and get a better map, with zero re-capture.

### 3.3 Implementation platform options

Two viable hosts for the capture runtime; we should pick one for MVP and keep
the format engine-agnostic.

- **Option A — Unity + OpenXR plugin.** Closest to Synth Riders' own stack,
  best Quest support, mature input/pose APIs, easy in-headset visualization.
  *Recommended for MVP* because it minimizes platform risk and gives us a
  built-in renderer for the visualization layer.
- **Option B — Native OpenXR (C/C++/Rust) capture daemon.** Leaner, runs
  alongside SteamVR with no game engine, lower overhead, but we have to build
  visualization separately. Better long-term for a "records in the background
  while you use any media player" companion.

> **Decision for the planning pass:** target **Unity + OpenXR** for the MVP
> capture+visualization client; keep the capture *format* and the
> transcription pipeline as a separate, engine-independent module (likely
> Python or TypeScript) so it can run offline and reuse Product A.

---

## 4. Data Format

The capture file is the single most important artifact in Product B. It is the
durable, append-friendly, lossless record of a performance. Everything else can
be regenerated from it.

### 4.1 The `.scp` capture bundle

A capture is a **bundle** (a directory or zip) — call it a `.scp` bundle —
containing:

```
take_2026-06-13_0521.scp/
├── manifest.json          # metadata, clock, hardware, song reference
├── motion.ndjson          # newline-delimited per-frame pose samples (the bulk)
├── input.ndjson           # discrete button/trigger/grip events
└── audio.ref              # path/hash of the song (audio not embedded by default)
```

We use **NDJSON (newline-delimited JSON)** for the motion stream because:

- It is append-only and crash-resilient — a hard stop mid-take loses at most the
  last line, not the whole file.
- It streams without loading the whole take into memory.
- It is trivially inspectable and diffable during development.
- It can be transparently swapped for a binary columnar format later (see §4.4)
  once the schema stabilizes — the manifest declares the encoding.

### 4.2 `manifest.json`

```jsonc
{
  "formatVersion": "0.1.0",
  "captureId": "b1c2...uuid",
  "createdAtUtc": "2026-06-13T05:21:41Z",
  "song": {
    "title": "Example Track",
    "artist": "Example Artist",
    "durationMs": 198000,
    "bpm": 128,            // optional; nullable if unknown
    "audioHashSha256": "…", // ties capture to a specific audio file
    "audioRef": "audio.ref"
  },
  "clock": {
    "sampleRateHz": 90,     // nominal; real timestamps are authoritative
    "monotonicEpochNs": 123456789,
    "songT0MonotonicNs": 123900000,  // when song playback time = 0
    "unit": "nanoseconds"
  },
  "hardware": {
    "runtime": "OpenXR",            // OpenXR | SteamVR | OculusMobile
    "hmdModel": "Quest 3",
    "controllerModel": "Quest Touch Plus",
    "trackingSpace": "stage",       // local | stage (room-scale origin)
    "handedness": "right"            // dominant hand, performer-declared
  },
  "performer": { "id": "anon", "heightCm": 178 },
  "encoding": { "motion": "ndjson", "input": "ndjson" },
  "counts": { "motionFrames": 17820, "inputEvents": 64 }
}
```

`heightCm` and `trackingSpace` matter because Synth Riders note positions are
*body-relative* — we need to normalize away the performer's stature and the
room origin (see §8).

### 4.3 `motion.ndjson` — one line per sampled frame

Each line is a self-contained record. Positions in **meters**, rotations as
**unit quaternions**, in a right-handed, Y-up tracking space (OpenXR
convention).

```jsonc
{
  "t": 1041666,            // song time in microseconds since songT0
  "mt": 124941666,         // monotonic device time (ns) — authoritative
  "hmd": { "p": [0.01, 1.62, -0.30], "q": [0,0,0,1] },
  "l":   { "p": [-0.34, 1.20, -0.45], "q": [0.1,0,0,0.99], "v": [0.8,-0.2,0.1] },
  "r":   { "p": [0.31, 1.25, -0.40],  "q": [0.0,0,0,1.0],  "v": [-0.5,0.3,0.2] },
  "ok":  { "hmd": true, "l": true, "r": true }  // per-device tracking validity
}
```

- `p` = position (m), `q` = orientation quaternion (x,y,z,w), `v` = linear
  velocity (m/s) when the runtime provides it (OpenXR can; otherwise derived
  offline).
- `ok` flags tracking validity per device so the transcriber can ignore frames
  where a controller was occluded / lost.
- `t` is song time (the thing we transcribe against); `mt` is the
  hardware-authoritative monotonic timestamp (the thing we trust for jitter /
  dropped-frame analysis).

### 4.4 `input.ndjson` — discrete control events

```jsonc
{ "t": 1041700, "device": "r", "control": "trigger", "action": "down", "value": 1.0 }
{ "t": 1100200, "device": "l", "control": "grip",    "action": "up",   "value": 0.0 }
```

These are **explicit authoring signals**. A trigger pull during a take can mean
"emphasize this as a hard note" or "start a rail here" — giving the performer a
deliberate, in-the-moment way to annotate their choreography while dancing.

### 4.5 Forward-compat notes

- `formatVersion` is semver; readers must tolerate unknown fields.
- Binary upgrade path: once schema is stable, motion can move to Parquet /
  a flat float32 columnar buffer (timestamp, 3×pos, 4×quat, 3×vel per device =
  ~30 floats/frame). At 90 Hz that's ~10.8 KB/s/device, ~2 MB/min for three
  devices uncompressed — small. NDJSON is fine for MVP.

### 4.6 Relationship to the Synth Riders `.synth` format (the output)

The `.scp` bundle is **not** a beatmap. The beatmap is the *transcription
output*. Synth Riders custom maps are distributed as `.synth` files — a
JSON-based, zipped structure describing, per difficulty:

- **Notes (single hits):** a time + a normalized 2D position in the play plane,
  colored/assigned to left or right hand.
- **Rails:** ordered sequences of connected note points forming a continuous
  path the hand traces.
- **Walls:** positioned/oriented obstacles the body must avoid (lean, crouch,
  sidestep).
- Plus timing/BPM, bookmarks, and per-difficulty metadata.

> We will confirm the exact `.synth` schema against Product A's existing writer
> before implementing L6, since Product A already encodes that knowledge. This
> doc intentionally does not restate fields we have not re-verified.

---

## 5. OpenXR / SteamVR / Quest Considerations

### 5.1 Why OpenXR as the primary abstraction

OpenXR is the cross-vendor standard that SteamVR (Valve), Quest (Meta), and
others implement. Targeting OpenXR gets us PCVR (via SteamVR's OpenXR runtime)
**and** standalone Quest (via Meta's mobile OpenXR runtime) from largely one
codebase. We treat OpenXR as L0 and keep vendor quirks behind it.

### 5.2 Pose acquisition

- Use **`XR_REFERENCE_SPACE_TYPE_STAGE`** (room-scale, floor-origin) when
  available so positions are floor-referenced and reproducible; fall back to
  `LOCAL` and record which we used (manifest `trackingSpace`).
- Query HMD via the **view space**; query controllers via **action spaces**
  bound to the `/grip` (and optionally `/aim`) poses through the OpenXR action
  system.
- Prefer **`xrLocateSpace`** with predicted display time per frame, and capture
  **velocities** from `XR_SPACE_VELOCITY` when the runtime supplies them — this
  saves us from numerically differentiating noisy positions later.
- Record **pose validity flags** (`*_POSITION_TRACKED_BIT`,
  `*_ORIENTATION_TRACKED_BIT`). Inside-out tracking *will* drop controllers when
  they leave the camera frustum (behind the back, near the hips, hands together)
  — exactly the poses dancers hit. We must record validity, not silently
  interpolate.

### 5.3 Timing

- Capture at the headset's native refresh (72 / 80 / 90 / 120 Hz depending on
  device). Do **not** assume a fixed rate — store per-frame timestamps.
- Use the OpenXR predicted display time / a monotonic clock as authoritative;
  derive song time from `songT0`. Audio-visual sync to within one frame is the
  bar.

### 5.4 Platform-specific notes

| Concern | SteamVR / PCVR | Quest standalone | Quest (Link/AirLink to PC) |
|--------|----------------|-------------------|----------------------------|
| Runtime | SteamVR OpenXR | Meta mobile OpenXR | SteamVR OpenXR over Link |
| Compute headroom | High (PC) | Constrained (mobile SoC; capture must be cheap) | High |
| Tracking | Lighthouse (if Index/Vive) = very stable; or inside-out | Inside-out only | Inside-out, but PC renders |
| File storage | Local disk, easy | App sandbox / scoped storage; export friction | PC disk |
| Audio playback | Easy, low-latency | Watch for output latency; calibrate `songT0` | Easy |
| Distribution | Steam / sideload | Meta Store review **or** sideload (SideQuest) | n/a |

Key Quest-standalone risks: **audio output latency** (must be measured and
compensated so motion isn't systematically offset from the beat), **thermals /
CPU budget** (capture loop must be lightweight), and **storage/export** (getting
the `.scp` bundle off the headset). For MVP, Quest-via-Link or PCVR sidesteps
the storage and compute constraints; native standalone Quest is a fast-follow.

### 5.5 Coordinate-system normalization

All three runtimes deliver poses in a right-handed, Y-up, meters space, but with
**different room origins and player heights**. Before transcription we normalize
into a **body-relative frame**: re-origin around the HMD's horizontal position,
align forward to the HMD yaw (or a calibration pose), and scale by performer
height. This makes a tall and a short performer's identical *gesture* map to the
same Synth Riders note position.

---

## 6. Motion Recording Pipeline

The recording hot path. Priority order: **never drop a frame, never lose the
file, stay in sync.** Quality of transcription is a *later* concern.

```
 per-frame (on the XR frame loop, 72–120 Hz):
   1. xrWaitFrame / xrBeginFrame      → get predicted display time
   2. locate HMD view space           → pose + velocity + validity
   3. locate L grip action space      → pose + velocity + validity
   4. locate R grip action space      → pose + velocity + validity
   5. poll input actions              → trigger/grip/button deltas
   6. stamp song time t = now - songT0
   7. push CaptureFrame to lock-free ring buffer   (cheap; no I/O here)
   8. xrEndFrame (render minimal visualization)

 background writer thread:
   - drains ring buffer
   - serializes frames to motion.ndjson (batched, fsync periodically)
   - serializes input events to input.ndjson
   - updates manifest counts on stop
```

### Design rules

- **Decouple sampling from I/O.** The XR frame loop only writes to an in-memory
  lock-free buffer; a separate thread does serialization and disk flush. Disk
  hiccups must never stall tracking.
- **Append-only, periodic fsync.** Crash resilience: a power loss costs the last
  unflushed batch, never the whole take.
- **Authoritative timestamps over assumed rate.** Store real per-frame times;
  treat `sampleRateHz` as nominal.
- **Record gaps honestly.** Dropped/occluded frames get `ok=false`, not silent
  interpolation. Interpolation is a *transcription-time* decision, reversible
  and tunable.
- **Calibration step.** Before/at take start, capture a known pose (e.g., "hands
  at sides" or "arms forward") and audio-latency calibration, to seed the
  body-relative normalization and the `songT0` offset.
- **Take = one file.** Re-records are new bundles; no in-place mutation of raw
  captures (raw capture is immutable source of truth).

### Failure handling

- Controller lost mid-take → keep recording, flag invalid frames, surface a
  warning in review.
- Song playback drift → we trust `mt` (monotonic) and re-derive `t`; if audio
  engine reports its own clock, reconcile and log drift.
- App crash → on next launch, detect a partially-written bundle, finalize the
  manifest from the NDJSON line count, mark it `recovered`.

---

## 7. Motion Visualization Pipeline

Visualization exists to make captured data **trustable and reviewable** before
we invest in transcription. If the replay looks right, the data is right.

### MVP visualization

- **Ghost replay:** three objects (head + two hands) animated from
  `motion.ndjson`, synced to the audio via the shared timeline.
- **Motion trails:** fading ribbons behind each hand (last N frames) to reveal
  the *shape* of gestures — the thing that becomes rails.
- **Timeline scrubber:** play / pause / scrub / loop a region; current song time
  and frame index displayed.
- **Validity overlay:** frames where a controller was lost are visibly marked so
  the creator knows where the data is thin.
- **Take comparison (stretch):** overlay two takes to pick the better one.

### Transcription-debug visualization (L4/L5 dev tool)

A developer-facing overlay that draws the *derived* features on top of the
motion: velocity-peak markers (candidate notes), detected hand-crossings, dwell
regions (candidate rail anchors), and the proposed notes/rails/walls. This is
how we'll tune transcription — see the algorithm's interpretation against the
real movement.

### Rendering approach

- In Unity (MVP host): simple primitives + `TrailRenderer` + a line/ribbon for
  trails; a 2D timeline UI. Runs both on the desktop "monitor" view and
  optionally in-headset.
- Keep the visualizer reading the **same** `.scp` format the transcriber reads —
  one parser, no divergence.

### 7.1 Current developer tooling (offline, no VR required)

Ahead of any VR runtime, a small Python toolchain already lets us generate,
inspect, and visualize recordings using the `synthcopilot.motion` data model.
All of it runs on a laptop with **no headset attached**.

**Generate a fake dance recording** (deterministic, 30s @ 60 Hz by default):

```bash
python3 tools/generate_fake_motion.py
# writes debug/fake_motion_recording.json (1800 frames)

# options:
python3 tools/generate_fake_motion.py \
    --output debug/fake_motion_recording.json \
    --duration 30 --sample-rate 60 --bpm 120 --seed 1234
```

It synthesizes a headset bob/sway, left/right controller side-to-side sweeps,
periodic two-hand expansions, and alternating forward punches — enough motion
shape to build the rest of the pipeline against before real capture exists.

**Visualize a recording** (requires `pip install matplotlib`):

```bash
python3 tools/visualize_motion.py \
    --input debug/fake_motion_recording.json \
    --output debug/motion_plot.png
```

This writes **three** PNGs:

| File | View | Axes |
|------|------|------|
| `debug/motion_plot.png` | 3D trajectory of all three devices | X / Z / Y (Y up) |
| `debug/motion_plot_topdown.png` | top-down floor plane | X (right) / Z (forward) |
| `debug/motion_plot_frontview.png` | front / camera-facing | X (right) / Y (up) |

Each trajectory is colored by device (left = blue, right = pink, head = green,
matching Synth Riders hand colors) and shaded light→dark over time, with a
circle at the start and a square at the end. Pass `--no-time-color` for solid
lines. The visualizer reads the same `MotionRecording` JSON that real capture
will write, so it works unchanged once hardware capture lands.

**Analyze a recording** (stdlib only — the Product B "evaluator/debug report"):

```bash
python3 tools/analyze_motion.py \
    --input debug/fake_motion_recording.json \
    --output debug/motion_report.json
```

This runs the analysis pipeline (smooth → velocities → `segment_motion`), prints
a human-readable summary, and saves a JSON report containing: duration, sample
rate, average/peak hand speed (left, right, combined), segment count, per-
primitive counts, and a time-ordered timeline of detected movement segments. It
reports *choreography intent only* — no Synth Riders notes are produced.

---

## 8. How Captured Movement Becomes Notes, Rails, and Walls

This is the heart of Product B's long-term value and the **hardest** part. We
plan it now but build it incrementally (and **not** in the MVP at quality).

### 8.1 The transcription pipeline (offline, iterative)

```
.scp capture
   │
   ▼  (L4) preprocessing & features
   ├─ resample to a uniform grid (e.g. 120 Hz) with gap-aware interpolation
   ├─ normalize to body-relative frame (re-origin on HMD, scale by height)
   ├─ smooth (low-pass) position; derive velocity, acceleration, jerk
   ├─ project hand positions onto the Synth Riders play plane (the 2D target grid)
   ├─ detect events:
   │     • velocity minima/direction reversals  → hit candidates
   │     • sustained smooth travel               → rail candidates
   │     • explicit trigger/grip input events    → forced annotations
   │     • torso/head displacement & ducking     → wall candidates
   ▼  (L5) musical alignment & symbol assignment
   ├─ snap event times to the song's beat grid (BPM/subdivision) — *here* audio
   │     analysis from Product A returns as a *supporting* signal, not the source
   ├─ assign handedness (which controller → left/right note color)
   ├─ cluster nearby travel into rails; fit a smooth polyline of rail points
   ├─ deduplicate / enforce playability constraints (min spacing, reachability)
   ▼  (L6) emit
   └─ write notes + rails + walls into a valid .synth (reuse Product A writer)
```

### 8.2 Notes (single hits)

A **note** is a deliberate strike. Signals:

- A hand's velocity peaks then sharply decelerates / reverses (a "punch" or
  "hit") → strong note candidate at the moment of the velocity minimum/reversal.
- An explicit trigger press (§4.4) → forced note.
- Position at the hit, projected onto the play plane and normalized, becomes the
  note's 2D location. The controller (L/R) drives the note color/hand.

### 8.3 Rails (continuous traces)

A **rail** is a held, flowing path. Signals:

- Sustained, smooth, relatively high-velocity travel **without** a sharp
  reversal — the hand is *drawing*, not *striking*.
- Optionally bracketed by trigger-down → trigger-up to let performers explicitly
  author rails in the moment.
- The path is downsampled/fit into an ordered set of rail points; we enforce
  smoothness and minimum segment spacing for playability.

> **Implemented (first pass):** `synthcopilot/motion_to_map.py` is the initial
> rails-only bridge. `normalize_motion_to_playfield` maps body-relative hand
> positions into a normalized `[-1,1]×[-1,1]` Synth Riders-style playfield (X =
> left/right, Y = down/up; depth dropped — time is the approach axis).
> `generate_rails_from_motion` takes the detected `MovementSegment`s, merges a
> hand's continuous-motion primitives (sweeps, lifts/drops, circles) into bounded
> expressive spans, then downsamples + smooths + clamps + jitter-filters each
> span into a `Rail` of `RailNode`s. Node times are *lightly* snapped to the beat
> grid via `synthcopilot/timing.py` (`seconds_to_beat` / `beat_to_seconds` /
> `snap_time_to_grid`): start/end anchors snap onto the grid for clean downbeats,
> while internal nodes snap only within a tight tolerance so the dancer's
> expressive off-grid timing survives. A `difficulty` knob (`Easy`…`Master`)
> controls node spacing, rail length, and grid subdivision. Notes and walls are **not** generated yet, and no
> `.synth` file is written — the `Rail.to_dict()` output is a clean intermediate
> for a future `.synth` writer.

### 8.4 Walls (body obstacles)

A **wall** is something the body avoids — and in capture terms, a wall is the
*shadow* of a body movement: a lean, a duck, a sidestep. Signals:

- Large HMD horizontal displacement (lean/dodge left/right) → a wall positioned
  so that the recorded motion would have dodged it.
- HMD vertical drop (crouch/duck) → an overhead wall.
- We *invert* the avoidance: the performer's dodge defines where the obstacle
  must have been. This is the elegant payoff of capturing the head, not just the
  hands.

### 8.5 The role of music analysis now

Product A's audio analysis is **not discarded** — it is demoted to a *support
role*: providing BPM/beat grid for **snapping** event times, helping align takes
to the song, and offering optional accent cues. The *choreography* comes from
the body; audio just helps quantize it to feel musical.

### 8.6 Human-in-the-loop assumption

We assume — for a long time — that transcription output is a **draft** a human
reviews and edits, not a finished map. The pipeline's job is to get a creator
80% of the way from "a dance I performed" to "a map I'd ship," fast. Full
auto-quality is an aspiration, not a near-term promise.

### 8.7 Coordinate systems: capture-normalized vs `.synth` (deferred)

There are **two** coordinate spaces, kept strictly separate by a single boundary
layer (`synthcopilot/coordinate_systems.py`):

1. **Normalized capture/map space** — a hardware- and game-agnostic `[-1, 1]`
   space (`NormalizedPoint`, range `NORMALIZED_RANGE`). *All* motion capture,
   analysis, and map-object generation happen here. Every generated rail node is
   a normalized point.
2. **Synth space** — the final Synth Riders `.synth`/editor units (`SynthPoint`).

Conversion happens in **exactly one place**: the future writer/export step,
which applies a `CoordinateMappingProfile` (per-axis `synth = normalized *
scale + offset`). Nothing upstream of export ever touches `.synth` units.

> **We intentionally do NOT know the real Synth Riders coordinate range yet,**
> and we refuse to invent it. The default `DEFAULT_NORMALIZED_PROFILE` is
> identity/pass-through, so motion never gets silently scaled into made-up units.
> A `SYNTH_RIDERS_UNVERIFIED_PROFILE` placeholder marks the remaining work; its
> values are `NaN` so `validate_profile()` rejects it and it cannot be used by
> accident.
>
> **Before any real `.synth` emission**, we must *verify* the coordinate scale
> and offset from a **known editor-exported `.synth` file**: export a beatmap
> from the Synth Riders editor, read back note/rail/wall positions, solve for the
> per-axis scale/offset, and only then fill in a verified profile. Until that
> measurement exists, export stays in normalized space.

---

## 9. What We Are NOT Building Yet

Explicit non-goals for this phase, to keep scope honest:

- **No high-quality automatic transcription in the MVP.** We build the
  *pipeline shape* and heuristic stubs; we do not promise ship-ready maps from
  one click.
- **No in-headset map editor / authoring UI.** Review and re-take only.
- **No full-body / hip / feet / finger tracking.** Three points (HMD + 2
  controllers) only.
- **No cloud, accounts, sharing, or multiplayer.** Local files only.
- **No native standalone-Quest store release.** PCVR / Quest-via-Link first;
  standalone is a fast-follow.
- **No deletion or rewrite of Product A.** Its code stays; we *reuse* its
  `.synth` writer and audio analysis later, untouched in this pass.
- **No real-time transcription.** Transcription is offline/async by design.
- **No binary capture format yet.** NDJSON until the schema stabilizes.
- **No music-library / DRM integration.** Creator supplies their own local
  audio.

---

## 10. Risks and Unknowns

| # | Risk / Unknown | Impact | Mitigation / next step |
|---|----------------|--------|------------------------|
| R1 | **Audio↔motion sync / output latency** (esp. Quest standalone) | High — systematic offset ruins map timing | Build a latency calibration step; trust monotonic clock; measure per-device output latency |
| R2 | **Inside-out tracking loss** during dance poses (hands behind back, at hips, together) | High — gaps exactly where dancers move | Record validity flags; gap-aware interpolation at transcription; consider Lighthouse for authoring |
| R3 | **`.synth` format drift** (Synth Riders updates its custom-map schema) | Med — breaks L6 writer | Pin to Product A's verified writer; version the writer; re-verify schema before L6 work |
| R4 | **Transcription quality** — heuristics may produce unplayable / un-fun maps | High — it's the core value | Human-in-the-loop drafts; debug visualization; iterate L5 with the capture corpus |
| R5 | **Body-relative normalization** across heights / room origins | Med — note positions inconsistent between performers | Calibration pose; normalize on HMD + height; validate with multi-performer captures |
| R6 | **Distinguishing notes vs rails vs incidental motion** | High — ambiguous gestures | Explicit trigger annotation as ground truth; tune thresholds against labeled takes |
| R7 | **Quest compute/thermal budget** for capture loop | Med | Keep hot path I/O-free (ring buffer + bg writer); profile on-device |
| R8 | **Storage / export off-headset** (Quest sandbox) | Med | PCVR first; design export/share path before standalone release |
| R9 | **Engine choice lock-in** (Unity vs native) | Med | Keep format + transcription engine-independent so the capture host is replaceable |
| R10 | **Legal/IP** — performers dancing to copyrighted songs; what's distributed | Med | Distribute *maps/captures*, not audio; creators supply own audio; revisit before any sharing feature |
| R11 | **Velocity availability** varies by runtime | Low | Capture `XR_SPACE_VELOCITY` when present; derive numerically otherwise |
| R12 | **Re-take UX / take management** sprawl | Low | One-file-per-take, immutable raw captures, clear metadata |

### Open questions to resolve before/during implementation

1. What exactly does Product A's `.synth` writer assume about note/rail/wall
   structure? (Re-verify the schema from the existing code.)
2. Unity vs native OpenXR for the MVP host — confirm the §3.3 recommendation
   with a tracking-loop spike.
3. What is the minimum viable transcription heuristic that yields a *playable*
   (not great) map, to validate the full pipeline end to end?
4. How do we want creators to annotate intent during a take (trigger = note?
   grip = rail?) — needs a quick in-VR ergonomics test.
5. Target headset(s) and tracking system for the *first* authoring experience
   (Index/Lighthouse for stability vs Quest for reach).

---

## 11. Incremental Implementation Roadmap

Each milestone is independently demonstrable and de-risks the next. **No
generator/Product-A logic is modified until Milestone 6.**

### M0 — Planning & format spec *(this document)*
- ✅ Define product, architecture, and the `.scp` capture format.
- Deliverable: this doc. No code.

### M1 — Capture spike (prove tracking + timestamps)
- Minimal OpenXR (or Unity+OpenXR) loop that reads HMD + 2 controller poses and
  prints them with timestamps and validity flags.
- **Exit:** stable 72–90 Hz pose stream with authoritative timestamps; we can
  see tracking-loss flags fire.

### M2 — Capture store (lossless persistence)
- Ring buffer + background writer; write `manifest.json` + `motion.ndjson` +
  `input.ndjson`; crash-recovery finalize.
- **Exit:** record a 60s take, kill the app, reopen the bundle intact; no frame
  loss; periodic fsync verified.

### M3 — Song sync
- Load + play a local audio file; establish `songT0`; add audio-latency
  calibration; stamp song time onto every frame.
- **Exit:** motion and audio share one timeline; sync within one frame on the
  target device.

### M4 — Visualization / replay (make it trustable)
- Ghost replay of the three points + motion trails + timeline scrubber +
  validity overlay, synced to audio.
- **Exit:** a creator can watch an accurate synced replay of their take — **MVP
  capture loop complete.**

### M5 — Transcription scaffolding (offline, engine-independent)
- L4 feature extraction (resample, normalize, velocity/accel/jerk, play-plane
  projection) + a debug visualization of derived events.
- Emit an **intermediate events file** (candidate notes/rails/walls) — *not* a
  `.synth` yet.
- **Exit:** we can see derived candidate events overlaid on a real take.

### M6 — First end-to-end `.synth` (reuse Product A writer)
- L5 alignment/assignment (beat-snap via Product A's audio analysis as support;
  handedness; rail fitting; wall inversion) + L6 emit via Product A's existing
  `.synth` writer.
- **Exit:** a recorded dance produces a *playable* (not necessarily great)
  Synth Riders map, loadable in-game. First time we touch Product A — by
  **reuse**, not modification.

### M7 — Quality & human-in-the-loop
- Tune heuristics against a corpus of labeled takes; trigger/grip intent
  annotation; basic draft-editing affordances; multi-performer normalization
  validation.
- **Exit:** maps feel human; creators reliably get to ~80% with light editing.

### Later (post-roadmap)
- Native standalone-Quest release + on-device storage/export.
- In-headset authoring/editing.
- Sharing / community corpus.
- Optional full-body tracking inputs.

---

## 12. Summary

Product B reframes SynthCoPilot from a *predictor of choreography* into a
*recorder and transcriber of real human choreography*. The architecture's spine
is a **lossless, durable, body-relative motion capture format (`.scp`)** that
cleanly separates a rock-solid real-time capture path from an iterative,
offline transcription pipeline. The MVP proves **capture → store → visualize**;
transcription into notes, rails, and walls is planned in detail but built
incrementally, reusing — not rewriting — Product A's audio analysis and `.synth`
writer. We are explicit about what we are *not* building yet, and the roadmap
de-risks the hardest part (transcription) only after the capture foundation is
trustworthy.
