"""Baseline: current single-exemplar NCC digit accuracy on the harvested REAL crops.
This is the bar a learned classifier must beat. For each labeled crop we re-normalize it
the way _box_patch would and run H._best_digit against EACH clip's own digit templates
(the fairest match: a crop is scored by the calibration that produced it)."""
import sys, os, csv
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from PIL import Image
from collections import Counter, defaultdict
from tests.lib import recordings as R
from tests.lib import detect as D
import src.winwinghaptics.detection.hud_detect as H

DS = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "..", "tests", "digit_dataset"))

# map clip-tag -> its calib (templates)
tag_cal = {}
for clip in R.discover():
    cal, _ = D._calibrate(clip)
    if cal is None:
        continue
    H._ensure_mats(cal)
    tag_cal[clip.key.split("/")[0][-6:]] = cal

rows = []
with open(os.path.join(DS, "manifest.csv")) as f:
    for r in csv.DictReader(f):
        rows.append(r)

gw = next(iter(tag_cal.values())).gw
gh = next(iter(tag_cal.values())).gh

total = 0
correct = 0
conf = Counter()       # (true,pred) -> n  for errors
per_digit = defaultdict(lambda: [0, 0])   # digit -> [correct, total]
for r in rows:
    d = r["digit"]; tag = r["clip"]; wp = r["weapon"]; fr = r["frame"]; pos = r["pos"]
    cal = tag_cal.get(tag)
    if cal is None:
        continue
    path = os.path.join(DS, d, f"{tag}_{wp}_f{fr}_{pos}.png")
    if not os.path.exists(path):
        continue
    img = np.asarray(Image.open(path)).astype(np.float32)
    patch = H._norm(img)            # zero-mean unit-norm, same space as templates
    if patch is None:
        continue
    ch, s, mg = H._best_digit(patch, cal)
    total += 1
    per_digit[d][1] += 1
    if ch == d:
        correct += 1
        per_digit[d][0] += 1
    else:
        conf[(d, ch)] += 1

print(f"NCC digit accuracy on real crops: {correct}/{total} = {100.0*correct/total:.2f}%")
print("per-digit accuracy:")
for d in "0123456789":
    c, t = per_digit[d]
    print(f"  {d}: {c}/{t} = {(100.0*c/t):.1f}%" if t else f"  {d}: (none)")
print("top NCC confusions (true->pred : n):")
for (td, pd), n in conf.most_common(12):
    print(f"  {td}->{pd}: {n}")
