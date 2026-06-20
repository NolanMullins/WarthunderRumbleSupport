"""Apply human verify feedback to a clip's ground-truth file.

Reconstructs a hold-constant step function from the human anchor marks (the user marks
transitions + confirmations; the value holds between marks), writes it as the new dense GT,
and marks it VERIFIED (_unverified=false) so it gates the build.

Outlier guard: a single anchor that spikes away from both neighbours (|d|>OUTLIER and the
neighbours agree) is treated as a typo and dropped (reported), so e.g. CHFF 270,279,270 keeps
270. Use --keep-outliers to disable.

Usage:
  python tools/apply_feedback.py "<clip_key>"            # write refined GT (verified)
  python tools/apply_feedback.py "<clip_key>" --dry-run  # show, don't write
"""
import os
import sys
import json
import glob
import argparse

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "tests"))
from lib import recordings as R       # noqa: E402
from lib import groundtruth as G      # noqa: E402
from lib import detect as D           # noqa: E402

FB_DIR = os.path.normpath(os.path.join(_HERE, "..", "tests", "feedback"))
GT_DIR = os.path.normpath(os.path.join(_HERE, "..", "tests", "ground_truth"))
CLIPS = {c.key: c for c in R.discover()}
OUTLIER = 8


def anchors_for(clip, fb, keep_outliers=False):
    reads = D.redetect(clip).get("reads", [])
    pts = {}
    for n, cells in fb.get("frames", {}).items():
        n = int(n)
        det = reads[n] if n < len(reads) else {}
        for wp, e in cells.items():
            st = e.get("status")
            if st == "correct":
                v = det.get(wp)
                if v is None:
                    continue
            elif st == "value":
                v = e.get("value")
            elif st == "absent":
                v = None
            else:
                continue
            pts.setdefault(wp, {})[n] = v
    # per weapon: drop typo outliers, then collapse to change anchors
    out = {}
    dropped = []
    for wp, fr_map in pts.items():
        seq = sorted(fr_map.items())
        if not keep_outliers and len(seq) >= 3:
            clean = []
            for i, (fr, v) in enumerate(seq):
                if 0 < i < len(seq) - 1 and v is not None:
                    a = seq[i - 1][1]; b = seq[i + 1][1]
                    # (1) big-spike typo: a lone value far from BOTH agreeing neighbours
                    # (e.g. CHFF 270, 279, 270 -> the 279 is a slip).
                    if a is not None and b is not None and abs(v - a) > OUTLIER \
                            and abs(v - b) > OUTLIER and abs(a - b) <= 2:
                        dropped.append((wp, fr, v, a))
                        continue
                    # (2) equal-anchor rule (user): between two EQUAL marks every frame holds
                    # that value, so a lone mark that differs from both equal neighbours is a
                    # stray (e.g. AAM 4, 3, 4 -> the 3 is a mis-click; a missile cannot launch
                    # then un-launch). Any delta, since the neighbours pin the true value.
                    if a is not None and b is not None and a == b and v != a:
                        dropped.append((wp, fr, v, a))
                        continue
                clean.append((fr, v))
            seq = clean
        anchors = []
        for fr, v in seq:
            if not anchors or anchors[-1][1] != v:
                anchors.append((fr, v))
        out[wp] = anchors
    return out, dropped


def build_segments(anchors, n_frames):
    """Hold-constant: each anchor's value spans until the next anchor (or clip end).
    Absent anchors (value None) create a gap in presence (handled via _present)."""
    segs = []
    present = []
    for i, (fr, v) in enumerate(anchors):
        end = (anchors[i + 1][0] - 1) if i + 1 < len(anchors) else (n_frames - 1)
        if v is None:
            continue                          # absent stretch -> not a value segment
        segs.append([fr, end, v])
        present.append([fr, end])
    # merge adjacent equal-value segments
    merged = []
    for s in segs:
        if merged and merged[-1][2] == s[2] and merged[-1][1] + 1 == s[0]:
            merged[-1][1] = s[1]
        else:
            merged.append(s)
    # merge adjacent presence spans
    pmerged = []
    for s in present:
        if pmerged and pmerged[-1][1] + 1 >= s[0]:
            pmerged[-1][1] = max(pmerged[-1][1], s[1])
        else:
            pmerged.append(list(s))
    return merged, pmerged


