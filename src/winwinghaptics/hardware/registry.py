"""Device registry — discovery + selection across haptic backends.

Backends register themselves here, each exposing a static `probe()` that cheaply reports whether
its device is present. `select_device()` returns an instance of the first backend that probes
present, so adding hardware is "write a backend, register it" with no edit to the controller.

The controller still owns the open/retry loop: select_device() returns an UNOPENED instance (it
does not hold the device open), and falls back to a default backend when nothing is detected so
the stick worker keeps retrying open() exactly as before.
"""

_BACKENDS = []


def register(cls):
    """Register a HapticDevice backend (usable as a decorator). Backends should define a static
    `probe() -> bool`. Idempotent: registering the same class twice is a no-op."""
    if cls not in _BACKENDS:
        _BACKENDS.append(cls)
    return cls


def backends():
    """Registered backend classes, in registration order."""
    return list(_BACKENDS)


def _probe(cls):
    probe = getattr(cls, "probe", None)
    try:
        return bool(probe()) if callable(probe) else False
    except Exception:
        return False


def detect():
    """Return the first registered backend CLASS whose device is present, or None."""
    for cls in _BACKENDS:
        if _probe(cls):
            return cls
    return None


def select_device(default=None):
    """Return an unopened instance of the first detected backend. If none is detected, fall back
    to `default` (a backend class), else the first registered backend, else None -- so callers
    always get an object whose open() they can retry."""
    cls = detect() or default or (_BACKENDS[0] if _BACKENDS else None)
    return cls() if cls is not None else None
