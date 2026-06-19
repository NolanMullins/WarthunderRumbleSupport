"""pytest wrapper for the misread (faithful-detector) track.

Runs only on recordings that carry a calib.json sidecar (faithful tier). Until a clip is
recorded with the current build, this is skipped. Asserts misread_rate does not regress vs
the committed baseline. Run: `pytest tests/`.
"""
import os
import sys
import json
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
from lib import recordings as R       # noqa: E402
from lib import groundtruth as G      # noqa: E402
from lib import metrics as M          # noqa: E402

BASELINE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "baseline_metrics.json")


def _faithful_clips():
    out = []
    for clip in R.discover():
        if not G.has_gt(clip.key) or not clip.has_calib:
            continue
        gt = G.load(clip.key, len(clip.png_paths()))
        if not gt.unverified:
            out.append((clip, gt))
    return out


_CLIPS = _faithful_clips()
_IDS = [c.key for c, _ in _CLIPS]


@pytest.mark.skipif(not _CLIPS, reason="no faithful (calib.json) recordings present yet")
@pytest.mark.parametrize("clip,gt", _CLIPS, ids=_IDS)
def test_misread_no_regression(clip, gt):
    base = None
    if os.path.exists(BASELINE):
        with open(BASELINE, encoding="utf-8") as f:
            base = json.load(f)
    res = M.score_misreads(clip, gt)
    if base is None:
        pytest.skip("no baseline snapshot yet")
    expected = base.get("per_recording", {}).get(clip.key, {}).get("misread_rate")
    if expected is None:
        pytest.skip(f"{clip.key} has no misread baseline")
    assert res["misread_rate"] <= expected + 1e-12, (
        f"{clip.key} misread_rate regressed: "
        f"{expected*100:.3f}% -> {res['misread_rate']*100:.3f}%  "
        f"(first fails: {res['_fail_list'][:8]})")
