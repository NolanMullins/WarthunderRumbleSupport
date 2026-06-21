"""
platform_report.py — higher-level test platform for the WinwingHaptics detector + tracker.

It RE-DETECTS on every PNG of every recording (current detector, not frozen reads) and scores
three tracks against ground truth, treating EACH RECORDING AS A RESPAWN/LIFE (the tracker is
reset between clips, exactly as it would be on a real respawn):

  ROW    : missed-row rate (a present weapon row read None) + whole-row calibration misses
           + false rows.  <-- the "missed row" problem, measured directly.
  VALUE  : misread rate (read value != ground truth on present/read frames).
  EVENT  : per real fire EPISODE -> HIT / MISS / FALSE (experience-denominated).

All recordings are included (the corpus = a sequence of respawns). Only VERIFIED ground truth
gates the build + feeds the baseline; UNVERIFIED (auto-derived) GT is shown ADVISORY only.

Usage:
  python tools/platform_report.py                 # run + compare to baseline (exit 1 on regress)
  python tools/platform_report.py --update-baseline
  python tools/platform_report.py --no-cache      # force full re-detection
"""
import os
import sys
import json
import argparse

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tests"))
from lib import recordings as R          # noqa: E402
from lib import groundtruth as G         # noqa: E402
from lib import detect as D              # noqa: E402
from lib import platform_metrics as P    # noqa: E402

BASELINE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                        "tests", "platform_baseline.json")


def _rate(x):
    return "n/a" if x is None else f"{x*100:.2f}%"


def run(use_cache=True):
    """Score every clip with GT. Returns ordered list of per-clip result dicts."""
    results = []
    for clip in R.discover():          # discover() is sorted -> deterministic respawn order
        if not G.has_gt(clip.key):
            continue
        det = D.redetect(clip, use_cache=use_cache)   # RESPAWN: fresh detect per clip
        n = len(clip.png_paths())
        gt = G.load(clip.key, n)
        rv = P.score_rows_values(clip, gt, det)
        ev = P.score_events(clip, gt, det)
        results.append({"key": clip.key, "unverified": gt.unverified,
                        "rows": rv, "events": ev})
    return results


def aggregate(results):
    pc = mr = fr = sv = ms = 0
    ev = hit = miss = false = false_ep = 0
    calib_missing = []
    for r in results:
        rv = r["rows"]; e = r["events"]
        pc += rv["present_cells"]; mr += rv["missed_row"]
        fr += rv["false_row"]; sv += rv["scored_value_cells"]; ms += rv["misread"]
        ev += e["events"]; hit += e["hits"]; miss += e["misses"]; false += e["false_fires"]
        false_ep += e.get("false_episodes", 0)
        for w in rv["calib_missing_rows"]:
            calib_missing.append(f"{r['key'].split('/')[-1]}:{w}")
    return {
        "present_cells": pc, "missed_row": mr,
        "missed_row_rate": (mr / pc) if pc else None,
        "false_row": fr,
        "scored_value_cells": sv, "misread": ms,
        "misread_rate": (ms / sv) if sv else None,
        "events": ev, "hits": hit, "misses": miss, "false_fires": false,
        "false_episodes": false_ep,
        "event_miss_rate": (miss / ev) if ev else None,
        "false_episode_rate": (false_ep / ev) if ev else None,
        "calib_missing_rows": calib_missing,
    }


def snapshot(results):
    verified = [r for r in results if not r["unverified"]]
    per = {}
    for r in verified:
        per[r["key"]] = {
            "missed_row_rate": r["rows"]["missed_row_rate"],
            "misread_rate": r["rows"]["misread_rate"],
            "event_miss_rate": r["events"]["event_miss_rate"],
            "event_false_fires": r["events"]["false_fires"],
            "false_episodes": r["events"].get("false_episodes", 0),
            "false_episode_rate": r["events"].get("false_episode_rate"),
        }
    return {"detector_hash": D.detector_hash(),
            "aggregate": aggregate(verified),
            "per_recording": per}


def print_clip(r, gated):
    rv = r["rows"]; e = r["events"]
    tag = "" if gated else "  [ADVISORY]"
    src = rv["source"] or "CALIB-FAILED"
    print(f"  {r['key']}  (detect:{src}){tag}")
    print(f"     ROW   missed={_rate(rv['missed_row_rate'])} "
          f"({rv['missed_row']}/{rv['present_cells']})  false_rows={rv['false_row']}")
    if rv["calib_missing_rows"]:
        print(f"           !! CALIB MISSED WHOLE ROW(S): {rv['calib_missing_rows']}")
    if rv["missed_by_weapon"]:
        by = ", ".join(f"{w}:{c}" for w, c in sorted(rv["missed_by_weapon"].items()))
        print(f"           by weapon: {by}")
    print(f"     VALUE misread={_rate(rv['misread_rate'])} "
          f"({rv['misread']}/{rv['scored_value_cells']})")
    print(f"     EVENT miss={_rate(e['event_miss_rate'])} "
          f"(hits={e['hits']} misses={e['misses']} of {e['events']}; "
          f"false_fires={e['false_fires']} -> {e.get('false_episodes', 0)} felt)")
    if e["_miss_list"]:
        print(f"           missed events: " +
              ", ".join(f"f{z}:{w} {o}->{n}" for z, w, o, n in e["_miss_list"][:8]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--update-baseline", action="store_true")
    ap.add_argument("--no-cache", action="store_true")
    args = ap.parse_args()

    clips = [c for c in R.discover() if G.has_gt(c.key)]
    if not clips:
        print("No recordings with ground truth found under recordings/. Nothing to do.")
        return 0

    print("=" * 80)
    print("PLATFORM REPORT — re-detection over all recordings (each clip = a respawn)")
    print(f"detector hash: {D.detector_hash()}")
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
    print("\n--- AGGREGATE (ALL recordings, corpus-as-respawns) ---")
    print(f"  missed_row_rate : {_rate(agg['missed_row_rate'])} "
          f"({agg['missed_row']}/{agg['present_cells']} present cells)")
    print(f"  misread_rate    : {_rate(agg['misread_rate'])} "
          f"({agg['misread']}/{agg['scored_value_cells']})")
    print(f"  event_miss_rate : {_rate(agg['event_miss_rate'])} "
          f"(misses={agg['misses']} of {agg['events']} events; "
          f"false_fires={agg['false_fires']} -> {agg['false_episodes']} felt phantoms)")
    if agg["calib_missing_rows"]:
        print(f"  WHOLE-ROW calib misses: {agg['calib_missing_rows']}")
    print(f"\n  (verified-only aggregate: missed_row={_rate(vagg['missed_row_rate'])}, "
          f"event_miss={_rate(vagg['event_miss_rate'])})")

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
        print("\n(no platform baseline yet — run with --update-baseline)")
        return 0

    print("\n--- vs BASELINE (verified, per track) ---")
    ok = True
    b = baseline["aggregate"]
    c = vagg
    for name in ("missed_row_rate", "misread_rate", "event_miss_rate", "false_episode_rate"):
        cur, base = c.get(name), b.get(name)
        if base is None and cur is None:
            print(f"  {name}: n/a"); continue
        if base is None:
            print(f"  {name}: {_rate(cur)} (no baseline)"); continue
        if cur is None:
            print(f"  {name}: n/a (was {_rate(base)})"); continue
        verdict = "OK" if cur <= base + 1e-9 else "REGRESSION"
        if cur > base + 1e-9:
            ok = False
        print(f"  {name}: {_rate(base)} -> {_rate(cur)}  {verdict}")

    print("\nRESULT:", "PASS" if ok else "FAIL (regression)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
