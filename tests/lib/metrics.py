"""Metric computation for the two independent failure tracks.

TRACK 2 - event failures (tracker layer): replay the CURRENT TemporalTracker over the FROZEN
saved reads and score per-frame false-fires / missed-fires against the ground truth. Runs on
every recording (deterministic; no detector/calibration involved).

TRACK 1 - misreads (detector layer): re-run the CURRENT read_counts over every PNG using the
clip's SAVED calibration (calib.json) and score each (frame x weapon) read against the GT
value. Runs only on clips that have calib.json ("faithful" tier).
"""
import os
import sys

# make src/ importable (hud_detect)
sys.path.insert(0, os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "src")))
import winwinghaptics.detection.hud_detect as H  # noqa: E402

ASSOC = 2   # onset-association window (frames). ~20 Hz capture => +/-2 frames ~= +/-0.1 s.


# ----------------------------------------------------------------------------------------
# TRACK 2 - event failures
# ----------------------------------------------------------------------------------------
def tracker_events(clip):
    """Replay current TemporalTracker over the clip's frozen saved reads.
    Returns dict: weapon -> sorted list of frame indices where an event fired."""
    weapons = clip.weapons
    classes = {w: H.WEAPON_CLASS.get(w, "discrete") for w in weapons}
    tk = H.TemporalTracker(classes=classes)
    fired = {w: [] for w in weapons}
    for i, reads in enumerate(clip.saved_reads()):
        rd = {wp: (v, 0.9) for wp, v in reads.items()}
        for wp, _eff, _kind, _delta, _old, _new in tk.update(rd):
            fired.setdefault(wp, []).append(i)
    return fired


def score_events(clip, gt):
    """Per-frame event-failure scoring for one clip.

    A frame FAILS if, for ANY weapon, there is a false fire (event on a silent/stable frame
    outside any fire zone +/- ASSOC) OR a missed fire is attributed to it (a down fire-zone
    with no event in [zone_start-ASSOC, zone_end+ASSOC], attributed to zone_start).

    Returns a dict of counts + the explicit failure list (for diagnosis).
    """
    n = len(clip.saved_reads())
    n_scored = sum(1 for i in range(n) if not gt.is_excluded(i))
    fired = tracker_events(clip)
    failed_frames = set()
    false_fires = []     # (frame, weapon, old?, new?) - event in a silent region
    missed = []          # (zone_start, weapon, old, new) - real fire with no event

    for wp in gt.weapons:
        zones = gt.fire_zones(wp)
        # frames covered by any fire zone, padded by the association window -> "fire-allowed"
        allowed = set()
        for zs, ze, _o, _n in zones:
            allowed.update(range(zs - ASSOC, ze + ASSOC + 1))
        silent = gt.silent_frames(wp)

        # false fires: an event on a stable/silent frame that is NOT explained by a fire zone
        for f in fired.get(wp, []):
            if f in allowed or gt.is_excluded(f):
                continue
            if f in silent:
                false_fires.append((f, wp))
                failed_frames.add(f)

        # missed fires: a down zone with no event anywhere in its padded window
        for zs, ze, old, new in zones:
            if gt.is_excluded(zs):
                continue
            lo, hi = zs - ASSOC, ze + ASSOC
            if not any(lo <= f <= hi for f in fired.get(wp, [])):
                missed.append((zs, wp, old, new))
                failed_frames.add(min(max(zs, 0), n - 1))

    return {
        "n_frames": n_scored,
        "failed_frames": len(failed_frames),
        "false_fires": len(false_fires),
        "missed_fires": len(missed),
        "failure_rate": (len(failed_frames) / n_scored) if n_scored else 0.0,
        "_false_fire_list": false_fires,
        "_missed_list": missed,
    }


# ----------------------------------------------------------------------------------------
# TRACK 1 - misreads (faithful tier)
# ----------------------------------------------------------------------------------------
def fresh_reads(clip):
    """Re-run the current detector over every PNG using the clip's SAVED calibration.
    Returns list[dict wp->int] indexed by frame. Requires clip.has_calib."""
    cal = H.Calib.from_dict(clip.load_calib())
    grays = clip.grays()
    out = []
    shift = cxh = None
    for g in grays:
        reads, shift, cxh = H.read_counts(
            g, cal, shift_hint=shift, return_shift=True, cx_hint=cxh, return_cx=True)
        out.append({wp: int(v[0]) for wp, v in reads.items()})
    return out


def score_misreads(clip, gt):
    """Per-(frame x weapon) misread scoring on the faithful tier.

    A scored cell = a frame where the weapon has a STABLE GT value (transition frames are
    bracket-tolerant: a read within [lo,hi] or no-read passes; anything else fails).
    A stable cell FAILS if the fresh read != GT value (a no-read on a stable cell also fails).
    """
    reads = fresh_reads(clip)
    n = len(reads)
    scored = 0
    bad = 0
    fails = []
    for i in range(n):
        for wp in gt.weapons:
            r = reads[i].get(wp)
            v = gt.value_at(wp, i)
            if v is not None:                      # stable frame -> exact match required
                scored += 1
                if r != v:
                    bad += 1
                    fails.append((i, wp, r, v))
            else:                                  # transition -> bracket-tolerant
                br = gt.transition_bracket(wp, i)
                if br is not None and r is not None:
                    lo, hi = br
                    scored += 1
                    if not (lo <= r <= hi):
                        bad += 1
                        fails.append((i, wp, r, f"[{lo},{hi}]"))
    return {
        "n_frames": n,
        "scored_cells": scored,
        "misread_cells": bad,
        "misread_rate": (bad / scored) if scored else 0.0,
        "_fail_list": fails,
    }
