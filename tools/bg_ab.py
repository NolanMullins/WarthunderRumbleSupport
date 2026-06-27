"""
bg_ab.py — frozen-field A/B for detector hardening against cloud/snow background noise.

Unlike bg_noise_stress (which advanced one shared RNG across conditions, so per-condition
difficulty depended on order), this seeds a DETERMINISTIC field per condition, then scores the
SAME field twice: baseline (H.BAND_DEBLEND=False) vs candidate (True). Metrics are split into
FELT weapons (discrete AAM/RKT/BMB + counters FLR/CHFF -- HUD-only, no telemetry net) vs the GUN
(CNN -- covered by the weapon2 trigger at runtime, so its HUD misses/phantoms are not felt).

A candidate is acceptable iff: CLEAN does not regress on any felt metric, and the felt metrics
improve (fewer misreads / missed / phantoms) summed across the noisy conditions.

Run: python tools/bg_ab.py
"""
import os
import sys
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tests"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "src")))
from lib import recordings as R          # noqa: E402
from lib import groundtruth as G         # noqa: E402
from lib import detect as D              # noqa: E402
from lib import platform_metrics as P    # noqa: E402
import winwinghaptics.detection.hud_detect as H   # noqa: E402
import bg_noise_stress as B              # noqa: E402

FELT = {w for w, c in H.WEAPON_CLASS.items() if c != "rapid"}   # everything except the gun

# Fixed condition list, each with its own seed so the field is identical run-to-run and
# baseline-vs-candidate. (kind, mode, alpha, seed)
CONDS = []
_seed = 100
for _kind in ("cloud", "snow"):
    for _alpha in (0.55, 0.75):
        for _mode in ("static", "drifting"):
            CONDS.append((_kind, _mode, _alpha, _seed))
            _seed += 1


def field_grays(grays, kind, mode, alpha, seed):
    B.RNG = np.random.default_rng(seed)
    return B.composite(grays, kind, mode, alpha)


def reads_for(clip, cal, grays):
    """Read every frame once (gate-independent -- the noise gate is a TRACKER change)."""
    shift = cx = None
    reads = []
    for g in grays:
        rd, shift, cx = H.read_counts(g, cal, shift_hint=shift, return_shift=True,
                                      cx_hint=cx, return_cx=True)
        reads.append({wp: int(v[0]) for wp, v in rd.items()})
    return reads


def felt_read_stats(gt, reads):
    """Felt (non-gun) read-level tallies straight from reads vs GT (gate-independent)."""
    missed = misread = present = 0
    for i in range(len(reads)):
        if gt.is_excluded(i):
            continue
        for wp in gt.weapons:
            if wp not in FELT or not gt.is_present(wp, i):
                continue
            present += 1
            got = reads[i].get(wp)
            if got is None:
                missed += 1
                continue
            v = gt.value_at(wp, i)
            if v is not None and got != v:
                misread += 1
            elif v is None:
                br = gt.transition_bracket(wp, i)
                if br is not None and not (br[0] <= got <= br[1]):
                    misread += 1
    return missed, misread, present


def felt_events(clip, gt, cal, reads, dmin):
    """Felt phantom + missed-event counts under a given DISCRETE_MIN."""
    H.TemporalTracker.DISCRETE_MIN = dmin
    det = {"source": "pinned", "calib_rows": {k: int(v) for k, v in cal.rows.items()},
           "reads": reads, "confs": [{wp: 1.0 for wp in r} for r in reads]}
    ev = P.score_events(clip, gt, det)
    felt_ph = sum(1 for a, b, wp in ev.get("_false_ep_list", []) if wp in FELT)
    felt_mi = sum(1 for tup in ev.get("_miss_list", []) if tup[1] in FELT)
    return felt_ph, felt_mi


VARIANTS = [2, 3, 4]   # DISCRETE_MIN sweep; 2 = the old (pre-hardening) behaviour


def main():
    clip = next(c for c in R.discover() if B.CLIP in c.key)
    cal, _ = D._calibrate(clip)
    H._ensure_mats(cal)
    grays = clip.grays()
    gt = G.load(clip.key, len(grays))

    # read every condition once (gate-independent: DISCRETE_MIN is a TRACKER setting), and keep
    # the read streams + a felt read-level summary in memory.
    conds = [("clean", grays)] + [
        (f"{k} {m} a={a}", field_grays(grays, k, m, a, s)) for k, m, a, s in CONDS]
    reads_by = {tag: reads_for(clip, cal, gn) for tag, gn in conds}
    clean_missed, clean_misread, _ = felt_read_stats(gt, reads_by["clean"])
    noisy_missed = noisy_misread = 0
    for tag, _gn in conds:
        if tag == "clean":
            continue
        mi, mr, _ = felt_read_stats(gt, reads_by[tag])
        noisy_missed += mi
        noisy_misread += mr

    print("=" * 78)
    print(f"FELT (non-gun) phantom / evtmiss vs DISCRETE_MIN   clip={B.CLIP.split('/')[-1]}")
    print(f"read-level (DISCRETE_MIN-independent): clean missed/misread = "
          f"{clean_missed}/{clean_misread}; noisy = {noisy_missed}/{noisy_misread}")
    print("=" * 78)
    print(f"{'DISCRETE_MIN':<14}{'clean ph/miss':>16}{'noisy phantoms':>18}{'noisy evtmiss':>16}")
    print("-" * 78)
    for dmin in VARIANTS:
        clean_ph, clean_mi = felt_events(clip, gt, cal, reads_by["clean"], dmin)
        nph = nmi = 0
        for tag, _gn in conds:
            if tag == "clean":
                continue
            p, m = felt_events(clip, gt, cal, reads_by[tag], dmin)
            nph += p
            nmi += m
        mark = "  <- old behaviour" if dmin == 2 else ("  <- shipped" if dmin == 3 else "")
        print(f"{dmin:<14}{clean_ph:>8}/{clean_mi:<7}{nph:>18}{nmi:>16}{mark}")
    H.TemporalTracker.DISCRETE_MIN = 3


if __name__ == "__main__":
    main()
