"""
hud_eval.py — offline calibration + accuracy harness for hud_detect.

Calibrates ONCE with Windows OCR (harvests the user's monospace digit/label templates +
column geometry), then runs the fast template reader on every labelled frame and reports
per-weapon leading-integer accuracy against ground_truth.json.

Because the three capture sets share the same game font (identical glyph pixels) we pool
digit/label templates across all clear frames; per-set we calibrate only the geometry
(label column x, count column x, row Ys). The label->count x offset is fixed in game
pixels, so for the all-cloud set (where OCR can't read the numbers) we recover count_x
from that shared offset.
"""
import sys, glob, os, struct, json, time, re, asyncio
import numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
import hud_detect as H

LABELS = ["RKT", "BMB", "AAM", "FLR", "CHFF", "CNN"]
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "datasets")

from winsdk.windows.media.ocr import OcrEngine
from winsdk.windows.globalization import Language
from winsdk.windows.graphics.imaging import SoftwareBitmap, BitmapPixelFormat, BitmapAlphaMode
from winsdk.windows.security.cryptography import CryptographicBuffer
_eng = OcrEngine.try_create_from_language(Language("en-US"))
_loop = asyncio.new_event_loop()


def load_gray(path):
    d = open(path, "rb").read(); off = struct.unpack_from("<I", d, 10)[0]
    w = struct.unpack_from("<i", d, 18)[0]; h = struct.unpack_from("<i", d, 22)[0]
    h2 = abs(h); top = h < 0; rb = (w * 3 + 3) & ~3
    px = np.frombuffer(d, np.uint8, count=rb * h2, offset=off).reshape(h2, rb)
    g = px[:, :w * 3].reshape(h2, w, 3).astype(np.float32).mean(2)
    return g if top else g[::-1]


def ocr_words(g, scale=3, mode="gated"):
    """OCR a high-contrast reconstruction; return [(text,x,y,w,h)] in native coords."""
    tn = H.text_feature(g, mode, 3.0)
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
    res = _loop.run_until_complete(_eng.recognize_async(sb))
    out = []
    for line in res.lines:
        for wd in line.words:
            r = wd.bounding_rect
            out.append((wd.text.strip(), r.x / scale, r.y / scale,
                        r.width / scale, r.height / scale))
    return out


def _label_token(word):
    """Map an OCR word to a weapon label if it starts with one (handles 'BMB', 'AAM',
    merged 'BMBCCIP', minor misreads). Returns the label or None."""
    w = re.sub(r"[^A-Z]", "", word.upper())
    for lab in LABELS:
        if w.startswith(lab):
            return lab
    # tolerate a 1-char OCR error on the 3-letter labels
    for lab in ("RKT", "BMB", "AAM", "FLR", "CNN"):
        if len(w) >= 3 and sum(a == b for a, b in zip(w[:3], lab)) >= 2:
            return lab
    return None


