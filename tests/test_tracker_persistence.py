"""Unit tests for TemporalTracker discrete-fire persistence (DISCRETE_MIN).

Background: a drifting bright background (cloud / snow flying past behind the HUD) produces
SHORT correlated misread bursts -- a discrete count momentarily reads a lower value for a frame
or two and then RECOVERS. The old 2-frame discrete onset fired a phantom buzz on such dips.
Requiring THREE consecutive sub-baseline reads rejects a 2-frame transient while still firing
real launches and salvos (one frame later). These tests pin that contract directly on the
tracker (no recordings needed), so a future tweak that re-introduces the phantom is caught.
"""
import os
import sys

sys.path.insert(0, os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "src")))
import winwinghaptics.detection.hud_detect as H   # noqa: E402


def _drive(seq, wp="RKT", cls="discrete"):
    """Feed a sequence of per-frame reads (None = no read) to a fresh tracker; return the
    list of (frame_index, old, new) for every discrete fire it emits."""
    tk = H.TemporalTracker(classes={wp: cls})
    fires = []
    for i, v in enumerate(seq):
        reads = {wp: (v, 0.9)} if v is not None else {}
        for ev in tk.update(reads):
            fires.append((i, ev[4], ev[5]))
    return fires


def test_transient_dip_does_not_fire():
    # 48 stable, then a 2-frame dip to 45,40 that RECOVERS to 48 -- the cloud-misread signature.
    assert _drive([48] * 6 + [45, 40, 48, 48, 48]) == []


def test_two_frame_hold_then_recover_does_not_fire():
    # Even a 2-frame HOLD at a lower value that bounces back must not fire (needs a third frame).
    assert _drive([48] * 6 + [46, 46, 48, 48]) == []


def test_sustained_drop_fires_once():
    fires = _drive([48] * 6 + [47, 47, 47, 47])
    assert len(fires) == 1
    assert fires[0][1:] == (48, 47)


def test_single_round_small_count_fires():
    # A single missile launch on a small count (4 -> 3) must still register.
    fires = _drive([4] * 6 + [3, 3, 3, 3], wp="AAM")
    assert len(fires) == 1
    assert fires[0][1:] == (4, 3)


def test_salvo_fires_every_round():
    # 48 -> 44 is four rounds; each must produce its own fire (overlaps merge in the effect).
    fires = _drive([48] * 6 + [47, 46, 45, 44, 44, 44])
    assert len(fires) == 4
    assert [f[1:] for f in fires] == [(48, 47), (47, 46), (46, 45), (45, 44)]


def test_two_frame_min_would_fire_the_phantom():
    """Guard: with DISCRETE_MIN=2 (the old behaviour) the transient dip DOES phantom-fire --
    proving the 3-frame default is what suppresses it, not some other change."""
    old = H.TemporalTracker.DISCRETE_MIN
    H.TemporalTracker.DISCRETE_MIN = 2
    try:
        assert _drive([48] * 6 + [45, 40, 48, 48, 48]) != []
    finally:
        H.TemporalTracker.DISCRETE_MIN = old
