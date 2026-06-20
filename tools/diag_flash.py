"""Characterize the 'cloud flash' failure: a frame where the count reads as FEWER digits than
truth (or None) while the SAME row read fine just before/after. These are transient washouts,
not persistent blind spots.

For each verified (clip, frame, weapon) with exact GT, classify the read:
  OK            : read == GT
  TRUNC         : read is a prefix-ish shorter number (e.g. 130->1, 216->2, 78->7)
  NONE          : read is None but neighbours (+/-2) read GT fine  -> transient dropout
  OTHER_MISREAD : wrong but same length (digit confusion, not a flash)
We tally, and for TRUNC/NONE we record whether the row read GT correctly within +/-2 frames
(=> the information was THERE, a flash ate it -> recoverable by temporal logic).
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from collections import Counter
from tests.lib import recordings as R
from tests.lib import detect as D
from tests.lib import groundtruth as G

cat = Counter()
recoverable = 0
trunc_unrecoverable = []
for clip in R.discover():
    if not G.has_gt(clip.key):
        continue
    reads = D.redetect(clip)["reads"]
    gt = G.load(clip.key, len(reads))
    if gt.unverified:
        continue
    tag = clip.key.split("/")[-1][-6:]
    for wp in gt.weapons:
        for i in range(len(reads)):
            if gt.is_excluded(i):
                continue
            if not gt.is_present(wp, i):
                continue
            v = gt.value_at(wp, i)
            if v is None:
                continue
            got = reads[i].get(wp)
            if got == v:
                cat["OK"] += 1
                continue
            # neighbour GT-correct reads within +/-2?
            near_ok = any(reads[j].get(wp) == v for j in range(max(0, i-2), min(len(reads), i+3))
                          if j != i and not gt.is_excluded(j))
            if got is None:
                cat["NONE"] += 1
                if near_ok:
                    recoverable += 1
            elif len(str(got)) < len(str(v)) and str(v).startswith(str(got)[:1]):
                cat["TRUNC"] += 1
                if near_ok:
                    recoverable += 1
                elif len(trunc_unrecoverable) < 15:
                    trunc_unrecoverable.append((tag, wp, i, v, got))
            elif len(str(got)) < len(str(v)):
                cat["TRUNC"] += 1
                if near_ok:
                    recoverable += 1
            else:
                cat["OTHER_MISREAD"] += 1

tot = sum(cat.values())
print(f"over {tot} GT cells:")
for k in ("OK", "TRUNC", "NONE", "OTHER_MISREAD"):
    print(f"  {k:14} {cat[k]:6} ({100*cat[k]/tot:.2f}%)")
flash = cat["TRUNC"] + cat["NONE"]
print(f"\nFLASH (TRUNC+NONE) = {flash} ({100*flash/tot:.2f}%)")
print(f"  of those, RECOVERABLE (a +/-2 neighbour read GT correctly): {recoverable} "
      f"({100*recoverable/flash:.1f}% of flashes)")
print("\nsample UNRECOVERABLE truncations (no good neighbour):")
for e in trunc_unrecoverable:
    print("  ", e)
