"""Hardware backends for haptic output.

Extensibility axis: new haptic devices implement HapticDevice (base.py) and register a
discovery probe. Effects are authored in normalized 0.0-1.0 intensity; each device maps that
to its native range, so effect definitions stay device-independent.
"""
from .base import HapticDevice, Capabilities          # noqa: F401
from .winwing import WinwingUrsaMinor, Stick          # noqa: F401
from .registry import register, select_device, backends, detect   # noqa: F401

# Register the built-in backends for discovery. New devices register themselves the same way so
# the controller never needs editing to support more hardware.
register(WinwingUrsaMinor)