def calibrate(frame_sets, clear_pick):
    """frame_sets: {set_name: [paths]}.  clear_pick: {set_name: [indices for templates]}.
    Returns {set_name: Calib} sharing a global template bank.

    Templates are harvested on the SAME 'gated' feature used at runtime (this is critical
    for high NCC). Windows OCR (on a high-contrast 'fill' reconstruction) is used only to
    locate labels/numbers and read their identities for the one-time harvest."""
    digit_bank = {}   # char -> list[patch]
    label_bank = {}   # wp -> list[patch]
    nondigit_bank = []
    pitches = []
    digit_ws = []
    row_hs = []
    geom = {}

    GW = int(os.environ.get("HUD_GW", 20)); GH = int(os.environ.get("HUD_GH", 30))
    LGW, LGH = 36, 18
    LABEL_W = 60
    GROUP_GAP = 9     # px; merges letters of a token, splits token from suffix space
    MATCH_FLOOR = float(os.environ.get("HUD_FLOOR", 90.0))  # MUST equal Calib.match_floor
    TRIM_INK = float(os.environ.get("HUD_TRIMINK", 60.0))   # MUST equal Calib.trim_ink

    def harvest_label_token(tn, lx, yc, lh):
        """Tight, translation-invariant token crop -- IDENTICAL math to runtime
        H.label_token_patch (window -> seg -> leading group -> tight resize/norm)."""
        x0 = int(lx) - 3; x1 = int(lx) + LABEL_W
        rh = int(lh)
        band = tn[max(0, yc - rh):yc + rh, max(0, x0):x1]
        if band.shape[0] < 6 or band.shape[1] < 4:
            return None
        boxes = H._seg_boxes(band, min_w=2)
        grp = H._leading_group(boxes, GROUP_GAP)
        if grp is None or grp[1] - grp[0] < 4:
            return None
        return H._crop_norm(band, grp[0], grp[1], 0, band.shape[0], LGW, LGH,
                            trim_rows=True, trim_cols=True, ink=TRIM_INK, floor=MATCH_FLOOR)

    for s, paths in frame_sets.items():
        label_x0s = []
        rows = {}
        count_xs = []
        for idx, p in enumerate(paths):
            g = load_gray(p)
            words = ocr_words(g)
            tn = H.text_feature(g, "gated", 3.0)   # harvest on runtime feature
            harvest = idx in clear_pick.get(s, [])
            # ---- digit templates: harvest leading-digit runs from EVERY number-ish word
            # (THR/SPD/ALT/counts ...). Gives many samples per glyph incl. rare ones (4/6/9).
            if harvest:
                for (t, x, y, w, h) in words:
                    m = re.match(r"\d+", t)
                    if not m:
                        continue
                    lead = m.group()
                    cyc = int(y + h / 2); rh = int(h / 2) + 3
                    nb0 = max(0, int(x) - 3); nb1 = min(tn.shape[1], int(x + w) + 8)
                    band = tn[max(0, cyc - rh):cyc + rh, nb0:nb1]
                    boxes = H._seg_boxes(band)
                    if len(boxes) >= len(lead):
                        for (bx0, bx1), ch in zip(boxes[:len(lead)], lead):
                            dp = H._crop_norm(band, bx0, bx1, 0, band.shape[0], GW, GH,
                                              trim_rows=True, trim_cols=True, ink=TRIM_INK,
                                              floor=MATCH_FLOOR)
                            if dp is not None:
                                digit_bank.setdefault(ch, []).append(dp)
                        # boxes after the number are suffix glyphs ( ( ) L F / [ ] : )
                        for (bx0, bx1) in boxes[len(lead):len(lead) + 3]:
                            npp = H._crop_norm(band, bx0, bx1, 0, band.shape[0], GW, GH,
                                               trim_rows=True, trim_cols=True, ink=TRIM_INK,
                                               floor=MATCH_FLOOR)
                            if npp is not None:
                                nondigit_bank.append(npp)
            # ---- labels + geometry
            nums = [(t, x, y, w, h) for (t, x, y, w, h) in words if re.match(r"^\d+$", t)]
            wlabels = []
            for (t, x, y, w, h) in words:
                lab = _label_token(t)
                if lab is not None:
                    wlabels.append((lab, x, y, w, h))
            for name, lx, ly, lw, lh in wlabels:
                yc = int(ly + lh / 2)
                rows.setdefault(name, []).append(yc)
                label_x0s.append(lx)
                row_hs.append(lh / 2)
                if harvest:
                    lp = harvest_label_token(tn, lx, yc, lh)
                    if lp is not None:
                        label_bank.setdefault(name, []).append(lp)
                # nearest number on this row -> count_x + pitch estimate
                best = None; bd = 1e9
                for nt, nx, ny, nw, nh in nums:
                    nyc = ny + nh / 2
                    if nx > lx and abs(nyc - yc) < 14 and abs(nyc - yc) < bd:
                        bd = abs(nyc - yc); best = (nt, nx, nyc, nw, nh)
                if best is None:
                    continue
                nt, nx, nyc, nw, nh = best
                count_xs.append(nx)
                pitch = nw / max(1, len(nt))
                pitches.append(pitch); digit_ws.append(pitch * 0.82)
        geom[s] = {
            "label_x0": int(np.median(label_x0s)) if label_x0s else 10,
            "rows": {k: int(np.median(v)) for k, v in rows.items()},
            "count_x_direct": int(np.median(count_xs)) if count_xs else None,
        }

    pitch = float(np.median(pitches)) if pitches else 13.7
    digit_w = int(round(np.median(digit_ws))) if digit_ws else 11
    row_h = int(round(np.median(row_hs))) + 4 if row_hs else 14
    offsets = [geom[s]["count_x_direct"] - geom[s]["label_x0"]
               for s in geom if geom[s]["count_x_direct"] is not None]
    offset = int(np.median(offsets)) if offsets else int(round(15 * pitch))

    def _with_centroids(bank, cap):
        """Cap each class' raw templates and PREPEND a denoised centroid (renormalised
        mean of all samples). The centroid matches degraded cloud glyphs more stably than
        any single noisy sample, sharpening e.g. 6-vs-5 / 8-vs-2 discrimination."""
        out = {}
        for k, v in bank.items():
            if not v:
                continue
            m = np.mean(np.stack(v), axis=0)
            mn = H._norm(m)
            tmpls = ([mn] if mn is not None else []) + v[:cap]
            out[k] = tmpls
        return out

    digit_bank = _with_centroids(digit_bank, 18)
    label_bank = _with_centroids(label_bank, 8)
    nondigit_bank = nondigit_bank[:24]

    calibs = {}
    for s in frame_sets:
        c = H.Calib()
        c.mode = "gated"; c.gain = 3.0
        c.gw, c.gh, c.lgw, c.lgh = GW, GH, LGW, LGH
        c.pitch = pitch; c.digit_w = digit_w; c.row_h = row_h
        c.match_floor = MATCH_FLOOR; c.trim_ink = TRIM_INK
        lx0 = geom[s]["label_x0"]
        c.label_x0 = max(0, lx0); c.label_w = LABEL_W
        cxd = geom[s]["count_x_direct"]
        c.count_x = cxd if cxd is not None else (lx0 + offset)
        c.rows = geom[s]["rows"]
        c.digits = digit_bank
        c.labels = label_bank
        c.nondigit = nondigit_bank
        c.valid = True
        calibs[s] = c
    return calibs, {"pitch": pitch, "digit_w": digit_w, "row_h": row_h, "offset": offset,
                    "digits": sorted(digit_bank), "labels": sorted(label_bank),
                    "nondigit": len(nondigit_bank)}


