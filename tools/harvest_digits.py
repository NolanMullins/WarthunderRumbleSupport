"""Harvest a labeled DIGIT dataset from human-verified ground truth.

For every (clip, weapon, frame) where GT gives an exact stable value, we locate the count
row exactly the way the detector does (text_feature -> _estimate_shift -> count bands ->
label-verified row center), segment the count cell into glyph boxes, and -- ONLY when the
number of digit-width boxes equals the number of digits in the TRUE value -- emit one labeled
crop per digit (box i -> true_str[i]).

The label comes from GROUND TRUTH, not from NCC, so frames the current matcher MISREADS are
captured with the CORRECT label -- exactly the hard blur/ambiguity examples a learned
classifier needs. Crops are the same gw x gh normalized patches NCC consumes (H._box_patch),
so the classifier is a drop-in for _best_digit with no pipeline change.

Output: tests/digit_dataset/{digit}/{clip}_{wp}_f{frame}_{pos}.png  (8-bit, gw x gh)
plus a manifest.csv and a class histogram printed to stdout.
"""
import sys, os, csv
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from PIL import Image
from collections import Counter
from tests.lib import recordings as R
from tests.lib import detect as D
from tests.lib import groundtruth as G
import src.winwinghaptics.detection.hud_detect as H

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tests", "digit_dataset")
OUT = os.path.normpath(OUT)


def locate_row(tn, wp, calib, shift, count_x):
    """Replicate read_counts' per-row center selection (label-verified count band)."""
    yc0 = calib.rows[wp] + shift
    Hh = tn.shape[0]
    cands = H._count_bands(tn, yc0, calib, win=13, cx=count_x)
    best_cy, best_s = None, -1.0
    quick = (-1, 0, 1)
    wide = (-6, -5, -4, -3, -2, 2, 3, 4, 5, 6)
    for cy in cands:
        s = H._label_score_at(tn, wp, cy, calib, dys=quick)
        if s < calib_label_min:
            s = max(s, H._label_score_at(tn, wp, cy, calib, dys=wide))
        if s > best_s:
            best_s, best_cy = s, cy
    return best_cy


def digit_boxes(tn, best_cy, calib, count_x):
    """Replicate read_count_seg's box loop; return the digit-width boxes (no suffix)."""
    y0 = best_cy - calib.row_h; y1 = best_cy + calib.row_h
    cx0 = max(0, count_x - 4)
    cx1 = min(tn.shape[1], count_x + int(calib.pitch * 5))
    band = tn[max(0, y0):y1, cx0:cx1]
    if band.shape[0] < 6:
        return band, []
    boxes = H._seg_boxes(band, min_w=4)
    if not boxes or boxes[0][0] > calib.pitch * 0.85:
        return band, []
    min_digit_w = calib.pitch * 0.62
    out = []
    prev_x1 = None
    for (bx0, bx1) in boxes:
        w = bx1 - bx0
        if w > calib.pitch * 1.8:
            break
        if prev_x1 is not None and (bx0 - prev_x1) > calib.pitch * 0.7:
            break
        if w < min_digit_w:
            break
        out.append((bx0, bx1))
        prev_x1 = bx1
        if len(out) >= 4:
            break
    return band, out


calib_label_min = 0.42
counts = Counter()
manifest = []
skipped_segmismatch = 0
scored = 0

for clip in R.discover():
    if not G.has_gt(clip.key):
        continue
    cal, src = D._calibrate(clip)
    if cal is None:
        continue
    H._ensure_mats(cal)
    grays = clip.grays()
    gt = G.load(clip.key, len(grays))
    if gt.unverified:
        continue
    clip_tag = clip.key.split("/")[-1][-6:]
    for i, g in enumerate(grays):
        if gt.is_excluded(i):
            continue
        # locate block once per frame
        H0, W0 = g.shape
        tn = H.text_feature(g, cal.mode, cal.gain)
        shift, _ = H._estimate_shift(tn, cal)
        count_x = cal.count_x
        for wp in gt.weapons:
            if wp not in cal.rows or not gt.is_present(wp, i):
                continue
            v = gt.value_at(wp, i)
            if v is None:
                continue
            scored += 1
            best_cy = locate_row(tn, wp, cal, shift, count_x)
            if best_cy is None:
                continue
            band, boxes = digit_boxes(tn, best_cy, cal, count_x)
            ts = str(v)
            if len(boxes) != len(ts):
                skipped_segmismatch += 1
                continue
            for pos, (bx0, bx1) in enumerate(boxes):
                patch = H._box_patch(band, bx0, bx1, cal)
                if patch is None:
                    continue
                d = ts[pos]
                # un-normalize for storage: scale the zero-mean unit-norm patch to 0..255
                p = patch.astype(np.float32)
                p = p - p.min()
                mx = p.max()
                if mx > 1e-6:
                    p = p / mx * 255.0
                img = p.astype(np.uint8)
                ddir = os.path.join(OUT, d)
                os.makedirs(ddir, exist_ok=True)
                name = f"{clip_tag}_{wp}_f{i}_{pos}.png"
                Image.fromarray(img).save(os.path.join(ddir, name))
                counts[d] += 1
                manifest.append((d, clip_tag, wp, i, pos, v))

os.makedirs(OUT, exist_ok=True)
with open(os.path.join(OUT, "manifest.csv"), "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["digit", "clip", "weapon", "frame", "pos", "true_value"])
    w.writerows(manifest)

print(f"scored cells: {scored}   seg-count mismatches skipped: {skipped_segmismatch}")
print(f"total labeled digit crops: {sum(counts.values())}")
print("per-class histogram:")
for d in "0123456789":
    print(f"  {d}: {counts.get(d,0)}")
print("dataset ->", OUT)
