"""Effects library — declarative haptic envelopes.

Each effect is a list of (level, duration_ms) SEGMENTS played in order on the motor; the engine
holds `level` (native 0-255) for `duration_ms`, then drops the motor to 0 at the end. Adding a
new haptic is a data edit here plus a binding in the router -- no engine change.

These segment lists are a faithful transcription of the original hardcoded effect sequences
(the trailing vib(0) and any zero-duration no-op holds are applied by the engine, so the felt
output is byte-identical). `log` is the activity-log line emitted when the effect starts
(None = no log line, matching the original flare which logged nothing).
"""

# Effect name -> {"log": str|None, "segments": [(level_0_255, duration_ms), ...]}
EFFECTS = {
    "missile": {
        "log": "EFFECT: missile launch",
        "segments": [(255, 360), (0, 40),
                     (255, 70), (0, 30), (190, 55), (0, 35), (140, 50),
                     (0, 40), (90, 45), (0, 45), (50, 40)],
    },
    "rocket": {
        "log": "EFFECT: rocket",
        # quick, snappy: a sharp whoosh + short ripple (rockets leave fast, lighter than a
        # missile's big rail launch).
        "segments": [(255, 110), (0, 25), (210, 70), (0, 25), (140, 55)],
    },
    "bomb": {
        "log": "EFFECT: bomb release",
        "segments": [(255, 220), (120, 120)],
    },
    "flare": {
        # a firm, quick knock -- countermeasures should be clearly felt but brief.
        "log": None,
        "segments": [(160, 45)],
    },
    "kill": {
        "log": "EFFECT: kill confirm",
        "segments": [(255, 90), (0, 70), (255, 90)],
    },
    "hit": {
        "log": "EFFECT: took a hit",
        "segments": [(200, 70), (0, 40), (150, 50)],
    },
    "death": {
        "log": "EFFECT: death",
        # a long hold then a smooth ramp-down.
        "segments": [(255, 500)] + [(v, 18) for v in range(255, 0, -10)],
    },
}
