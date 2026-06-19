"""Recording discovery + loading.

A "recording clip" is a leaf folder containing f####.png frames + telemetry.jsonl, optionally
calib.json (faithful-detector tier). Clips nest one level (the recorder writes
<outer>/<inner>/), and one outer may hold multiple inner clips. We index by the inner-leaf
relative path (e.g. "hud_rec_..._153642/hud_rec_..._153552") so each capture is a distinct
clip, matching how the ground-truth files are keyed.
"""
import os
import glob
import json
import struct
import zlib

REC_ROOT = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "recordings"))


class Clip:
    def __init__(self, key, path):
        self.key = key            # stable id, e.g. "outer/inner"
        self.path = path          # absolute path to the leaf folder
        self._header = None

    # ---- lazy telemetry access ----
    def _read_jsonl(self):
        rows = []
        with open(os.path.join(self.path, "telemetry.jsonl"), encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
        return rows

    @property
    def header(self):
        if self._header is None:
            with open(os.path.join(self.path, "telemetry.jsonl"), encoding="utf-8") as f:
                self._header = json.loads(f.readline())
        return self._header

    @property
    def weapons(self):
        return list(self.header.get("weapons", []))

    def frames(self):
        """List of telemetry 'frame' records (in capture order)."""
        return [r for r in self._read_jsonl() if r.get("type") == "frame"]

    def saved_reads(self):
        """Per-frame frozen reads as captured live: list[dict wp->int] indexed by position.

        These are the EXACT reads the detector produced at record time -> deterministic input
        for the event-failure (tracker) track, independent of any later detector change.
        """
        out = []
        for r in self.frames():
            d = {}
            for wp, rr in (r.get("reads") or {}).items():
                if rr and rr.get("val") is not None:
                    d[wp] = int(rr["val"])
            out.append(d)
        return out

    @property
    def has_frozen_reads(self):
        """True if the telemetry stores per-frame reads (newer schema). The oldest clip used
        a different schema with empty 'reads' -> not usable for the frozen-read event track."""
        reads = self.saved_reads()
        if not reads:
            return False
        nonempty = sum(1 for d in reads if d)
        return nonempty >= 0.5 * len(reads)

    def dispatched(self):
        """Per-frame list of events that fired LIVE in-game (build that recorded the clip)."""
        out = []
        for r in self.frames():
            out.append(list(r.get("dispatched") or []))
        return out

    # ---- faithful-detector tier ----
    @property
    def has_calib(self):
        cf = os.path.join(self.path, "calib.json")
        return self.header.get("calib_file") and os.path.exists(cf)

    def load_calib(self):
        with open(os.path.join(self.path, "calib.json"), encoding="utf-8") as f:
            return json.load(f)

    def png_paths(self):
        return sorted(glob.glob(os.path.join(self.path, "f*.png")))

    def grays(self):
        return [load_png_gray(p) for p in self.png_paths()]


def discover():
    """Find all leaf clips under recordings/, keyed by relative path. Sorted by key."""
    clips = []
    if not os.path.isdir(REC_ROOT):
        return clips
    for tel in glob.glob(os.path.join(REC_ROOT, "**", "telemetry.jsonl"), recursive=True):
        path = os.path.dirname(tel)
        key = os.path.relpath(path, REC_ROOT).replace("\\", "/")
        clips.append(Clip(key, path))
    clips.sort(key=lambda c: c.key)
    return clips


def load_png_gray(p):
    """Decode an 8-bit grayscale PNG (as written by hud_detect.save_gray_png) to a 2D
    numpy float32 array. Standalone so the suite never depends on Pillow."""
    import numpy as np
    d = open(p, "rb").read()
    i = 8
    W = Hh = 0
    idat = b""
    while i < len(d):
        ln = struct.unpack(">I", d[i:i + 4])[0]
        typ = d[i + 4:i + 8]
        data = d[i + 8:i + 8 + ln]
        i += 12 + ln
        if typ == b"IHDR":
            W, Hh = struct.unpack(">II", data[:8])
        elif typ == b"IDAT":
            idat += data
        elif typ == b"IEND":
            break
    raw = zlib.decompress(idat)
    g = np.zeros((Hh, W), np.float32)
    st = 0
    for y in range(Hh):
        st += 1  # per-row filter byte (filter type 0 assumed, as written by save_gray_png)
        g[y] = np.frombuffer(raw[st:st + W], np.uint8)
        st += W
    return g
