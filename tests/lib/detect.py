"""Re-detection layer: run the CURRENT detector over every PNG of a clip.

This is the foundation of the higher-level test platform. Unlike the frozen telemetry reads
(which were produced by whatever detector build recorded the clip, often obsolete), this
re-runs the CURRENT read_counts on the raw PNG frames -> it measures the detector as it is
NOW. That is what makes a true A/B possible and what surfaces missed-row errors.

Calibration source, in priority:
  1. clip's saved calib.json (faithful: exact live calibration) -- when present.
  2. else calibrate_from_grays() on the clip's own early frames (Windows OCR) -- representative
     but not identical to live; this is how we cover the older clips that predate calib.json.

Results are cached to tests/.cache keyed by (clip, detector-hash, calib-source). The detector
hash is a digest of src/hud_detect.py, so ANY change to the detector auto-invalidates the
cache -> reruns recompute, stale runs never silently pass.
"""
import os
import sys
import json
import glob
import hashlib

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.normpath(os.path.join(_HERE, "..", "..", "src")))
import winwinghaptics.detection.hud_detect as H  # noqa: E402

CACHE_DIR = os.path.normpath(os.path.join(_HERE, "..", ".cache"))
_DETECT_SRC = os.path.normpath(os.path.join(
    _HERE, "..", "..", "src", "winwinghaptics", "detection", "hud_detect.py"))


def detector_hash():
    with open(_DETECT_SRC, "rb") as f:
        return hashlib.sha1(f.read()).hexdigest()[:12]


def _cache_path(clip, source):
    safe = clip.key.replace("/", "__").replace("\\", "__")
    return os.path.join(CACHE_DIR, f"{safe}.{source}.{detector_hash()}.json")


_PIN_DIR = os.path.normpath(os.path.join(_HERE, "..", "pinned_calib"))


def _read_coverage(cal, grays):
    """GT-free calibration quality proxy: how many weapon-cells this calib reads over a spread
    of frames. A good calibration (correct geometry + complete row templates) reads more."""
    n = len(grays)
    sample = grays if n <= 30 else [grays[int(i * n / 30.0)] for i in range(30)]
    total = 0
    shift = cx = None
    for g in sample:
        rd, shift, cx = H.read_counts(
            g, cal, shift_hint=shift, return_shift=True, cx_hint=cx, return_cx=True)
        total += len(rd)
    return total


def _calibrate(clip):
    """Return (Calib, source_tag). Priority:
      1. clip's saved calib.json (faithful: the EXACT live calibration).
      2. a PINNED derived calibration committed under tests/pinned_calib/ (deterministic, so
         read_counts/tracker A/B is fair -- the geometry is frozen across detector versions).
      3. else compute the best OCR calibration (try several whole-clip samples, keep the one
         that learns the most rows then reads the most cells) and PIN it for next time.
    Pinning is what removes recalibration NOISE from the A/B signal on the older clips that
    predate calib.json."""
    if clip.has_calib:
        return H.Calib.from_dict(clip.load_calib()), "saved"

    safe = clip.key.replace("/", "__").replace("\\", "__")
    pin = os.path.join(_PIN_DIR, safe + ".json")
    if os.path.exists(pin):
        with open(pin, encoding="utf-8") as f:
            return H.Calib.from_dict(json.load(f)), "pinned"

    grays = clip.grays()
    n = len(grays)

    def spread(k):
        if n <= k:
            return grays
        step = n / float(k)
        return [grays[int(i * step)] for i in range(k)]

    best = None
    best_score = (-1, -1)
    for k in (16, 24, 32, 40):
        cal = H.calibrate_from_grays(spread(k))
        if cal is None:
            continue
        score = (len(cal.rows), _read_coverage(cal, grays))
        if score > best_score:
            best, best_score = cal, score

    if best is not None:
        os.makedirs(_PIN_DIR, exist_ok=True)
        with open(pin, "w", encoding="utf-8") as f:
            json.dump(best.to_dict(), f)
    return best, "pinned"


def redetect(clip, use_cache=True):
    """Run the current detector over every PNG of `clip`.

    Returns dict:
      {"source": "saved"|"ocr"|None,
       "calib_rows": {weapon: y} or None,     # which rows calibration actually learned
       "reads": [ {weapon: int_value}, ... ]  # per frame, only successfully-read cells
      }
    If calibration fails entirely, reads is [] and calib_rows is None.
    """
    # try cache first (cheap)
    if use_cache:
        for src in ("saved", "pinned", "ocr", "none"):
            cp = _cache_path(clip, src)
            if os.path.exists(cp):
                with open(cp, encoding="utf-8") as f:
                    return json.load(f)

    cal, source = _calibrate(clip)
    if cal is None:
        out = {"source": None, "calib_rows": None, "reads": []}
    else:
        grays = clip.grays()
        reads = []
        shift = cx = None
        stab = H.ReadStabilizer()
        for g in grays:
            rd, shift, cx = H.read_counts(
                g, cal, shift_hint=shift, return_shift=True, cx_hint=cx, return_cx=True)
            rd = stab.feed(rd)
            reads.append({wp: int(v[0]) for wp, v in rd.items()})
        out = {"source": source,
               "calib_rows": {k: int(v) for k, v in cal.rows.items()},
               "reads": reads}

    if use_cache:
        os.makedirs(CACHE_DIR, exist_ok=True)
        src = out["source"] or "none"
        with open(_cache_path(clip, src), "w", encoding="utf-8") as f:
            json.dump(out, f)
    return out


def clear_cache():
    for p in glob.glob(os.path.join(CACHE_DIR, "*.json")):
        os.remove(p)
