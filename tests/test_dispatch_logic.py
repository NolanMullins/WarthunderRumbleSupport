"""Unit tests for the kill-feed classifier and the HUD event->effect dispatch planner.

These exercise the previously-untestable decision logic that was buried in hud_worker /
handle_damage closures. Pure functions -> fast, deterministic, no hardware/game needed.
Run: pytest tests/
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from winwinghaptics.sources import killfeed          # noqa: E402
from winwinghaptics.effects import dispatch          # noqa: E402
from winwinghaptics.events import EventType          # noqa: E402


# ----------------------------------------------------------------------------------------
# kill-feed classifier
# ----------------------------------------------------------------------------------------
CS = "DeerSlug"

def test_kill_when_i_am_attacker():
    assert killfeed.classify("DeerSlug destroyed EnemyGuy", CS) == EventType.KILL

def test_death_when_i_am_victim():
    assert killfeed.classify("EnemyGuy destroyed DeerSlug", CS) == EventType.DEATH

def test_hit_when_i_am_victim_of_mild_verb():
    assert killfeed.classify("EnemyGuy damaged DeerSlug", CS) == EventType.HIT

def test_hit_not_fired_when_i_am_attacker():
    # I damaged someone else -> not my "took a hit"
    assert killfeed.classify("DeerSlug damaged EnemyGuy", CS) is None

def test_crash_is_death_when_me():
    assert killfeed.classify("DeerSlug has crashed", CS) == EventType.DEATH

def test_crash_not_me_is_none():
    assert killfeed.classify("SomeoneElse has crashed", CS) is None

def test_kill_verb_decisive_when_neither_side_me():
    # a kill verb matched but I'm not in it -> None, and must NOT fall through to hit verbs
    assert killfeed.classify("EnemyA destroyed EnemyB", CS) is None

def test_no_callsign_returns_none():
    assert killfeed.classify("DeerSlug destroyed EnemyGuy", "") is None
    assert killfeed.classify("DeerSlug destroyed EnemyGuy", None) is None

def test_empty_message_returns_none():
    assert killfeed.classify("", CS) is None

def test_case_insensitive():
    assert killfeed.classify("ENEMY DESTROYED DEERSLUG", CS) == EventType.DEATH

def test_squadron_prefix_still_matches():
    # user typed just "DEERSLUG"; the feed has a squadron tag -> still matches via tokens
    assert killfeed.classify("=GRIND= DeerSlug shot down EnemyGuy", "DEERSLUG") == EventType.KILL

def test_short_callsign_token_fallback():
    # callsign with no >=3 alnum token falls back to the whole string
    assert killfeed.callsign_tokens("ab") == ["ab"]
    assert killfeed.callsign_tokens("=GR= DeerSlug") == ["deerslug"]

def test_shot_down_kill():
    assert killfeed.classify("DeerSlug shot down Bandit", CS) == EventType.KILL

def test_severely_damaged_is_kill_path_not_hit():
    # "severely damaged" is in KILL_VERBS -> if I'm the victim it's a DEATH, not a HIT
    assert killfeed.classify("Enemy severely damaged DeerSlug", CS) == EventType.DEATH


# ----------------------------------------------------------------------------------------
# dispatch planner
# ----------------------------------------------------------------------------------------
def _ev(wp, effect, kind, old, new):
    return (wp, effect, kind, old - new, old, new)

def test_discrete_fires_named_effect():
    p = dispatch.plan([_ev("AAM", "missile", "discrete", 4, 3)], now=100.0, last_counter_knock=0.0)
    assert p.actions == [("fire_effect", "missile")]
    assert p.dispatched[0]["effect"] == "missile"
    assert p.logs == ["HUD AAM 4->3  →  missile"]

def test_rapid_records_but_does_not_fire():
    p = dispatch.plan([_ev("CNN", "gun", "rapid", 100, 98)], now=100.0, last_counter_knock=0.0)
    assert p.actions == []                       # gun is sustained separately, not per-event
    assert p.dispatched[0]["effect"] == "gun_active"

def test_counter_fires_flare_when_not_throttled():
    p = dispatch.plan([_ev("FLR", "flare", "counter", 60, 58)], now=100.0, last_counter_knock=0.0)
    assert p.actions == [("flare",)]
    assert p.dispatched[0]["effect"] == "flare"
    assert p.last_counter_knock == 100.0

def test_counter_throttled_within_interval():
    # a second knock 0.1s after the last is throttled (interval 0.30)
    p = dispatch.plan([_ev("FLR", "flare", "counter", 58, 56)], now=100.1, last_counter_knock=100.0)
    assert p.actions == []
    assert p.dispatched[0]["effect"] == "flare_throttled"
    assert p.last_counter_knock == 100.0          # unchanged

def test_counter_fires_again_after_interval():
    p = dispatch.plan([_ev("FLR", "flare", "counter", 56, 54)], now=100.4, last_counter_knock=100.0)
    assert p.actions == [("flare",)]
    assert p.last_counter_knock == 100.4

def test_multiple_events_batch():
    evs = [_ev("AAM", "missile", "discrete", 4, 3),
           _ev("RKT", "rocket", "discrete", 20, 18),
           _ev("CNN", "gun", "rapid", 100, 99)]
    p = dispatch.plan(evs, now=100.0, last_counter_knock=0.0)
    assert p.actions == [("fire_effect", "missile"), ("fire_effect", "rocket")]
    assert len(p.dispatched) == 3
    assert len(p.logs) == 3

def test_empty_events():
    p = dispatch.plan([], now=100.0, last_counter_knock=50.0)
    assert p.actions == [] and p.dispatched == [] and p.logs == []
    assert p.last_counter_knock == 50.0          # carried through unchanged
