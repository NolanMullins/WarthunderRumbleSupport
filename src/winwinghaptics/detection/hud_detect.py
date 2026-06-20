"""
hud_detect.py — robust, fast HUD weapon-counter detector (overhaul v4).

Design, derived from inspecting real War Thunder HUD captures (clear sky, blue sky,
bright cloud, terrain):

  * The HUD font is MONOSPACE and the count column is LEFT-ALIGNED at a fixed x.
    -> read digits by SEGMENTING the clean text feature into glyph boxes and classifying
       each (digit vs suffix). We stop at the first non-digit / gap, which naturally drops
       suffixes like "(L)", "/1", "(F)", "[23]", ":43".
  * Weapon labels can carry suffixes ("AAM ACQ", "AAM PWR", "RKT CCIP", "BMB CCIP") and a
    left-gutter selector marker (">", "-").
    -> anchor each row by template-matching ONLY the weapon TOKEN (AAM/RKT/...). The token
       is extracted as the LEADING contiguous letter group in the label column and matched
       TRANSLATION-INVARIANTLY (tight ink bbox -> fixed grid -> zero-mean unit-norm NCC),
       so a few px of horizontal jitter no longer destroys the match.
  * HUD reflow (a WEP/MACH warning row appearing, loadout changes) is a GLOBAL vertical
    translation of the whole weapon block -> one global shift places every row; each row is
    then fine-refined and verified by its own label match.
  * Bright cloud washes out the bright glyph fill; the killer is cloud TEXTURE noise, not
    weak text. The glyph always has a near-black dark trim around a bright core.
    -> robust text feature = bright-core gated by nearby dark-trim presence. Cloud texture
       rarely has a bright pixel AND a near-black pixel within a few px, so it is strongly
       suppressed while glyphs survive.  (This feature is excellent; do not change it.)

Runtime: feature + coarse/fine label search + segmented read ~ a few ms/frame, numpy-only.
Calibration (one-time) uses Windows OCR to harvest the user's own monospace digit/label-token
templates and the column geometry (see hud_eval.py).
"""
import numpy as np
import os as _os


# ----------------------------- learned digit classifier -----------------------------
# A tiny MLP (600->64->10) trained OFFLINE (tools/train_digit_mlp.py) on human-verified
# digit crops + augmented calibration templates, deployed here as a PURE-NUMPY forward pass
# so the shipped app keeps numpy as its only runtime dependency. It replaces single-exemplar
# NCC for digit IDENTITY: NCC ties on blur (digit '6' read correctly only 35% of the time,
# usually as 0/8); this classifier learns the real per-digit distribution (6 -> ~98%). The
# input is the SAME zero-mean unit-norm gw*gh patch NCC consumes, so it is a drop-in.
class DigitModel:
    def __init__(self, W1, b1, W2, b2, classes, gw, gh):
        self.W1 = W1; self.b1 = b1; self.W2 = W2; self.b2 = b2
        self.classes = classes; self.gw = int(gw); self.gh = int(gh)

    @classmethod
    def load(cls, path):
        try:
            d = np.load(path)
            return cls(d["W1"], d["b1"], d["W2"], d["b2"], d["classes"],
                       int(d["gw"]), int(d["gh"]))
        except Exception:
            return None

    def predict(self, patch):
        """patch: zero-mean unit-norm (gh,gw) float array (a _box_patch output).
        Returns (char, prob_top, prob_margin) or (None,-1,-1) on shape mismatch."""
        v = patch.ravel()
        if v.shape[0] != self.W1.shape[0]:
            return None, -1.0, -1.0
        z1 = v @ self.W1 + self.b1
        np.maximum(z1, 0, out=z1)                   # relu, in place
        z2 = z1 @ self.W2 + self.b2
        z2 -= z2.max()
        e = np.exp(z2); p = e / e.sum()
        i = int(p.argmax())
        top = float(p[i])
        p[i] = -1.0
        second = float(p.max())
        return str(int(self.classes[i])), top, top - second


_MODEL_PATH = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "digit_model.npz")
_DIGIT_MODEL = None
_DIGIT_MODEL_TRIED = False
# Ambiguity gate for the learned classifier: reject a digit whose top-vs-second softmax
# probability gap is below this (the value is only right if every digit is -- a near-tie
# means we don't actually know it, so we no-read rather than emit a coin-flip).
MODEL_MARGIN_FLOOR = 0.20


def _digit_model():
    """Lazy-load the shipped digit classifier once (None if absent -> NCC fallback)."""
    global _DIGIT_MODEL, _DIGIT_MODEL_TRIED
    if not _DIGIT_MODEL_TRIED:
        _DIGIT_MODEL_TRIED = True
        if _os.path.exists(_MODEL_PATH):
            _DIGIT_MODEL = DigitModel.load(_MODEL_PATH)
    return _DIGIT_MODEL


# ----------------------------- text features -----------------------------
def _minpool(g, r):
    out = g.copy()
    for s in range(1, r + 1):
        out = np.minimum(out, np.roll(g, s, 1)); out = np.minimum(out, np.roll(g, -s, 1))
    g2 = out.copy()
    for s in range(1, r + 1):
        out = np.minimum(out, np.roll(g2, s, 0)); out = np.minimum(out, np.roll(g2, -s, 0))
    return out


def _maxpool(g, r):
    out = g.copy()
    for s in range(1, r + 1):
        out = np.maximum(out, np.roll(g, s, 1)); out = np.maximum(out, np.roll(g, -s, 1))
    g2 = out.copy()
    for s in range(1, r + 1):
        out = np.maximum(out, np.roll(g2, s, 0)); out = np.maximum(out, np.roll(g2, -s, 0))
    return out


def text_feature(g, mode="gated", gain=3.0):
    """Background-invariant 'textness' map (higher = more glyph-like).

    modes:
      'fill'  : clip((g - localmin)*gain)            -- old approach (bright stroke)
      'trim'  : clip((localmax - g)*gain)            -- the dark outline only
      'gated' : bright stroke AND a dark trim nearby -- cloud-robust (default)
    """
    if mode == "fill":
        return np.clip((g - _minpool(g, 2)) * gain, 0, 255)
    if mode == "trim":
        return np.clip((_maxpool(g, 2) - g) * gain, 0, 255)
    # gated (default)
    bright = np.clip(g - _minpool(g, 2), 0, 255)
    trim = np.clip(_maxpool(g, 2) - g, 0, 255)
    trim_dil = _maxpool(trim, 3)           # let the dark-trim presence reach the stroke
    gated = np.minimum(bright, trim_dil)   # stroke that is flanked by a dark trim
    return np.clip(gated * gain, 0, 255)


# ----------------------------- patch / NCC -----------------------------
_RESIZE_IDX = {}


def _resize(sub, gw, gh):
    h, w = sub.shape
    key = (h, gh)
    yi = _RESIZE_IDX.get(key)
    if yi is None:
        yi = np.linspace(0, h - 1, gh).astype(np.intp); _RESIZE_IDX[key] = yi
    key2 = (w, gw)
    xi = _RESIZE_IDX.get(key2)
    if xi is None:
        xi = np.linspace(0, w - 1, gw).astype(np.intp); _RESIZE_IDX[key2] = xi
    return sub[yi][:, xi]


def _norm(patch):
    p = patch.astype(np.float32)
    p -= p.mean()
    n = np.linalg.norm(p)
    if n < 1e-3:
        return None
    return p / n


def _crop_norm(tn, x0, x1, y0, y1, gw, gh, trim_rows=True, trim_cols=False, ink=25,
               floor=0.0):
    sub = tn[max(0, y0):y1, max(0, x0):x1]
    if sub.shape[0] < 2 or sub.shape[1] < 2:
        return None
    # Trim to the glyph STROKE CORE using a threshold ABOVE the cloud-residue band (ink):
    # the vertical/horizontal framing then ignores cloud rows/cols, so the same glyph is
    # framed identically whether the surrounding band is clean or cloudy, tall or short --
    # which is what keeps harvest/read NCC high.
    if trim_rows:
        rows = np.where(sub.max(axis=1) > ink)[0]
        if len(rows) >= 2:
            sub = sub[rows[0]:rows[-1] + 1]
    if trim_cols:
        cols = np.where(sub.max(axis=0) > ink)[0]
        if len(cols) >= 2:
            sub = sub[:, cols[0]:cols[-1] + 1]
    if sub.shape[0] < 2 or sub.shape[1] < 2:
        return None
    res = _resize(sub, gw, gh).astype(np.float32)
    if floor > 0:
        # suppress mid-level cloud residue (gated glyph cores >> texture noise) so the
        # NCC sees a clean glyph shape even over bright cloud.
        res = np.clip(res - floor, 0, None)
    return _norm(res)


