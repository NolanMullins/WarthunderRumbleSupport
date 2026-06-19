"""Diagnose WHY specific re-detected fire onsets are missed by the tracker.

Replays the tracker over the re-detected reads of a clip and, for a chosen weapon,
prints per-frame: raw read, median level, current baseline (conf), and which branch
of update() fired/vetoed. Lets us see exactly which veto eats a real burst.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests.lib import detect as D
from tests.lib import recordings as R
from tests.lib import groundtruth as G
import src.winwinghaptics.detection.hud_detect as H

CLIP = sys.argv[1] if len(sys.argv) > 1 else "hud_rec_20260618_140000"
WP = sys.argv[2] if len(sys.argv) > 2 else "CNN"
LO = int(sys.argv[3]) if len(sys.argv) > 3 else 248
HI = int(sys.argv[4]) if len(sys.argv) > 4 else 262

clip = next(c for c in R.discover() if c.key == CLIP)
det = D.redetect(clip)
reads = det["reads"]

cls = H.WEAPON_CLASS.get(WP, "discrete")
classes = {WP: cls}
tk = H.TemporalTracker(classes=classes)

print(f"clip={CLIP} weapon={WP} class={cls} frames={LO}..{HI}")
print("f    raw  level conf  -> result")
for i, rd in enumerate(reads):
    upd = {WP: (rd[WP], 0.9)} if WP in rd and rd[WP] is not None else {}
    before = tk.conf.get(WP)
    evts = tk.update(upd)
    after = tk.conf.get(WP)
    if LO <= i <= HI:
        raw = rd.get(WP)
        lvl = tk._level(WP)
        fired = [e for e in evts if e[0] == WP]
        tag = ""
        if fired:
            tag = f"FIRE {fired[0][4]}->{fired[0][5]}"
        elif before is not None and lvl is not None and lvl < before:
            # a drop that did NOT fire -> figure out which veto
            if cls in ("rapid", "counter") and tk._leading_digit_flip(before, lvl):
                tag = "veto:leading_digit_flip"
            elif cls in ("rapid", "counter") and tk._recovered(WP, before):
                tag = "veto:recovered"
            elif cls == "rapid" and (before - lvl) > tk.RAPID_MAX_STEP:
                tag = "veto:too_big"
            elif not tk._is_fire(cls, before, lvl):
                tag = "veto:is_fire(keep_frac)"
            else:
                tag = "drop-no-fire(resync?)"
        print(f"{i:<4} {str(raw):>4} {str(lvl):>5} {str(before):>4}  {tag}")
