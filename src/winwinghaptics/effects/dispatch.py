"""HUD event -> effect dispatch planning (pure logic).

Given the fire events from the detector for one poll, decide which haptic actions to take and
what dispatch records to log/record. Side-effect-free so it can be unit-tested; the worker
performs the returned actions (effects.flare()/fire_effect(), gun_active) and logging.

Faithful extraction of the original hud_worker dispatch block:
  * kind == "rapid"  (gun): NOT fired per-event here -- a sustained rumble is driven separately
    by is_firing(); we only record a "gun_active" dispatch entry.
  * kind == "counter" (flares/chaff): fire one flare knock, THROTTLED so a rapid dump becomes a
    couple of knocks (>= COUNTER_KNOCK_INTERVAL seconds apart); throttled hits are recorded as
    "flare_throttled" with no action.
  * otherwise (discrete: missile/rocket/bomb): fire the named effect.
Each event also yields a log line "HUD wp old->new  ->  effect".
"""
from collections import namedtuple

COUNTER_KNOCK_INTERVAL = 0.30   # seconds between flare/chaff knocks (dump -> a few knocks)

# action: ("flare",) | ("fire_effect", effect_name)  -- what the worker should call
# dispatch: the dict recorded into telemetry / returned to the caller
# log: the activity-log line for this event
Plan = namedtuple("Plan", ["actions", "dispatched", "logs", "last_counter_knock"])


def plan(events, now, last_counter_knock, knock_interval=COUNTER_KNOCK_INTERVAL):
    """Plan effect actions for a batch of detector events.

    events: iterable of (weapon, effect, kind, delta, old, new).
    now: current monotonic-ish timestamp (time.time()).
    last_counter_knock: timestamp of the previous flare knock (for throttling).
    Returns Plan(actions, dispatched, logs, last_counter_knock).
    """
    actions = []
    dispatched = []
    logs = []
    for wp, effect, kind, delta, old, new in events:
        if kind == "rapid":
            dispatched.append({"weapon": wp, "effect": "gun_active", "kind": kind,
                               "old": old, "new": new, "delta": delta})
        elif kind == "counter":
            if now - last_counter_knock >= knock_interval:
                actions.append(("flare",))
                last_counter_knock = now
                dispatched.append({"weapon": wp, "effect": "flare", "kind": kind,
                                   "old": old, "new": new, "delta": delta})
            else:
                dispatched.append({"weapon": wp, "effect": "flare_throttled",
                                   "kind": kind, "old": old, "new": new, "delta": delta})
        else:
            actions.append(("fire_effect", effect))
            dispatched.append({"weapon": wp, "effect": effect, "kind": kind,
                               "old": old, "new": new, "delta": delta})
        logs.append(f"HUD {wp} {old}->{new}  →  {effect}")
    return Plan(actions, dispatched, logs, last_counter_knock)
