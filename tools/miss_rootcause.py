"""Per-event miss anatomy with ROOT CAUSE: for every missed GT fire episode, replay the tracker
and classify WHY no fire landed in the +/-2 window:

  GT_TIMING    : the reader already shows the post-fire value BEFORE the GT onset -> GT is
                 marked late; the fire was detected earlier (or there was nothing to fire).
  CLIP_EDGE    : the transition starts within the last ~2 frames of the clip -> not enough
                 frames left for the tracker to confirm (needs 2 supporting reads).
  MERGED       : a fire DID land but covered a bigger step (adjacent single-round ticks merged,
                 e.g. gun 115->114->113 fires once as 115->113 a frame later).
  BLINK_LATENCY: a None (cloud blink) split the two confirming reads, delaying the fire past
                 the window (it still fired, ~1 frame late).
  TRUE_MISS    : none of the above -- a genuinely lost fire.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests.lib import recordings as R
from tests.lib import detect as D
from tests.lib import groundtruth as G
from tests.lib import platform_metrics as P
import src.winwinghaptics.detection.hud_detect as H

ASSOC = P.ASSOC


def fires_for(det, gt):
    classes = {w: H.WEAPON_CLASS.get(w, "discrete") for w in gt.weapons}
    tk = H.TemporalTracker(classes=classes)
    fired = {w: {} for w in gt.weapons}      # frame -> (old,new)
    for i, rd in enumerate(det["reads"]):
        upd = {wp: (v, 0.9) for wp, v in rd.items()}
        for wp, _e, _k, _d, o, n in tk.update(upd):
            fired[wp][i] = (o, n)
    return fired


summary = {}
for clip in R.discover():
    if not G.has_gt(clip.key):
        continue
    det = D.redetect(clip)
    reads = det["reads"]
    n = len(reads)
    gt = G.load(clip.key, n)
    if gt.unverified:
        continue
    tag = clip.key.split("/")[-1][-6:]
    fired = fires_for(det, gt)
    for wp in gt.weapons:
        for zs, ze, old, new in P._episodes_from_segments(gt, wp):
            if gt.is_excluded(zs):
                continue
            lo, hi = zs - ASSOC, ze + ASSOC
            if any(lo <= f <= hi for f in fired[wp]):
                continue                              # HIT
            # ---- classify the miss ----
            # GT_TIMING: reader shows <= new value already at/just before zs
            pre = [reads[j].get(wp) for j in range(max(0, zs - 4), zs)]
            if any(v is not None and v <= new for v in pre):
                cause = "GT_TIMING"
            elif ze >= n - 3:
                cause = "CLIP_EDGE"
            else:
                near = sorted(fired[wp].items(), key=lambda kv: abs(kv[0] - zs))
                nf = near[0] if near and abs(near[0][0] - zs) <= 6 else None
                if nf is not None:
                    fo, fn = nf[1]
                    # was there a None between zs and the fire? -> blink latency
                    blink = any(reads[j].get(wp) is None for j in range(zs, nf[0] + 1))
                    if (fo - fn) > (old - new):
                        cause = "MERGED"
                    elif blink:
                        cause = "BLINK_LATENCY"
                    else:
                        cause = "LATENCY"
                else:
                    cause = "TRUE_MISS"
            summary.setdefault(cause, []).append((tag, wp, zs, f"{old}->{new}"))

print("MISSED EVENT ROOT CAUSES:\n")
order = ["TRUE_MISS", "MERGED", "LATENCY", "BLINK_LATENCY", "CLIP_EDGE", "GT_TIMING"]
for cause in order:
    items = summary.get(cause, [])
    if not items:
        continue
    print(f"{cause} ({len(items)}):")
    for tag, wp, fr, step in items:
        print(f"    {tag} {wp} f{fr} {step}")
    print()
total = sum(len(v) for v in summary.values())
felt = len(summary.get("TRUE_MISS", [])) + len(summary.get("LATENCY", [])) + \
       len(summary.get("BLINK_LATENCY", []))
print(f"total missed events: {total}")
print(f"  GT/clip artifacts (not real): "
      f"{len(summary.get('GT_TIMING', [])) + len(summary.get('CLIP_EDGE', []))}")
print(f"  gun-merge (felt as continuous rumble): {len(summary.get('MERGED', []))}")
print(f"  genuine latency/lost: {felt}")
