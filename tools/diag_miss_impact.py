"""Do missed rows actually MATTER? A missed read only costs a haptic event if it falls in a
FIRE zone (a GT downward transition). A None while the count holds steady = the tracker keeps
its baseline = zero impact. Classify every missed-row cell:

  IN_FIRE_ZONE : within +/-2 frames of a GT downward step for that weapon (could cost a fire)
  STABLE_HOLD  : inside a constant-value GT segment (harmless -- tracker holds baseline)
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from collections import Counter
from tests.lib import recordings as R
from tests.lib import detect as D
from tests.lib import groundtruth as G
from tests.lib import platform_metrics as P

cat = Counter()
in_fire_examples = []
for clip in R.discover():
    if not G.has_gt(clip.key):
        continue
    reads = D.redetect(clip)["reads"]
    gt = G.load(clip.key, len(reads))
    if gt.unverified:
        continue
    tag = clip.key.split("/")[-1][-6:]
    # fire-zone frames per weapon
    fire_frames = {}
    for wp in gt.weapons:
        fz = set()
        for zs, ze, _o, _n in P._episodes_from_segments(gt, wp):
            fz.update(range(zs - 2, ze + 3))
        fire_frames[wp] = fz
    for wp in gt.weapons:
        for i in range(len(reads)):
            if gt.is_excluded(i) or not gt.is_present(wp, i):
                continue
            if gt.value_at(wp, i) is None:
                continue
            if reads[i].get(wp) is not None:
                continue
            if i in fire_frames[wp]:
                cat["IN_FIRE_ZONE"] += 1
                if len(in_fire_examples) < 20:
                    in_fire_examples.append((tag, wp, i, gt.value_at(wp, i)))
            else:
                cat["STABLE_HOLD"] += 1

tot = sum(cat.values())
print(f"missed-row cells: {tot}")
print(f"  STABLE_HOLD  (harmless, tracker holds): {cat['STABLE_HOLD']} "
      f"({100*cat['STABLE_HOLD']/tot:.1f}%)")
print(f"  IN_FIRE_ZONE (could cost a fire):       {cat['IN_FIRE_ZONE']} "
      f"({100*cat['IN_FIRE_ZONE']/tot:.1f}%)")
print("\nIN_FIRE_ZONE examples (clip,wp,frame,gt_value):")
for e in in_fire_examples:
    print("  ", e)
