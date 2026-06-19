"""pytest wrapper for the CALIBRATION-QUALITY track.

Asserts the real auto-calibration does not regress vs the committed baseline: rows-learned
must not drop, fail-rate must not rise, and no NEW systematically-missed rows. Runs the same
logic as tools/calib_report.py (tests/lib/calib_quality). Run: `pytest tests/`.
"""
import os
import sys
import json
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
from lib import recordings as R          # noqa: E402
from lib import groundtruth as G         # noqa: E402
from lib import calib_quality as C       # noqa: E402

BASELINE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "calib_baseline.json")


def _verified():
    out = []
    for clip in R.discover():
        if not G.has_gt(clip.key):
            continue
        gt = G.load(clip.key, len(clip.png_paths()))
        if not gt.unverified:
            out.append((clip, gt))
    return out


_CLIPS = _verified()
_IDS = [c.key for c, _ in _CLIPS]


def _baseline():
    if os.path.exists(BASELINE):
        with open(BASELINE, encoding="utf-8") as f:
            return json.load(f)
    return None


@pytest.mark.skipif(not _CLIPS, reason="no verified recordings present")
@pytest.mark.parametrize("clip,gt", _CLIPS, ids=_IDS)
def test_calibration_rows_no_regression(clip, gt):
    base = _baseline()
    if base is None:
        pytest.skip("no calibration baseline yet")
    exp = base.get("per_recording", {}).get(clip.key)
    if exp is None:
        pytest.skip(f"{clip.key} not in calibration baseline")
    sc = C.score_calibration(clip, gt)
    # rows learned must not drop
    if exp["mean_rows_frac"] is not None and sc["mean_rows_frac"] is not None:
        assert sc["mean_rows_frac"] >= exp["mean_rows_frac"] - 1e-9, (
            f"{clip.key} calibration rows_learned regressed: "
            f"{exp['mean_rows_frac']*100:.1f}% -> {sc['mean_rows_frac']*100:.1f}%")
    # no new systematically-missed rows
    new_missed = set(sc["always_missed"]) - set(exp.get("always_missed", []))
    assert not new_missed, f"{clip.key} newly never-calibrated rows: {sorted(new_missed)}"
    # fail rate must not rise
    if exp["fail_rate"] is not None and sc["fail_rate"] is not None:
        assert sc["fail_rate"] <= exp["fail_rate"] + 1e-9, (
            f"{clip.key} calibration fail_rate rose: "
            f"{exp['fail_rate']*100:.1f}% -> {sc['fail_rate']*100:.1f}%")