def clamp_anchors(anchors, scope):
    """Apply a per-weapon scope cap {wp:[lo,hi]}: drop anchor frames past hi (so a weapon that
    leaves scope -- e.g. an arcade-only reload timer after depletion, which the user's real game
    mode never shows -- is simply not tracked past that point). Anchors at/just before hi are
    kept so the last in-scope value still holds to the cap."""
    out = {}
    for wp, anch in anchors.items():
        cap = scope.get(wp)
        if not cap:
            out[wp] = anch
            continue
        lo, hi = cap
        kept = [(fr, v) for (fr, v) in anch if fr <= hi]
        out[wp] = kept
    return out, {wp: scope[wp][1] for wp in scope}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("clip")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--keep-outliers", action="store_true")
    ap.add_argument("--scope-out", action="append", default=[],
                    help="Mark a weapon out-of-scope past a frame, e.g. BMB:424 (the row "
                         "becomes arcade-only reload-timer data the real game mode never "
                         "shows). Durable: stored in _scope and preserved on re-apply.")
    args = ap.parse_args()

    key = args.clip
    clip = CLIPS.get(key)
    if not clip:
        print(f"unknown clip {key}"); return 1
    fbp = os.path.join(FB_DIR, key.replace("/", "__") + ".json")
    if not os.path.exists(fbp):
        print(f"no feedback for {key}"); return 1
    with open(fbp, encoding="utf-8") as f:
        fb = json.load(f)

    n = len(clip.png_paths())
    anchors, dropped = anchors_for(clip, fb, args.keep_outliers)
    if dropped:
        print("Dropped likely typos (value far from both neighbours):")
        for wp, fr, v, a in dropped:
            print(f"   {wp} f{fr}: {v} -> using neighbour {a}")

    # preserve _exclude (polluted frame ranges) and _scope (per-weapon scope caps) from prior GT
    prev_exclude = []
    prev_scope = {}
    prevp = os.path.join(GT_DIR, key.replace("/", "__") + ".json")
    if os.path.exists(prevp):
        try:
            with open(prevp, encoding="utf-8-sig") as f:
                prev = json.load(f)
                prev_exclude = prev.get("_exclude", [])
                prev_scope = prev.get("_scope", {})
        except Exception:
            prev_exclude = []
            prev_scope = {}
    # merge any new --scope-out CLI caps (start frame defaults to clip's first in-scope frame)
    scope = dict(prev_scope)
    for spec in args.scope_out:
        wp, _, hi = spec.partition(":")
        if wp and hi:
            scope[wp] = [scope.get(wp, [0, 0])[0], int(hi)]
    if scope:
        anchors, _ = clamp_anchors(anchors, scope)

    data = {"_comment": f"VERIFIED from human feedback ({key}); hold-constant between marks.",
            "_unverified": False, "_present": {}}
    if prev_exclude:
        data["_exclude"] = prev_exclude
    if scope:
        data["_scope"] = scope
    for wp, anch in anchors.items():
        segs, present = build_segments(anch, n)
        if segs:
            # clamp presence/segment END to the scope cap so out-of-scope frames aren't scored
            cap = scope.get(wp)
            if cap:
                hi = cap[1]
                segs = [[s, min(e, hi), v] for s, e, v in segs if s <= hi]
                present = [[s, min(e, hi)] for s, e in present if s <= hi]
            data[wp] = segs
            data["_present"][wp] = present

    print(f"\nRefined GT for {key}:")
    for wp in sorted(k for k in data if not k.startswith("_")):
        print(f"  {wp}: " + "  ".join(f"[{s},{e}]={v}" for s, e, v in data[wp]))

    if args.dry_run:
        print("\n(dry-run, not written)")
        return 0
    outp = os.path.join(GT_DIR, key.replace("/", "__") + ".json")
    with open(outp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    print(f"\nwrote {os.path.relpath(outp)}  (VERIFIED)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
