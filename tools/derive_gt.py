"""Derive candidate dense GT (stable [start,end,value] segments) from a clip's saved reads,
using a centered settled-level oracle (mode in a +/-R window). Output is a STARTING POINT to
be hand-verified, not gospel. Prints JSON-ready segments per weapon.

Usage:  python tools/derive_gt.py "<clip_key>"   (clip_key as printed by ab_report)
"""
import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tests"))
import numpy as np
from lib import recordings as R

R_WIN, MINc = 6, 5
PLATEAU_MIN = 6   # a stable segment must persist at least this many frames


def settled(vals):
    n = len(vals); out = [None]*n; carry = None
    for i in range(n):
        lo, hi = max(0, i-R_WIN), min(n, i+R_WIN+1)
        win = [v for v in vals[lo:hi] if v is not None]
        lvl = None
        if win:
            u, c = np.unique(win, return_counts=True); k = int(c.argmax())
            if c[k] >= MINc and c[k]*2 > len(win):
                lvl = int(u[k])
        if lvl is None:
            lvl = carry
        out[i] = lvl
        if lvl is not None:
            carry = lvl
    return out


def segments(vals):
    s = settled(vals)
    segs = []
    i = 0; n = len(s)
    while i < n:
        if s[i] is None:
            i += 1; continue
        j = i
        while j+1 < n and s[j+1] == s[i]:
            j += 1
        if j - i + 1 >= PLATEAU_MIN:
            segs.append([i, j, s[i]])
        i = j + 1
    # merge adjacent equal-value segments (across short gaps)
    merged = []
    for seg in segs:
        if merged and merged[-1][2] == seg[2]:
            merged[-1][1] = seg[1]
        else:
            merged.append(seg)
    return merged


def collapse_returns(segs, max_excursion=40):
    """Remove short A->B->A excursions where B returns to the SAME value A: that is the
    signature of a transient MISREAD (e.g. 120->12->120, 48->4->48, 18->181->18), not a real
    fire. Real fires step DOWN and stay (A->B, B<A, no return), so they are preserved."""
    changed = True
    while changed and len(segs) >= 3:
        changed = False
        for i in range(1, len(segs) - 1):
            a, b, c = segs[i-1], segs[i], segs[i+1]
            length = b[1] - b[0] + 1
            if a[2] == c[2] and b[2] != a[2] and length <= max_excursion:
                merged = [a[0], c[1], a[2]]
                segs = segs[:i-1] + [merged] + segs[i+2:]
                changed = True
                break
    return segs


key = sys.argv[1]
clip = next(c for c in R.discover() if c.key == key)
reads = clip.saved_reads()
n = len(reads)
out = {"_comment": f"CANDIDATE GT auto-derived from saved reads of {key}; HAND-VERIFY. "
                   "Return-to-same misread excursions were auto-collapsed; real monotonic "
                   "fires preserved. Verify fire counts/values before setting _unverified=false.",
       "_unverified": True}
for wp in clip.weapons:
    vals = [reads[i].get(wp) for i in range(n)]
    out[wp] = collapse_returns(segments(vals))
print(json.dumps(out, indent=2))
