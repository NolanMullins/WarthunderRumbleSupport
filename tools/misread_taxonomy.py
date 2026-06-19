"""Misread taxonomy: WHERE and HOW per-frame reads go wrong, aggregated over all verified
clips. For every (frame, weapon) where GT gives an exact stable value, compare the detector's
read against truth and bucket the failure. The point is to attack the DOMINANT failure mode
with data, not a guess.

Buckets:
  - exact        : read == truth (good)
  - missed_row   : read is None where a value was present (no haptic risk, tracker holds)
  - len_mismatch : read has wrong number of digits (e.g. 36 -> 361, 268 -> 26)
  - digit_sub    : same length, one-or-more single-position digit substitutions
For digit_sub we tabulate the (position, true->got) confusion pairs so we can see e.g.
"middle digit 6->0" dominating. Position is counted from the LEFT (0 = leading).
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from collections import Counter
from tests.lib import recordings as R
from tests.lib import detect as D
from tests.lib import groundtruth as G

buckets = Counter()
pos_conf = Counter()      # (which, true_digit, got_digit) -> count  ; which in {lead,mid,trail}
len_pairs = Counter()     # (true_len, got_len) -> count
per_weapon = Counter()    # wp -> misread count (digit_sub + len_mismatch)
scored = 0

def positions(true_s, got_s):
    """Yield (which, td, gd) for same-length strings."""
    n = len(true_s)
    for i, (td, gd) in enumerate(zip(true_s, got_s)):
        if td == gd:
            continue
        if n == 1:
            which = "solo"
        elif i == 0:
            which = "lead"
        elif i == n - 1:
            which = "trail"
        else:
            which = "mid"
        yield which, td, gd

for clip in R.discover():
    if not G.has_gt(clip.key):
        continue
    det = D.redetect(clip)
    reads = det["reads"]
    gt = G.load(clip.key, len(reads))
    if gt.unverified:
        continue                      # taxonomy only on human-verified clips
    for i in range(len(reads)):
        if gt.is_excluded(i):
            continue
        rd = reads[i]
        for wp in gt.weapons:
            if not gt.is_present(wp, i):
                continue
            v = gt.value_at(wp, i)    # exact stable value only
            if v is None:
                continue
            scored += 1
            got = rd.get(wp)
            if got is None:
                buckets["missed_row"] += 1
                continue
            gv = got[0] if isinstance(got, tuple) else got
            if gv == v:
                buckets["exact"] += 1
                continue
            ts, gs = str(v), str(gv)
            per_weapon[wp] += 1
            if len(ts) != len(gs):
                buckets["len_mismatch"] += 1
                len_pairs[(len(ts), len(gs))] += 1
            else:
                buckets["digit_sub"] += 1
                for which, td, gd in positions(ts, gs):
                    pos_conf[(which, td, gd)] += 1

print(f"scored (frame x weapon, exact GT) cells: {scored}\n")
print("BUCKETS:")
for k in ("exact", "missed_row", "digit_sub", "len_mismatch"):
    c = buckets[k]
    print(f"  {k:13} {c:6}  ({100.0*c/scored:.2f}%)" if scored else k)
err = buckets["missed_row"] + buckets["digit_sub"] + buckets["len_mismatch"]
print(f"  {'TOTAL ERR':13} {err:6}  ({100.0*err/scored:.2f}%)\n")

print("DIGIT-SUB confusions by position (top 15):  which  true->got  count")
for (which, td, gd), c in pos_conf.most_common(15):
    print(f"  {which:5}  {td}->{gd}   {c}")

print("\nLEN-MISMATCH (true_len -> got_len): count")
for (tl, gl), c in len_pairs.most_common(10):
    print(f"  {tl} -> {gl}   {c}")

print("\nMISREADS by weapon:")
for wp, c in per_weapon.most_common():
    print(f"  {wp:5} {c}")
