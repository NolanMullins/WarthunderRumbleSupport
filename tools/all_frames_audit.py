"""
all_frames_audit.py — faithful Method-2 detector + tracker audit over EVERY frame.

If a recording has a sidecar calib.json (new recordings), the CURRENT detector is re-run
over every PNG with the EXACT live calibration -> a true detector A/B (fresh reads vs the
reads saved in telemetry). Recordings WITHOUT calib.json (older) are flagged NON-FAITHFUL:
their rebuilt calibration differs from live, so detector-drift numbers are unreliable (use
miss_audit.py, which audits the real saved live reads, for those).

For each recording it reports, over all frames:
  - detector drift:  fresh reads vs saved telemetry reads (same / diff / fresh_only / tel_only)
  - read stability:  per weapon, how often consecutive valid reads FLICKER (A->B->A) -- the
                     root cause of felt lag in busy moments
  - tracker events + oracle latency / miss per firing episode (fresh reads)
"""
import sys, glob, os, json, statistics as S
import numpy as np
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
import winwinghaptics.detection.hud_detect as H
import event_harness as eh

# Recordings live under <repo>/recordings/<clip>/<clip>/ (gitignored). Drop your own
# Record-30s captures there to run this audit.
REC_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "recordings")
RECS = [
    r"hud_rec_20260618_101336\hud_rec_20260618_101336",
    r"hud_rec_20260618_153642\hud_rec_20260618_153552",
    r"hud_rec_20260618_153642\hud_rec_20260618_153642",
    r"hud_rec_20260618_155235\hud_rec_20260618_155235",
]
ORACLE_R, ORACLE_MIN = 6, 5
BURST_GAP, LATE_FRAMES, MISS_FRAMES = 14, 6, 25


def load_header_and_tel(rec):
    hdr = None; tel = {}
    with open(os.path.join(rec, "telemetry.jsonl"), encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            j = json.loads(line)
            if j.get("type") == "header":
                hdr = j
            elif j.get("type") == "frame":
                d = {}
                for wp, r in j.get("reads", {}).items():
                    if r and r.get("val") is not None:
                        d[wp] = r["val"]
                tel[j["n"]] = d
    return hdr, tel


def load_calib(rec, hdr):
    cf = os.path.join(rec, "calib.json")
    if hdr and hdr.get("calib_file") and os.path.exists(cf):
        with open(cf, encoding="utf-8") as f:
            return H.Calib.from_dict(json.load(f)), "saved"
    fs = sorted(glob.glob(os.path.join(rec, "*.png")))
    grays = [eh.load_png_gray(p) for p in fs[:12]]
    return H.calibrate_from_grays(grays), "rebuilt"


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


def flicker_count(vals):
    seq = [v for v in vals if v is not None]
    f = 0
    for i in range(2, len(seq)):
        if seq[i] == seq[i - 2] and seq[i] != seq[i - 1]:
            f += 1
    return f, len(seq)


def audit(rec):
    name = os.path.basename(rec)
    hdr, tel = load_header_and_tel(rec)
    cal, source = load_calib(rec, hdr)
    if cal is None:
        print(f"\n===== {name}: CALIBRATION UNAVAILABLE — skipped =====")
        return None
    weapons = list(cal.rows.keys())
    fs = sorted(glob.glob(os.path.join(rec, "*.png")))
    grays = [eh.load_png_gray(p) for p in fs]
    n = len(grays)

    shift = cxh = None
    fresh = []
    for g in grays:
        reads, shift, cxh = H.read_counts(g, cal, shift_hint=shift, return_shift=True,
                                          cx_hint=cxh, return_cx=True)
        fresh.append({wp: int(v[0]) for wp, v in reads.items()})

    same = diff = fresh_only = tel_only = 0
    for i in range(n):
        fr = fresh[i]; tr = tel.get(i, {})
        for wp in weapons:
            a, b = fr.get(wp), tr.get(wp)
            if a is None and b is None:
                continue
            if a is not None and b is None:
                fresh_only += 1
            elif a is None and b is not None:
                tel_only += 1
            elif a == b:
                same += 1
            else:
                diff += 1

    classes = {w: H.WEAPON_CLASS.get(w, "discrete") for w in weapons}
    tk = H.TemporalTracker(classes=classes)
    events = {w: [] for w in weapons}
    for i in range(n):
        rd = {wp: (v, 0.9) for wp, v in fresh[i].items()}
        for wp, eff, kind, delta, old, new in tk.update(rd):
            events.setdefault(wp, []).append(i)

    tag = "FAITHFUL (saved calib)" if source == "saved" else "NON-FAITHFUL (rebuilt calib)"
    print(f"\n===== {name}  weapons={weapons}  frames={n}  [{tag}] =====")
    if source == "saved":
        agree = same / max(1, same + diff) * 100
        print(f"  detector A/B vs live telemetry: same={same} diff={diff} "
              f"fresh_only={fresh_only} tel_only={tel_only}  (agreement {agree:.1f}%)")
    print("  read flicker (A->B->A in valid reads):")
    for wp in weapons:
        f, tot = flicker_count([fresh[i].get(wp) for i in range(n)])
        if tot:
            print(f"    {wp:5s}: {f} flickers / {tot} valid reads ({f/tot*100:.1f}%)")

    tot = hit = late = miss = 0; lat = []
    for wp in weapons:
        st = settled_series([fresh[i].get(wp) for i in range(n)])
        eps = episodes(true_onsets(st)); ev = events.get(wp, [])
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
              f"max={max(lat)} (~{S.median(lat)*50:.0f}ms @20Hz)")
    print(f"  EPISODES total={tot} hit={hit} late={late} miss={miss}")
    return source, (tot, hit, late, miss, lat)


def main():
    base = REC_ROOT
    T = [0, 0, 0, 0]; allat = []; nf = 0
    for rec in RECS:
        full = os.path.join(base, rec)
        if not os.path.isdir(full):
            print(f"(skip missing {rec})"); continue
        r = audit(full)
        if r and r[0] == "saved":
            nf += 1
            t, h, l, m, la = r[1]
            T[0] += t; T[1] += h; T[2] += l; T[3] += m; allat += la
    print("\n================ AGGREGATE (faithful recordings only) ================")
    print(f"  faithful recordings: {nf}")
    if nf:
        print(f"  episodes total={T[0]} hit={T[1]} late={T[2]} miss={T[3]}")
        if allat:
            print(f"  latency median={S.median(allat):.1f} frames (~{S.median(allat)*50:.0f}ms) "
                  f"max={max(allat)}")
    else:
        print("  None yet — record a new clip with the updated app to capture calib.json,")
        print("  then re-run this for a true detector A/B.")


if __name__ == "__main__":
    main()
