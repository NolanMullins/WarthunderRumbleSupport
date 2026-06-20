"""Per-digit CNN classifier -- A/B vs the MLP on the SAME data + temporal split.

The current classifier is an MLP that FLATTENS the 20x30 glyph (loses 2D structure). A small
CNN respects pixel locality and is translation-tolerant -- the standard tool for single-glyph
recognition, and (unlike the CRNN sequence model) it is NOT data-hungry: classifying one glyph
into 10 classes is a small, well-posed problem we have ~10k labels for.

This trains the CNN on the IDENTICAL digit dataset + temporal split as train_digit_mlp.py and
reports held-out accuracy so the comparison is apples-to-apples. Offline PyTorch only (the
decision to deploy + how to ship pure-numpy comes AFTER we see if it actually wins).
"""
import sys, os, csv
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from PIL import Image, ImageFilter
from collections import defaultdict
import torch
import torch.nn as nn
from tests.lib import recordings as R
from tests.lib import detect as D
import src.winwinghaptics.detection.hud_detect as H

torch.manual_seed(0); np.random.seed(0)
torch.set_num_threads(max(1, (os.cpu_count() or 4)))
rng = np.random.default_rng(0)
DS = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "..", "tests", "digit_dataset"))
GW = GH = None


def norm_img(img2d):
    """zero-mean unit-norm 2D (keep shape for conv)."""
    p = img2d.astype(np.float32)
    p -= p.mean()
    n = np.linalg.norm(p)
    if n < 1e-3:
        return None
    return p / n


# ---- load real crops with the SAME temporal split as the MLP trainer ----
rows = []
with open(os.path.join(DS, "manifest.csv")) as f:
    for r in csv.DictReader(f):
        rows.append(r)
by_grp = defaultdict(list)
for r in rows:
    by_grp[(r["clip"], r["weapon"])].append(r)
split_frame = {}
for grp, rs in by_grp.items():
    frs = sorted(set(int(x["frame"]) for x in rs))
    split_frame[grp] = frs[int(len(frs) * 0.6)] if len(frs) > 2 else frs[-1] + 1

Xtr, ytr, Xva, yva, Mva = [], [], [], [], []
for r in rows:
    d = r["digit"]; tag = r["clip"]; wp = r["weapon"]; fr = int(r["frame"]); pos = r["pos"]
    path = os.path.join(DS, d, f"{tag}_{wp}_f{fr}_{pos}.png")
    if not os.path.exists(path):
        continue
    img = np.asarray(Image.open(path))
    if GW is None:
        GH, GW = img.shape
    v = norm_img(img)
    if v is None:
        continue
    if fr < split_frame[(tag, wp)]:
        Xtr.append(v); ytr.append(int(d))
    else:
        Xva.append(v); yva.append(int(d)); Mva.append(tag)
print(f"real crops: train={len(Xtr)} val={len(Xva)} patch={GW}x{GH}", flush=True)


# ---- clean templates + augmentation (TRAIN only) -- same as MLP trainer ----
def augment(img2d, k):
    base = Image.fromarray(np.clip(img2d, 0, 255).astype(np.uint8))
    for _ in range(k):
        im = base
        if rng.random() < 0.7:
            im = im.filter(ImageFilter.GaussianBlur(radius=float(rng.uniform(0.4, 1.6))))
        a = np.asarray(im).astype(np.float32)
        sy, sx = int(rng.integers(-2, 3)), int(rng.integers(-1, 2))
        a = np.roll(a, sy, axis=0); a = np.roll(a, sx, axis=1)
        a = a * float(rng.uniform(0.75, 1.25)) + float(rng.uniform(-15, 15))
        a = a + rng.normal(0, float(rng.uniform(2, 18)), a.shape)
        yield np.clip(a, 0, 255)


tmpl = defaultdict(list)
for clip in R.discover():
    cal, _ = D._calibrate(clip)
    if cal is None:
        continue
    H._ensure_mats(cal)
    for d, tmpls in cal.digits.items():
        for t in tmpls:
            a = np.asarray(t).astype(np.float32)
            a = a - a.min(); mx = a.max()
            if mx > 1e-6:
                a = a / mx * 255.0
            if a.shape != (GH, GW):
                a = np.asarray(Image.fromarray(a.astype(np.uint8)).resize((GW, GH)))
            tmpl[d].append(a.astype(np.float32))
for d, imgs in tmpl.items():
    for img in imgs:
        for aug in augment(img, 40):
            v = norm_img(aug)
            if v is not None:
                Xtr.append(v); ytr.append(int(d))

Xtr = np.stack(Xtr).astype(np.float32)[:, None, :, :]
ytr = np.array(ytr)
Xva_a = np.stack(Xva).astype(np.float32)[:, None, :, :]
yva = np.array(yva)
print(f"train total (real+aug)={len(Xtr)}", flush=True)


class DigitCNN(nn.Module):
    def __init__(self):
        super().__init__()
        self.c = nn.Sequential(
            nn.Conv2d(1, 16, 3, padding=1), nn.BatchNorm2d(16), nn.ReLU(True),
            nn.MaxPool2d(2),                                  # 15x10
            nn.Conv2d(16, 32, 3, padding=1), nn.BatchNorm2d(32), nn.ReLU(True),
            nn.MaxPool2d(2),                                  # 7x5
            nn.Conv2d(32, 48, 3, padding=1), nn.BatchNorm2d(48), nn.ReLU(True),
            nn.AdaptiveAvgPool2d(1),
        )
        self.fc = nn.Linear(48, 10)

    def forward(self, x):
        return self.fc(self.c(x).flatten(1))


dev = torch.device("cpu")
net = DigitCNN().to(dev)
opt = torch.optim.Adam(net.parameters(), lr=2e-3)
lossf = nn.CrossEntropyLoss()
Xtr_t = torch.from_numpy(Xtr); ytr_t = torch.from_numpy(ytr)
Xva_t = torch.from_numpy(Xva_a)


def val_acc():
    net.eval(); correct = defaultdict(lambda: [0, 0]); tot = 0; cor = 0
    with torch.no_grad():
        for s in range(0, len(Xva_t), 512):
            p = net(Xva_t[s:s+512]).argmax(1).numpy()
            for pi, ti, clip in zip(p, yva[s:s+512], Mva[s:s+512]):
                tot += 1; cor += int(pi == ti)
                correct[clip][1] += 1; correct[clip][0] += int(pi == ti)
    return cor / tot, correct


EPOCHS = int(os.environ.get("CNN_EPOCHS", "14"))
N = len(Xtr_t)
for ep in range(EPOCHS):
    net.train(); idx = torch.randperm(N); tot = 0.0; nb = 0
    for s in range(0, N, 256):
        sl = idx[s:s+256]
        out = net(Xtr_t[sl]); loss = lossf(out, ytr_t[sl])
        opt.zero_grad(); loss.backward(); opt.step()
        tot += float(loss); nb += 1
    if ep % 3 == 2 or ep == EPOCHS - 1:
        acc, _ = val_acc()
        print(f"ep{ep:2d} loss={tot/nb:.3f} val_acc={100*acc:.2f}%", flush=True)

acc, per = val_acc()
print(f"\nCNN held-out digit accuracy: {100*acc:.2f}%  (MLP baseline ~97.6%)")
print("per-clip:")
for clip in sorted(per):
    c, n = per[clip]
    print(f"  {clip}: {c}/{n} = {100*c/n:.2f}%")
