"""
event_harness.py — EVENT-LEVEL accuracy on the real recording.

What actually matters for haptics is not per-frame value accuracy but whether we FIRE when
a weapon fires and stay SILENT otherwise. This harness replays all 360 frames through the
detector + TemporalTracker and scores:

  * HITS:   each true fire window got >=1 haptic   (good)
  * MISSES: a true fire window got no haptic        (bad - missed feedback)
  * FALSE:  any haptic during a known-silent region (bad - random buzzing)

True events were established from the read timelines + hand inspection of this recording:
  AAM: 5 -> 4 (~f227), 4 -> 3 (~f313)            two missiles
  CNN: 270 -> 244 -> 216  (gun burst ~f32-62)    gun rumble
  FLR: 138 -> 130          (flare dribble ~f98-117)
  CHFF: 270 the entire time                       ZERO events
Everything else read (scattered 1 / 2 / 218 / 870) is single-frame misread noise.
"""
import sys, glob, os, struct, zlib
import numpy as np
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
import winwinghaptics.detection.hud_detect as H

REC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "recordings",
                   "hud_rec_20260618_101336", "hud_rec_20260618_101336")

# (weapon, start, end) frames where a fire SHOULD produce >=1 haptic (with tolerance)
EXPECT = [
    ("AAM", 220, 240),
    ("AAM", 305, 326),
    ("CNN", 26, 70),
    ("FLR", 92, 126),
]
# regions where NO haptic should occur (per weapon). Everything not in EXPECT, basically.
SILENT = {
    "CHFF": [(0, 360)],
    "AAM": [(0, 215), (245, 300), (331, 360)],
    "CNN": [(75, 360)],
    "FLR": [(0, 88), (131, 360)],
}


def load_png_gray(p):
    d = open(p, "rb").read(); i = 8; W = Hh = 0; idat = b""
    while i < len(d):
        ln = struct.unpack(">I", d[i:i + 4])[0]; typ = d[i + 4:i + 8]
        data = d[i + 8:i + 8 + ln]; i += 12 + ln
        if typ == b"IHDR":
            W, Hh = struct.unpack(">II", data[:8])
        elif typ == b"IDAT":
            idat += data
        elif typ == b"IEND":
            break
    raw = zlib.decompress(idat); g = np.zeros((Hh, W), np.float32); st = 0
    for y in range(Hh):
        st += 1; g[y] = np.frombuffer(raw[st:st + W], np.uint8); st += W
    return g


def in_any(frame, ranges):
    return any(a <= frame <= b for a, b in ranges)


def main():
    rec = sys.argv[1] if len(sys.argv) > 1 else REC
    fs = sorted(glob.glob(os.path.join(rec, "*.png")))
    grays = [load_png_gray(p) for p in fs]
    n = len(grays)
    cal = H.calibrate_from_grays(grays[:12])
    if cal is None:
        print("CALIBRATION FAILED"); return
    print(f"calibrated count_x={cal.count_x} rows={cal.rows}  frames={n}")

    tracker = H.TemporalTracker()
    fires = []   # (frame, wp, old, new, kind)
    for i, g in enumerate(grays):
        reads = H.read_counts(g, cal)
        evs = tracker.update(reads)
        for wp, effect, kind, delta, old, new in evs:
            fires.append((i, wp, old, new, kind))

    by_wp = {}
    for fr, wp, old, new, kind in fires:
        by_wp.setdefault(wp, []).append((fr, old, new))

    print("\nfires by weapon:")
    for wp in ["AAM", "RKT", "BMB", "CNN", "FLR", "CHFF"]:
        if wp in by_wp:
            print(f"  {wp}: " + ", ".join(f"f{fr}:{o}->{nw}" for fr, o, nw in by_wp[wp]))

    # HITS / MISSES
    print("\nexpected fire windows:")
    hits = misses = 0
    for wp, a, b in EXPECT:
        got = [fr for fr, o, nw in by_wp.get(wp, []) if a <= fr <= b]
        ok = len(got) > 0
        hits += ok; misses += (not ok)
        print(f"  {wp} [{a}-{b}]: {'HIT' if ok else 'MISS'}  ({len(got)} fires)")

    # FALSE POSITIVES
    print("\nfalse fires in silent regions:")
    false = 0
    for wp, lst in by_wp.items():
        for fr, o, nw in lst:
            if wp in SILENT and in_any(fr, SILENT[wp]):
                false += 1
                print(f"  {wp} f{fr}: {o}->{nw}  (should be silent)")
    if false == 0:
        print("  (none)")

    print(f"\nSCORE: hits={hits}/{len(EXPECT)}  misses={misses}  false_fires={false}")


if __name__ == "__main__":
    main()
