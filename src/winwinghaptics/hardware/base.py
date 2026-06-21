"""HapticDevice abstraction — the hardware extensibility axis.

A backend implements HapticDevice so the rest of the app (effects engine) is device-agnostic.
Effects are authored in NORMALIZED intensity 0.0-1.0; each device maps that to its native
range via set_level(). The legacy 0-255 `vib()` / `arm()` methods remain on the concrete
device for now (the effects engine still uses them); they will be migrated to set_level() in
the effects phase. Capabilities lets the engine adapt (e.g. whether a heartbeat is needed).
"""
import abc
import time
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

    # ---- keep-alive (device-owned cadence) ----
    # The engine calls these every loop tick; the DEVICE decides whether and how often to re-arm
    # based on its Capabilities, so the heartbeat interval is no longer hardcoded in the engine.
    # Devices that don't need a heartbeat (needs_heartbeat=False) make these no-ops.
    def start_keepalive(self) -> None:
        """Arm now and reset the heartbeat clock. Called when output starts."""
        if self.capabilities.needs_heartbeat:
            self.arm()
        self._last_arm = time.time()

    def keepalive(self, now: float = None) -> None:
        """Re-arm if the device's heartbeat interval has elapsed since the last arm."""
        caps = self.capabilities
        if not caps.needs_heartbeat:
            return
        if now is None:
            now = time.time()
        if now - getattr(self, "_last_arm", 0.0) >= caps.heartbeat_interval:
            self.arm()
            self._last_arm = now
