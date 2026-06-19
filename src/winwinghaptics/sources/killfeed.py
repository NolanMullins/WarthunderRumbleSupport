"""War Thunder kill-feed classification (pure logic).

Given a raw kill-feed line and the player's callsign, decide the outcome: KILL / DEATH / HIT /
None. This is a faithful, side-effect-free extraction of the original handle_damage() decision
logic so it can be unit-tested; the worker keeps the effect-firing + logging.

Matching rules (unchanged from the original):
  * callsign -> alphanumeric tokens of length >= 3 (a squadron prefix like "=GRIND= DEERSLUG"
    still matches when the user typed just "DEERSLUG"); if none, the whole lowercased callsign.
  * a line segment "is me" if any token is a substring of it (case-insensitive).
  * kill verbs split attacker (left) / victim (right): me=attacker -> KILL, me=victim -> DEATH;
    a kill verb is decisive (no further checks) even if neither side is me.
  * crash terms with no attacker verb and me present -> DEATH.
  * hit verbs (checked only after kill/crash) with me=victim -> HIT.
"""
import re
from typing import Optional

from ..events import EventType

KILL_VERBS = (" destroyed ", " shot down ", " has shot down ", " wrecked ",
              " set afire ", " severely damaged ", " has destroyed ")
CRASH_TERMS = ("has crashed", "has been wrecked", "wasted", "crashed")
HIT_VERBS = (" hit ", " damaged ", " has damaged ", " set on fire ")


def callsign_tokens(callsign):
    """Tokenize a callsign into the alphanumeric fragments used for matching."""
    cs_raw = (callsign or "").strip().lower()
    if not cs_raw:
        return []
    toks = [t for t in re.findall(r"[a-z0-9]+", cs_raw) if len(t) >= 3]
    return toks or [cs_raw]


def classify(msg, callsign) -> Optional[EventType]:
    """Classify a kill-feed line for `callsign`. Returns EventType.KILL/DEATH/HIT or None.

    Returns None when there is no message, no callsign, or the line doesn't concern the player.
    """
    if not msg:
        return None
    tokens = callsign_tokens(callsign)
    if not tokens:
        return None

    def is_me(segment):
        seg = segment.lower()
        return any(t in seg for t in tokens)

    low = msg.lower()

    for verb in KILL_VERBS:
        if verb in low:
            attacker, victim = low.split(verb, 1)
            if is_me(attacker):
                return EventType.KILL
            if is_me(victim):
                return EventType.DEATH
            return None                       # a kill verb is decisive

    if any(t in low for t in CRASH_TERMS) and is_me(low):
        return EventType.DEATH

    for verb in HIT_VERBS:
        if verb in low:
            attacker, victim = low.split(verb, 1)
            if is_me(victim):
                return EventType.HIT
            return None

    return None
