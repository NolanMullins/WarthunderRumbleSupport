"""Reconstruct a per-weapon ground-truth timeline from human verify feedback and compare it
to the current GT. 'confirmed' marks lock the detector's read at that frame as truth;
'value' marks give an explicit true value; 'absent' marks the row as not present.

Prints, per weapon: the human-anchored known points, the segments they imply, the current GT
segments, and any disagreements -> so we can fold corrections into the GT files.
"""
import os
import sys
import json
import glob

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "tests"))
from lib import recordings as R       # noqa: E402
from lib import groundtruth as G      # noqa: E402
from lib import detect as D           # noqa: E402

FB_DIR = os.path.normpath(os.path.join(_HERE, "..", "tests", "feedback"))
CLIPS = {c.key: c for c in R.discover()}


def known_points(clip, fb):
    """Return weapon -> sorted list of (frame, value-or-None-for-absent) anchored by the human.
    'correct' resolves to the detector's read at that frame."""
    reads = D.redetect(clip).get("reads", [])
    pts = {}
    for n, cells in fb.get("frames", {}).items():
        n = int(n)
        det = reads[n] if n < len(reads) else {}
        for wp, e in cells.items():
            st = e.get("status")
            if st == "correct":
                v = det.get(wp)
                if v is None:
                    continue        # 'correct' on a MISSED row is contradictory -> skip
            elif st == "value":
                v = e.get("value")
            elif st == "absent":
                v = None
            else:
                continue
            pts.setdefault(wp, []).append((n, v))
    for wp in pts:
        pts[wp] = sorted(pts[wp])
    return pts


def main():
    files = sorted(glob.glob(os.path.join(FB_DIR, "*.json")))
    if not files:
        print("No feedback yet.")
        return
    for p in files:
        with open(p, encoding="utf-8") as f:
            fb = json.load(f)
        key = fb["clip"]
        clip = CLIPS.get(key)
        if not clip:
            continue
        n_frames = len(clip.png_paths())
        gt = G.load(key, n_frames) if G.has_gt(key) else None
        pts = known_points(clip, fb)
        print(f"\n================ {key} ================")
        for wp in sorted(pts):
            seq = pts[wp]
            # collapse to value-change anchors
            anchors = []
            for fr, v in seq:
                if not anchors or anchors[-1][1] != v:
                    anchors.append((fr, v))
            astr = "  ".join(f"f{fr}:{'absent' if v is None else v}" for fr, v in anchors)
            print(f"  {wp}: {len(seq)} pts, {len(anchors)} change-anchors")
            print(f"     anchors: {astr}")
            if gt:
                gstr = "  ".join(f"[{s},{e}]={val}" for s, e, val in gt.segments.get(wp, []))
                print(f"     GT now: {gstr}")
                # flag anchors that fall in a stable GT segment with a DIFFERENT value
                for fr, v in anchors:
                    if v is None:
                        continue
                    gtv = gt.value_at(wp, fr)
                    if gtv is not None and gtv != v:
                        print(f"     !! MISMATCH f{fr}: human={v} vs GT={gtv}")
        # outlier sanity: values wildly off the local trend (likely a typo)
        for wp, seq in pts.items():
            vals = [v for _, v in seq if v is not None]
            if len(vals) >= 3:
                for i in range(1, len(vals) - 1):
                    a, b, c = vals[i - 1], vals[i], vals[i + 1]
                    if abs(b - a) > 5 and abs(b - c) > 5 and abs(a - c) <= 2:
                        fr = [f for f, v in seq if v == b][0]
                        print(f"  ? possible typo {wp} f{fr}: {b} (neighbours {a},{c})")


if __name__ == "__main__":
    main()
