"""
falsefire_rootcause.py — classify every FALSE FIRE the tracker produces on the
verified clips, so we can reduce them without touching real-fire latency.

A false fire = the tracker emitted a fire event at a frame that is NOT inside (or
adjacent to) any real GT fire zone for that weapon. For each one we reconstruct:
  - the fire's (kind, old->new, delta)
  - the GT value around that frame (was the count actually steady?)
  - the raw detector reads in the window (flicker? transient misread? recovery?)
  - how far it sits from the nearest real fire zone (a near-miss double-fire vs a
    pure phantom)

and bucket it into a cause class. Run:  python tools/falsefire_rootcause.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tests"))
from lib import recordings as R          # noqa: E402
from lib import groundtruth as G         # noqa: E402
from lib import detect as D              # noqa: E402
from lib import platform_metrics as P    # noqa: E402
sys.path.insert(0, os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "src")))
import winwinghaptics.detection.hud_detect as H   # noqa: E402

ASSOC = P.ASSOC


def fire_log(clip, gt, det):
    """Replay tracker, capturing every fire with full context per weapon/frame."""
    classes = {w: H.WEAPON_CLASS.get(w, "discrete") for w in gt.weapons}
    tk = H.TemporalTracker(classes=classes)
    fires = []   # (frame, wp, kind, old, new, delta)
    for i, rd in enumerate(det["reads"]):
        upd = {wp: (v, 0.9) for wp, v in rd.items()}
        for wp, _e, kind, delta, old, new in tk.update(upd):
            fires.append((i, wp, kind, old, new, delta))
    return fires


def real_zones(gt, wp):
    return P._episodes_from_segments(gt, wp)


def classify(clip, gt, det, frame, wp, kind, old, new, delta):
    reads = det["reads"]
    n = len(reads)

    def raw(i):
        return reads[i].get(wp) if 0 <= i < n else None

    win = [raw(j) for j in range(max(0, frame - 6), min(n, frame + 4))]
    gtv = gt.value_at(wp, frame)
    gt_prev = gt.value_at(wp, max(0, frame - 4))
    gt_next = gt.value_at(wp, min(n - 1, frame + 4))

    # distance to nearest real fire zone for this weapon
    zones = real_zones(gt, wp)
    nearest = None
    for zs, ze, o, nw in zones:
        d = 0 if (zs - ASSOC) <= frame <= (ze + ASSOC) else min(
            abs(frame - (zs - ASSOC)), abs(frame - (ze + ASSOC)))
        if nearest is None or d < nearest[0]:
            nearest = (d, zs, ze, o, nw)

    # did the raw value bounce back up to >= old after this frame? (transient misread)
    recovered = any((v is not None and v >= old) for v in
                    [raw(j) for j in range(frame + 1, min(n, frame + 6))])
    # was GT actually steady across this frame? (true value didn't change)
    gt_steady = (gt_prev is not None and gt_next is not None and gt_prev == gt_next)

    # cause buckets
    if nearest is not None and nearest[0] <= 4:
        cause = "DOUBLE_FIRE_near_real"   # extra fire adjacent to a genuine event
    elif gt_steady and recovered:
        cause = "TRANSIENT_MISREAD"       # GT flat, raw dipped then recovered -> phantom
    elif gt_steady and not recovered:
        cause = "PERSISTED_MISREAD"       # GT flat but raw stuck low (resync/poison)
    elif gtv is None:
        cause = "GT_UNKNOWN_window"       # in a transition/unknown GT span
    else:
        cause = "OTHER"

    return {
        "frame": frame, "wp": wp, "kind": kind, "old": old, "new": new,
        "delta": delta, "gtv": gtv, "gt_prev": gt_prev, "gt_next": gt_next,
        "win": win, "recovered": recovered, "gt_steady": gt_steady,
        "nearest_d": (nearest[0] if nearest else None), "cause": cause,
    }


def main():
    buckets = {}
    rows = []
    for clip in R.discover():
        if not G.has_gt(clip.key):
            continue
        gt = G.load(clip.key, len(clip.png_paths()))
        if gt.unverified:
            continue   # gated clips only
        det = D.redetect(clip, use_cache=True)
        ev = P.score_events(clip, gt, det)
        false_list = ev["_false_list"]   # (frame, wp)
        if not false_list:
            continue
        fires = fire_log(clip, gt, det)
        fmap = {(f, w): (k, o, nw, d) for (f, w, k, o, nw, d) in fires}
        for (frame, wp) in false_list:
            k, o, nw, d = fmap.get((frame, wp), ("?", None, None, None))
            info = classify(clip, gt, det, frame, wp, k, o, nw, d)
            info["clip"] = clip.key.split("/")[-1]
            rows.append(info)
            buckets[info["cause"]] = buckets.get(info["cause"], 0) + 1

    print("=" * 78)
    print(f"FALSE FIRES (verified clips): {len(rows)}")
    print("=" * 78)
    for c, n in sorted(buckets.items(), key=lambda x: -x[1]):
        print(f"  {c:24s} {n}")
    print("-" * 78)
    for r in sorted(rows, key=lambda x: (x["cause"], x["clip"], x["frame"])):
        print(f"[{r['cause']:20s}] {r['clip']} f{r['frame']:<4d} {r['wp']:5s} "
              f"{r['kind']:8s} {r['old']}->{r['new']} (d={r['delta']}) "
              f"gt~{r['gt_prev']}/{r['gtv']}/{r['gt_next']} "
              f"nearReal={r['nearest_d']} recov={r['recovered']}")
        print(f"      raw win: {r['win']}")


if __name__ == "__main__":
    main()
