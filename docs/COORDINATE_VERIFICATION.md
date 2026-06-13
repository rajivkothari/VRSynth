# Coordinate calibration checklist

How to measure the **real Synth Riders editor coordinate units** and turn them
into a verified `CoordinateMappingProfile`, so SynthCoPilot can eventually export
maps that land where the dancer actually moved.

> **Why this exists.** All of SynthCoPilot's motion capture, analysis, and map
> generation happen in a **normalized `[-1, 1]` space** on purpose. We have *not*
> measured how that space maps to real `.synth`/editor units, so we refuse to
> invent it (see [`VR_CHOREOGRAPHY_CAPTURE.md`](VR_CHOREOGRAPHY_CAPTURE.md) §8.7).
> This document is the manual loop that closes that gap. **Until this loop is
> completed and the profile is verified, real `.synth` export is NOT solved** —
> the pipeline writes a normalized-coordinate draft instead.

## Status of each step (be honest about what's wired)

| Step | Tooling state |
|------|---------------|
| 1–3 Make & export a test map | manual, in the official editor |
| 4 Inspect the `.synth` | ✅ `tools/inspect_synth_coordinates.py` works today |
| 5 Create a verified profile | ✅ author + `CoordinateMappingProfile.save/load` (JSON), or edit `coordinate_systems.py` |
| 6 Re-run with the profile | ◐ **profile flag + conversion boundary wired**; the real `.synth` writer is still pending (see "Pending work"). With a valid profile the pipeline now converts coordinates and writes a synth-space *preview*, status `profile_applied_no_real_writer`. |
| 7 Import & visually confirm | ⏳ blocked on the real `.synth` writer |

So today you can complete steps 1–5 fully, and step 6 up to the conversion
boundary (coordinates are pushed through the profile and a synth-space preview is
written). Importing into the editor (step 7) still needs the real `.synth` writer
— the last item in "Pending work".

---

## The loop

### 1. Open the Synth Riders editor
Use the **official** Synth Riders custom-map editor (not SynthCoPilot). You want
ground truth from the real game tool.

### 2. Create a tiny test map with notes at known visual positions
Make a short map (a few seconds is enough) and place single notes at the
**extremes** of the playfield, so the measured min/max bracket the real range.
Place one note at each, and note *which hand/color* you used:

