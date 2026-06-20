"""GT audit: flag ground-truth that the (now-accurate) re-detected reads DISAGREE with, so a
human can review whether the GT is wrong vs the read genuinely unreadable.

Two flags per verified clip:
  A) STUCK-DROP: a GT down-step episode (old->new) where NO re-detected read in the onset
     window [zs-3, ze+3] is <= new. Either the drop never happened (GT wrong) or the new value
     was unreadable there (genuine miss). Worth human eyes.
  B) READ-CONTRADICTS-STABLE: inside a GT stable segment (true value v), the re-detected read
     is a CONFIDENT, CONSISTENT different value for >=60% of the segment's frames. Now that the
     MLP reads ~96%+, a sustained disagreement points at a GT error.

For rapid weapons (CNN) we also report whether each missed episode is INTRA-BURST (another CNN
fire within +/-4 frames), since a sustained gun rumble covers those -- they are metric
granularity, not felt misses.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from collections import Counter
from tests.lib import recordings as R
from tests.lib import detect as D
from tests.lib import groundtruth as G
from tests.lib import platform_metrics as P
import src.winwinghaptics.detection.hud_detect as H


def fires_for(clip, gt, det):
    classes = {w: H.WEAPON_CLASS.get(w, "discrete") for w in gt.weapons}
    tk = H.TemporalTracker(classes=classes)
    fired = {w: [] for w in gt.weapons}
    for i, rd in enumerate(det["reads"]):
        upd = {wp: (v, 0.9) for wp, v in rd.items()}
        for wp, _e, _k, _d, _o, _n in tk.update(upd):
            fired[wp].append(i)
    return fired


for clip in R.discover():
    if not G.has_gt(clip.key):
        continue
    det = D.redetect(clip)
    reads = det["reads"]
    gt = G.load(clip.key, len(reads))
    if gt.unverified:
        continue
    fired = fires_for(clip, gt, det)
    print(f"\n=== {clip.key.split('/')[0][-6:]} ===")
    # A) stuck-drop episodes
    for wp in gt.weapons:
        cls = H.WEAPON_CLASS.get(wp, "discrete")
        for zs, ze, old, new in P._episodes_from_segments(gt, wp):
            if gt.is_excluded(zs):
                continue
            lo, hi = max(0, zs - 3), min(len(reads) - 1, ze + 3)
            window_reads = [reads[i].get(wp) for i in range(lo, hi + 1)]
            confirms = [r for r in window_reads if r is not None and r <= new]
            if not confirms:
                # did a fire still land here?
                fhit = any(zs - P.ASSOC <= f <= ze + P.ASSOC for f in fired[wp])
                intra = ""
                if cls == "rapid":
                    near = any(abs(f - zs) <= 4 for f in fired[wp] if not (zs - P.ASSOC <= f <= ze + P.ASSOC))
                    intra = " INTRA-BURST(sustained)" if near else " NO-NEARBY-FIRE"
                rr = [r for r in window_reads if r is not None]
                print(f"  [STUCK-DROP] {wp} {old}->{new} @f{zs}: reads in window never <= {new} "
                      f"(saw {sorted(set(rr))[:6]}); fired={fhit}{intra}")
    # B) read contradicts stable segment
    for wp in gt.weapons:
        for seg in gt.segments.get(wp, []):
            s0, s1, v = seg
            if s1 - s0 < 5:
                continue
            vals = Counter()
            for i in range(s0, s1 + 1):
                if gt.is_excluded(i):
                    continue
                r = reads[i].get(wp)
                if r is not None:
                    vals[r] += 1
            tot = sum(vals.values())
            if tot < 5:
                continue
            top, n = vals.most_common(1)[0]
            if top != v and n >= 0.60 * tot:
                print(f"  [READ!=GT] {wp} seg[{s0},{s1}] GT={v} but reads say {top} "
                      f"in {n}/{tot} frames")
