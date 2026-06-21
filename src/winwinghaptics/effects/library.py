"""Effects library — declarative haptic envelopes as device-independent Effect descriptors.

Each entry is built as an Effect (see model.py): an ordered list of Segments, each a NORMALIZED
intensity (0.0-1.0) held for a duration on a channel role. A renderer turns the descriptor into
device output, so adding a new haptic is a data edit here plus a router binding -- no engine or
device change.

Intensities are authored via `_n(native_0_255)` so they stay recognizable as the originally-tuned
envelope AND round-trip to the original 0-255 values exactly on a 0-255 device (felt output is
unchanged). `log` is the activity-log line emitted when the effect starts (None = silent, matching
the original flare which logged nothing).
"""
from .model import Effect, Segment


def _n(v):
    """Native 0-255 intensity -> normalized 0.0-1.0 (exact round-trip on a 0-255 device)."""
    return v / 255.0


def _seg(native, ms):
    return Segment(_n(native), ms)


def _effect(name, log, native_segments):
    return Effect(name=name, log=log,
                  segments=[_seg(native, ms) for native, ms in native_segments])


# Effect name -> Effect descriptor.
EFFECTS = {
    "missile": _effect("missile", "EFFECT: missile launch",
                       [(255, 360), (0, 40), (255, 70), (0, 30), (190, 55), (0, 35),
                        (140, 50), (0, 40), (90, 45), (0, 45), (50, 40)]),
    # quick, snappy: a sharp whoosh + short ripple (rockets leave fast, lighter than a missile's
    # big rail launch).
    "rocket": _effect("rocket", "EFFECT: rocket",
                      [(255, 110), (0, 25), (210, 70), (0, 25), (140, 55)]),
    "bomb": _effect("bomb", "EFFECT: bomb release", [(255, 220), (120, 120)]),
    # a firm, quick knock -- countermeasures should be clearly felt but brief.
    "flare": _effect("flare", None, [(160, 45)]),
    "kill": _effect("kill", "EFFECT: kill confirm", [(255, 90), (0, 70), (255, 90)]),
    "hit": _effect("hit", "EFFECT: took a hit", [(200, 70), (0, 40), (150, 50)]),
    # a long hold then a smooth ramp-down.
    "death": _effect("death", "EFFECT: death",
                     [(255, 500)] + [(v, 18) for v in range(255, 0, -10)]),
}


def get_effect(name):
    """Return the Effect descriptor for `name`, or None if unknown."""
    return EFFECTS.get(name)


# Sustained gun rumble level (normalized). Driven continuously by the engine heartbeat while the
# trigger is held, rather than played as a one-shot Effect envelope.
GUN_LEVEL = _n(135)
