# SynthCoPilot

**SynthCoPilot captures human VR movement and turns it into a Synth Riders draft map.**

Instead of guessing choreography from audio, you *dance the map you want*: put on a
headset, play a song, move naturally, and SynthCoPilot records your headset and
controller motion and transcribes it into rails and notes you can refine in the
Synth Riders editor. **Dance first; map second.**

---

## 1. What it does

1. **Records** your headset + both controllers while you dance to a song.
2. **Analyzes** that motion into movement primitives — sweeps, lifts, punches,
   two-hand expansions, circular motion.
3. **Transcribes** the motion into Synth Riders objects:
   - **rails** that follow your actual hand paths, and
   - **notes** placed as checkpoints on your motion (punches, big accents, sweep
     beats), lightly snapped to the song's beat.
4. **Exports** a draft you open and polish in the Synth Riders editor.

The output is a **draft**, not a finished map — see status below.

## 2. Current status

Honest picture of what works today:

| Piece | Status |
|------|--------|
| Motion data format + validation | ✅ working |
| Motion analysis → movement segments | ✅ working |
| Motion → rails + notes (draft map) | ✅ working |
| Visualization & analysis reports | ✅ working |
| Run the whole pipeline on **fake** motion (no headset) | ✅ working |
| **Real VR capture** (headset/controllers) | 🧪 **experimental** — scaffolding only; see [`vr_recorder/`](vr_recorder/) |
| Editor-importable **`.synth`** export | ⏳ **not yet** — currently exports a normalized-JSON draft (see below) |

Three things to be upfront about:

- **Real VR capture is experimental.** The recording pipeline and data format are
  done and tested, but the actual headset/controller backends are unbuilt stubs
  (no hardware in our test environment). Today you capture with a **synthetic**
  (fake) motion source, or build a real recorder from the provided spec.
- **Generated maps still need editor polish.** SynthCoPilot gets you most of the
  way from "a dance I performed" to "a map I'd play," fast — but you finish it in
  the Synth Riders editor. It is not a one-click finished map.
- A real `.synth` file also needs a **verified coordinate scale** we haven't
  measured yet, so for now the map is written as normalized JSON (the same data,
  minus final Synth Riders units). See [the design doc](docs/VR_CHOREOGRAPHY_CAPTURE.md) §8.7.

## 3. Quickstart without VR hardware (fake motion)

You can try the whole pipeline right now with no headset. Requires **Python 3.11**.

```bash
# 1. Make a fake 30-second dance recording (stands in for a real capture)
python3 tools/generate_fake_motion.py
# -> debug/fake_motion_recording.json

# 2. Turn that motion into a draft map (rails + notes)
python3 -m synthcopilot motion-new \
    --motion debug/fake_motion_recording.json \
    --audio song.mp3 --bpm 123 \
    --output captured_dance.synth
# -> captured_dance.normalized.json  (normalized draft; see status)
```

That's the core loop: **motion in → draft map out.**

## 4. Motion capture workflow (recording your dance)

> 🧪 Real headset capture is experimental. Pick the path that matches you:

**A. No headset (today):** use the fake generator from step 3, or the synthetic
recorder:

```bash
python3 -m vr_recorder.python_recorder \
    --backend synthetic --song song.mp3 --duration 30 --bpm 123 \
    --output debug/recorded_session.json
```

This produces a valid recording from **fake** motion — good for trying the
pipeline, not a real performance.

**B. Build a real recorder (for developers with a headset):** two options are
specified in [`vr_recorder/`](vr_recorder/):
- **Unity + OpenXR** — full spec in [`vr_recorder/UNITY_RECORDER_SPEC.md`](vr_recorder/UNITY_RECORDER_SPEC.md)
  with a first-draft C# script. Recommended for Quest/PCVR.
- **Python + SteamVR/OpenXR** — lean recorder in
  [`vr_recorder/python_recorder/`](vr_recorder/python_recorder/) (hardware backends
  are stubs to be completed on a machine with a headset).

**Recording tips (for the eventual real capture):**
- Stand still in a relaxed **neutral pose for the first ~3 seconds** — this is
  used for calibration (centering you and finding which way you face).
- Then dance the song the way you'd want to *play* it: big, clear gestures read
  better than tiny ones.
- One song = one take = one file.

## 5. Convert motion to a map

```bash
python3 -m synthcopilot motion-new \
    --motion debug/fake_motion_recording.json \
    --audio my-song.mp3 \
    --bpm 123 \
    --difficulty Master \
    --output my-dance.synth
```

- `--bpm` helps line notes up to the beat (leave it off if you don't know it).
- `--difficulty` is `Easy`, `Normal`, `Hard`, `Expert`, or `Master` — higher
  difficulties make denser, finer maps.
- It prints a summary (rails, notes, sources) and writes the draft. Because real
  `.synth` export isn't wired up yet, you get `my-dance.normalized.json`.

## 6. Visualize the motion

See what was captured before (or after) transcription — a great sanity check.

```bash
# Plots: 3D trajectory + top-down + front views (needs matplotlib)
pip install matplotlib
python3 tools/visualize_motion.py \
    --input debug/fake_motion_recording.json \
    --output debug/motion_plot.png

# Text/JSON report: durations, hand speeds, detected movement timeline
python3 tools/analyze_motion.py \
    --input debug/fake_motion_recording.json \
    --output debug/motion_report.json
```

## 7. Evaluate / refine in the Synth Riders editor

The generated map is a **starting point**. Plan to:

1. Open the draft in the Synth Riders custom-map editor.
2. Play it through and feel where it flows and where it fights you.
3. Adjust: trim accidental notes, smooth or shorten rails, fix spacing, align
   anything that drifts off the beat.
4. Iterate — re-dance a section and re-transcribe, or hand-edit.

> Until editor-importable `.synth` export lands, treat the `.normalized.json`
> output as the *content* of the map (the rails/notes and where they sit). Use
> the visualizer to review it. Direct editor import is on the roadmap and depends
> on verifying the Synth Riders coordinate units.

## 8. Product A legacy commands

SynthCoPilot started as **Product A**: automatic *MP3-to-map* generation from
audio analysis. That approach didn't produce danceable maps — *music analysis is
not choreography* — so we pivoted to capturing real movement (Product B, above).

The old command is kept as a deprecated stub and is **not** part of this build:

```bash
python3 -m synthcopilot new     # prints a deprecation notice; use 'motion-new'
```

## 9. Developer docs

- **Design & architecture:** [`docs/VR_CHOREOGRAPHY_CAPTURE.md`](docs/VR_CHOREOGRAPHY_CAPTURE.md)
  (product concept, MVP, data format, OpenXR/SteamVR/Quest, recording &
  visualization pipelines, motion→notes/rails/walls, coordinate systems, roadmap).
- **Code map:**
  - `synthcopilot/motion.py` — the `MotionRecording` data model + JSON I/O.
  - `synthcopilot/motion_calibration.py` — raw room → canonical frame.
  - `synthcopilot/motion_analysis.py` — motion → movement segments.
  - `synthcopilot/motion_to_map.py` — segments → rails + notes.
  - `synthcopilot/timing.py` — seconds↔beats, beat snapping.
  - `synthcopilot/coordinate_systems.py` — normalized ↔ `.synth` boundary.
  - `synthcopilot/cli.py` — the `motion-new` / `new` commands.
  - `tools/` — fake-motion generator, visualizer, analysis report.
  - `vr_recorder/` — experimental VR recorder (Unity + Python).
- **Tests:** `python3 -m unittest discover -s tests`

---

*SynthCoPilot is dance-first map creation: your movement is the choreography, and
the software is the recorder and transcriber.*
