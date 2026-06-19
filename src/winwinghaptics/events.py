"""Event model — the common currency between signal sources and the effects router.

A SignalSource (War Thunder telemetry, HUD detector, ...) observes the game and emits Events.
The effects router maps an EventType to a haptic effect. Keeping this as a small, explicit
vocabulary makes "add a new tracked event" a matter of adding an EventType + a router binding
rather than threading new ad-hoc calls through the app.

The string values intentionally match the effect names the detector already emits
(hud_detect.WEAPON_EFFECT: missile/rocket/bomb/gun/flare) so existing dispatch keeps working.
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Dict, Any


class EventType(str, Enum):
    # weapon fires (from HUD ammo-counter decrements or the gun trigger)
    GUN = "gun"
    MISSILE = "missile"
    ROCKET = "rocket"
    BOMB = "bomb"
    FLARE = "flare"        # flares / chaff (countermeasures)
    # match outcomes (from the War Thunder kill/damage feed)
    KILL = "kill"
    DEATH = "death"
    HIT = "hit"


@dataclass
class Event:
    """A single observed game event.

    type   : what happened (drives the effect).
    weapon : originating weapon code (AAM/RKT/BMB/CNN/FLR/CHFF) when applicable.
    source : which SignalSource produced it ("telemetry" | "hud").
    meta   : free-form extras (old/new count, delta, raw kill-feed line, ...).
    """
    type: EventType
    weapon: Optional[str] = None
    source: str = ""
    meta: Dict[str, Any] = field(default_factory=dict)
