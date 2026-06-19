"""Apply human verify feedback to a clip's ground-truth file.

Reconstructs a hold-constant step function from the human anchor marks (the user marks
transitions + confirmations; the value holds between marks), writes it as the new dense GT,
and marks it VERIFIED (_unverified=false) so it gates the build.

Outlier guard: a single anchor that spikes away from both neighbours (|d|>OUTLIER and the
neighbours agree) is treated as a typo and dropped (reported), so e.g. CHFF 270,279,270 keeps
270. Use --keep-outliers to disable.

Usage:
  python tools/apply_feedback.py "<clip_key>"            # write refined GT (verified)
  python tools/apply_feedback.py "<clip_key>" --dry-run  # show, don't write
"""
import os
import sys
import json
import glob
import argparse

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "tests"))
from lib import recordings as R       # noqa: E402
from lib import groundtruth as G      # noqa: E402
from lib import detect as D           # noqa: E402

FB_DIR = os.path.normpath(os.path.join(_HERE, "..", "tests", "feedback"))
GT_DIR = os.path.normpath(os.path.join(_HERE, "..", "tests", "ground_truth"))
CLIPS = {c.key: c for c in R.discover()}
OUTLIER = 8


def anchors_for(clip, fb, keep_outliers=False):
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
                    continue
            elif st == "value":
                v = e.get("value")
            elif st == "absent":
                v = None
            else:
                continue
            pts.setdefault(wp, {})[n] = v
    # per weapon: drop typo outliers, then collapse to change anchors
    out = {}
    dropped = []
    for wp, fr_map in pts.items():
        seq = sorted(fr_map.items())
        if not keep_outliers and len(seq) >= 3:
            clean = []
            for i, (fr, v) in enumerate(seq):
                if 0 < i < len(seq) - 1 and v is not None:
                    a = seq[i - 1][1]; b = seq[i + 1][1]
                    if a is not None and b is not None and abs(v - a) > OUTLIER \
                            and abs(v - b) > OUTLIER and abs(a - b) <= 2:
                        dropped.append((wp, fr, v, a))
                        continue
                clean.append((fr, v))
            seq = clean
        anchors = []
        for fr, v in seq:
            if not anchors or anchors[-1][1] != v:
                anchors.append((fr, v))
        out[wp] = anchors
    return out, dropped


def build_segments(anchors, n_frames):
    """Hold-constant: each anchor's value spans until the next anchor (or clip end).
    Absent anchors (value None) create a gap in presence (handled via _present)."""
    segs = []
    present = []
    for i, (fr, v) in enumerate(anchors):
        end = (anchors[i + 1][0] - 1) if i + 1 < len(anchors) else (n_frames - 1)
        if v is None:
            continue                          # absent stretch -> not a value segment
        segs.append([fr, end, v])
        present.append([fr, end])
    # merge adjacent equal-value segments
    merged = []
    for s in segs:
        if merged and merged[-1][2] == s[2] and merged[-1][1] + 1 == s[0]:
            merged[-1][1] = s[1]
        else:
            merged.append(s)
    # merge adjacent presence spans
    pmerged = []
    for s in present:
        if pmerged and pmerged[-1][1] + 1 >= s[0]:
            pmerged[-1][1] = max(pmerged[-1][1], s[1])
        else:
            pmerged.append(list(s))
    return merged, pmerged


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("clip")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--keep-outliers", action="store_true")
    args = ap.parse_args()

    key = args.clip
    clip = CLIPS.get(key)
    if not clip:
        print(f"unknown clip {key}"); return 1
    fbp = os.path.join(FB_DIR, key.replace("/", "__") + ".json")
    if not os.path.exists(fbp):
        print(f"no feedback for {key}"); return 1
    with open(fbp, encoding="utf-8") as f:
        fb = json.load(f)

    n = len(clip.png_paths())
    anchors, dropped = anchors_for(clip, fb, args.keep_outliers)
    if dropped:
        print("Dropped likely typos (value far from both neighbours):")
        for wp, fr, v, a in dropped:
            print(f"   {wp} f{fr}: {v} -> using neighbour {a}")

    # preserve _exclude (polluted frame ranges) from the prior GT file, if any
    prev_exclude = []
    prevp = os.path.join(GT_DIR, key.replace("/", "__") + ".json")
    if os.path.exists(prevp):
        try:
            with open(prevp, encoding="utf-8-sig") as f:
                prev_exclude = json.load(f).get("_exclude", [])
        except Exception:
            prev_exclude = []

    data = {"_comment": f"VERIFIED from human feedback ({key}); hold-constant between marks.",
            "_unverified": False, "_present": {}}
    if prev_exclude:
        data["_exclude"] = prev_exclude
    for wp, anch in anchors.items():
        segs, present = build_segments(anch, n)
        if segs:
            data[wp] = segs
            data["_present"][wp] = present

    print(f"\nRefined GT for {key}:")
    for wp in sorted(k for k in data if not k.startswith("_")):
        print(f"  {wp}: " + "  ".join(f"[{s},{e}]={v}" for s, e, v in data[wp]))

    if args.dry_run:
        print("\n(dry-run, not written)")
        return 0
    outp = os.path.join(GT_DIR, key.replace("/", "__") + ".json")
    with open(outp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    print(f"\nwrote {os.path.relpath(outp)}  (VERIFIED)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
