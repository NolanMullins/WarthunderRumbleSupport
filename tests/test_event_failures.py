"""pytest wrapper for the A/B regression gate.

These tests are thin adapters over tools/ab_report.py's logic (tests/lib). The same gate runs
WITHOUT pytest via `python tools/ab_report.py`; pytest just gives nicer per-recording output
and CI integration. Run: `pytest tests/`.
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


def _baseline():
    if os.path.exists(BASELINE):
        with open(BASELINE, encoding="utf-8") as f:
            return json.load(f)
    return None


def _verified_event_clips():
    out = []
    for clip in R.discover():
        if not G.has_gt(clip.key) or not clip.has_frozen_reads:
            continue
        gt = G.load(clip.key, len(clip.saved_reads()))
        if not gt.unverified:
            out.append((clip, gt))
    return out


_CLIPS = _verified_event_clips()
_IDS = [c.key for c, _ in _CLIPS]


@pytest.mark.skipif(not _CLIPS, reason="no verified recordings with frozen reads present")
@pytest.mark.parametrize("clip,gt", _CLIPS, ids=_IDS)
def test_event_failure_no_regression(clip, gt):
    """Per recording: event_failure_rate must not exceed the committed baseline."""
    base = _baseline()
    res = M.score_events(clip, gt)
    if base is None:
        pytest.skip("no baseline snapshot yet (run tools/ab_report.py --update-baseline)")
    expected = base.get("per_recording", {}).get(clip.key, {}).get("event_failure_rate")
    if expected is None:
        pytest.skip(f"{clip.key} not in baseline")
    assert res["failure_rate"] <= expected + 1e-12, (
        f"{clip.key} event_failure_rate regressed: "
        f"{expected*100:.3f}% -> {res['failure_rate']*100:.3f}%  "
        f"(false={res['false_fires']} missed={res['missed_fires']}; "
        f"new false fires={res['_false_fire_list']}; new misses={res['_missed_list']})")


@pytest.mark.skipif(not _CLIPS, reason="no verified recordings present")
def test_aggregate_event_failure_no_regression():
    """Aggregate event_failure_rate across all verified clips must not regress."""
    base = _baseline()
    if base is None:
        pytest.skip("no baseline snapshot yet")
    bexp = base.get("aggregate", {}).get("event_failure_rate")
    if bexp is None:
        pytest.skip("baseline has no aggregate event rate")
    failed = frames = 0
    for clip, gt in _CLIPS:
        r = M.score_events(clip, gt)
        failed += r["failed_frames"]; frames += r["n_frames"]
    rate = (failed / frames) if frames else 0.0
    assert rate <= bexp + 1e-12, (
        f"aggregate event_failure_rate regressed: {bexp*100:.3f}% -> {rate*100:.3f}%")
