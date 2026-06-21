"""Effects library — declarative haptic envelopes (normalized intensity).

Each effect is a list of (level, duration_ms) SEGMENTS played in order on the device; the engine
holds `level` for `duration_ms`, then drops the device to 0 at the end. Adding a new haptic is a
data edit here plus a binding in the router -- no engine change.

`level` is NORMALIZED intensity in 0.0-1.0 (device-independent). Each device maps it to its native
range in set_level(). The values are authored via `_n(native_0_255)` so they stay recognizable as
the originally-tuned envelope AND map back exactly (a device that scales 0.0-1.0 -> 0-255 round-
trips them byte-identically). `log` is the activity-log line emitted when the effect starts
(None = no log line, matching the original flare which logged nothing).
"""


def _n(v):
    """Native 0-255 intensity -> normalized 0.0-1.0 (exact round-trip on a 0-255 device)."""
    return v / 255.0


# Effect name -> {"log": str|None, "segments": [(level_0_1, duration_ms), ...]}
EFFECTS = {
    "missile": {
        "log": "EFFECT: missile launch",
        "segments": [(_n(255), 360), (_n(0), 40),
                     (_n(255), 70), (_n(0), 30), (_n(190), 55), (_n(0), 35), (_n(140), 50),
                     (_n(0), 40), (_n(90), 45), (_n(0), 45), (_n(50), 40)],
    },
    "rocket": {
        "log": "EFFECT: rocket",
        # quick, snappy: a sharp whoosh + short ripple (rockets leave fast, lighter than a
        # missile's big rail launch).
        "segments": [(_n(255), 110), (_n(0), 25), (_n(210), 70), (_n(0), 25), (_n(140), 55)],
    },
    "bomb": {
        "log": "EFFECT: bomb release",
        "segments": [(_n(255), 220), (_n(120), 120)],
    },
    "flare": {
        # a firm, quick knock -- countermeasures should be clearly felt but brief.
        "log": None,
        "segments": [(_n(160), 45)],
    },
    "kill": {
        "log": "EFFECT: kill confirm",
        "segments": [(_n(255), 90), (_n(0), 70), (_n(255), 90)],
    },
    "hit": {
        "log": "EFFECT: took a hit",
        "segments": [(_n(200), 70), (_n(0), 40), (_n(150), 50)],
    },
    "death": {
        "log": "EFFECT: death",
        # a long hold then a smooth ramp-down.
        "segments": [(_n(255), 500)] + [(_n(v), 18) for v in range(255, 0, -10)],
    },
}

# Sustained gun rumble level (normalized). Driven continuously by the engine heartbeat while the
# trigger is held, rather than played as a one-shot envelope.
GUN_LEVEL = _n(135)
