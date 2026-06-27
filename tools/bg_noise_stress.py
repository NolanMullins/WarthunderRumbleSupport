"""
bg_noise_stress.py — reproduce the cloud/snow failure on EXISTING clean clips.

Hypothesis: detection is fine on our (mostly static, mild) recorded backgrounds, but a CHANGING
bright background (flying over cloud / snow) breaks it. We test this by compositing procedural
bright texture behind the HUD of a clean clip and scoring against that clip's ground truth.

The composite is a SCREEN blend (out = 1-(1-bg)(1-noise)), which lifts the darker pixels --
the glyph's near-black DARK TRIM and the background -- toward white while leaving the brightest
glyph cores ~unchanged. That is exactly how bright haze/cloud washes over the HUD, and it
directly attacks text_feature's "bright stroke must have a dark trim nearby" gate.

Three conditions, swept over intensity:
  clean     : the original frames (sanity baseline)
  static    : the SAME bright texture every frame (correlated but stationary)
  drifting  : the texture SCROLLS over time (non-stationary -- the theorized worst case, since
              the temporal median can't average out correlated, moving corruption)

Scored with the real platform metrics (missed_row / misread / event_miss) so the numbers are
directly comparable to the gate. Run: python tools/bg_noise_stress.py
"""
import os
import sys
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tests"))
from lib import recordings as R          # noqa: E402
from lib import groundtruth as G         # noqa: E402
from lib import detect as D              # noqa: E402
from lib import platform_metrics as P    # noqa: E402
sys.path.insert(0, os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "src")))
import winwinghaptics.detection.hud_detect as H   # noqa: E402

CLIP = "153642/hud_rec_20260618_153642"   # cleanest verified clip (misread 0.34%)
RNG = np.random.default_rng(7)


def fractal_noise(h, w, octaves=5, persistence=0.55):
    """Cloud-like value noise in [0,1]: sum of upsampled random grids at doubling frequencies."""
    out = np.zeros((h, w), np.float32)
    amp = 1.0
    total = 0.0
    for o in range(octaves):
        gh = max(2, (h // (2 ** (octaves - o))))
        gw = max(2, (w // (2 ** (octaves - o))))
        grid = RNG.random((gh, gw)).astype(np.float32)
        yi = np.linspace(0, gh - 1, h).astype(np.intp)
        xi = np.linspace(0, gw - 1, w).astype(np.intp)
        out += amp * grid[yi][:, xi]
        total += amp
        amp *= persistence
    out /= total
    out = (out - out.min()) / (out.max() - out.min() + 1e-6)
    return out


def screen_blend(g, noise01, alpha):
    """Screen-blend a [0,1] noise field onto an 8-bit-ish gray frame at strength alpha."""
    bg = g / 255.0
    nz = noise01 * alpha
    out = 1.0 - (1.0 - bg) * (1.0 - nz)
    return np.clip(out * 255.0, 0, 255).astype(np.float32)


def make_field(h, w, kind):
    if kind == "snow":              # high-frequency sparkle
        base = fractal_noise(h, w, octaves=6, persistence=0.7)
        return np.clip(base ** 1.5 * 1.3, 0, 1)
    return fractal_noise(h, w, octaves=5, persistence=0.55)   # cloud: soft blobs


def composite(grays, kind, mode, alpha):
    """Return new grays with bright texture screen-blended behind the HUD."""
    h, w = grays[0].shape
    field = make_field(h + 64, w + 64, kind)        # oversize so drift can scroll into view
    out = []
    for i, g in enumerate(grays):
        if mode == "static":
            sub = field[:h, :w]
        else:                                        # drifting: scroll a few px/frame
            dy = (i * 2) % 64
            dx = (i * 3) % 64
            sub = field[dy:dy + h, dx:dx + w]
        out.append(screen_blend(g, sub, alpha))
    return out


def bg_difficulty(grays, calib):
    """Mean texture energy in the count band (proxy for 'how noisy is the background')."""
    energies = []
    y0 = max(0, min(calib.rows.values()) - 20)
    y1 = min(grays[0].shape[0], max(calib.rows.values()) + 20)
    x0 = max(0, calib.count_x - 10)
    x1 = min(grays[0].shape[1], calib.count_x + int(calib.pitch * 6))
    for g in grays:
        band = g[y0:y1, x0:x1]
        gx = np.abs(np.diff(band, axis=1)).mean()
        energies.append(gx)
    return float(np.mean(energies))


def score(clip, gt, cal, grays):
    shift = cx = None
    reads = []
    confs = []
    for g in grays:
        rd, shift, cx = H.read_counts(g, cal, shift_hint=shift, return_shift=True,
                                      cx_hint=cx, return_cx=True)
        reads.append({wp: int(v[0]) for wp, v in rd.items()})
        confs.append({wp: float(v[1]) for wp, v in rd.items()})
    det = {"source": "pinned", "calib_rows": {k: int(v) for k, v in cal.rows.items()},
           "reads": reads, "confs": confs}
    rv = P.score_rows_values(clip, gt, det)
    ev = P.score_events(clip, gt, det)
    return (rv["missed_row_rate"] * 100, rv["misread_rate"] * 100,
            (ev["event_miss_rate"] or 0) * 100, ev.get("false_episodes", 0))


def main():
    clip = next(c for c in R.discover() if CLIP in c.key)
    cal, _ = D._calibrate(clip)
    H._ensure_mats(cal)
    grays = clip.grays()
    gt = G.load(clip.key, len(grays))

    print("=" * 78)
    print(f"BACKGROUND-NOISE STRESS  clip={clip.key.split('/')[-1]}  ({len(grays)} frames)")
    print("=" * 78)
    base = score(clip, gt, cal, grays)
    print(f"{'condition':<28}{'missed%':>9}{'misread%':>10}{'evt_miss%':>11}"
          f"{'phantoms':>10}{'bg_diff':>9}")
    print("-" * 78)
    print(f"{'clean (baseline)':<28}{base[0]:>9.2f}{base[1]:>10.2f}{base[2]:>11.2f}"
          f"{base[3]:>10}{bg_difficulty(grays, cal):>9.1f}")
    for kind in ("cloud", "snow"):
        for alpha in (0.35, 0.55, 0.75):
            for mode in ("static", "drifting"):
                gn = composite(grays, kind, mode, alpha)
                s = score(clip, gt, cal, gn)
                tag = f"{kind} {mode} a={alpha}"
                print(f"{tag:<28}{s[0]:>9.2f}{s[1]:>10.2f}{s[2]:>11.2f}"
                      f"{s[3]:>10}{bg_difficulty(gn, cal):>9.1f}")
        print("-" * 78)


if __name__ == "__main__":
    main()
