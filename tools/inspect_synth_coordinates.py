"""Inspect real Synth Riders ``.synth`` files to MEASURE their coordinate system.

This is a **measurement-only** harness for closing the coordinate/export gate
(see ``docs/VR_CHOREOGRAPHY_CAPTURE.md`` §8.7). Given one or more known-good
``.synth`` files exported from the official Synth Riders editor, it extracts the
raw note/rail coordinates and reports their actual ranges, per-hand spreads,
outliers, and whether the Z field looks like a beat, seconds, or a spatial/depth
axis. From the measurements it derives a **recommended** (and clearly UNVERIFIED)
``CoordinateMappingProfile``.

It does NOT guess or hardcode the Synth Riders coordinate range, and it does NOT
modify the writer. Everything it reports comes from the input files.

Usage::

    python tools/inspect_synth_coordinates.py --input map1.synth map2.synth

Output: a printed summary plus ``debug/synth_coordinate_report.json``.

### Reading ``.synth``

A ``.synth`` is a ZIP archive whose ``beatmap.meta.bin`` member is JSON. We read
it with the standard library only (no ``synth_mapping_helper`` dependency), and
we read the **raw** editor coordinates straight from the JSON -- we do not apply
any normalization, because the raw numbers are exactly what we want to measure.
If a file is not a recognizable editor ``.synth`` we fail with a clear message.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
import zipfile
from pathlib import Path
from typing import Any, Iterable, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from synthcopilot.coordinate_systems import (  # noqa: E402
    CoordinateMappingProfile,
    validate_profile,
)

# Synth Riders note Type index -> hand/color (from the editor's beatmap format).
_NOTE_TYPES = ("right", "left", "single", "both")
_BEATMAP_MEMBER = "beatmap.meta.bin"
_DIFFICULTIES = ("Easy", "Normal", "Hard", "Expert", "Master", "Custom")


class SynthReadError(RuntimeError):
    """Raised when an input is not a readable Synth Riders ``.synth`` file."""


# --------------------------------------------------------------------------- #
# Reading
# --------------------------------------------------------------------------- #
def read_synth_beatmap(path: Path) -> dict[str, Any]:
    """Read the raw beatmap JSON from a ``.synth`` file (stdlib only).

    Raises :class:`SynthReadError` with an explanation if the file is not a
    recognizable editor ``.synth`` (a ZIP containing a JSON beatmap with a
    ``Track``).
    """
    if not path.exists():
        raise SynthReadError(f"{path}: file not found")

    if not zipfile.is_zipfile(path):
        # Be helpful about common non-editor inputs.
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except (ValueError, OSError):
            raise SynthReadError(
                f"{path}: not a Synth Riders .synth (expected a ZIP archive "
                f"containing '{_BEATMAP_MEMBER}'). "
                "Export a map from the official Synth Riders editor."
            )
        if isinstance(data, dict) and data.get("format") == "vrsynth_normalized_map":
            raise SynthReadError(
                f"{path}: this is a SynthCoPilot normalized draft, not an editor "
                ".synth. This tool measures real editor exports."
            )
        if isinstance(data, dict) and "Track" in data:
            return data  # a bare JSON beatmap (unusual, but usable)
        raise SynthReadError(
            f"{path}: JSON file without a 'Track' beatmap; not an editor .synth."
        )

    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        member = _BEATMAP_MEMBER if _BEATMAP_MEMBER in names else None
        if member is None:
            # Fall back to any member that decodes to a JSON beatmap.
            for name in names:
                try:
                    candidate = json.loads(zf.read(name).decode("utf-8-sig"))
                except (ValueError, UnicodeDecodeError):
                    continue
                if isinstance(candidate, dict) and "Track" in candidate:
                    return candidate
            raise SynthReadError(
                f"{path}: ZIP has no '{_BEATMAP_MEMBER}' (or any JSON beatmap) "
                f"member. Members: {names}"
            )
        raw = zf.read(member)
        try:
            return json.loads(raw.decode("utf-8-sig"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise SynthReadError(f"{path}: could not parse '{member}' as JSON ({exc})")


def iter_objects(beatmap: dict[str, Any], file_label: str) -> Iterable[dict[str, Any]]:
    """Yield one record per note/rail: hand, is_rail, nodes [(x,y,z)...], time_key."""
    track = beatmap.get("Track", {})
    if not isinstance(track, dict):
        return
    for difficulty, time_map in track.items():
        if not isinstance(time_map, dict):
            continue
        for time_key, notes in time_map.items():
            try:
                tk = float(time_key)
            except (TypeError, ValueError):
                tk = math.nan
            for note in notes or []:
                pos = note.get("Position")
                if not (isinstance(pos, (list, tuple)) and len(pos) >= 3):
                    continue
                segments = note.get("Segments")
                ttype = note.get("Type")
                hand = _NOTE_TYPES[ttype] if isinstance(ttype, int) and 0 <= ttype < 4 else "unknown"
                nodes = [tuple(float(c) for c in pos[:3])]
                is_rail = bool(segments)
                if is_rail:
                    for seg in segments:
                        if isinstance(seg, (list, tuple)) and len(seg) >= 3:
                            nodes.append(tuple(float(c) for c in seg[:3]))
                yield {
                    "file": file_label,
                    "difficulty": difficulty,
                    "hand": hand,
                    "type": ttype,
                    "is_rail": is_rail,
                    "nodes": nodes,
                    "time_key": tk,
                }


# --------------------------------------------------------------------------- #
# Analysis
# --------------------------------------------------------------------------- #
def _axis_stats(values: list[float]) -> dict[str, float]:
    if not values:
        return {"count": 0, "min": None, "max": None, "mean": None, "std": None}
    return {
        "count": len(values),
        "min": min(values),
        "max": max(values),
        "mean": statistics.fmean(values),
        "std": statistics.pstdev(values) if len(values) > 1 else 0.0,
    }


def _pearson(xs: list[float], ys: list[float]) -> Optional[float]:
    n = len(xs)
    if n < 3:
        return None
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return sxy / math.sqrt(sxx * syy)


def classify_z(records: list[dict[str, Any]], bpm: Optional[float]) -> dict[str, Any]:
    """Decide whether Z looks like a beat, seconds, an editor-depth, or spatial.

    Uses correlation of each note head's Z with its time key. A near-perfect
    correlation means Z is a time/depth axis, not a spatial one. When a strong
    time relationship and a BPM are available, reports the most consistent of a
    few unit hypotheses (Z≈beats, Z≈seconds) by coefficient of variation.
    """
    time_keys = [r["time_key"] for r in records if not math.isnan(r["time_key"])]
    head_z = [r["nodes"][0][2] for r in records if not math.isnan(r["time_key"])]
    corr = _pearson(time_keys, head_z)

    info: dict[str, Any] = {"time_key_z_correlation": corr, "bpm": bpm}
    if corr is None:
        info["classification"] = "unknown (too few notes)"
        return info

    if abs(corr) > 0.95:
        info["classification"] = "time/depth axis (Z tracks song time, not spatial)"
        info["evidence"] = (
            f"Z correlates with the note time key (r={corr:.4f}); it advances with "
            "the song rather than spanning a fixed spatial range."
        )
        # Hypotheses for the unit, judged by how constant the ratio is.
        hyps: dict[str, Any] = {}
        nonzero = [(tk, z) for tk, z in zip(time_keys, head_z) if tk not in (0.0,)]
        if nonzero:
            ratios = [z / tk for tk, z in nonzero]
            hyps["z_per_time_key"] = {
                "mean_ratio": statistics.fmean(ratios),
                "cv": _cv(ratios),
            }
            if bpm:
                # If time_key were in 1/64 beats: beats = tk/64; seconds = beats*60/bpm.
                sec = [(tk / 64.0) * 60.0 / bpm for tk, _ in nonzero]
                zs = [z for _, z in nonzero]
                sec_ratios = [z / s for z, s in zip(zs, sec) if s != 0]
                if sec_ratios:
                    hyps["z_per_second_if_key_is_64th_beats"] = {
                        "mean_ratio": statistics.fmean(sec_ratios),
                        "cv": _cv(sec_ratios),
                    }
        info["unit_hypotheses"] = hyps
    elif abs(corr) < 0.5:
        info["classification"] = "spatial axis (Z bounded, like X/Y)"
        info["evidence"] = f"Z weakly correlated with time (r={corr:.4f})."
    else:
        info["classification"] = "ambiguous"
        info["evidence"] = f"Z partially correlated with time (r={corr:.4f})."
    return info


def _cv(values: list[float]) -> Optional[float]:
    if len(values) < 2:
        return None
    m = statistics.fmean(values)
    if m == 0:
        return None
    return statistics.pstdev(values) / abs(m)


def analyze(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate coordinate statistics over all extracted objects."""
    notes = [r for r in records if not r["is_rail"]]
    rails = [r for r in records if r["is_rail"]]
    all_nodes = [node for r in records for node in r["nodes"]]
    xs = [n[0] for n in all_nodes]
    ys = [n[1] for n in all_nodes]
    zs = [n[2] for n in all_nodes]

    per_hand: dict[str, Any] = {}
    for hand in set(r["hand"] for r in records):
        hnodes = [node for r in records if r["hand"] == hand for node in r["nodes"]]
        per_hand[hand] = {
            "objects": sum(1 for r in records if r["hand"] == hand),
            "nodes": len(hnodes),
            "x": _axis_stats([n[0] for n in hnodes]),
            "y": _axis_stats([n[1] for n in hnodes]),
            "z": _axis_stats([n[2] for n in hnodes]),
        }

    return {
        "total_notes": len(notes),
        "total_rails": len(rails),
        "total_rail_nodes": sum(len(r["nodes"]) for r in rails),
        "total_nodes": len(all_nodes),
        "x": _axis_stats(xs),
        "y": _axis_stats(ys),
        "z": _axis_stats(zs),
        "per_hand": per_hand,
        "outliers": _find_outliers(all_nodes, xs, ys, zs),
    }


