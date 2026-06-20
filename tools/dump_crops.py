"""Dump the COUNT-CELL crop for specific (clip,weapon,frame) cases as upscaled PNGs so a
human (or me) can SEE what the matcher is choking on. Saves raw grayscale + the text_feature
map side by side, big enough to read.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from PIL import Image
from tests.lib import recordings as R
from tests.lib import detect as D
import src.winwinghaptics.detection.hud_detect as H

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_crops")
os.makedirs(OUT, exist_ok=True)

# (clip_key substring, weapon, frame, true, got) — pulled from taxonomy/diag earlier
CASES = [
    ("140000", "CNN", 253, 268, 208),
    ("140000", "CNN", 254, 266, 200),
    ("140000", "AAM", 182, 3, None),
    ("101336", "CNN", 120, 216, 215),
    ("140000", "RKT", 300, 24, None),
]

def upscale(a, k=8):
    a = np.clip(a, 0, 255).astype(np.uint8)
    return np.kron(a, np.ones((k, k), np.uint8))

for sub, wp, frame, true, got in CASES:
    clip = next((c for c in R.discover() if sub in c.key), None)
    if clip is None:
        print("no clip", sub); continue
    cal, src = D._calibrate(clip)
    grays = clip.grays()
    if frame >= len(grays):
        print("frame oob", sub, frame); continue
    g = grays[frame]
    y0 = cal.rows.get(wp)
    if y0 is None:
        print("no row", wp, "in", sub, "rows=", sorted(cal.rows)); continue
    # crop a generous count-cell window around the calibrated row + count_x
    rh = cal.row_h
    cx0 = max(0, cal.count_x - 6)
    cx1 = min(g.shape[1], cal.count_x + int(cal.pitch * 5))
    yy0 = max(0, y0 - rh); yy1 = min(g.shape[0], y0 + rh)
    raw = g[yy0:yy1, cx0:cx1]
    tn = H.text_feature(raw, cal.mode, cal.gain)
    # stack raw over feature
    big_raw = upscale(raw, 8)
    big_tn = upscale(tn, 8)
    H_ = max(big_raw.shape[0], big_tn.shape[0])
    W_ = max(big_raw.shape[1], big_tn.shape[1])
    canvas = np.zeros((H_*2 + 8, W_), np.uint8)
    canvas[:big_raw.shape[0], :big_raw.shape[1]] = big_raw
    canvas[H_+8:H_+8+big_tn.shape[0], :big_tn.shape[1]] = big_tn
    name = f"{sub}_{wp}_f{frame}_true{true}_got{got}.png"
    Image.fromarray(canvas).save(os.path.join(OUT, name))
    print("saved", name, "raw crop shape", raw.shape)
