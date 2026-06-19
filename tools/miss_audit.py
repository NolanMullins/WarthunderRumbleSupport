"""
miss_audit.py — MISSED / LATE detection audit on the REAL live reads.

Faithful method: instead of re-detecting offline (which needs the live calibration
templates that recordings don't save), we run the offline ORACLE directly on the
reads the detector actually produced live (telemetry.jsonl 'reads'). The oracle can
look both BACKWARD and FORWARD over the whole recording, so it is a better 'truth'
than any online tracker. We then run the CURRENT TemporalTracker over those same real
reads and measure, per firing episode:
    latency = frames from the true (oracle) decrement to the tracker's first event
    MISS    = a true firing episode the tracker never fired on (within MISS_FRAMES)
    LATE    = first event > LATE_FRAMES after the true onset
"""
import os, json, statistics as S
import numpy as np
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
import hud_detect as H

RECS = [
    r"hud_rec_20260618_101336\hud_rec_20260618_101336",
    r"hud_rec_20260618_153642\hud_rec_20260618_153552",
    r"hud_rec_20260618_153642\hud_rec_20260618_153642",
    r"hud_rec_20260618_155235\hud_rec_20260618_155235",
]
ORACLE_R, ORACLE_MIN = 6, 5
BURST_GAP, LATE_FRAMES, MISS_FRAMES = 14, 6, 25


def load(rec):
    hdr, frames = None, []
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


def settled_series(vals):
    n = len(vals); out = [None] * n; carry = None
    for i in range(n):
        lo, hi = max(0, i - ORACLE_R), min(n, i + ORACLE_R + 1)
        win = [v for v in vals[lo:hi] if v is not None]
        lvl = None
        if win:
            u, c = np.unique(win, return_counts=True); k = int(c.argmax())
            if c[k] >= ORACLE_MIN and c[k] * 2 > len(win):
                lvl = int(u[k])
        if lvl is None:
            lvl = carry
        out[i] = lvl
        if lvl is not None:
            carry = lvl
    return out


def true_onsets(st):
    res = []; prev = None
    for i, lvl in enumerate(st):
        if lvl is None:
            continue
        if prev is not None and lvl < prev:
            res.append((i, prev, lvl))
        if prev is None or lvl != prev:
            prev = lvl
    return res


def episodes(ons):
    eps = []
    for fr, o, nw in ons:
        if eps and fr - eps[-1][2] <= BURST_GAP:
            eps[-1][1] = nw; eps[-1][2] = fr
        else:
            eps.append([fr, nw, fr])
    return eps


def audit(rec):
    hdr, frames = load(rec)
    weapons = hdr["weapons"]
    n = len(frames)
    # real live reads, indexed by position
    reads_by_frame = []
    for r in frames:
        d = {}
        for wp, rr in r.get("reads", {}).items():
            if rr and rr.get("val") is not None:
                d[wp] = rr["val"]
        reads_by_frame.append(d)

    classes = {w: H.WEAPON_CLASS.get(w, "discrete") for w in weapons}
    tr = H.TemporalTracker(classes=classes)
    events = {w: [] for w in weapons}
    for i, d in enumerate(reads_by_frame):
        rd = {wp: (v, 0.9) for wp, v in d.items()}
        for wp, eff, kind, delta, old, new in tr.update(rd):
            events.setdefault(wp, []).append(i)

    print(f"\n===== {os.path.basename(rec)}  weapons={weapons}  frames={n} =====")
    tot = hit = late = miss = 0; lat = []
    for wp in weapons:
        vals = [reads_by_frame[i].get(wp) for i in range(n)]
        st = settled_series(vals)
        eps = episodes(true_onsets(st))
        ev = events.get(wp, [])
        if not eps:
            continue
        rows = []
        for start, endval, end in eps:
            cand = [e for e in ev if start - 2 <= e <= end + MISS_FRAMES]
            tot += 1
            if not cand:
                miss += 1; rows.append(f"f{start}(MISS)")
            else:
                L = cand[0] - start; lat.append(L)
                if L > LATE_FRAMES:
                    late += 1; rows.append(f"f{start}(LATE+{L})")
                else:
                    hit += 1; rows.append(f"f{start}(+{L})")
        print(f"  {wp:5s}: {len(eps)} episodes -> " + ", ".join(rows))
    if lat:
        print(f"  LATENCY frames median={S.median(lat):.1f} mean={S.mean(lat):.1f} "
              f"max={max(lat)} (~{S.median(lat)*50:.0f}ms median @20Hz)")
    print(f"  EPISODES total={tot} hit={hit} late={late} miss={miss}")
    return tot, hit, late, miss, lat


def main():
    base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "recordings"); T = [0, 0, 0, 0]; allat = []
    for rec in RECS:
        full = os.path.join(base, rec)
        if os.path.isdir(full):
            t, h, l, m, la = audit(full)
            T[0] += t; T[1] += h; T[2] += l; T[3] += m; allat += la
    print("\n================ AGGREGATE (real live reads) ================")
    print(f"  episodes total={T[0]} hit={T[1]} late={T[2]} miss={T[3]}")
    if allat:
        print(f"  latency median={S.median(allat):.1f} frames (~{S.median(allat)*50:.0f}ms) "
              f"mean={S.mean(allat):.1f} max={max(allat)}")


if __name__ == "__main__":
    main()