def _find_outliers(nodes, xs, ys, zs, sigma: float = 4.0, limit: int = 10) -> list[dict[str, Any]]:
    """Flag nodes more than ``sigma`` standard deviations from the mean on any axis."""
    out: list[dict[str, Any]] = []
    if len(nodes) < 3:
        return out
    stats = {"x": _axis_stats(xs), "y": _axis_stats(ys), "z": _axis_stats(zs)}
    for node in nodes:
        flags = []
        for axis, value in zip(("x", "y", "z"), node):
            s = stats[axis]
            if s["std"] and abs(value - s["mean"]) > sigma * s["std"]:
                flags.append(axis)
        if flags:
            out.append({"node": list(node), "axes": flags})
            if len(out) >= limit:
                break
    return out


def recommend_profile(stats: dict[str, Any], z_info: dict[str, Any]) -> dict[str, Any]:
    """Derive a candidate CoordinateMappingProfile from measured ranges.

    Maps the *observed* extent of each spatial axis to normalized ``[-1, 1]``
    (``synth = normalized * scale + offset``). If Z reads as a time/depth axis it
    is left identity (handled by timing, not spatial mapping). The result is
    explicitly UNVERIFIED -- it reflects only the supplied files' note extent.
    """
    def axis_scale_offset(s: dict[str, Any]) -> tuple[float, float, bool]:
        if s["count"] == 0 or s["min"] is None:
            return 1.0, 0.0, True
        half = (s["max"] - s["min"]) / 2.0
        center = (s["max"] + s["min"]) / 2.0
        if half <= 1e-9:
            return 1.0, center, True  # degenerate: no spread
        return half, center, False

    xs, xo, x_deg = axis_scale_offset(stats["x"])
    ys, yo, y_deg = axis_scale_offset(stats["y"])

    z_spatial = z_info.get("classification", "").startswith("spatial")
    if z_spatial:
        zs, zo, z_deg = axis_scale_offset(stats["z"])
    else:
        zs, zo, z_deg = 1.0, 0.0, False  # time/depth: not a spatial mapping

    profile = CoordinateMappingProfile(
        name="inferred_UNVERIFIED",
        x_scale=xs, y_scale=ys, z_scale=zs,
        x_offset=xo, y_offset=yo, z_offset=zo,
        notes=(
            "DERIVED FROM MEASUREMENT of the supplied .synth file(s) -- UNVERIFIED. "
            "Maps normalized [-1,1] onto the OBSERVED note extent, which may not "
            "cover the full editor playfield. Z is "
            + ("treated as spatial." if z_spatial else "left identity (time/depth axis).")
            + " Verify against the editor before using for real export."
        ),
    )
    return {
        "profile": profile.to_dict(),
        "validation": validate_profile(profile),
        "degenerate_axes": [a for a, d in (("x", x_deg), ("y", y_deg), ("z", z_deg)) if d],
    }


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def build_report(inputs: list[Path]) -> tuple[dict[str, Any], list[str]]:
    """Read all inputs and produce the full report dict and a list of errors."""
    records: list[dict[str, Any]] = []
    errors: list[str] = []
    files: list[dict[str, Any]] = []
    bpm: Optional[float] = None

    for path in inputs:
        try:
            beatmap = read_synth_beatmap(path)
        except SynthReadError as exc:
            errors.append(str(exc))
            continue
        if bpm is None and isinstance(beatmap.get("BPM"), (int, float)):
            bpm = float(beatmap["BPM"])
        recs = list(iter_objects(beatmap, str(path)))
        records.extend(recs)
        files.append({
            "file": str(path),
            "bpm": beatmap.get("BPM"),
            "objects": len(recs),
        })

    if not records:
        return {"files": files, "errors": errors, "records": 0}, errors

    stats = analyze(records)
    z_info = classify_z(records, bpm)
    report = {
        "files": files,
        "errors": errors,
        "bpm": bpm,
        **stats,
        "z_interpretation": z_info,
        "recommended_profile": recommend_profile(stats, z_info),
    }
    return report, errors


