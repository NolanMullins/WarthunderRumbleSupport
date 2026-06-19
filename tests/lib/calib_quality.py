"""Calibration-quality track.

Measures the REAL runtime auto-calibration -- exactly what the app does on (re)calibrate:
capture a window of consecutive frames, then calibrate_from_grays(window). NO best-of-N, NO
spreading, NO pinning. This exposes calibration failures honestly (whole rows never learned,
outright failures, geometry instability) so the core calibration system can be driven down.

For each clip (= a respawn) we simulate "the user/app calibrated at moment X" at several
evenly-spaced windows across the clip, and score each window against ground-truth row
presence:

  expected(window) = weapons GT-present during that window's frames (what SHOULD be learned)
  learned(window)  = weapons calibrate_from_grays actually returned rows for
  metrics per window: ok (calib succeeded), learned/expected, count_x, pitch

  per-clip summary:
    mean_rows_frac  : avg over windows of (learned-present / expected)   [higher better]
    worst_rows_frac : min over windows                                    [robustness]
    fail_rate       : fraction of windows where calibration outright failed
    always_missed   : weapons present in >=1 window but learned in NONE  [systematic blind
                      spots -- the BMB-row problem]
    count_x_spread  : max-min count_x across successful windows           [geometry stability]

Results cached under tests/.cache_calib, keyed by detector hash (src/hud_detect.py), so any
change to calibration auto-invalidates.
"""
import os
import sys
import json
import hashlib

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.normpath(os.path.join(_HERE, "..", "..", "src")))
import winwinghaptics.detection.hud_detect as H  # noqa: E402

CACHE_DIR = os.path.normpath(os.path.join(_HERE, "..", ".cache_calib"))
_DETECT_SRC = os.path.normpath(os.path.join(
    _HERE, "..", "..", "src", "winwinghaptics", "detection", "hud_detect.py"))

WIN = 24          # frames per calibration window (matches HudDetector.calibrate n_frames)
N_WINDOWS = 6     # evenly-spaced calibration moments per clip


def _hash():
    with open(_DETECT_SRC, "rb") as f:
        return hashlib.sha1(f.read()).hexdigest()[:12]


def _cache_path(clip):
    safe = clip.key.replace("/", "__").replace("\\", "__")
    return os.path.join(CACHE_DIR, f"{safe}.{_hash()}.json")


def _window_starts(n, win, k):
    if n <= win:
        return [0]
    last = n - win
    if k == 1:
        return [0]
    return [int(round(i * last / (k - 1))) for i in range(k)]


def score_calibration(clip, gt, use_cache=True):
    if use_cache and os.path.exists(_cache_path(clip)):
        with open(_cache_path(clip), encoding="utf-8") as f:
            return json.load(f)

    grays = clip.grays()
    n = len(grays)
    starts = _window_starts(n, WIN, N_WINDOWS)

    windows = []
    learned_union_present = set()      # weapons learned in >=1 window (while present)
    present_union = set()              # weapons present in >=1 window
    for s in starts:
        # skip a calibration window that overlaps excluded (polluted) frames -- the capture is
        # corrupt there (an overlay covered the HUD), so it would unfairly look like a calib
        # failure of the detector rather than of the test data.
        if any(gt.is_excluded(i) for i in range(s, min(n, s + WIN))):
            continue
        win_grays = grays[s:s + WIN]
        # expected = weapons present anywhere in this window's frame span (per GT)
        expected = set()
        for wp in gt.weapons:
            if any(gt.is_present(wp, i) for i in range(s, min(n, s + WIN))):
                expected.add(wp)
        present_union |= expected

        cal = H.calibrate_from_grays(win_grays)
        ok = cal is not None and bool(cal.rows)
        learned = set(cal.rows) if ok else set()
        learned_present = learned & expected
        learned_union_present |= learned_present

        windows.append({
            "start": s,
            "ok": ok,
            "expected": sorted(expected),
            "learned": sorted(learned),
            "learned_present": sorted(learned_present),
            "rows_frac": (len(learned_present) / len(expected)) if expected else None,
            "count_x": (int(cal.count_x) if ok else None),
            "pitch": (round(float(cal.pitch), 2) if ok else None),
        })

    ok_windows = [w for w in windows if w["ok"]]
    fracs = [w["rows_frac"] for w in windows if w["rows_frac"] is not None]
    cxs = [w["count_x"] for w in ok_windows if w["count_x"] is not None]
    always_missed = sorted(present_union - learned_union_present)

    out = {
        "n_frames": n,
        "n_windows": len(windows),
        "windows": windows,
        "mean_rows_frac": (sum(fracs) / len(fracs)) if fracs else None,
        "worst_rows_frac": (min(fracs) if fracs else None),
        "fail_rate": (1 - len(ok_windows) / len(windows)) if windows else None,
        "always_missed": always_missed,
        "count_x_spread": ((max(cxs) - min(cxs)) if len(cxs) >= 2 else 0),
        "present_union": sorted(present_union),
    }

    if use_cache:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(_cache_path(clip), "w", encoding="utf-8") as f:
            json.dump(out, f)
    return out


def clear_cache():
    import glob
    for p in glob.glob(os.path.join(CACHE_DIR, "*.json")):
        os.remove(p)
