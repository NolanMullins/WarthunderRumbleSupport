"""
ab_report.py — standalone A/B regression gate for the WinwingHaptics detector + tracker.

Runs the two INDEPENDENT failure tracks over every recording and compares against the
committed baseline snapshot. No pytest required.

  TRACK 2  event failures  (false fires + missed fires) — frozen reads, ALL recordings
  TRACK 1  misreads         (read != ground-truth value) — faithful tier, calib.json clips

Only VERIFIED ground truth (_unverified=false) contributes to the PASS/FAIL gate and the
baseline. UNVERIFIED (auto-derived) GT is run in ADVISORY mode: shown for information, never
gates, never written to the baseline — until a human verifies it and flips the flag.

Usage:
  python tools/ab_report.py                 # run + compare to baseline, exit 1 on regression
  python tools/ab_report.py --update-baseline   # re-snapshot the baseline (after a win)
"""
import os
import sys
import json
import argparse

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tests"))
from lib import recordings as R          # noqa: E402
from lib import groundtruth as G         # noqa: E402
from lib import metrics as M             # noqa: E402

BASELINE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                        "tests", "baseline_metrics.json")


def compute():
    """Run both tracks over every recording with GT. Returns (verified, advisory) dicts:
    {clip_key: {"event": {...}|None, "misread": {...}|None, "unverified": bool}}.

    A clip is included in the EVENT track only if it has frozen per-frame reads (the oldest
    clip used a schema with empty reads and is skipped). The MISREAD track needs calib.json.
    Clips with neither usable tier are skipped entirely.
    """
    verified, advisory = {}, {}
    for clip in R.discover():
        if not G.has_gt(clip.key):
            continue
        n = len(clip.saved_reads())
        gt = G.load(clip.key, n)
        ev = M.score_events(clip, gt) if clip.has_frozen_reads else None
        mr = M.score_misreads(clip, gt) if clip.has_calib else None
        if ev is None and mr is None:
            continue                      # no usable tier (e.g. oldest clip, no calib)
        rec = {"event": ev, "misread": mr, "unverified": gt.unverified}
        (advisory if gt.unverified else verified)[clip.key] = rec
    return verified, advisory


def aggregate(results):
    """Sum the two tracks across recordings into aggregate rates."""
    ff = mf = failed = frames = 0
    mis = cells = 0
    faithful = 0
    for r in results.values():
        e = r["event"]
        if e is not None:
            ff += e["false_fires"]; mf += e["missed_fires"]
            failed += e["failed_frames"]; frames += e["n_frames"]
        if r["misread"] is not None:
            faithful += 1
            mis += r["misread"]["misread_cells"]; cells += r["misread"]["scored_cells"]
    return {
        "event_failed_frames": failed, "event_total_frames": frames,
        "event_false_fires": ff, "event_missed_fires": mf,
        "event_failure_rate": (failed / frames) if frames else None,
        "misread_faithful_clips": faithful,
        "misread_cells": mis, "misread_scored_cells": cells,
        "misread_rate": (mis / cells) if cells else None,
    }


def snapshot(verified):
    """The committed contract: per-clip + aggregate numbers for VERIFIED recordings only."""
    per = {}
    for k, r in verified.items():
        e = r["event"]
        m = r["misread"]
        per[k] = {
            "event_failure_rate": (e["failure_rate"] if e else None),
            "event_failed_frames": (e["failed_frames"] if e else None),
            "event_false_fires": (e["false_fires"] if e else None),
            "event_missed_fires": (e["missed_fires"] if e else None),
            "n_frames": (e["n_frames"] if e else None),
            "misread_rate": (m["misread_rate"] if m else None),
        }
    return {"aggregate": aggregate(verified), "per_recording": per}


def _fmt_rate(x):
    return "n/a" if x is None else f"{x*100:.3f}%"


