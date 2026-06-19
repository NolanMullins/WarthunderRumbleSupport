"""Ground-truth model.

Dense GT format (per clip, per weapon) = a list of STABLE segments [start, end, value]
(frame indices inclusive). Between two consecutive stable segments lies a TRANSITION zone
where the value is changing (a real fire, a reload, or a brief unreadable patch). Semantics:

  * Inside a stable segment  -> the true value is exactly `value`; NO fire is happening.
  * In a transition zone going DOWN (next value < prev value) -> a real fire occurred
    somewhere in the zone; an event anywhere in the zone (+/- the association window) is a
    correct HIT, and the absence of one is a MISS.
  * In a transition zone going UP (reload/rearm) -> no fire; the tracker never fires upward,
    so these are simply not scored as fires.

This single representation drives BOTH tracks:
  - Track 1 (misreads): the per-frame true value inside stable segments (transition frames
    are excluded / bracket-tolerant, since the exact value mid-change is ambiguous).
  - Track 2 (event failures): the silent frames (stable) vs the fire-onset zones (down
    transitions).
"""
import os
import json

GT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ground_truth")


def _safe(key):
    return key.replace("/", "__").replace("\\", "__")


def gt_path(clip_key):
    return os.path.join(GT_DIR, _safe(clip_key) + ".json")


def has_gt(clip_key):
    return os.path.exists(gt_path(clip_key))


class GroundTruth:
    def __init__(self, clip_key, data, n_frames):
        self.clip_key = clip_key
        self.n_frames = n_frames
        self.unverified = bool(data.get("_unverified", False))
        # Excluded frame ranges [[start,end],...] (inclusive): frames the capture was polluted
        # (an overlay/another program covering the HUD region) or otherwise unusable. These are
        # skipped by EVERY metric so corrupt frames don't count as detector failures. The app
        # captures a screen RECTANGLE (desktop BitBlt), not the WT window, so an overlay on top
        # genuinely corrupts those frames -- excluding them keeps the test measuring the
        # detector, not a transient occlusion.
        self.exclude = [tuple(r) for r in data.get("_exclude", [])]
        self.segments = {                # weapon -> sorted list of [start, end, value]
            w: sorted([list(s) for s in segs], key=lambda s: s[0])
            for w, segs in data.items() if not w.startswith("_")
        }
        # Row-presence spans: per weapon, the frame range(s) the row is ON THE HUD (and so
        # SHOULD be readable). This is the basis of the missed-row metric and is independent
        # of the exact values. If GT supplies an explicit "_present" map it is used; otherwise
        # we derive it as [first_segment_start, last_segment_end] (a weapon is continuously
        # present across its value plateaus + the transitions between them).
        explicit = data.get("_present")
        self.present = {}
        for w, segs in self.segments.items():
            if explicit and w in explicit:
                self.present[w] = [tuple(s) for s in explicit[w]]
            elif segs:
                self.present[w] = [(segs[0][0], segs[-1][1])]
            else:
                self.present[w] = []

    @property
    def weapons(self):
        return list(self.segments)

    def is_excluded(self, frame):
        """True if `frame` is in an excluded (polluted/unusable) range -> skip in all metrics."""
        return any(s <= frame <= e for s, e in self.exclude)

    def is_present(self, wp, frame):
        """True if weapon `wp`'s row is on the HUD at `frame` (should be readable)."""
        for s, e in self.present.get(wp, []):
            if s <= frame <= e:
                return True
        return False

    def present_weapons_at(self, frame):
        return [w for w in self.weapons if self.is_present(w, frame)]

    def present_cells(self):
        """Total (frame x weapon) cells where a row is present -> denominator for missed-row."""
        total = 0
        for w in self.weapons:
            for s, e in self.present.get(w, []):
                total += min(self.n_frames - 1, e) - max(0, s) + 1
        return total

    def value_at(self, wp, frame):
        """True value inside a stable segment, else None (transition / unknown)."""
        for s, e, v in self.segments.get(wp, []):
            if s <= frame <= e:
                return v
        return None

    def is_stable(self, wp, frame):
        return self.value_at(wp, frame) is not None

    def transition_bracket(self, wp, frame):
        """If `frame` is in a transition zone for `wp`, return (low, high) bracket of the two
        surrounding stable values; else None. Used for bracket-tolerant misread scoring."""
        segs = self.segments.get(wp, [])
        for i in range(len(segs) - 1):
            e_prev = segs[i][1]
            s_next = segs[i + 1][0]
            if e_prev < frame < s_next:
                lo = min(segs[i][2], segs[i + 1][2])
                hi = max(segs[i][2], segs[i + 1][2])
                return (lo, hi)
        return None

    def fire_zones(self, wp):
        """List of DOWNWARD transition zones (real fire episodes) for `wp`:
        (zone_start, zone_end, old_value, new_value), frame indices inclusive.

        Two segment layouts are supported:
          * GAPPED  — a transition zone exists between plateaus (sparse GT): the fire happened
            somewhere in (e_prev+1 .. s_next-1).
          * ADJACENT — segments touch (dense, frame-by-frame GT from human marks): the step is
            exactly at s_next, so the zone collapses to that single onset frame.
        """
        zones = []
        segs = self.segments.get(wp, [])
        for i in range(len(segs) - 1):
            old = segs[i][2]
            new = segs[i + 1][2]
            if new < old:
                zs = segs[i][1] + 1
                ze = segs[i + 1][0] - 1
                if ze < zs:                       # adjacent (no gap) -> onset at the boundary
                    zs = ze = segs[i + 1][0]
                zones.append((zs, ze, old, new))
        return zones

    def silent_frames(self, wp):
        """Set of frames where `wp` is on a stable plateau (no fire expected)."""
        out = set()
        for s, e, _v in self.segments.get(wp, []):
            out.update(range(max(0, s), min(self.n_frames, e + 1)))
        return out


def load(clip_key, n_frames):
    with open(gt_path(clip_key), encoding="utf-8-sig") as f:
        data = json.load(f)
    return GroundTruth(clip_key, data, n_frames)
