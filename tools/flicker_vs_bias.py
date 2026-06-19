"""Flicker vs bias: within each STABLE GT segment (true value constant), is the wrong read
the MINORITY (flicker -> temporal voting fixes it) or the MAJORITY (consistent template bias
-> voting can't help, need better glyph templates / tiebreak)?

For each (clip, weapon, stable segment) we tally reads. A segment is:
  - clean       : >=95% exact
  - flicker     : exact is the plurality but some wrong frames
  - bias        : a WRONG value is the plurality (true value loses)
  - sparse_none : mostly None
Counts are frame-weighted so we see where the 11% digit_sub actually lives.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from collections import Counter
from tests.lib import recordings as R
from tests.lib import detect as D
from tests.lib import groundtruth as G

seg_kinds = Counter()      # kind -> segment count
frame_kinds = Counter()    # kind -> frame count
bias_examples = []

for clip in R.discover():
    if not G.has_gt(clip.key):
        continue
    det = D.redetect(clip)
    reads = det["reads"]
    gt = G.load(clip.key, len(reads))
    if gt.unverified:
        continue
    for wp in gt.weapons:
        # walk contiguous runs where value_at is a constant exact int
        i = 0
        n = len(reads)
        while i < n:
            v = gt.value_at(wp, i) if (gt.is_present(wp, i) and not gt.is_excluded(i)) else None
            if v is None:
                i += 1
                continue
            j = i
            vals = Counter()
            while j < n and gt.is_present(wp, j) and not gt.is_excluded(j) and gt.value_at(wp, j) == v:
                got = reads[j].get(wp)
                gv = (got[0] if isinstance(got, tuple) else got)
                vals[gv] += 1
                j += 1
            total = sum(vals.values())
            if total >= 4:                      # ignore tiny segments
                exact = vals.get(v, 0)
                none_c = vals.get(None, 0)
                wrong = {k: c for k, c in vals.items() if k not in (v, None)}
                top_wrong = max(wrong.values()) if wrong else 0
                top_wrong_val = max(wrong, key=wrong.get) if wrong else None
                if none_c >= 0.6 * total:
                    kind = "sparse_none"
                elif exact >= 0.95 * total:
                    kind = "clean"
                elif exact >= top_wrong:
                    kind = "flicker"
                else:
                    kind = "bias"
                    if len(bias_examples) < 20:
                        bias_examples.append((clip.key.split('/')[0][-6:], wp, v, top_wrong_val,
                                              top_wrong, total))
                seg_kinds[kind] += 1
                frame_kinds[kind] += total
            i = j if j > i else i + 1

print("SEGMENTS by kind:", dict(seg_kinds))
print("FRAMES   by kind:", dict(frame_kinds))
tot_f = sum(frame_kinds.values())
if tot_f:
    for k in ("clean", "flicker", "bias", "sparse_none"):
        print(f"  {k:11} {frame_kinds[k]:6} frames ({100.0*frame_kinds[k]/tot_f:.1f}%)")
print("\nBIAS segments (clip,wp,true,topwrong,wrongN/total):")
for e in bias_examples:
    print("  ", e)
