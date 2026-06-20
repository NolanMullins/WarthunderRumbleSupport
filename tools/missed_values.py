"""Where do MISSED VALUES (detector returns None where a value is present) come from?
Per verified clip + weapon, count present-cells that read None, and characterize them:
  - is the None ISOLATED (1-2 frame blink, neighbours read fine) or a RUN (sustained blank)?
  - what value SHOULD it be (GT), and does the row read fine just before/after?
This tells us if missed values are brief cloud blips (tracker bridges them) or real blind spots.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from collections import Counter
from tests.lib import recordings as R
from tests.lib import detect as D
from tests.lib import groundtruth as G

tot_present = 0
tot_missed = 0
by_wp = Counter()
run_hist = Counter()      # run-length of consecutive None -> count of runs
clip_wp_runs = {}

for clip in R.discover():
    if not G.has_gt(clip.key):
        continue
    det = D.redetect(clip)
    reads = det["reads"]
    gt = G.load(clip.key, len(reads))
    if gt.unverified:
        continue
    tag = clip.key.split("/")[-1][-6:]
    for wp in gt.weapons:
        present = [i for i in range(len(reads))
                   if gt.is_present(wp, i) and not gt.is_excluded(i)]
        missed = [i for i in present if reads[i].get(wp) is None]
        tot_present += len(present)
        tot_missed += len(missed)
        by_wp[wp] += len(missed)
        # run-lengths of consecutive missed frames
        runs = []
        prev = None; cur = 0
        for i in missed:
            if prev is not None and i == prev + 1:
                cur += 1
            else:
                if cur:
                    runs.append(cur)
                cur = 1
            prev = i
        if cur:
            runs.append(cur)
        for r in runs:
            run_hist[r] += 1
        if missed:
            clip_wp_runs[(tag, wp)] = (len(missed), len(present), sorted(Counter(runs).items()))

print(f"TOTAL missed values: {tot_missed}/{tot_present} = {100.0*tot_missed/tot_present:.2f}%\n")
print("by weapon (missed count):")
for wp, c in by_wp.most_common():
    print(f"  {wp:5} {c}")
print("\nrun-length histogram (consecutive-None run -> how many such runs):")
for rl in sorted(run_hist):
    print(f"  {rl:3} frames: {run_hist[rl]} runs")
print("\nworst clip/weapon (missed/present, run distribution):")
for (tag, wp), (m, p, runs) in sorted(clip_wp_runs.items(), key=lambda kv: -kv[1][0])[:10]:
    print(f"  {tag} {wp:5} {m}/{p}  runs={runs}")