def main():
    gt = json.load(open(os.path.join(ROOT, "ground_truth.json")))
    sets = ["hud_frames", "hud_frames2", "hud_frames3"]
    frame_sets = {s: sorted(glob.glob(os.path.join(ROOT, s, "hud_0*.bmp"))) for s in sets}
    clear_pick = {s: list(range(len(frame_sets[s]))) for s in sets}

    t0 = time.perf_counter()
    calibs, info = calibrate(frame_sets, clear_pick)
    print("calibrated in %.0f ms  pitch=%.1f digit_w=%d row_h=%d offset=%d digits=%s labels=%s"
          % ((time.perf_counter() - t0) * 1000, info["pitch"], info["digit_w"],
             info["row_h"], info["offset"], "".join(info["digits"]), ",".join(info["labels"])))

    accept = float(sys.argv[1]) if len(sys.argv) > 1 else 0.50
    total_ok = total = 0
    tread = nread = 0
    for s in sets:
        calib = calibs[s]
        gset = gt[s]
        print(f"\n=== {s}  count_x={calib.count_x} rows={calib.rows} ===")
        set_ok = set_tot = 0
        for i, p in enumerate(frame_sets[s]):
            g = load_gray(p)
            t = time.perf_counter()
            reads = H.read_counts(g, calib, accept=accept)
            tread += time.perf_counter() - t; nread += 1
            line = []
            for wp in gset:
                truth = gset[wp][i]
                got = reads.get(wp)
                gotv = got[0] if got else None
                ok = (gotv == truth)
                set_ok += ok; set_tot += 1
                line.append(f"{wp}:{gotv if gotv is not None else '-'}/{truth}{'' if ok else ' X'}")
            print(f"  {os.path.basename(p)}  " + "  ".join(line))
        total_ok += set_ok; total += set_tot
        print(f"  set acc: {set_ok}/{set_tot} = {100*set_ok/set_tot:.0f}%")
    print(f"\nOVERALL: {total_ok}/{total} = {100*total_ok/total:.1f}%   "
          f"read {tread/max(1,nread)*1000:.1f} ms/frame")


if __name__ == "__main__":
    main()
