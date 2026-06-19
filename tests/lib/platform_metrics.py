"""Higher-level platform metrics, computed on RE-DETECTED reads (lib.detect.redetect), so
they measure the CURRENT detector + tracker -- not the obsolete frozen telemetry reads.

Tracks, all scored against ground truth (row-presence spans + value segments):

  ROW track (the headline -- "missed row" errors the user feels):
    - missed_row : a frame where a GT-PRESENT weapon row read None (detector missed the row)
    - false_row  : a frame where a GT-ABSENT weapon read a value (hallucinated a row)
    - calib_missing_rows : weapons present in GT that calibration never even learned (a whole
                           row missed for the entire clip -- the worst missed-row failure)

  VALUE track:
    - misread : on a stable, present, READ frame, value != GT (bracket-tolerant in transitions)

  EVENT track (experience-denominated): replay the tracker over the re-detected reads and
    score per real fire EPISODE -> HIT / MISS / FALSE (counts over EVENTS, not frames).
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.normpath(os.path.join(_HERE, "..", "..", "src")))
import hud_detect as H  # noqa: E402

from . import detect as D

ASSOC = 2   # onset-association window (frames) ~ +/-0.1s at 20Hz


# ----------------------------------------------------------------------------------------
# ROW + VALUE tracks (per-frame, on re-detected reads)
# ----------------------------------------------------------------------------------------
def score_rows_values(clip, gt, det=None):
    det = det or D.redetect(clip)
    reads = det["reads"]
    n = len(reads)
    calib_rows = set(det.get("calib_rows") or {})

    # whole-row calibration misses: GT-present weapon that calibration never learned
    calib_missing = [w for w in gt.weapons
                     if w not in calib_rows
                     and any(gt.is_present(w, i) and not gt.is_excluded(i) for i in range(n))]

    present_cells = missed_row = 0
    false_row = 0
    scored_val = misread = 0
    miss_by_wp = {}
    missed_examples = []
    false_examples = []
    misread_examples = []

    for i in range(n):
        if gt.is_excluded(i):
            continue
        rd = reads[i]
        for wp in gt.weapons:
            present = gt.is_present(wp, i)
            got = rd.get(wp)
            if present:
                present_cells += 1
                if got is None:
                    missed_row += 1
                    miss_by_wp[wp] = miss_by_wp.get(wp, 0) + 1
                    if len(missed_examples) < 20:
                        missed_examples.append((i, wp))
                else:
                    # value check (only where GT value is known)
                    v = gt.value_at(wp, i)
                    if v is not None:
                        scored_val += 1
                        if got != v:
                            misread += 1
                            if len(misread_examples) < 20:
                                misread_examples.append((i, wp, got, v))
                    else:
                        br = gt.transition_bracket(wp, i)
                        if br is not None:
                            scored_val += 1
                            lo, hi = br
                            if not (lo <= got <= hi):
                                misread += 1
                                if len(misread_examples) < 20:
                                    misread_examples.append((i, wp, got, f"[{lo},{hi}]"))
            else:
                if got is not None:
                    false_row += 1
                    if len(false_examples) < 20:
                        false_examples.append((i, wp, got))

    return {
        "source": det["source"],
        "n_frames": n,
        "calib_missing_rows": calib_missing,
        "present_cells": present_cells,
        "missed_row": missed_row,
        "missed_row_rate": (missed_row / present_cells) if present_cells else None,
        "missed_by_weapon": miss_by_wp,
        "false_row": false_row,
        "false_row_rate": (false_row / (n * len(gt.weapons))) if n and gt.weapons else None,
        "scored_value_cells": scored_val,
        "misread": misread,
        "misread_rate": (misread / scored_val) if scored_val else None,
        "_missed_examples": missed_examples,
        "_false_examples": false_examples,
        "_misread_examples": misread_examples,
    }


# ----------------------------------------------------------------------------------------
# EVENT track (event-denominated) on re-detected reads
# ----------------------------------------------------------------------------------------
def _episodes_from_segments(gt, wp):
    """Downward value steps in GT = real fire episodes: (zone_start, zone_end, old, new)."""
    zones = []
    segs = gt.segments.get(wp, [])
    for i in range(len(segs) - 1):
        old, new = segs[i][2], segs[i + 1][2]
        if new < old:
            zones.append((segs[i][1] + 1, segs[i + 1][0] - 1, old, new))
    return zones


def tracker_fire_frames(clip, gt, det=None):
    det = det or D.redetect(clip)
    weapons = gt.weapons
    classes = {w: H.WEAPON_CLASS.get(w, "discrete") for w in weapons}
    tk = H.TemporalTracker(classes=classes)
    fired = {w: [] for w in weapons}
    for i, rd in enumerate(det["reads"]):
        upd = {wp: (v, 0.9) for wp, v in rd.items()}
        for wp, _e, _k, _d, _o, _n in tk.update(upd):
            fired.setdefault(wp, []).append(i)
    return fired


def score_events(clip, gt, det=None):
    det = det or D.redetect(clip)
    fired = tracker_fire_frames(clip, gt, det)
    hits = misses = false_fires = 0
    miss_list = []
    false_list = []
    for wp in gt.weapons:
        zones = _episodes_from_segments(gt, wp)
        allowed = set()
        for zs, ze, _o, _n in zones:
            allowed.update(range(zs - ASSOC, ze + ASSOC + 1))
        # hits / misses per episode (skip episodes whose onset is in an excluded range)
        for zs, ze, old, new in zones:
            if gt.is_excluded(zs):
                continue
            lo, hi = zs - ASSOC, ze + ASSOC
            if any(lo <= f <= hi for f in fired.get(wp, [])):
                hits += 1
            else:
                misses += 1
                miss_list.append((zs, wp, old, new))
        # false fires: events outside any fire zone (excluded frames don't count)
        for f in fired.get(wp, []):
            if f not in allowed and not gt.is_excluded(f):
                false_fires += 1
                false_list.append((f, wp))
    total = hits + misses
    return {
        "events": total,
        "hits": hits,
        "misses": misses,
        "false_fires": false_fires,
        "event_miss_rate": (misses / total) if total else None,
        "event_false_per_event": (false_fires / total) if total else None,
        "_miss_list": miss_list,
        "_false_list": false_list,
    }
