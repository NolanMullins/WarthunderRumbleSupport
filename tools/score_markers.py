"""
score_markers.py -- score missile detection against the keyboard ground-truth markers in an
enriched recording (see sources/marker.py + the recorder).

Given a recording directory (the hud_rec_* folder the app writes), this reads telemetry.jsonl and
aligns the user's real fire MARKERS (key-down at the instant of launch) with the detector's
dispatched fires. It reports, with zero hand-labelling:
  * hits    -- a marked launch that the detector fired for (within the match window), + latency
  * misses  -- a marked launch with NO detector fire nearby (the "missiles rarely work" symptom)
  * phantoms-- a detector fire with NO marker nearby (a false buzz)

Usage:
    python tools\\score_markers.py <path-to-hud_rec_dir> [--weapon AAM] [--window-s 1.5]

The marker is the user's intent ("I launched"); the detector fire follows the count drop a few
frames later, so we match each marker to the nearest fire within +/- window seconds.
"""
import os
import sys
import json
import argparse


def load_jsonl(path):
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except Exception:
                    pass
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("rec_dir", help="path to a hud_rec_* recording directory")
    ap.add_argument("--weapon", default="AAM", help="weapon to score (default AAM = missiles)")
    ap.add_argument("--effect", default=None,
                    help="match dispatched fires by effect name instead of weapon")
    ap.add_argument("--window-s", type=float, default=1.2,
                    help="forward match window (s): a detector fire may lag a marker by up to this")
    ap.add_argument("--lead-s", type=float, default=0.4,
                    help="how far BEFORE a marker a fire may sit (marker-reaction lag tolerance)")
    args = ap.parse_args()

    tpath = os.path.join(args.rec_dir, "telemetry.jsonl")
    if not os.path.isfile(tpath):
        print(f"no telemetry.jsonl in {args.rec_dir}")
        return 2
    rows = load_jsonl(tpath)
    header = next((r for r in rows if r.get("type") == "header"), {})
    frames = [r for r in rows if r.get("type") == "frame"]
    markers = [r for r in rows if r.get("type") == "marker"]

    print("=" * 70)
    print(f"SESSION {os.path.basename(args.rec_dir.rstrip(os.sep))}")
    print(f"  frames={len(frames)} markers={len(markers)} "
          f"marker_key={header.get('marker_key')} "
          f"marker_available={header.get('marker_available')}")
    print(f"  duration_s={header.get('duration_s')} screen={header.get('screen')} "
          f"weapons={header.get('weapons')}")
    print("=" * 70)
    if not header.get("marker_available", True):
        print("WARNING: marker key was not available during capture (no ground truth).")
    if not markers:
        print("No markers recorded -- nothing to score. (Tap the mark key when you fire.)")
        return 1

    # detector fires for the target weapon/effect, from the per-frame dispatched list
    fires = []   # (t, n, old, new)
    for fr in frames:
        for d in (fr.get("dispatched") or []):
            hit = (d.get("effect") == args.effect) if args.effect else (d.get("weapon") == args.weapon)
            if hit:
                fires.append((fr.get("t"), fr.get("n"), d.get("old"), d.get("new")))
    W = args.window_s
    LEAD = args.lead_s

    used = set()
    hits = []
    misses = []
    for m in markers:
        mt = m.get("t")
        # nearest unused fire in the asymmetric window [-LEAD, +W] around the marker: a real
        # launch's detector fire lands AT or just AFTER the marker (count drops post-launch, then
        # a few frames of confirmation), so a fire well before the marker is NOT this launch.
        best = None
        for i, (ft, fn, old, new) in enumerate(fires):
            if i in used or ft is None or mt is None:
                continue
            dt = ft - mt
            if -LEAD <= dt <= W and (best is None or abs(dt) < abs(best[1])):
                best = (i, dt, fn, old, new)
        if best is not None:
            used.add(best[0])
            hits.append((m.get("idx"), m.get("n"), round(best[1], 2), best[3], best[4]))
        else:
            misses.append((m.get("idx"), m.get("n")))
    phantoms = [fires[i] for i in range(len(fires)) if i not in used]

    n_marks = len(markers)
    print(f"\nGROUND-TRUTH LAUNCHES (markers): {n_marks}")
    print(f"  detector HITS   : {len(hits)}  ({100.0*len(hits)/n_marks:.0f}%)")
    print(f"  detector MISSES : {len(misses)}  ({100.0*len(misses)/n_marks:.0f}%)")
    print(f"  detector PHANTOMS (fires with no nearby launch): {len(phantoms)}")
    if hits:
        lats = [h[2] for h in hits]
        print(f"  hit latency (fire_t - marker_t): "
              f"min={min(lats):+.2f}s avg={sum(lats)/len(lats):+.2f}s max={max(lats):+.2f}s")
    if misses:
        print("\n  MISSED launches (marker idx, frame):")
        for idx, n in misses:
            print(f"    #{idx} @ frame {n}")
    if phantoms:
        print("\n  PHANTOM fires (t, frame, old->new):")
        for ft, fn, old, new in phantoms:
            print(f"    frame {fn}  {old}->{new}")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