def print_report(verified, advisory, baseline):
    print("=" * 78)
    print("A/B REGRESSION REPORT — WinwingHaptics detector + tracker")
    print("=" * 78)

    def block(title, results, gated):
        if not results:
            return
        print(f"\n--- {title} ---")
        for k in sorted(results):
            e = results[k]["event"]
            m = results[k]["misread"]
            tag = "" if gated else "  [ADVISORY/unverified]"
            print(f"  {k}{tag}")
            if e is not None:
                print(f"      event: failrate={_fmt_rate(e['failure_rate'])} "
                      f"(failed={e['failed_frames']}/{e['n_frames']}  "
                      f"false={e['false_fires']} missed={e['missed_fires']})")
            else:
                print(f"      event: no frozen reads (event tier inactive)")
            if m is not None:
                print(f"      misread: rate={_fmt_rate(m['misread_rate'])} "
                      f"(bad={m['misread_cells']}/{m['scored_cells']})")
            else:
                print(f"      misread: no calib.json (faithful tier inactive)")

    block("VERIFIED (gated)", verified, True)
    block("ADVISORY (auto-derived GT, not gated)", advisory, False)

    agg = aggregate(verified)
    print("\n--- AGGREGATE (verified only) ---")
    print(f"  event_failure_rate : {_fmt_rate(agg['event_failure_rate'])} "
          f"({agg['event_failed_frames']}/{agg['event_total_frames']} frames; "
          f"false={agg['event_false_fires']} missed={agg['event_missed_fires']})")
    print(f"  misread_rate       : {_fmt_rate(agg['misread_rate'])} "
          f"({agg['misread_faithful_clips']} faithful clip(s))")

    if baseline is None:
        print("\n(no baseline yet — run with --update-baseline to create one)")
        return True

    # ---- compare to baseline, PER TRACK, gate on regression ----
    print("\n--- vs BASELINE ---")
    ok = True
    bagg = baseline["aggregate"]
    cagg = agg

    def cmp_rate(name, cur, base):
        nonlocal ok
        if base is None and cur is None:
            print(f"  {name}: n/a")
            return
        if base is None:
            print(f"  {name}: {_fmt_rate(cur)} (no baseline)")
            return
        if cur is None:
            print(f"  {name}: n/a (was {_fmt_rate(base)})")
            return
        d = cur - base
        verdict = "OK" if cur <= base + 1e-12 else "REGRESSION"
        if cur > base + 1e-12:
            ok = False
        print(f"  {name}: {_fmt_rate(base)} -> {_fmt_rate(cur)} "
              f"(delta {d*100:+.3f}pp)  {verdict}")

    cmp_rate("event_failure_rate", cagg["event_failure_rate"], bagg["event_failure_rate"])
    cmp_rate("misread_rate", cagg["misread_rate"], bagg["misread_rate"])

    # per-recording no-regression check (event track)
    bper = baseline.get("per_recording", {})
    for k in sorted(verified):
        cur = verified[k]["event"]["failure_rate"]
        base = bper.get(k, {}).get("event_failure_rate")
        if base is not None and cur > base + 1e-12:
            ok = False
            print(f"  REGRESSION {k}: event {_fmt_rate(base)} -> {_fmt_rate(cur)}")

    print("\nRESULT:", "PASS" if ok else "FAIL (regression detected)")
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--update-baseline", action="store_true",
                    help="re-snapshot the baseline from the current verified results")
    args = ap.parse_args()

    if not R.discover():
        print("No recordings found under recordings/. Nothing to do.")
        return 0

    verified, advisory = compute()
    baseline = None
    if os.path.exists(BASELINE):
        with open(BASELINE, encoding="utf-8") as f:
            baseline = json.load(f)

    if args.update_baseline:
        snap = snapshot(verified)
        with open(BASELINE, "w", encoding="utf-8") as f:
            json.dump(snap, f, indent=2)
        print(f"Baseline updated -> {os.path.relpath(BASELINE)}")
        print_report(verified, advisory, None)
        return 0

    ok = print_report(verified, advisory, baseline)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
