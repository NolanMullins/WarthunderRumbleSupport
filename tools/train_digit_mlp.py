"""Train a small MLP digit classifier and export PURE-NUMPY weights.

Data:
  - REAL crops harvested from human-verified GT (tools/harvest_digits.py): hard, realistic
    blur/cloud, but imbalanced and missing class '9'.
  - CLEAN templates from every calibration (all 10 digits), AUGMENTED (blur / sub-pixel
    shift / brightness / noise) to balance the classes and cover '9'.

Honest validation: real crops are split TEMPORALLY per (clip,weapon) -- first 60% of frames
to train, last 40% to validate -- so near-identical adjacent frames never straddle the split
(no leakage). Templates + augmentation go to TRAIN only. We report overall + per-digit val
accuracy, with special attention to '6' (NCC got it 35.5% right).

Deploy: sklearn is used OFFLINE only. We extract (W1,b1,W2,b2) into tests/digit_model.npz;
runtime does a pure-numpy forward pass (relu + softmax), so the shipped app keeps numpy as
its only dependency.
"""
import sys, os, csv
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from PIL import Image, ImageFilter
from collections import defaultdict
from tests.lib import recordings as R
from tests.lib import detect as D
import src.winwinghaptics.detection.hud_detect as H

rng = np.random.default_rng(0)
DS = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "..", "tests", "digit_dataset"))
OUT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "..", "tests", "digit_model.npz"))
GW = GH = None


def norm_vec(img2d):
    p = img2d.astype(np.float32)
    p -= p.mean()
    n = np.linalg.norm(p)
    if n < 1e-3:
        return None
    return (p / n).ravel()


# ---- load REAL crops with temporal split ----
rows = []
with open(os.path.join(DS, "manifest.csv")) as f:
    for r in csv.DictReader(f):
        rows.append(r)

# group by (clip,weapon) to compute per-group frame split point
by_grp = defaultdict(list)
for r in rows:
    by_grp[(r["clip"], r["weapon"])].append(r)
split_frame = {}
for grp, rs in by_grp.items():
    frs = sorted(set(int(x["frame"]) for x in rs))
    cut = frs[int(len(frs) * 0.6)] if len(frs) > 2 else (frs[-1] + 1)
    split_frame[grp] = cut

Xtr, ytr, Xva, yva = [], [], [], []
for r in rows:
    d = r["digit"]; tag = r["clip"]; wp = r["weapon"]; fr = int(r["frame"]); pos = r["pos"]
    path = os.path.join(DS, d, f"{tag}_{wp}_f{fr}_{pos}.png")
    if not os.path.exists(path):
        continue
    img = np.asarray(Image.open(path))
    if GW is None:
        GH, GW = img.shape
    v = norm_vec(img)
    if v is None:
        continue
    if fr < split_frame[(tag, wp)]:
        Xtr.append(v); ytr.append(int(d))
    else:
        Xva.append(v); yva.append(int(d))

print(f"real crops: train={len(Xtr)} val={len(Xva)}  patch={GW}x{GH}")


# ---- clean templates + augmentation (TRAIN only) ----
def augment(img2d, k):
    """Yield k degraded variants of a clean uint8 glyph image."""
    base = Image.fromarray(np.clip(img2d, 0, 255).astype(np.uint8))
    for _ in range(k):
        im = base
        if rng.random() < 0.7:
            im = im.filter(ImageFilter.GaussianBlur(radius=float(rng.uniform(0.4, 1.6))))
        a = np.asarray(im).astype(np.float32)
        # sub-pixel-ish shift via integer roll (patch is already stroke-trimmed)
        sy, sx = int(rng.integers(-2, 3)), int(rng.integers(-1, 2))
        a = np.roll(a, sy, axis=0); a = np.roll(a, sx, axis=1)
        # brightness/contrast
        a = a * float(rng.uniform(0.75, 1.25)) + float(rng.uniform(-15, 15))
        # additive cloud noise
        a = a + rng.normal(0, float(rng.uniform(2, 18)), a.shape)
        yield np.clip(a, 0, 255)


tmpl_per_class = defaultdict(list)
for clip in R.discover():
    cal, _ = D._calibrate(clip)
    if cal is None:
        continue
    H._ensure_mats(cal)
    for d, tmpls in cal.digits.items():
        for t in tmpls:
            # templates are stored normalized; rescale to a 0..255 image for augmentation
            a = np.asarray(t).astype(np.float32)
            a = a - a.min()
            mx = a.max()
            if mx > 1e-6:
                a = a / mx * 255.0
            if a.shape != (GH, GW):
                a = np.asarray(Image.fromarray(a.astype(np.uint8)).resize((GW, GH)))
            tmpl_per_class[d].append(a.astype(np.float32))

AUG_PER_TEMPLATE = 40
for d, imgs in tmpl_per_class.items():
    for img in imgs:
        for aug in augment(img, AUG_PER_TEMPLATE):
            v = norm_vec(aug)
            if v is not None:
                Xtr.append(v); ytr.append(int(d))

Xtr = np.array(Xtr, np.float32); ytr = np.array(ytr)
Xva = np.array(Xva, np.float32); yva = np.array(yva)
print(f"train total (real+aug)={len(Xtr)}  classes={sorted(set(ytr.tolist()))}")

# ---- train MLP (sklearn, offline) ----
from sklearn.neural_network import MLPClassifier
clf = MLPClassifier(hidden_layer_sizes=(64,), activation="relu", alpha=1e-3,
                    batch_size=256, learning_rate_init=2e-3, max_iter=120,
                    early_stopping=False, random_state=0)
clf.fit(Xtr, ytr)

# ---- validate on held-out REAL crops ----
pred = clf.predict(Xva)
acc = float((pred == yva).mean())
print(f"\nMLP val accuracy on REAL held-out crops: {100*acc:.2f}%  (NCC baseline ~93.08%)")
per = defaultdict(lambda: [0, 0])
conf = defaultdict(int)
for t, p in zip(yva, pred):
    per[t][1] += 1
    if t == p:
        per[t][0] += 1
    else:
        conf[(int(t), int(p))] += 1
print("per-digit val accuracy:")
for d in range(10):
    c, n = per[d]
    print(f"  {d}: {c}/{n} = {100*c/n:.1f}%" if n else f"  {d}: (no val samples)")
print("top val confusions (true->pred:n):")
for (t, p), n in sorted(conf.items(), key=lambda kv: -kv[1])[:10]:
    print(f"  {t}->{p}: {n}")

# ---- export pure-numpy weights ----
W1, W2 = clf.coefs_
b1, b2 = clf.intercepts_
np.savez(OUT, W1=W1.astype(np.float32), b1=b1.astype(np.float32),
         W2=W2.astype(np.float32), b2=b2.astype(np.float32),
         classes=clf.classes_.astype(np.int64), gw=GW, gh=GH)
print("\nsaved numpy weights ->", OUT,
      f"(W1{W1.shape} W2{W2.shape})")
