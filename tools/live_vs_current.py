"""
live_vs_current.py — apples-to-apples TRACKER comparison.

Each recording's telemetry.jsonl stores, per frame:
  - reads       : what the detector read that frame (frozen at record time)
  - dispatched  : what actually FIRED live in-game (the build that recorded the clip)

This replays the CURRENT TemporalTracker over those SAME saved reads and diffs its fire
events against the live dispatched events. Because the reads are identical, any difference
is purely the TRACKER logic change between the recording build and now.

Reports per recording:
  - live fire count vs current fire count (per weapon)
  - REMOVED fires (live fired, current does NOT) -> i.e. false positives we now suppress
  - ADDED fires   (current fires, live did NOT)  -> new fires (faster onset, or new FP risk)
  - onset shift on shared firing episodes (current earlier/later than live)
"""
import os, json, statistics as S
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
import hud_detect as H

RECS = [
    r"hud_rec_20260618_101336\hud_rec_20260618_101336",
    r"hud_rec_20260618_153642\hud_rec_20260618_153552",
    r"hud_rec_20260618_153642\hud_rec_20260618_153642",
    r"hud_rec_20260618_155235\hud_rec_20260618_155235",
]
BURST_GAP = 14   # group fires <= this apart into one episode


def load(rec):
    hdr = None; frames = []
    with open(os.path.join(rec, "telemetry.jsonl"), encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            j = json.loads(line)
            if j.get("type") == "header":
                hdr = j
            elif j.get("type") == "frame":
                frames.append(j)
    return hdr, frames


def episodes(frames_list):
    """group a sorted list of frame indices into episodes (<=BURST_GAP apart)."""
    eps = []
    for f in sorted(frames_list):
        if eps and f - eps[-1][1] <= BURST_GAP:
            eps[-1][1] = f
        else:
            eps.append([f, f])
    return eps  # [start, end]


def match_episodes(live_eps, cur_eps):
    """pair episodes that overlap or are within BURST_GAP; return (pairs, live_only, cur_only)."""
    pairs = []; used_c = set()
    for li, (ls, le) in enumerate(live_eps):
        best = None
        for ci, (cs, ce) in enumerate(cur_eps):
            if ci in used_c:
                continue
            # overlap or near
            if cs <= le + BURST_GAP and ce >= ls - BURST_GAP:
                if best is None:
                    best = ci
        if best is not None:
            used_c.add(best); pairs.append((li, best))
    live_only = [li for li in range(len(live_eps)) if li not in {p[0] for p in pairs}]
    cur_only = [ci for ci in range(len(cur_eps)) if ci not in used_c]
    return pairs, live_only, cur_only


def audit(rec):
    name = os.path.basename(rec)
    hdr, frames = load(rec)
    weapons = hdr["weapons"]

    # live dispatched fires per weapon -> frame indices
    live = {w: [] for w in weapons}
    for r in frames:
        for d in (r.get("dispatched") or []):
            live.setdefault(d["weapon"], []).append(r["n"])

    # current tracker over saved reads
    classes = {w: H.WEAPON_CLASS.get(w, "discrete") for w in weapons}
    tk = H.TemporalTracker(classes=classes)
    cur = {w: [] for w in weapons}
    nmap = {r["n"]: i for i, r in enumerate(frames)}
    for r in frames:
        reads = {}
        for wp, rr in r.get("reads", {}).items():
            if rr and rr.get("val") is not None:
                reads[wp] = (rr["val"], rr.get("conf", 0.9))
        for wp, eff, kind, delta, old, new in tk.update(reads):
            cur.setdefault(wp, []).append(r["n"])

    print(f"\n===== {name}  weapons={weapons}  frames={len(frames)} =====")
    onset_shifts = []
    tot_removed = tot_added = 0
    for wp in weapons:
        lv = live.get(wp, []); cv = cur.get(wp, [])
        if not lv and not cv:
            continue
        le = episodes(lv); ce = episodes(cv)
        pairs, lonly, conly = match_episodes(le, ce)
        # onset shift on paired episodes
        shifts = []
        for li, ci in pairs:
            shifts.append(ce[ci][0] - le[li][0])  # +ve = current LATER, -ve = current EARLIER
            onset_shifts.append(ce[ci][0] - le[li][0])
        removed = [le[i] for i in lonly]   # live fired, current silent
        added = [ce[i] for i in conly]     # current fires, live silent
        tot_removed += len(removed); tot_added += len(added)
        msg = (f"  {wp:5s}: live_fires={len(lv)} cur_fires={len(cv)}  "
               f"episodes live={len(le)} cur={len(ce)} paired={len(pairs)}")
        if shifts:
            msg += f"  onset-delta med={S.median(shifts):+.0f}f"
        print(msg)
        if removed:
            print(f"         REMOVED (live fired, now silent): " +
                  ", ".join(f"f{a}-{b}" for a, b in removed))
        if added:
            print(f"         ADDED   (now fires, live silent):  " +
                  ", ".join(f"f{a}-{b}" for a, b in added))
    return onset_shifts, tot_removed, tot_added


def main():
    base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "recordings")
    allsh = []; R = A = 0
    for rec in RECS:
        full = os.path.join(base, rec)
        if not os.path.isdir(full):
            print(f"(skip {rec})"); continue
        sh, r, a = audit(full)
        allsh += sh; R += r; A += a
    print("\n================ AGGREGATE ================")
    print(f"  episodes REMOVED (live fired -> now suppressed): {R}")
    print(f"  episodes ADDED   (now fires -> live didn't):     {A}")
    if allsh:
        earlier = sum(1 for s in allsh if s < 0)
        print(f"  onset shift on paired episodes: median={S.median(allsh):+.1f}f "
              f"mean={S.mean(allsh):+.1f}f  ({earlier}/{len(allsh)} now EARLIER)")


if __name__ == "__main__":
    main()