def _ensure_mats(calib):
    """Build (and cache on the calib) raveled template matrices for vectorised NCC:
    one (n, gh*gw) matrix per digit / per weapon-label / for the suffix bank. Matching is
    then a single matrix-vector product per class instead of a Python loop over templates."""
    if getattr(calib, "_mats_ready", False):
        return
    def stack(tmpls):
        if not tmpls:
            return None
        return np.stack([t.ravel() for t in tmpls]).astype(np.float32)
    calib._digit_mats = {k: stack(v) for k, v in calib.digits.items()}
    calib._label_mats = {k: stack(v) for k, v in calib.labels.items()}
    calib._nd_mat = stack(calib.nondigit)
    calib._mats_ready = True


def _mat_best(patch, mat):
    if mat is None or patch is None:
        return -1.0
    return float(mat.dot(patch.ravel()).max())


# ----------------------------- segmentation -----------------------------
def _seg_boxes(tn_row, thr=None, min_w=2):
    """Column-segment a clean textness row band into glyph boxes (x0,x1).

    Uses a HIGH adaptive threshold (relative to the band's own peak) so adjacent
    monospace digits -- whose anti-aliased edges bridge at low thresholds -- split
    cleanly at the gaps between their stroke cores. Vectorised run detection."""
    if tn_row.size == 0:
        return []
    if thr is None:
        peak = float(tn_row.max())
        thr = max(70.0, 0.55 * peak)
    colink = (tn_row > thr).any(axis=0)
    if not colink.any():
        return []
    d = np.diff(colink.view(np.int8))
    starts = list(np.where(d == 1)[0] + 1)
    ends = list(np.where(d == -1)[0] + 1)
    if colink[0]:
        starts.insert(0, 0)
    if colink[-1]:
        ends.append(colink.shape[0])
    return [(s, e) for s, e in zip(starts, ends) if e - s >= min_w]


def _leading_group(boxes, max_gap):
    """Merge the leading run of boxes whose inter-box gap <= max_gap (one token)."""
    if not boxes:
        return None
    gx0, gx1 = boxes[0]
    for (a, b) in boxes[1:]:
        if a - gx1 <= max_gap:
            gx1 = b
        else:
            break
    return gx0, gx1


# ----------------------------- calibration model -----------------------------
class Calib:
    def __init__(self):
        self.mode = "gated"
        self.gain = 3.0
        self.gw = 20          # digit grid w/h for matching
        self.gh = 30
        self.lgw = 36         # label-token grid
        self.lgh = 18
        self.pitch = 13.7     # monospace digit pitch (px)
        self.digit_w = 11     # nominal glyph width
        self.count_x = 264    # left edge of count column
        self.label_x0 = 56    # left edge of label column (labels are left-aligned)
        self.label_w = 60     # label match window width (covers the widest token, e.g. CHFF)
        self.rows = {}        # weapon -> calibrated y-center (dominant alignment)
        self.row_h = 14       # half-height of a text row
        self.line_pitch = 30  # vertical spacing between weapon rows
        self.match_floor = 90.0  # noise floor subtracted before NCC (cloud suppression)
        self.trim_ink = 60.0     # ink threshold for the glyph bounding box (above the cloud
                                 # residue band -> vertical framing ignores cloud rows)
        self.digits = {}      # char -> list[normalized patch]
        self.labels = {}      # weapon -> list[normalized token patch]
        self.nondigit = []    # list[normalized patch] of suffix glyphs ( ( ) L F R / [ ] : )
        self.valid = False

    def to_dict(self):
        # templates are normalized patches (range ~+-0.1); 5 decimals is bit-exact for the
        # NCC match (verified 0 read mismatches) while ~halving the calib.json size.
        def rp(p):
            return [[round(float(x), 5) for x in row] for row in p]
        return {
            "mode": self.mode, "gain": self.gain, "gw": self.gw, "gh": self.gh,
            "lgw": self.lgw, "lgh": self.lgh, "pitch": self.pitch, "digit_w": self.digit_w,
            "count_x": self.count_x, "label_x0": self.label_x0, "label_w": self.label_w,
            "row_h": self.row_h, "line_pitch": self.line_pitch, "rows": self.rows,
            "match_floor": self.match_floor, "trim_ink": self.trim_ink,
            "digits": {k: [rp(p) for p in v] for k, v in self.digits.items()},
            "labels": {k: [rp(p) for p in v] for k, v in self.labels.items()},
            "nondigit": [rp(p) for p in self.nondigit],
        }

    @classmethod
    def from_dict(cls, d):
        c = cls()
        for k in ("mode", "gain", "gw", "gh", "lgw", "lgh", "pitch", "digit_w",
                  "count_x", "label_x0", "label_w", "row_h", "line_pitch", "match_floor",
                  "trim_ink"):
            if k in d:
                setattr(c, k, d[k])
        c.rows = {k: int(v) for k, v in d.get("rows", {}).items()}
        c.digits = {k: [np.array(p, np.float32) for p in v]
                    for k, v in d.get("digits", {}).items()}
        c.labels = {k: [np.array(p, np.float32) for p in v]
                    for k, v in d.get("labels", {}).items()}
        c.nondigit = [np.array(p, np.float32) for p in d.get("nondigit", [])]
        c.valid = bool(c.digits and c.labels and c.rows)
        return c


# ----------------------------- label token -----------------------------
def _token_cols(tn, yc, calib):
    """Column extent (gx0, gx1) of the leading label token at row yc (segmentation +
    leading-group merge). Shared by all small vertical offsets so it is computed once."""
    x0 = calib.label_x0 - 3
    x1 = calib.label_x0 + calib.label_w
    band = tn[max(0, yc - calib.row_h):yc + calib.row_h, max(0, x0):x1]
    if band.shape[0] < 6 or band.shape[1] < 4:
        return None
    boxes = _seg_boxes(band, min_w=2)
    grp = _leading_group(boxes, max_gap=int(0.7 * calib.pitch))
    if grp is None or grp[1] - grp[0] < 4:
        return None
    bx0 = max(0, x0)
    return bx0 + grp[0], bx0 + grp[1]


def _token_patch_cols(tn, yc, gx0, gx1, calib):
    """Normalised token patch using a KNOWN column extent (no re-segmentation)."""
    band = tn[max(0, yc - calib.row_h):yc + calib.row_h, gx0:gx1]
    if band.shape[0] < 6 or band.shape[1] < 4:
        return None
    return _crop_norm(band, 0, band.shape[1], 0, band.shape[0], calib.lgw, calib.lgh,
                      trim_rows=True, trim_cols=True, ink=calib.trim_ink,
                      floor=calib.match_floor)


def label_token_patch(tn, yc, calib):
    """Tight, translation-invariant crop of the LEADING label token at row yc.

    Segments the label column band into letter boxes, merges the leading contiguous
    group (the weapon token, stopping before the big space to a suffix like CCIP/ACQ),
    and returns its normalized fixed-grid patch. The left-gutter selector marker
    (">", "-") lives further left than label_x0 and is outside this window."""
    cols = _token_cols(tn, yc, calib)
    if cols is None:
        return None
    return _token_patch_cols(tn, yc, cols[0], cols[1], calib)


def _label_score_at(tn, wp, yc, calib, dys=(-1, 0, 1)):
    mat = calib._label_mats.get(wp)
    if mat is None:
        return -1.0
    if len(dys) <= 1:
        p = label_token_patch(tn, yc + (dys[0] if dys else 0), calib)
        return _mat_best(p, mat) if p is not None else -1.0
    # multi-offset: re-segment the token columns at a few anchors (cloud can shift where the
    # token splits), then reuse the nearest anchor's columns for each offset -- captures the
    # true token framing without re-segmenting at every single offset. Small offset ranges
    # need only one segmentation.
    lo, hi = min(dys), max(dys)
    anchors = sorted({lo, 0, hi}) if hi - lo > 4 else [0]
    acols = []
    for a in anchors:
        c = _token_cols(tn, yc + a, calib)
        if c is not None:
            acols.append((a, c))
    if not acols:
        return -1.0
    best = -1.0
    for dy in dys:
        a, cols = min(acols, key=lambda ac: abs(ac[0] - dy))
        p = _token_patch_cols(tn, yc + dy, cols[0], cols[1], calib)
        if p is None:
            continue
        s = _mat_best(p, mat)
        if s > best:
            best = s
    return best


# ----------------------------- block alignment -----------------------------
def _block_score(tn, calib, shift, Hh):
    total = 0.0; n = 0
    for wp, y0 in calib.rows.items():
        yc = y0 + shift
        if yc < calib.row_h or yc > Hh - calib.row_h:
            continue
        s = _label_score_at(tn, wp, yc, calib, dys=(0,))
        if s > 0.32:
            total += s; n += 1
    return total, n


