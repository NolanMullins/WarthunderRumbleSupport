"""Unit tests for TemporalTracker discrete digit-ceiling clamping (DIGIT_BOUND / _bound_digits).

Real-match symptom this fixes: missiles "rarely fire" during a match. War Thunder renders an
adjacent number (a missile's lock-range / seeker readout) immediately right of the missile count,
in the same font and baseline. The left-to-right count reader segments it INTO the count, so a
1-digit "4" reads as "442" / "341". Without bounding, those inflated reads (a) corrupt the tracked
baseline upward (conf -> 341/442) and (b) make every subsequent real launch look like an
impossible drop, so the tracker goes deaf to missiles for the rest of the match.

The fix clamps a discrete count to a conservative per-weapon ceiling well above any real fighter
loadout. A read above the ceiling is digit-inflation; because the count is LEFT-ALIGNED at
count_x, the leading digit(s) are the true count, so we drop trailing digits until the value is
within the ceiling. The ceiling is STATELESS -- it can't be mis-seeded by an abnormal start and
never caps a genuine rearm (still <= ceiling).
"""
import os
import sys

sys.path.insert(0, os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "src")))
import winwinghaptics.detection.hud_detect as H   # noqa: E402


def _tk():
    return H.TemporalTracker(classes=dict(H.WEAPON_CLASS))


def test_inflation_clamped_to_leading_count():
    tk = _tk()
    assert tk._bound_digits("AAM", 442) == 4      # "4"+"42" adjacent -> 4
    assert tk._bound_digits("AAM", 341) == 3      # post-launch "3"+"41" -> 3
    assert tk._bound_digits("AAM", 44) == 4
    assert tk._bound_digits("AAM", 522) == 5
    assert tk._bound_digits("RKT", 482) == 48     # "48"+"2" -> 48 (RKT ceiling 200)
    assert tk._bound_digits("RKT", 4824) == 48


def test_plausible_values_pass_through():
    tk = _tk()
    assert tk._bound_digits("AAM", 4) == 4
    assert tk._bound_digits("AAM", 12) == 12      # a legit / rearm loadout (<= ceiling) survives
    assert tk._bound_digits("AAM", 24) == 24
    assert tk._bound_digits("RKT", 48) == 48
    assert tk._bound_digits("RKT", 150) == 150
    assert tk._bound_digits("AAM", None) is None


def test_counter_and_gun_not_bounded():
    # Counters (flares/chaff) and the gun have no ceiling -> never clamped (large/variable counts).
    tk = _tk()
    assert tk._bound_digits("FLR", 240) == 240
    assert tk._bound_digits("CHFF", 999) == 999
    assert tk._bound_digits("CNN", 842) == 842


def _drive(seq, wp="AAM", cls="discrete", bound=True):
    old = H.TemporalTracker.DIGIT_BOUND
    H.TemporalTracker.DIGIT_BOUND = bound
    try:
        tk = H.TemporalTracker(classes={wp: cls})
        fires = []
        for v in seq:
            for ev in tk.update({wp: (v, 0.9)} if v is not None else {}):
                fires.append((ev[4], ev[5]))
        return fires, tk.conf.get(wp)
    finally:
        H.TemporalTracker.DIGIT_BOUND = old


def test_missile_launch_recovered_through_inflation():
    # Seed at 4, the adjacent element inflates the read to 442, then the real launch makes the
    # count 3 (read 341). With clamping the launch fires cleanly 4->3, baseline never corrupts.
    fires, conf = _drive([4] * 6 + [442] * 8 + [341] * 4, bound=True)
    assert fires == [(4, 3)]
    assert conf == 3


def test_without_bounding_baseline_corrupts():
    # Guard: with clamping OFF the same stream corrupts the baseline and mislabels the launch.
    fires, conf = _drive([4] * 6 + [442] * 8 + [341] * 4, bound=False)
    assert conf != 3
    assert fires != [(4, 3)]


def test_clean_stream_unaffected():
    fires, conf = _drive([4] * 6 + [3, 3, 3, 3], bound=True)
    assert fires == [(4, 3)]
    assert conf == 3


def test_seed_from_inflation_self_corrects():
    # Reviewer edge case: the app starts mid-match while the adjacent number is already present,
    # so the FIRST reads are inflated (442). The stateless ceiling clamps them at ingestion, so
    # the baseline seeds at the true 4 -- never locking in the inflated 442/341.
    fires, conf = _drive([442] * 8 + [341] * 6 + [3] * 6, bound=True)
    assert conf == 3                       # recovered (4 -> 3), not stuck at 341
    assert fires == [(4, 3)]


def test_wide_rearm_not_truncated():
    # Reviewer edge case: a genuine rearm to a wider count (a fuller loadout) is below the ceiling,
    # so it is NOT clamped and the baseline re-bases up to it. 8 rockets fired, rearm to 48.
    # (The reload path needs a few frames of persistence before it adopts the higher level.)
    fires, conf = _drive([8] * 6 + [48] * 14, wp="RKT", cls="discrete", bound=True)
    assert conf == 48                      # adopted the rearm, did not truncate 48 -> 4