def _print_summary(report: dict[str, Any]) -> None:
    print("=" * 60)
    print("Synth Riders coordinate inspection (measurement only)")
    print("=" * 60)
    for f in report.get("files", []):
        print(f"file: {f['file']}  bpm={f['bpm']}  objects={f['objects']}")
    if report.get("records", None) == 0:
        print("\nNo readable coordinate data.")
        for e in report.get("errors", []):
            print(f"  ! {e}")
        return
    print(f"\ntotals: notes={report['total_notes']} rails={report['total_rails']} "
          f"rail_nodes={report['total_rail_nodes']} all_nodes={report['total_nodes']}")
    for axis in ("x", "y", "z"):
        s = report[axis]
        print(f"  {axis}: min={s['min']:.5g} max={s['max']:.5g} "
              f"mean={s['mean']:.5g} std={s['std']:.5g}")
    print("per-hand:")
    for hand, h in report["per_hand"].items():
        print(f"  {hand:<7} objs={h['objects']:<4} nodes={h['nodes']:<5} "
              f"x[{_rng(h['x'])}] y[{_rng(h['y'])}] z[{_rng(h['z'])}]")
    zi = report["z_interpretation"]
    print(f"\nZ interpretation: {zi.get('classification')}")
    if zi.get("evidence"):
        print(f"  {zi['evidence']}")
    if report["outliers"]:
        print(f"outliers (>{4}σ): {len(report['outliers'])} (showing up to 10)")
        for o in report["outliers"]:
            print(f"  {o['node']} on {o['axes']}")
    rec = report["recommended_profile"]
    print("\nRECOMMENDED CoordinateMappingProfile (UNVERIFIED):")
    p = rec["profile"]
    print(f"  scale  = ({p['x_scale']:.5g}, {p['y_scale']:.5g}, {p['z_scale']:.5g})")
    print(f"  offset = ({p['x_offset']:.5g}, {p['y_offset']:.5g}, {p['z_offset']:.5g})")
    if rec["degenerate_axes"]:
        print(f"  ! insufficient spread on axes: {rec['degenerate_axes']}")
    if rec["validation"]:
        print(f"  ! profile validation issues: {rec['validation']}")
    print("  NOTE: derived from observed note extent; verify against the editor.")
    print("=" * 60)


def _rng(s: dict[str, Any]) -> str:
    if s["count"] == 0 or s["min"] is None:
        return "-"
    return f"{s['min']:.3g},{s['max']:.3g}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", "-i", nargs="+", required=True,
                        help="one or more .synth files")
    parser.add_argument("--output", "-o", default="debug/synth_coordinate_report.json")
    args = parser.parse_args(argv)

    inputs = [Path(p) for p in args.input]
    report, errors = build_report(inputs)
    _print_summary(report)

    out = Path(args.output)
    if out.parent and not out.parent.exists():
        out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    print(f"\nWrote report to {out}")

    if report.get("records", None) == 0:
        print("\nNo coordinate data measured. Provide official Synth Riders editor "
              ".synth exports (ZIP archives containing beatmap.meta.bin).",
              file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