- [ ] **center** (as close to dead-center as the grid allows)
- [ ] **far left**
- [ ] **far right**
- [ ] **low** (bottom of the play area)
- [ ] **high** (top of the play area)
- [ ] **near / far** *if the editor exposes a depth axis you can place by hand*
      (often you can't — depth is usually time; see the Z note below)

Tips:
- Put the left-extreme note on the **left** color and the right-extreme on the
  **right** color, so the per-hand ranges in the report are meaningful.
- Hitting the actual edges matters: the inspector measures the *observed* extent,
  so a note that doesn't reach the edge will under-report the range.
- A couple of **rails** that sweep edge-to-edge are a useful bonus (they add rail
  nodes spanning the range).

### 3. Export the `.synth`
Export/save the map to a `.synth` file from the editor. Keep it somewhere handy,
e.g. `fixtures/known_center_edges.synth`.

### 4. Run the inspector
```bash
python3 tools/inspect_synth_coordinates.py --input fixtures/known_center_edges.synth
# add more files to aggregate:  --input a.synth b.synth
# -> prints a summary and writes debug/synth_coordinate_report.json
```
Read the output:
- **min/max/mean** for X, Y, Z and the **per-hand** ranges.
- The **Z interpretation** line: is Z a beat / seconds / editor-depth, or spatial?
  (The tool decides by correlating Z with each note's time key.)
- The **RECOMMENDED CoordinateMappingProfile** (marked `UNVERIFIED`).

> The recommendation maps normalized `[-1, 1]` onto the **observed** extent. It is
> a *starting point derived from measurement*, not a verified answer.

### 5. Create a real `CoordinateMappingProfile`
Sanity-check the measured numbers (do the far-left/far-right X values look
symmetric? does low/high Y bracket the playing area? does Z behave as the
interpretation says?). Then encode them as a profile in
`synthcopilot/coordinate_systems.py`, replacing the `NaN` placeholder
`SYNTH_RIDERS_UNVERIFIED_PROFILE` with a real, named profile, e.g.:

```python
SYNTH_RIDERS_PROFILE_V1 = CoordinateMappingProfile(
    name="synth_riders_v1",
    x_scale=...,  y_scale=...,  z_scale=...,   # from the report (synth units per normalized unit)
    x_offset=..., y_offset=..., z_offset=...,  # observed center
    notes="measured from fixtures/known_center_edges.synth on <date>; editor vX.Y; "
          "verified by visual import (step 7).",
)
```
`synth = normalized * scale + offset` per axis. Confirm it passes
`validate_profile(...)` (no `NaN`, non-zero scales). **Do not mark it verified in
the `notes` until step 7 passes.**

### 6. Re-run the pipeline with the verified profile
Save the profile to JSON (`CoordinateMappingProfile.save(...)`), then pass it via
`--profile` to either entry point:
```bash
python3 tools/demo_motion_pipeline.py --audio song.ogg --bpm 123 --profile profile.json
# or
python3 -m synthcopilot motion-new --motion take.json --audio song.ogg \
    --bpm 123 --profile profile.json --output captured_dance.synth
```
What happens now (the boundary is wired):
- **No `--profile`** → writes the normalized-JSON draft; status `normalized_json_only`.
- **Invalid profile** → hard fails with a clear error (never exports a broken map).
- **Valid profile** → every `Note`/`RailNode` is converted via
  `normalized_to_synth(point, profile)` and a **synth-space preview**
  (`*.synth_space.json`) is written; status `profile_applied_no_real_writer`.

The status is **never** `real_synth_written` yet — the real `.synth` writer is the
remaining pending item, so step 7 (editor import) is still blocked.

### 7. Import the generated map into the editor and confirm placement
Open the exported map in the Synth Riders editor and **look at it**:
- Do notes that were on the **right** of your dance appear on the right?
- Do **high** reaches sit high, **low** sit low?
- Do rails trace the same shape you see in `tools/visualize_motion.py`?
- Is the timing sane (notes near the beats you expect)?

---

## What success looks like
- The inspector's far-left and far-right X are roughly **symmetric** about the
  center note, and low/high Y bracket the play area.
- The profile passes `validate_profile` and round-trips:
  `synth_to_normalized(normalized_to_synth(p, profile), profile) ≈ p`.
- A map exported through the profile **imports cleanly** and notes/rails land
  where the corresponding motion was — left is left, high is high, no mirroring,
  no squashing, no off-screen notes.
- Re-measuring the *exported* map with the inspector reproduces editor-range
  values (a closed loop).

## What failure looks like
- **Mirrored** placement (left↔right or up↔down) → a sign/handedness error in the
  scale or in the capture→normalized conversion; revisit `flip_z`/axis signs.
- **Squashed or overshooting** notes (everything bunched near center, or pushed
  off the edges) → wrong scale, or the test map didn't actually reach the edges
  so the observed extent was too small/large.
- **Constant offset** (whole map shifted) → wrong offset (center).
- **Timing drift** → Z was treated as a spatial axis when it's really time, or
  vice-versa; re-check the inspector's Z interpretation.
- Inspector reports **"insufficient spread"** / degenerate axis → your test notes
  didn't span that axis; add edge notes and re-export.

## Don't confuse normalized space with real editor units
- **Normalized space** is SynthCoPilot's internal `[-1, 1]` (`NormalizedPoint`,
  `NORMALIZED_RANGE`). Every rail/note the pipeline produces lives here.
- **Editor/`.synth` units** are whatever the inspector measures (`SynthPoint`).
- Conversion happens in **exactly one place** — the export step, via a
  `CoordinateMappingProfile`. Nothing upstream should ever contain editor units.
- The inspector reports **raw editor units** (it does not normalize); the pipeline
  reports **normalized** values. If a number looks like `0.5` it's probably
  normalized; if it looks like the editor's grid scale it's raw. Label which space
  any coordinate is in before reasoning about it.
- A normalized-draft file (`*.normalized.json`, `format: vrsynth_normalized_map`)
  is **not** an editor `.synth` — the inspector will say so if you point it there.

## Definition of done (do not claim `.synth` export is solved before this)
- [ ] A verified `CoordinateMappingProfile` exists (non-`NaN`, validated) with
      `notes` recording the source map, editor version, and date.
- [ ] A map exported through it was **imported into the editor and visually
      confirmed** (step 7) — left/right/high/low correct, timing sane.
- [ ] The exported map re-measures back to editor-range values.
- [ ] Only then update the README / docs to call real `.synth` export "working",
      and flip the status table in `VR_CHOREOGRAPHY_CAPTURE.md`.

Until every box above is checked, real `.synth` export remains **unsolved** and
the pipeline must keep emitting the normalized-coordinate draft.

---

## Pending work (to make step 7 runnable)

1. **A real `.synth` writer.** `synthcopilot/smh_io.write_synth` currently raises
   `SynthExportUnavailable`. It needs to write the editor `.synth` format (a ZIP
   with a `beatmap.meta.bin` JSON beatmap — the same format
   `tools/inspect_synth_coordinates.py` reads), consuming the synth-space points
   the boundary already produces. `synth_mapping_helper` can do the zip/format
   work if installed, or it can be written directly. When this lands,
   `export_track` returns `real_synth_written` and step 7 becomes possible.

   _Done in this pass:_
   - ✅ **Profile flag** — `tools/demo_motion_pipeline.py` and the `motion-new`
     CLI accept `--profile path.json`.
   - ✅ **Profile JSON I/O** — `CoordinateMappingProfile.save/load`.
   - ✅ **Conversion boundary** — `smh_io.export_track` applies the profile (via
     `normalized_to_synth`) with explicit statuses (`normalized_json_only`,
     `profile_applied_no_real_writer`, `real_synth_written`) and hard-fails on an
     invalid profile. Real `.synth` writing stays gated.