def _estimate_shift(tn, calib, coarse=36):
    """Coarse global vertical-block alignment by total label agreement.

    Only needs to land within the per-weapon count-band search window (the per-row anchor
    then refines exactly), so a sparse step is fine. A cheap 1D label-ink profile rejects
    shifts that put the weapon block over empty sky, so the expensive per-weapon token NCC
    only runs on the ink-rich candidate shifts. The shift maximising TOTAL token match wins
    (a couple of low-confidence wrong matches can't outvote the true alignment)."""
    Hh = tn.shape[0]
    rows = np.fromiter(calib.rows.values(), dtype=np.int64)
    if rows.size == 0:
        return 0, -1.0
    x0 = max(0, calib.label_x0 - 3); x1 = min(tn.shape[1], calib.label_x0 + calib.label_w)
    strip = tn[:, x0:x1]
    thr = max(60.0, 0.45 * float(strip.max()))
    prof = (strip > thr).sum(axis=1).astype(np.float32)
    shifts = [s for s in range(-coarse, coarse + 1, 5)
              if calib.row_h <= rows.min() + s and rows.max() + s < Hh - calib.row_h]
    if not shifts:
        return 0, -1.0
    pmax = max(float(prof[rows + s].sum()) for s in shifts)
    cut = 0.55 * pmax
    best_shift, best_total = shifts[0], -1.0
    for s in shifts:
        if float(prof[rows + s].sum()) < cut:    # block over empty sky -> skip cheaply
            continue
        total, n = _block_score(tn, calib, s, Hh)
        if total > best_total:
            best_total, best_shift = total, s
    return best_shift, best_total


def _shift_score(tn, calib, shift, Hh):
    """Total label agreement at a specific shift (for temporal-lock comparison)."""
    if not (calib.row_h <= min(calib.rows.values()) + shift and
            max(calib.rows.values()) + shift < Hh - calib.row_h):
        return -1.0
    total, n = _block_score(tn, calib, shift, Hh)
    return total


def _count_bands(tn, yc0, calib, win=11, cx=None):
    """Return candidate row-centers of count-cell ink bands within +/-win of yc0.
    Anchoring on the (bright, left-aligned) count ink is more reliable than the label in
    cloud; the label is then used only to verify identity at each candidate. The center is
    an ink-weighted centroid (robust to asymmetric cloud clipping of the band)."""
    cx0 = calib.count_x if cx is None else cx
    cx1 = min(tn.shape[1], cx0 + int(calib.pitch * 2.5))
    sy0 = max(0, yc0 - win - calib.row_h); sy1 = min(tn.shape[0], yc0 + win + calib.row_h)
    col = tn[sy0:sy1, cx0:cx1]
    if col.size == 0:
        return []
    thr = max(60.0, 0.45 * float(col.max()))
    rowink = (col > thr).sum(axis=1).astype(np.float32)
    bands = []; inside = False; b0 = 0
    for i in range(len(rowink)):
        if rowink[i] >= 1 and not inside:
            inside = True; b0 = i
        elif rowink[i] < 1 and inside:
            inside = False
            if i - b0 >= 5:
                w = rowink[b0:i]
                ctr = b0 + float((np.arange(i - b0) * w).sum() / w.sum())
                bands.append(int(round(sy0 + ctr)))
    if inside and len(rowink) - b0 >= 5:
        w = rowink[b0:]
        ctr = b0 + float((np.arange(len(rowink) - b0) * w).sum() / w.sum())
        bands.append(int(round(sy0 + ctr)))
    return [b for b in bands if abs(b - yc0) <= win + calib.row_h]


# ----------------------------- digit reading -----------------------------
def _dominant_left(xs, tol=6):
    """The x with the most neighbours within +/-tol (mode-like), and its support count."""
    if not xs:
        return None, 0
    xs = sorted(xs)
    best_x, best_c = xs[0], 0
    for cx in xs:
        c = sum(1 for v in xs if abs(v - cx) <= tol)
        if c > best_c:
            best_c, best_x = c, cx
    near = [v for v in xs if abs(v - best_x) <= tol]
    return int(round(float(np.median(near)))), best_c


def _estimate_count_x(tn, calib, shift, search_lo, search_hi):
    """Locate the count column's left edge as the dominant left-edge of digit-width ink
    clusters across ALL weapon rows. The count is left-aligned at one x in every row, so
    that x collects a vote from every row and wins; a tooltip wedged between the label and
    count columns (which pushes the count right) only occupies a few rows with non-aligned
    text, so it can't out-vote the true column. Returns (count_x, support_rows)."""
    minw = calib.pitch * 0.55
    maxw = calib.pitch * 1.6
    lefts = []
    for wp, y0 in calib.rows.items():
        yc = y0 + shift
        if yc < calib.row_h or yc > tn.shape[0] - calib.row_h:
            continue
        band = tn[yc - calib.row_h:yc + calib.row_h, search_lo:search_hi]
        if band.size == 0:
            continue
        for (a, b) in _seg_boxes(band, min_w=4):
            if minw <= (b - a) <= maxw:
                lefts.append(search_lo + a)
    return _dominant_left(lefts)


def _box_patch(band, x0, x1, calib):
    return _crop_norm(band, x0, x1, 0, band.shape[0], calib.gw, calib.gh,
                      trim_rows=True, trim_cols=True, ink=calib.trim_ink,
                      floor=calib.match_floor)


def _best_digit(patch, calib):
    """Best digit char and score. NCC is always computed (it provides the scale-stable
    'digit-likeness' score the suffix/accept gates are tuned against, and is the fallback).
    When the learned classifier is present it OVERRIDES the identity (NCC ties on blur, e.g.
    6->0/8; the MLP learns the real distribution) and supplies a confidence margin; the
    returned score stays NCC-scale so downstream thresholds are unchanged.

    Returns (char, score_ncc, margin) where margin is MLP-prob-margin when the model is
    active (gate via MODEL_MARGIN_FLOOR) else NCC best-minus-second (NCC_MARGIN_FLOOR)."""
    s1, c1, s2 = -1.0, None, -1.0
    pr = patch.ravel()
    for ch, mat in calib._digit_mats.items():
        if mat is None:
            continue
        s = float(mat.dot(pr).max())
        if s > s1:
            s2, s1, c1 = s1, s, ch
        elif s > s2:
            s2 = s
    m = _digit_model()
    if m is not None:
        mc, _mp, mpm = m.predict(patch)
        if mc is not None:
            return mc, s1, mpm
    return c1, s1, s1 - s2


def _classify_box(band, x0, x1, calib):
    """Classify a segmented glyph box; return (char, score, is_digit). The suffix bank may
    win for non-digit glyphs (used to terminate a number at a suffix)."""
    patch = _box_patch(band, x0, x1, calib)
    if patch is None:
        return None, -1.0, False
    ch, s, _ = _best_digit(patch, calib)
    nd = _mat_best(patch, calib._nd_mat)
    if nd > s:
        return "#", nd, False
    return ch, s, True


def read_count_seg(tn, yc, calib, accept=0.50, margin_floor=0.04, cx=None):
    """Read the leading integer in the count cell at row yc by SEGMENTING the clean
    feature into glyph boxes and classifying each. Robust to small count_x errors and
    naturally drops suffixes / stops at the first gap or non-digit.

    The FIRST glyph of a weapon count is always a digit, so it is matched against the
    digit bank only (the suffix bank may not steal it); subsequent glyphs may be suffix
    glyphs, which terminate the number.

    DIGIT-MARGIN GATE (margin_floor): each digit is matched by NCC against all ten digit
    templates; `margin` is best minus second-best. A genuine glyph wins clearly (margin
    >~0.09); an ambiguous one in cloud (e.g. 5-vs-6, 8-vs-2) is nearly tied (margin <~0.03).
    Since the value is only right if EVERY digit is right, a single ambiguous digit means we
    don't actually know the number -- so we return None (a harmless no-read) instead of a
    coin-flip value. This is what stops digit flicker (216<->215, 216<->816) from ever
    reaching the event logic and causing an erroneous vibration."""
    count_x = calib.count_x if cx is None else cx
    y0 = yc - calib.row_h; y1 = yc + calib.row_h
    cx0 = max(0, count_x - 4)
    cx1 = min(tn.shape[1], count_x + int(calib.pitch * 5))
    band = tn[max(0, y0):y1, cx0:cx1]
    if band.shape[0] < 6:
        return None
    boxes = _seg_boxes(band, min_w=4)        # drop 2-3px anti-alias/cloud specks
    if not boxes:
        return None
    # the count is left-aligned at count_x: the first glyph must start at the cell's left.
    if boxes[0][0] > calib.pitch * 0.85:
        return None
    digits = ""; conf = 0.0; prev_x1 = None
    # In this monospace HUD font every digit fills its cell (~0.8*pitch wide); the count
    # suffix glyphs "(", ")", "/" are much narrower (~0.4*pitch) and the "(" in particular
    # is otherwise misread as a confident "1" (so "36(F)" -> "361"). A glyph far narrower
    # than a digit is therefore a SUFFIX, not a digit -> it terminates the number. This is
    # the single most important decode fix: it stops the bracket from inflating every count.
    min_digit_w = calib.pitch * 0.62
    # Ambiguity gate scale: the learned classifier reports a probability margin (0..1, large
    # when confident) which rejects on a different scale than NCC's best-minus-second.
    margin_gate = MODEL_MARGIN_FLOOR if _digit_model() is not None else margin_floor
    for i, (bx0, bx1) in enumerate(boxes):
        w = bx1 - bx0
        if w > calib.pitch * 1.8:                  # merged blob / not a single glyph
            break
        if prev_x1 is not None and (bx0 - prev_x1) > calib.pitch * 0.7:
            break                                   # gap -> end of number (before suffix)
        if w < min_digit_w:                         # too narrow to be a digit -> suffix glyph
            break
        patch = _box_patch(band, bx0, bx1, calib)
        if patch is None:
            break
        ch, s, mg = _best_digit(patch, calib)
        if i > 0:                                   # later glyphs may be a suffix -> stop
            nd = _mat_best(patch, calib._nd_mat)
            # Only treat as a suffix when it CLEARLY beats the digit. A real separator like
            # '/' or '(' matches its suffix template strongly; a cloud-degraded digit (e.g. a
            # faint '7') only weakly resembles '/'. Requiring a margin keeps "270"/"216"
            # intact in cloud while still cutting "5/2(L)" -> 5. (s stays NCC-scale even when
            # the MLP supplies identity, so this comparison is unaffected by the classifier.)
            if nd > s + 0.12:
                break
        if ch is None or s < accept:
            break
        if mg < margin_gate:            # ambiguous digit -> value unknown, refuse to guess
            return None
        digits += ch; conf += s; prev_x1 = bx1
        if len(digits) >= 4:                        # weapon counts are <= 3-4 digits
            break
    if not digits:
        return None
    return int(digits), conf / len(digits)


