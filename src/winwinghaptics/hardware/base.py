"""HapticDevice abstraction — the hardware extensibility axis.

A backend implements HapticDevice so the rest of the app (effects engine) is device-agnostic.
Effects are authored in NORMALIZED intensity 0.0-1.0; each device maps that to its native
range via set_level(). The legacy 0-255 `vib()` / `arm()` methods remain on the concrete
device for now (the effects engine still uses them); they will be migrated to set_level() in
the effects phase. Capabilities lets the engine adapt (e.g. whether a heartbeat is needed).
"""
import abc
from dataclasses import dataclass


@dataclass(frozen=True)
class Capabilities:
    name: str
    max_level: int = 255           # native max intensity value
    supports_intensity: bool = True
    needs_heartbeat: bool = True   # device must be periodically re-armed to stay active
    heartbeat_interval: float = 2.5  # seconds between heartbeats


class HapticDevice(abc.ABC):
    """Interface every haptic backend must implement."""

    @property
    @abc.abstractmethod
    def capabilities(self) -> Capabilities:
        ...

    @abc.abstractmethod
    def open(self) -> bool:
        """Find + open the device. Returns True on success."""

    @abc.abstractmethod
    def close(self) -> None:
        ...

    @abc.abstractmethod
    def is_open(self) -> bool:
        ...

    @abc.abstractmethod
    def arm(self) -> bool:
        """Send the keep-alive/arm packet (no-op for devices that don't need it)."""

    @abc.abstractmethod
    def set_level(self, level: float) -> bool:
        """Set vibration intensity from a normalized 0.0-1.0 value."""
