"""
calib_report.py — CALIBRATION-QUALITY track for WinwingHaptics.

Runs the REAL runtime auto-calibration (calibrate_from_grays on a consecutive window, exactly
as the app does on (re)calibrate) at several moments across every recording, treating each
recording as a respawn/life. Scores how well calibration learns the rows that are actually on
the HUD, how often it fails, which rows it systematically misses, and how stable its geometry
is. This is the harness for improving the calibration core.

Only VERIFIED ground truth gates + feeds the baseline; UNVERIFIED is ADVISORY.

Usage:
  python tools/calib_report.py
  python tools/calib_report.py --update-baseline
  python tools/calib_report.py --no-cache
"""
import os
import sys
import json
import argparse

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tests"))
from lib import recordings as R          # noqa: E402
from lib import groundtruth as G         # noqa: E402
from lib import calib_quality as C       # noqa: E402

BASELINE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                        "tests", "calib_baseline.json")


def _pct(x):
    return "n/a" if x is None else f"{x*100:.1f}%"


def run(use_cache=True):
    results = []
    for clip in R.discover():
        if not G.has_gt(clip.key):
            continue
        gt = G.load(clip.key, len(clip.png_paths()))
        sc = C.score_calibration(clip, gt, use_cache=use_cache)
        results.append({"key": clip.key, "unverified": gt.unverified, "calib": sc})
    return results


def aggregate(results):
    fracs = [r["calib"]["mean_rows_frac"] for r in results
             if r["calib"]["mean_rows_frac"] is not None]
    fails = [r["calib"]["fail_rate"] for r in results if r["calib"]["fail_rate"] is not None]
    always = []
    for r in results:
        for w in r["calib"]["always_missed"]:
            always.append(f"{r['key'].split('/')[-1]}:{w}")
    return {
        "mean_rows_frac": (sum(fracs) / len(fracs)) if fracs else None,
        "mean_fail_rate": (sum(fails) / len(fails)) if fails else None,
        "always_missed": always,
        "always_missed_count": len(always),
    }


def snapshot(results):
    verified = [r for r in results if not r["unverified"]]
    per = {r["key"]: {
        "mean_rows_frac": r["calib"]["mean_rows_frac"],
        "worst_rows_frac": r["calib"]["worst_rows_frac"],
        "fail_rate": r["calib"]["fail_rate"],
        "always_missed": r["calib"]["always_missed"],
        "count_x_spread": r["calib"]["count_x_spread"],
    } for r in verified}
    return {"detector_hash": C._hash(), "aggregate": aggregate(verified),
            "per_recording": per}


def print_clip(r, gated):
    c = r["calib"]
    tag = "" if gated else "  [ADVISORY]"
    print(f"  {r['key']}{tag}")
    print(f"     rows_learned: mean={_pct(c['mean_rows_frac'])} "
          f"worst={_pct(c['worst_rows_frac'])}  fail_rate={_pct(c['fail_rate'])}  "
          f"count_x_spread={c['count_x_spread']}px")
    if c["always_missed"]:
        print(f"     !! NEVER-CALIBRATED rows (present but learned in 0 windows): "
              f"{c['always_missed']}")
    # show the per-window learned/expected so failures are visible
    cells = []
    for w in c["windows"]:
        mark = "ok" if w["ok"] else "FAIL"
        cells.append(f"f{w['start']}:{len(w['learned_present'])}/{len(w['expected'])}"
                     f"{'' if w['ok'] else '(FAIL)'}")
    print(f"     windows (learned/expected @start): " + "  ".join(cells))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--update-baseline", action="store_true")
    ap.add_argument("--no-cache", action="store_true")
    args = ap.parse_args()

    if not [c for c in R.discover() if G.has_gt(c.key)]:
        print("No recordings with ground truth. Nothing to do.")
        return 0

    print("=" * 80)
    print("CALIBRATION-QUALITY REPORT — real auto-calibration per clip (each clip = respawn)")
    print(f"detector hash: {C._hash()}   windows/clip: {C.N_WINDOWS}   win={C.WIN} frames")
    print("=" * 80)

    results = run(use_cache=not args.no_cache)
    verified = [r for r in results if not r["unverified"]]
    advisory = [r for r in results if r["unverified"]]

    if verified:
        print("\n--- VERIFIED (gated) ---")
        for r in verified:
            print_clip(r, True)
    if advisory:
        print("\n--- ADVISORY (auto-derived GT, not gated) ---")
        for r in advisory:
            print_clip(r, False)

    agg = aggregate(results)
    vagg = aggregate(verified)
    print("\n--- AGGREGATE (ALL recordings as respawns) ---")
    print(f"  mean rows learned : {_pct(agg['mean_rows_frac'])}")
    print(f"  mean fail rate    : {_pct(agg['mean_fail_rate'])}")
    if agg["always_missed"]:
        print(f"  NEVER-calibrated rows: {agg['always_missed']}")
    print(f"\n  (verified-only: rows={_pct(vagg['mean_rows_frac'])}, "
          f"never-calibrated={vagg['always_missed']})")

    baseline = None
    if os.path.exists(BASELINE):
        with open(BASELINE, encoding="utf-8") as f:
            baseline = json.load(f)

    if args.update_baseline:
        with open(BASELINE, "w", encoding="utf-8") as f:
            json.dump(snapshot(results), f, indent=2)
        print(f"\nBaseline updated -> {os.path.relpath(BASELINE)}")
        return 0

    if baseline is None:
        print("\n(no calibration baseline yet — run with --update-baseline)")
        return 0

    print("\n--- vs BASELINE (verified) ---")
    ok = True
    b = baseline["aggregate"]
    c = vagg
    # rows learned must not DROP; fail rate + never-calibrated must not RISE
    cur, base = c["mean_rows_frac"], b["mean_rows_frac"]
    if cur is not None and base is not None:
        verdict = "OK" if cur >= base - 1e-9 else "REGRESSION"
        if cur < base - 1e-9:
            ok = False
        print(f"  mean_rows_frac: {_pct(base)} -> {_pct(cur)}  {verdict}")
    cur, base = c["mean_fail_rate"], b["mean_fail_rate"]
    if cur is not None and base is not None:
        verdict = "OK" if cur <= base + 1e-9 else "REGRESSION"
        if cur > base + 1e-9:
            ok = False
        print(f"  mean_fail_rate: {_pct(base)} -> {_pct(cur)}  {verdict}")
    if c["always_missed_count"] > b.get("always_missed_count", 0):
        ok = False
        print(f"  never-calibrated rows: {b.get('always_missed_count',0)} -> "
              f"{c['always_missed_count']}  REGRESSION")
    else:
        print(f"  never-calibrated rows: {b.get('always_missed_count',0)} -> "
              f"{c['always_missed_count']}  OK")

    print("\nRESULT:", "PASS" if ok else "FAIL (calibration regression)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
