"""For every GT fire episode in verified clips, find the nearest tracker fire of that weapon
and report the frame distance. Classifies each miss:
  LATENCY   : a fire exists within +/-6 frames but outside the +/-2 scoring window
  MERGED    : a fire exists but it covered a LARGER drop (multi-step collapsed into one event)
  NO-FIRE   : no fire of that weapon within +/-10 frames (genuinely lost)
This separates 'fired but scored late/merged' from 'never fired'.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests.lib import recordings as R
from tests.lib import detect as D
from tests.lib import groundtruth as G
from tests.lib import platform_metrics as P
import src.winwinghaptics.detection.hud_detect as H

ASSOC = P.ASSOC
cat = {"LATENCY": 0, "MERGED": 0, "NO-FIRE": 0, "HIT": 0}
for clip in R.discover():
    if not G.has_gt(clip.key):
        continue
    det = D.redetect(clip)
    gt = G.load(clip.key, len(det["reads"]))
    if gt.unverified:
        continue
    classes = {w: H.WEAPON_CLASS.get(w, "discrete") for w in gt.weapons}
    tk = H.TemporalTracker(classes=classes)
    fires = {w: [] for w in gt.weapons}     # frame -> (old,new)
    fire_at = {w: {} for w in gt.weapons}
    for i, rd in enumerate(det["reads"]):
        upd = {wp: (v, 0.9) for wp, v in rd.items()}
        for wp, _e, _k, _d, o, n in tk.update(upd):
            fires[wp].append(i); fire_at[wp][i] = (o, n)
    print(f"\n=== {clip.key.split('/')[0][-6:]} ===")
    for wp in gt.weapons:
        for zs, ze, old, new in P._episodes_from_segments(gt, wp):
            if gt.is_excluded(zs):
                continue
            lo, hi = zs - ASSOC, ze + ASSOC
            ff = fires[wp]
            if any(lo <= f <= hi for f in ff):
                cat["HIT"] += 1
                continue
            # missed in window -> find nearest fire
            near = [f for f in ff if abs(f - zs) <= 6]
            if near:
                nf = min(near, key=lambda f: abs(f - zs))
                o, n = fire_at[wp][nf]
                kind = "MERGED" if (o - n) > (old - new) else "LATENCY"
                cat[kind] += 1
                print(f"  [{kind}] {wp} {old}->{new} @f{zs}: nearest fire f{nf} "
                      f"({o}->{n}, d={nf-zs:+d})")
            else:
                cat["NO-FIRE"] += 1
                print(f"  [NO-FIRE] {wp} {old}->{new} @f{zs}: no fire within +/-6")
print("\nSUMMARY:", cat)