def read_counts(g, calib, accept=0.45, label_min=0.42, shift_hint=None,
                return_shift=False, cx_hint=None, return_cx=False):
    """Robust read: pick ONE global block shift by total label agreement, then read each
    weapon row by fixed geometry (calib_y + shift). Each row is verified by its own
    translation-invariant label-token match (so absent/clipped rows are skipped, never
    misread) and the count cell is self-centered before the monospace-aware read.

    The capture region is fixed, so the block shift is temporally STABLE. `shift_hint`
    (the previously locked shift) is kept unless a freshly searched shift beats it by a
    clear margin -- this stops cloud/terrain from snapping the whole block to a wrong
    alignment (which made every row read its neighbour's number). Pass return_shift=True
    to get the chosen shift back for the next frame's hint.

    HORIZONTAL re-location (`cx_hint`/return_cx): a tooltip wedged between the label and
    count columns at match start pushes the count column to the RIGHT; when it vanishes the
    column snaps back left. The count column's left edge is re-found each frame as the
    dominant digit-column across all rows, sticky-locked to the previous frame's value so a
    transient mislocation can't flap. This keeps reads aligned through the push/snap-back.

    Returns {weapon: (value, confidence)}  (plus shift and/or cx if requested)."""
    _ensure_mats(calib)
    # Compute the (relatively expensive) text feature only on the HUD region of interest.
    # Widen the right edge generously so a tooltip can push the count column right without
    # the count falling outside the ROI (else we couldn't re-find it).
    H0, W0 = g.shape
    max_push = int(calib.pitch * 11)
    xmax = min(W0, calib.count_x + int(calib.pitch * 5) + max_push)
    if calib.rows:
        rmin = min(calib.rows.values()); rmax = max(calib.rows.values())
        ymin = max(0, rmin - 45); ymax = min(H0, rmax + 70)
    else:
        ymin, ymax = 0, H0
    sub = g[ymin:ymax, :xmax]
    tn_sub = text_feature(sub, calib.mode, calib.gain)
    if ymin == 0:
        tn = tn_sub
    else:
        tn = np.zeros((ymax, xmax), np.float32)
        tn[ymin:ymax] = tn_sub
    Hh = tn.shape[0]
    shift, best_total = _estimate_shift(tn, calib)
    if shift_hint is not None and shift_hint != shift:
        # Temporal lock / hysteresis: only abandon the held shift if the new candidate is
        # clearly better. A fixed HUD does not jump 30px between frames, so this rejects the
        # cloud-induced wandering while still allowing a genuine reflow once it is decisive.
        hint_total = _shift_score(tn, calib, shift_hint, Hh)
        if hint_total >= 0 and best_total <= hint_total * 1.25:
            shift = shift_hint

    # --- horizontal count-column location (sticky) ---
    search_lo = max(0, calib.label_x0 + calib.label_w - 2)
    search_hi = min(tn.shape[1], calib.count_x + max_push + int(calib.pitch * 3))
    cx_cand, support = _estimate_count_x(tn, calib, shift, search_lo, search_hi)
    count_x = calib.count_x
    if cx_hint is not None:
        count_x = cx_hint                          # default: hold the locked column
    if cx_cand is not None and support >= 2:
        if cx_hint is None or abs(cx_cand - count_x) <= 2:
            count_x = cx_cand                      # adopt (first lock, or tiny adjust)
        else:
            # candidate disagrees with the lock: only move if the candidate has STRONG,
            # broad support (most rows agree there) -- a real tooltip push/snap moves every
            # row's count together, whereas noise supports only a row or two.
            n_rows = sum(1 for y in calib.rows.values()
                         if calib.row_h <= y + shift <= Hh - calib.row_h)
            if support >= max(3, (n_rows + 1) // 2):
                count_x = cx_cand
    out = {}
    for wp, y0 in calib.rows.items():
        yc0 = y0 + shift
        # Candidate row positions = count-ink bands near the geometric row (count ink is
        # bright/reliable). Verify each by its LABEL token; the best-labelled band wins.
        cands = _count_bands(tn, yc0, calib, win=13, cx=count_x)
        best_cy, best_s = None, -1.0
        quick = (-1, 0, 1)
        wide = (-6, -5, -4, -3, -2, 2, 3, 4, 5, 6)
        for cy in cands:
            s = _label_score_at(tn, wp, cy, calib, dys=quick)   # common: label ~ aligned
            if s < label_min:                                   # cloud-jittered label: widen
                s = max(s, _label_score_at(tn, wp, cy, calib, dys=wide))
            if s > best_s:
                best_s, best_cy = s, cy
        if best_cy is None:
            # no count band found: fall back to a label-only search (faint counts)
            for dy in range(-8, 9):
                yc = yc0 + dy
                if yc < calib.row_h or yc > Hh - calib.row_h:
                    continue
                s = _label_score_at(tn, wp, yc, calib, dys=(0,))
                if s > best_s:
                    best_s, best_cy = s, yc
        if best_cy is None:
            continue
        label_ok = best_s >= label_min
        # Micro-sweep the vertical center and keep the BEST read, ranked by (digit-count,
        # confidence): the label anchor can sit a couple px off the true digit row, and at
        # the right center the glyph NCC is far more decisive (e.g. 216 reads 0.98 there but
        # ambiguous 2/8, 6/5 just 2px away). Ranking longer reads first stops a high-mean
        # single-digit crop (e.g. "1") from beating the real multi-digit number.
        rc0 = read_count_seg(tn, best_cy, calib, accept=accept, cx=count_x)
        best_rc = rc0
        best_key = (len(str(rc0[0])), rc0[1]) if rc0 else (-1, -1.0)
        if not (rc0 is not None and rc0[1] >= 0.80 and rc0[0] >= 10):
            for dy in (-2, 2, -1, 1, -3, 3):
                cy = best_cy + dy
                if cy < calib.row_h or cy > Hh - calib.row_h:
                    continue
                rc = read_count_seg(tn, cy, calib, accept=accept, cx=count_x)
                if rc is None:
                    continue
                key = (len(str(rc[0])), rc[1])
                if key > best_key:
                    best_key = key; best_rc = rc
                if rc[1] >= 0.85 and rc[0] >= 10:   # strong multi-digit read -> stop sweep
                    break
        if best_rc is None:
            continue
        # Acceptance: the geometry is sticky-locked, so a row that the label can't verify
        # (its text obscured by terrain/cloud) is still trustworthy IF the number itself
        # reads cleanly. Accept when the label verifies, OR a multi-digit value reads with
        # decent confidence, OR a single digit reads very confidently. A weak-label,
        # weak-read row is skipped (no false haptic). This recovers clear counts (e.g. CNN
        # "48" over terrain where "CNN" is washed out) without inventing numbers.
        val, conf = best_rc
        strong = (conf >= 0.55 and val >= 10) or conf >= 0.88
        if label_ok or strong:
            out[wp] = best_rc
    if return_shift and return_cx:
        return out, shift, count_x
    if return_shift:
        return out, shift
    if return_cx:
        return out, count_x
    return out


# ============================ runtime wrapper ============================
# Live capture, one-time Windows-OCR calibration, and the polling detector used by the
# app. numpy is the only runtime dependency; winsdk is imported lazily and ONLY for the
# one-time calibration (harvesting the user's own glyph templates from their HUD).
import ctypes, json, re, time, asyncio
from ctypes import wintypes

WEAPON_EFFECT = {"AAM": "missile", "RKT": "rocket", "BMB": "bomb",
                 "CNN": "gun", "FLR": "flare", "CHFF": "flare"}
WEAPON_CLASS = {"AAM": "discrete", "RKT": "discrete", "BMB": "discrete",
                "CNN": "rapid", "FLR": "counter", "CHFF": "counter"}
_CAL_LABELS = ["RKT", "BMB", "AAM", "FLR", "CHFF", "CNN"]


# ============================ temporal fusion ============================
from collections import deque, Counter


class ReadStabilizer:
    """Despeckle the per-frame read stream BEFORE the tracker sees it.

    Measured fact (tools/flicker_vs_bias.py): ~57% of correctly-readable frames live in
    'flicker' segments -- the true value is the per-frame plurality but a minority of frames
    misread a single digit (e.g. 216 reading 215, 268 reading 269). Each frame is decoded
    independently, so these lone blips slip straight through to the value metric and can nudge
    the tracker. They are the single largest fixable read error.

    A flicker blip has a signature a continuous change does NOT: the value departs from a
    strong recent consensus for ONE frame and the immediately-preceding read was still ON that
    consensus. A real fire/reload instead produces a SUSTAINED new value -- the frame before
    the change is the old value, but the frame after is the new value, so the 'previous read ==
    consensus' guard releases it after a single frame. That bounds correction to exactly one
    frame at any genuine transition (~50 ms at 20 Hz, bridged by the tracker), which is why a
    gun burst that steps down every frame is NOT frozen: only its first step is held, then each
    subsequent step's predecessor is already off-consensus so it passes straight through.

    History holds RAW reads, so the consensus always reflects what the detector actually saw.
    Bias segments (a digit MIS-read the majority of the time) are not helped -- the plurality is
    wrong there -- but those are only ~6% of frames and need better glyph templates, not voting.
    """
    def __init__(self, win=5, min_cons=3):
        self.win = win
        self.min_cons = min_cons
        self._raw = {}          # wp -> deque(maxlen=win) of recent raw int reads
        self._prev = {}         # wp -> previous frame's raw read (or None)

    def reset(self):
        self._raw.clear()
        self._prev.clear()

    def feed(self, reads):
        """reads: {wp: (val, conf)} from read_counts -> same shape, lone flicker corrected."""
        out = {}
        for wp, rc in reads.items():
            val, conf = rc
            h = self._raw.get(wp)
            if h is None:
                h = deque(maxlen=self.win)
                self._raw[wp] = h
            corrected = val
            if len(h) >= self.min_cons:
                cons, cons_n = Counter(h).most_common(1)[0]
                # Correct only a LONE departure from a strong consensus whose predecessor was
                # still on that consensus -- the unmistakable flicker signature. A started
                # descent (prev already off consensus) is passed through untouched.
                if val != cons and cons_n >= self.min_cons and self._prev.get(wp) == cons:
                    corrected = cons
            h.append(val)
            self._prev[wp] = val
            out[wp] = (corrected, conf)
        return out


class TemporalTracker:
    """Turn a stream of noisy per-frame reads into reliable FIRE events.

    Each frame is read independently (~90%/digit), so values flicker in cloud. Haptics must
    follow the TRUE count, not the flicker. Design principles, each fixing a failure seen on
    real recordings:

      * ROBUST LEVEL, not raw frames. The acted-on value is the MEDIAN of the recent valid
        reads (`level`). A 1-2 frame misread can't move a median -> kills flicker without
        needing a value to repeat. Critically this also tracks a CONTINUOUSLY dropping gun
        (whose count changes every frame, so a "must repeat N times" vote would never fire).

      * PLAUSIBLE-DROP fire. A fire is a decrease that keeps most of the count: missiles step
        5->4, the gun may dump 144->78. Truncation misreads collapse to a tiny fraction
        (172->17, 138->1) -> rejected by a per-class keep-fraction.

      * NEVER FREEZE (resync). The old tracker, after rejecting one implausible drop, kept
        the stale baseline forever and ignored every later real fire (8 rockets -> 1). Now an
        implausible level that PERSISTS (several frames at a stable new level we can't explain
        as a fire) silently RE-BASELINES -- we resync to reality instead of getting stuck.
        Resync is silent (we don't know how many fires we missed, so we don't invent buzzes).

      * INCREASE = reload, re-baseline up (no fire), but only when the higher level persists
        (a brief HIGH misread like 270->870 must not stick and then phantom-fire on the drop).

    `confirm` (frames the level must be established) is short for responsiveness; the median
    of a 5-frame window already gives strong noise rejection.
    """

    KEEP_FRAC = {"discrete": 0.55, "rapid": 0.28, "counter": 0.55}
    RAPID_MAX_STEP = 16      # max plausible single-confirmation drop for a RAPID weapon (gun).
                             # At ~15-17 Hz the median lags ~4 frames (~0.25 s); even a fast
                             # cannon depletes only a handful of rounds in that span (every
                             # real gun decrement observed in recordings was 1-3). A larger
                             # drop is a correlated misread cluster (e.g. 81 read as 21/22/26
                             # during a blur poisons the median: 84->26). Such drops do NOT
                             # fire; they fall through to the resync path, which re-baselines
                             # only if the low value genuinely persists. Real long bursts step
                             # down smoothly (small deltas) and are unaffected; gun rumble is
                             # sustained via is_firing() so bridging a read gap loses no feel.
    RESYNC_FRAMES = 6        # frames an unexplained stable level must persist before resync
    RESYNC_FLOOR = 0.30      # never resync DOWN to a value below this fraction of baseline
                             # (a persistent tiny value is a truncation misread, e.g. 28->2,
                             # 254->26 -- adopting it would corrupt the baseline and then
                             # treat the real value's return as a reload, eating real fires)
    ABSENT_RESET = 18        # consecutive no-read frames that mean the HUD is GONE (death /
                             # respawn / menu / loadout screen), not a brief cloud dropout.
                             # At ~15-17 Hz this is ~1.1 s. After this, the weapon's baseline
                             # is cleared so the post-respawn value re-seeds SILENTLY instead
                             # of being compared to the pre-death count (which would fire a
                             # phantom on a reset like 270->150 or 4->3). Brief cloud dropouts
                             # are far shorter and never trigger this.

    def __init__(self, classes=None, window=7, min_valid=4, abs_floor=2):
        self.classes = dict(classes or WEAPON_CLASS)
        self.window = window
        self.min_valid = min_valid             # valid reads needed before we trust a level
        self.abs_floor = abs_floor             # always allow tiny drops (small counts)
        self.conf = {}                         # weapon -> confirmed (baseline) count
        self.raw = {wp: deque(maxlen=window) for wp in self.classes}
        self.hist = {wp: deque(maxlen=16) for wp in self.classes}  # longer raw history for
                                                                   # flicker (recovery) detection
        self._cand = {}                        # weapon -> (candidate_level, frames_seen)
        self._t = 0                            # frame counter (one tick per update())
        self._last_drop = {}                   # weapon -> frame index of last confirmed fire
        self._all_absent = 0                   # consecutive frames with NO weapon readable

    def reset(self):
        self.conf.clear()
        self._cand.clear()
        self._last_drop.clear()
        self._t = 0
        self._all_absent = 0
        for dq in self.raw.values():
            dq.clear()
        for dq in self.hist.values():
            dq.clear()

    def _level(self, wp):
        """Median of recent valid reads (robust current value), or None if too few."""
        vals = sorted(v for v in self.raw[wp] if v is not None)
        if len(vals) < self.min_valid:
            return None
        return vals[len(vals) // 2]

    def _is_fire(self, cls, cur, new):
        """A decrement is a real fire (not truncation/misread) if it keeps most of the
        count -- threshold per class (the gun may legitimately dump a big chunk)."""
        if new >= cur:
            return False
        kf = self.KEEP_FRAC.get(cls, 0.55)
        return new >= min(cur - 1, cur * kf) or (cur - new) <= self.abs_floor

    @staticmethod
    def _leading_digit_flip(cur, new):
        """True if new differs from cur ONLY in the leading digit (same length, identical
        trailing digits). This is the unmistakable signature of a leading-glyph MISREAD
        (86->66, 86->26, 270->170: the units/tens are the same correctly-read glyphs while
        the lead flips 8->6/2, 2->1). A genuine count decrement changes the trailing digits.
        Used to veto fires for fast weapons (gun/countermeasures), where skipping one such
        ambiguous tick is harmless (the next real tick fires) but a false buzz is not. NOT
        applied to discrete ordnance, where e.g. 24->14 is a real launch that must fire."""
        sc, sn = str(cur), str(new)
        return len(sc) == len(sn) and len(sc) >= 2 and sc[0] != sn[0] and sc[1:] == sn[1:]

    def _recovered(self, wp, cur):
        """True if, in the recent raw reads, the baseline `cur` REAPPEARS after a strictly
        lower read -- i.e. the value dropped then bounced back UP to baseline. That is the
        signature of a flicker MISREAD (a single digit toggling, e.g. 248 read as 242 about
        half the time: 248,242,248,248,242,242 ...), NOT a real decrement. A genuine gun
        burst is monotonic non-increasing: once the count leaves a value it never returns to
        it, so this stays False all the way down (248,246,244,242 ...). Used to veto fires
        for fast weapons (gun/countermeasures) only; one suppressed tick is harmless because
        rumble is sustained, but a phantom buzz while merely sitting at a flickering count is
        not. A held single step (100,100,99,99) is monotonic -> not flagged, still fires."""
        seen_lower = False
        for v in self.hist[wp]:                # oldest -> newest (longer history)
            if v is None:
                continue
            if v < cur:
                seen_lower = True
            elif v >= cur and seen_lower:
                return True
        return False

    def _note_candidate(self, wp, level):
        """Track how long an UNEXPLAINED level has persisted, for resync. Returns True when
        it has been stable long enough to adopt as the new baseline."""
        c = self._cand.get(wp)
        if c and abs(c[0] - level) <= 1:
            self._cand[wp] = (level, c[1] + 1)
        else:
            self._cand[wp] = (level, 1)
        return self._cand[wp][1] >= self.RESYNC_FRAMES

    def update(self, reads):
        """reads: {wp: (val, conf)} from read_counts. Returns list of fire events
        [(weapon, effect, kind, delta, old, new)]."""
        self._t += 1
        events = []
        # GLOBAL absence: a respawn / death / menu / loadout screen makes the WHOLE HUD
        # vanish (every weapon blanks together). One faint weapon (often AAM) blanking while
        # others still read is just cloud on that row -- NOT a reset. Tracking absence
        # globally (not per-weapon) fixes missed missile launches: a sparse AAM row no longer
        # self-clears its baseline mid-flight (which silently ate the launch), while genuine
        # respawns -- where all rows blank -- still reset cleanly.
        any_read = any(reads.get(wp) for wp in self.classes)
        if any_read:
            self._all_absent = 0
        else:
            self._all_absent += 1
            if self._all_absent == self.ABSENT_RESET:
                self.conf.clear(); self._cand.clear(); self._last_drop.clear()

        for wp, cls in self.classes.items():
            r = reads.get(wp)
            v = int(r[0]) if r else None
            self.raw[wp].append(v)
            self.hist[wp].append(v)
            level = self._level(wp)
            if level is None:
                continue
            cur = self.conf.get(wp)
            if cur is None:                        # seed baseline once reads are stable
                self.conf[wp] = level
                self._cand.pop(wp, None)
                continue
            # RAPID fast-onset: a STRICTLY progressing decrease in the raw reads (cur > a > b,
            # where a,b are the last two valid reads) is an unmistakable live cannon burst.
            # Fire on it immediately instead of waiting ~3 frames for the median to catch up --
            # this is what removes the felt input lag on the gun. A flicker (248<->242) or a
            # lone misread never forms a strict two-step descent, so this adds NO false fires;
            # bounded by RAPID_MAX_STEP to reject truncation clusters (84->26). The median path
            # below remains the fallback for non-progressive single steps and re-baselining.
            if cls == "rapid":
                vv = [x for x in self.raw[wp] if x is not None]
                if len(vv) >= 2:
                    a, b = vv[-2], vv[-1]
                    if (cur > a > b and (cur - b) <= self.RAPID_MAX_STEP
                            and not self._leading_digit_flip(cur, b)
                            and not self._recovered(wp, cur)):
                        events.append((wp, WEAPON_EFFECT.get(wp, "missile"), cls,
                                       cur - b, cur, b))
                        self.conf[wp] = b
                        self._cand.pop(wp, None)
                        self._last_drop[wp] = self._t
                        continue
            if level == cur:
                self._cand.pop(wp, None)           # back on baseline -> clear any candidate
                continue
            if level < cur:
                # Leading-digit-flip veto (fast weapons only): 86->66 / 86->26 / 81->21 keep
                # the same trailing digits -- a leading-glyph misread, not a fire. For the gun
                # / countermeasures, skipping such an ambiguous tick is harmless; for discrete
                # ordnance a same-trailing drop (24->14) is a real launch, so no veto there.
                if cls in ("rapid", "counter") and self._leading_digit_flip(cur, level):
                    continue
                # Baseline-recovery (flicker) veto, fast weapons only: if the count bounced
                # back UP to baseline within the window, the dip was a misread flicker
                # (248<->242), not a real burst -- a real burst is monotonic and never returns.
                if cls in ("rapid", "counter") and self._recovered(wp, cur):
                    continue
                # Rapid max-step gate: a gun cannot plausibly drop more than RAPID_MAX_STEP in
                # one confirmation. An oversized drop is a correlated misread cluster poisoning
                # the median (81 -> 21/22/26 => 84->26). Don't fire it; fall through to resync,
                # which only adopts the low value if it actually persists.
                too_big = (cls == "rapid" and (cur - level) > self.RAPID_MAX_STEP)
                if not too_big and self._is_fire(cls, cur, level):
                    events.append((wp, WEAPON_EFFECT.get(wp, "missile"), cls,
                                   cur - level, cur, level))
                    self.conf[wp] = level
                    self._cand.pop(wp, None)
                    self._last_drop[wp] = self._t   # mark activity (for sustained rumble)
                else:
                    # Implausible drop. Could be a truncation misread (28->2, 254->26) OR we
                    # genuinely lost sync during a fast burst. A gross truncation (level <
                    # RESYNC_FLOOR*cur) is NEVER adopted -- it is a misread; we hold the
                    # baseline so the real value, when it returns, fires normally. A moderate
                    # unexplained drop that PERSISTS is adopted (resync) so we never freeze.
                    if level >= cur * self.RESYNC_FLOOR and self._note_candidate(wp, level):
                        self.conf[wp] = level
                        self._cand.pop(wp, None)
            else:  # level > cur -> reload / rearm; re-baseline up only when it persists
                if self._note_candidate(wp, level):
                    self.conf[wp] = level
                    self._cand.pop(wp, None)
        return events

    def is_firing(self, wp, within=14):
        """True if `wp` had a confirmed fire within the last `within` updates. Used to
        sustain ONE continuous rumble for a rapid weapon (gun) across a held burst, instead
        of re-triggering a fixed-length animation on each decrement (which felt like waves).
        At ~17 Hz polling, within=14 ~= 0.8 s -- long enough to bridge the gaps between
        confirmed gun ticks, short enough to stop promptly when the trigger is released."""
        f = self._last_drop.get(wp)
        return f is not None and (self._t - f) <= within


def save_gray_png(path, arr):
    """Write a float/uint8 grayscale array to an 8-bit grayscale PNG using only the stdlib
    (zlib+struct) -- no PIL, so it works inside the frozen exe. Used by the recorder."""
    import zlib, struct
    a = np.clip(arr, 0, 255).astype(np.uint8)
    if a.ndim != 2:
        a = a.reshape(a.shape[0], -1)
    h, w = a.shape
    raw = bytearray()
    for y in range(h):
        raw.append(0)                 # filter type 0 (None) per scanline
        raw.extend(a[y].tobytes())
    comp = zlib.compress(bytes(raw), 6)

    def chunk(typ, data):
        return (struct.pack(">I", len(data)) + typ + data
                + struct.pack(">I", zlib.crc32(typ + data) & 0xffffffff))

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0)   # 8-bit grayscale
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(chunk(b"IHDR", ihdr))
        f.write(chunk(b"IDAT", comp))
        f.write(chunk(b"IEND", b""))


# ---- screen capture (GDI BitBlt, ~5 ms) ----
_user32 = ctypes.windll.user32
_gdi32 = ctypes.windll.gdi32
SRCCOPY = 0x00CC0020


class _BMIH(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


def capture_gray(left, top, width, height):
    """Return a (height,width) float32 grayscale numpy array of a screen region."""
    hdesktop = _user32.GetDesktopWindow()
    hdc = _user32.GetDC(hdesktop)
    mem = _gdi32.CreateCompatibleDC(hdc)
    bmp = _gdi32.CreateCompatibleBitmap(hdc, width, height)
    _gdi32.SelectObject(mem, bmp)
    _gdi32.BitBlt(mem, 0, 0, width, height, hdc, left, top, SRCCOPY)
    bih = _BMIH()
    bih.biSize = ctypes.sizeof(_BMIH); bih.biWidth = width; bih.biHeight = -height
    bih.biPlanes = 1; bih.biBitCount = 24; bih.biCompression = 0
    row = (width * 3 + 3) & ~3
    buf = ctypes.create_string_buffer(row * height)
    _gdi32.GetDIBits(mem, bmp, 0, height, buf, ctypes.byref(bih), 0)
    _gdi32.DeleteObject(bmp); _gdi32.DeleteDC(mem); _user32.ReleaseDC(hdesktop, hdc)
    arr = np.frombuffer(buf, dtype=np.uint8).reshape(height, row)
    img = arr[:, :width * 3].reshape(height, width, 3).astype(np.float32)
    return img.mean(axis=2)


# ---- one-time Windows OCR (winsdk) ----
_OCR = None
_OCR_READY = False
_OCR_LOOP = None


def _init_ocr():
    global _OCR, _OCR_READY
    if _OCR_READY:
        return _OCR is not None
    _OCR_READY = True
    try:
        from winsdk.windows.media.ocr import OcrEngine
        from winsdk.windows.globalization import Language
        eng = OcrEngine.try_create_from_language(Language("en-US"))
        if eng is None:
            eng = OcrEngine.try_create_from_user_profile_languages()
        _OCR = eng
    except Exception:
        _OCR = None
    return _OCR is not None


def _ocr_words(g, scale=3):
    """OCR a high-contrast 'gated' reconstruction; words in NATIVE region coords."""
    if not _init_ocr():
        return []
    from winsdk.windows.graphics.imaging import (SoftwareBitmap, BitmapPixelFormat,
                                                 BitmapAlphaMode)
    from winsdk.windows.security.cryptography import CryptographicBuffer
    global _OCR_LOOP
    if _OCR_LOOP is None:
        _OCR_LOOP = asyncio.new_event_loop()
    tn = text_feature(g, "gated", 3.0)
    img = (255 - np.clip(tn, 0, 255)).astype(np.uint8)
    img = np.repeat(np.repeat(img, scale, 0), scale, 1)
    hh, ww = img.shape
    bgra = np.empty((hh, ww, 4), np.uint8)
    for c in range(3):
        bgra[:, :, c] = img
    bgra[:, :, 3] = 255
    buf = CryptographicBuffer.create_from_byte_array(bgra.tobytes())
    sb = SoftwareBitmap.create_copy_from_buffer(buf, BitmapPixelFormat.BGRA8, ww, hh,
                                                BitmapAlphaMode.PREMULTIPLIED)
    res = _OCR_LOOP.run_until_complete(_OCR.recognize_async(sb))
    out = []
    for line in res.lines:
        for wd in line.words:
            r = wd.bounding_rect
            out.append((wd.text.strip(), r.x / scale, r.y / scale,
                        r.width / scale, r.height / scale))
    return out


def _label_token(word):
    w = re.sub(r"[^A-Z]", "", word.upper())
    for lab in _CAL_LABELS:
        if w.startswith(lab):
            return lab
    for lab in ("RKT", "BMB", "AAM", "FLR", "CNN"):
        if len(w) >= 3 and sum(a == b for a, b in zip(w[:3], lab)) >= 2:
            return lab
    return None


def calibrate_from_grays(grays):
    """Build a Calib from several captured frames of the user's HUD using Windows OCR ONCE
    (to locate + read labels/numbers) and harvesting templates on the runtime 'gated'
    feature. Single-HUD version of the offline harness calibration. Returns Calib or None."""
    GW, GH, LGW, LGH = 20, 30, 36, 18
    LABEL_W, GROUP_GAP, MATCH_FLOOR, TRIM_INK = 60, 9, 90.0, 60.0
    digit_bank, label_bank, nondigit_bank = {}, {}, []
    nondigit_cands = []   # candidate suffix glyphs; digit-shaped ones are filtered out
    pitches, digit_ws, row_hs = [], [], []
    label_x0s, rows, count_xs = [], {}, {}
    right_num_xs = []   # left-x of EVERY right-column number (whole readout left-aligns)

    def harvest_label_token(tn, lx, yc, lh):
        x0 = int(lx) - 3; x1 = int(lx) + LABEL_W; rh = int(lh)
        band = tn[max(0, yc - rh):yc + rh, max(0, x0):x1]
        if band.shape[0] < 6 or band.shape[1] < 4:
            return None
        boxes = _seg_boxes(band, min_w=2)
        grp = _leading_group(boxes, GROUP_GAP)
        if grp is None or grp[1] - grp[0] < 4:
            return None
        return _crop_norm(band, grp[0], grp[1], 0, band.shape[0], LGW, LGH,
                          trim_rows=True, trim_cols=True, ink=TRIM_INK, floor=MATCH_FLOOR)

    for g in grays:
        words = _ocr_words(g)
        if not words:
            continue
        tn = text_feature(g, "gated", 3.0)
        for (t, x, y, w, h) in words:
            m = re.match(r"\d+", t)
            if not m:
                continue
            lead = m.group()
            cyc = int(y + h / 2); rh = int(h / 2) + 3
            nb0 = max(0, int(x) - 3); nb1 = min(tn.shape[1], int(x + w) + 8)
            band = tn[max(0, cyc - rh):cyc + rh, nb0:nb1]
            boxes = _seg_boxes(band)
            if len(boxes) >= len(lead):
                for (bx0, bx1), ch in zip(boxes[:len(lead)], lead):
                    dp = _crop_norm(band, bx0, bx1, 0, band.shape[0], GW, GH,
                                    trim_rows=True, trim_cols=True, ink=TRIM_INK, floor=MATCH_FLOOR)
                    if dp is not None:
                        digit_bank.setdefault(ch, []).append(dp)
                for (bx0, bx1) in boxes[len(lead):len(lead) + 3]:
                    npp = _crop_norm(band, bx0, bx1, 0, band.shape[0], GW, GH,
                                     trim_rows=True, trim_cols=True, ink=TRIM_INK, floor=MATCH_FLOOR)
                    if npp is not None:
                        nondigit_cands.append(npp)   # filtered against digits below
        nums = [(t, x, y, w, h) for (t, x, y, w, h) in words if re.match(r"^\d+$", t)]
        # The whole HUD readout column (THR/SPD/ALT + weapon counts) shares one left edge.
        # Collect the left-x of every number sitting to the RIGHT of the label column -> a
        # robust, many-sample estimate of count_x (immune to a single mis-paired row).
        lx_ref = np.median(label_x0s) if label_x0s else 56
        for (t, x, y, w, h) in nums:
            if x > lx_ref + 60:
                right_num_xs.append(x)
        for (t, x, y, w, h) in words:
            lab = _label_token(t)
            if lab is None:
                continue
            yc = int(y + h / 2)
            rows.setdefault(lab, []).append(yc)
            label_x0s.append(x); row_hs.append(h / 2)
            lp = harvest_label_token(tn, x, yc, h)
            if lp is not None:
                label_bank.setdefault(lab, []).append(lp)
            best = None; bd = 1e9
            for nt, nx, ny, nw, nh in nums:
                nyc = ny + nh / 2
                if nx > x and abs(nyc - yc) < 14 and abs(nyc - yc) < bd:
                    bd = abs(nyc - yc); best = (nt, nx)
            if best is not None:
                count_xs.setdefault(lab, []).append(best[1])
    # pitch from any multi-digit number width
    pit = []
    for g in grays[:4]:
        for (t, x, y, w, h) in _ocr_words(g):
            if re.match(r"^\d{2,}$", t):
                pit.append(w / len(t))
    pitch = float(np.median(pit)) if pit else 13.7
    digit_w = int(round(pitch * 0.82))
    row_h = int(round(np.median(row_hs))) + 4 if row_hs else 14
    if not rows or not digit_bank or not label_bank:
        return None

    def dominant_left(xs, tol=6):
        """Mode-like dominant left edge: the x with the most neighbours within +/-tol."""
        if not xs:
            return None
        xs = sorted(xs)
        best_x, best_c = xs[0], 0
        for cx in xs:
            c = sum(1 for v in xs if abs(v - cx) <= tol)
            if c > best_c:
                best_c, best_x = c, cx
        near = [v for v in xs if abs(v - best_x) <= tol]
        return int(round(float(np.median(near))))

    # count_x: prefer the dominant left edge of the WHOLE right-hand number column (many
    # samples, robust). Fall back to weapon-paired counts, then a geometric guess. Also
    # sanity-check against the weapon-paired counts so a stray cluster can't win.
    paired = [v for vs in count_xs.values() for v in vs]
    count_x = dominant_left(right_num_xs)
    if count_x is None:
        count_x = int(np.median(paired)) if paired else (int(np.median(label_x0s)) + int(round(15 * pitch)))
    elif paired:
        pj = int(np.median(paired))
        # if the column estimate disagrees badly with the weapon-paired counts, trust the
        # weapon rows (they are the ones we actually read).
        if abs(count_x - pj) > 2 * pitch:
            count_x = pj

    def with_centroids(bank, cap):
        out = {}
        for k, v in bank.items():
            if not v:
                continue
            mn = _norm(np.mean(np.stack(v), axis=0))
            out[k] = ([mn] if mn is not None else []) + v[:cap]
        return out

    # Filter the suffix (non-digit) bank: a weapon count like "5/2(L)" or "4/1(R)" or
    # "3[3]" puts a DIGIT glyph into the suffix region. If such a digit leaks into the
    # non-digit bank it poisons classification (a real "2" then matches "suffix" and the
    # number is misread, e.g. 216 -> 815). So drop any candidate that looks like a digit.
    digit_tmpls = [t for v in digit_bank.values() for t in v]
    for npp in nondigit_cands:
        pr = npp.ravel()
        best_digit = max((float(np.dot(t.ravel(), pr)) for t in digit_tmpls), default=0.0)
        if best_digit < 0.60:                      # clearly not a digit -> safe suffix glyph
            nondigit_bank.append(npp)

    c = Calib()
    c.mode = "gated"; c.gain = 3.0
    c.gw, c.gh, c.lgw, c.lgh = GW, GH, LGW, LGH
    c.pitch = pitch; c.digit_w = digit_w; c.row_h = row_h
    c.match_floor = MATCH_FLOOR; c.trim_ink = TRIM_INK
    c.label_x0 = max(0, int(np.median(label_x0s))); c.label_w = LABEL_W
    c.count_x = count_x
    # Keep a weapon row if its label was OCR-detected in >=2 frames. A single sighting over
    # cloud is usually a misplacement -- BUT a genuinely sparse/single-digit row (e.g. BMB=1)
    # may only resolve once, and hard-dropping it means that weapon can NEVER fire. So also
    # keep a one-sighting row when it is CORROBORATED: it had a number paired to it that sits
    # in the real count column (within 2*pitch of count_x). That rejects random stray labels
    # while preserving weak real rows. (Depleted/dimmed rows like FLR=0 may still drop; an
    # empty weapon cannot fire, so that is fine.)
    def _row_ok(lab, ys):
        if len(ys) >= 2:
            return True
        xs = count_xs.get(lab)
        if not xs:
            return False
        return abs(float(np.median(xs)) - count_x) <= 2 * pitch

    rows2 = {k: v for k, v in rows.items() if _row_ok(k, v)}
    if not rows2:
        rows2 = {k: v for k, v in rows.items()}   # fallback: keep singletons if that's all
    c.rows = {k: int(np.median(v)) for k, v in rows2.items()}
    rys = sorted(c.rows.values())
    c.line_pitch = int(round(np.median(np.diff(rys)))) if len(rys) > 1 else 30
    c.digits = with_centroids(digit_bank, 18)
    c.labels = with_centroids(label_bank, 8)
    c.nondigit = nondigit_bank[:24]
    c.valid = True

    # ---- self-validation: a calibration is only good if it can actually READ the user's
    # counters back on the calibration frames. This rejects poisoned one-shot calibrations
    # (e.g. a transient frame that mislocated count_x) so the app retries instead of saving
    # and sticking with a dead calibration.
    want = max(1, len(c.rows) // 2 + 1)   # majority of weapon rows must read
    good = 0
    for g in grays[:8]:
        rd = read_counts(g, c)
        if len(rd) >= want:
            good += 1
    if good < 2:
        return None
    return c


class HudDetector:
    """Fast, robust HUD weapon-counter detector for the app.

    Calibrate once (live, weapon counters visible) -> harvests the user's own glyph
    templates + column geometry. Runtime reads counts in ~10-15 ms with no Windows OCR and
    fires haptic events on confident decrements, with per-class confirmation/voting."""
    def __init__(self, region=(0, 0, 400, 400), max_drop_discrete=8, max_drop_rapid=200):
        self.region = tuple(region)
        self.max_drop_discrete = max_drop_discrete
        self.max_drop_rapid = max_drop_rapid
        self.calib = None
        self.tracker = TemporalTracker()
        self.stab = ReadStabilizer()
        self.shift = None                      # sticky block shift (fixed HUD -> stable)
        self.cx = None                         # sticky count-column x (tooltip push/snap)
        self.available = (np is not None)

    def set_region(self, left, top, w, h):
        self.region = (left, top, w, h)

    @property
    def calibrated(self):
        return self.calib is not None and self.calib.valid

    def reset(self):
        self.tracker.reset()
        self.stab.reset()
        self.shift = None
        self.cx = None

    def calibrate(self, n_frames=24, interval=0.15, capture=None):
        """Capture n frames of the current region and build a Calibration via OCR.
        Returns (ok, message). More frames (vs the old 12) lets sparse cloud-obscured
        labels accumulate enough OCR sightings to be calibrated reliably."""
        if np is None:
            return False, "numpy unavailable"
        grays = []
        l, t, w, h = self.region
        for _ in range(n_frames):
            try:
                grays.append(capture(self.region) if capture else capture_gray(l, t, w, h))
            except Exception as e:
                return False, "capture failed: %s" % e
            time.sleep(interval)
        cal = calibrate_from_grays(grays)
        if cal is None or not cal.rows:
            return False, "no weapon counters found in region (move HUD into the box)"
        self.calib = cal
        self.reset()
        return True, "calibrated: %s" % ", ".join(sorted(cal.rows))

    def save(self, path):
        if self.calib is None:
            return False
        with open(path, "w") as f:
            json.dump({"region": list(self.region), "calib": self.calib.to_dict()}, f)
        return True

    def load(self, path):
        try:
            with open(path) as f:
                data = json.load(f)
            self.region = tuple(data.get("region", self.region))
            self.calib = Calib.from_dict(data["calib"])
            return self.calib.valid
        except Exception:
            return False

    def read(self):
        """Capture + fast read once; return {weapon: count}."""
        if not self.calibrated or np is None:
            return {}
        l, t, w, h = self.region
        try:
            g = capture_gray(l, t, w, h)
        except Exception:
            return {}
        reads, self.shift, self.cx = read_counts(
            g, self.calib, shift_hint=self.shift, return_shift=True,
            cx_hint=self.cx, return_cx=True)
        return {wp: v for wp, (v, _c) in reads.items()}

    def probe(self):
        """Cheap one-frame check: count of weapon-counter labels visible in the region.
        Gates the (expensive) auto-calibration so OCR only runs when the HUD is on screen."""
        return len(self.visible_labels())

    def visible_labels(self):
        """Set of weapon labels currently OCR-visible in the region (one OCR pass). Used to
        gate calibration AND to detect loadout changes (a new weapon row appearing)."""
        if np is None:
            return set()
        l, t, w, h = self.region
        try:
            g = capture_gray(l, t, w, h)
        except Exception:
            return set()
        seen = set()
        for (tok, _x, _y, _w, _h) in _ocr_words(g):
            lab = _label_token(tok)
            if lab is not None:
                seen.add(lab)
        return seen

    def poll(self):
        """Capture + read + temporal fusion; return (events, counts).
        events = [(weapon, effect, kind, delta, old, new)]. Fires only on confirmed,
        plausible decrements (see TemporalTracker), so single-frame misreads never buzz."""
        if not self.calibrated or np is None:
            return [], {}
        l, t, w, h = self.region
        try:
            g = capture_gray(l, t, w, h)
            reads, self.shift, self.cx = read_counts(
                g, self.calib, shift_hint=self.shift, return_shift=True,
                cx_hint=self.cx, return_cx=True)
        except Exception:
            return [], {}
        reads = self.stab.feed(reads)
        events = self.tracker.update(reads)
        counts = {wp: v for wp, (v, _c) in reads.items()}
        return events, counts

    def poll_capture(self):
        """Capture once and return (gray_frame, reads_with_conf, (capture_ms, read_ms))
        WITHOUT mutating detection state. For diagnostics/recording."""
        if not self.calibrated or np is None:
            return None, {}, (0.0, 0.0)
        l, t, w, h = self.region
        try:
            t0 = time.perf_counter()
            g = capture_gray(l, t, w, h)
            t1 = time.perf_counter()
            reads, self.shift, self.cx = read_counts(
                g, self.calib, shift_hint=self.shift, return_shift=True,
                cx_hint=self.cx, return_cx=True)
            t2 = time.perf_counter()
        except Exception:
            return None, {}, (0.0, 0.0)
        return g, reads, ((t1 - t0) * 1000.0, (t2 - t1) * 1000.0)

    def poll_debug(self):
        """Like poll() but also returns the captured frame and a telemetry snapshot
        (per-weapon read+confidence, confirmed baselines before/after, recent read window,
        timings). Returns (events, counts, gray_frame, info)."""
        g, reads, timings = self.poll_capture()
        reads = self.stab.feed(reads)
        pre_conf = dict(self.tracker.conf)
        events = self.tracker.update(reads)
        counts = {wp: v for wp, (v, _c) in reads.items()}
        info = {
            "reads": {wp: {"val": int(v), "conf": round(float(c), 3)}
                      for wp, (v, c) in reads.items()},
            "confirmed_before": pre_conf,
            "confirmed_after": dict(self.tracker.conf),
            "recent": {wp: [int(x) if x is not None else None for x in dq]
                       for wp, dq in self.tracker.raw.items() if any(v is not None for v in dq)},
            "capture_ms": round(timings[0], 2),
            "read_ms": round(timings[1], 2),
        }
        return events, counts, g, info

