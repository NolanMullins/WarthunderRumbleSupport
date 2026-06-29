"""Unit tests for TemporalTracker single-shot RELEASE-via-RELOAD-TIMER firing (BOMB_RELOAD_FIRE).

Real-match symptom this fixes: a single-shot weapon (a 1-bomb loadout, or the last round of a
small rocket/missile loadout) fires WITHOUT a visible count decrement. War Thunder replaces the
count "1" with a reload countdown "m:ss" and dims the row, so the count never steps 1 -> 0 and the
decrement-based fire logic is structurally blind to the release. The detector instead flags the
row as `reloading` (a colon-bearing timer in the count cell -- see _detect_reload_timer), and the
tracker turns the armed(1) -> reloading transition into exactly one fire.

Phantom guards under test: only fires from a CLEAN armed-at-1 state (rejects match-start
weapon-select flicker that bounces 1/83/19/...), never fires at a baseline > 1 (you cannot reload
with several rounds still loaded -- that 'timer' is a false colon hit on suffix/lock text), fires
exactly ONCE per release, and re-arms silently when the count returns.
"""
import os
import sys

sys.path.insert(0, os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "src")))
import winwinghaptics.detection.hud_detect as H   # noqa: E402


def _drive(steps, wp="BMB"):
    """steps: list of (count_or_None, reloading_bool). Returns list of fire (old,new) for wp."""
    tk = H.TemporalTracker(classes={wp: "discrete"})
    fires = []
    for val, reloading in steps:
        reads = {wp: (val, 0.9)} if val is not None else {}
        rl = {wp} if reloading else set()
        for ev in tk.update(reads, reloading=rl):
            if ev[0] == wp:
                fires.append((ev[4], ev[5]))
    return fires, tk


def test_single_bomb_release_fires_once():
    # Armed cleanly at 1 (>=5 reads), then the count vanishes and a reload timer appears.
    steps = [(1, False)] * 8 + [(None, True)] * 6
    fires, _ = _drive(steps)
    assert fires == [(1, 0)]                        # exactly one release fire


def test_release_fires_when_count_vanishes_before_timer():
    # The count can blank a beat BEFORE the timer renders; the armed-at-1 latch bridges the gap.
    steps = [(1, False)] * 8 + [(None, False)] * 10 + [(None, True)] * 4
    fires, _ = _drive(steps)
    assert fires == [(1, 0)]


def test_no_fire_at_baseline_above_one():
    # A 'reloading' flag while several rounds remain (e.g. 4 missiles) is a false colon hit on the
    # row's suffix/lock-info, never a release. Must not fire.
    steps = [(4, False)] * 8 + [(None, True)] * 6
    fires, _ = _drive(steps, wp="AAM")
    assert fires == []


def test_no_fire_from_startup_flicker():
    # Match-start weapon-select bounces the count (1, then 83, 19, 66, ... garbage). That dirty
    # history is NOT a clean armed-at-1 state, so a colon hit during the chaos must not arm a
    # release (the f164-class phantom). We assert NO reload-release fire (old==1, new==0) occurs.
    seq = [1, 1, 1, 83, 19, 66, 19, 66, 19, 66, 19]
    steps = [(v, False) for v in seq] + [(None, True)] * 4
    fires, _ = _drive(steps)
    assert (1, 0) not in fires                      # no single-shot RELEASE fire from the flicker


def test_fires_exactly_once_across_long_reload():
    # The timer is visible for many frames (a ~60s reload); only ONE fire, not one per frame.
    steps = [(1, False)] * 8 + [(None, True)] * 40
    fires, _ = _drive(steps)
    assert fires == [(1, 0)]


def test_rearm_does_not_fire_and_can_fire_again():
    # After release, the count rearms to 1 (timer gone) silently; a SECOND drop fires again.
    steps = ([(1, False)] * 8 + [(None, True)] * 6          # drop 1 -> fire
             + [(1, False)] * 8 + [(None, True)] * 6)        # rearm + drop 2 -> fire
    fires, _ = _drive(steps)
    assert fires == [(1, 0), (1, 0)]


def test_disabled_flag_suppresses_release_fire():
    old = H.BOMB_RELOAD_FIRE
    H.BOMB_RELOAD_FIRE = False
    try:
        steps = [(1, False)] * 8 + [(None, True)] * 6
        fires, _ = _drive(steps)
        assert fires == []
    finally:
        H.BOMB_RELOAD_FIRE = old
