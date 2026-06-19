"""Winwing Ursa Minor Fighter haptic device.

Implements HapticDevice. Keeps the legacy Stick API (open/close/is_open/arm/vib) byte-for-byte
identical so the existing effects engine is unchanged this phase; also exposes the normalized
set_level() from the ABC for the upcoming effects migration.

Vibration protocol (decoded from SimApp Pro capture):
  ARM/heartbeat : 02 01 00 00 00 01 00 ...            (resend ~every 2.5s)
  Set intensity : 02 0A BF 00 00 03 49 00 <0..255> ...(device holds level; 0 = stop)
"""
import threading

from . import hid_win
from .base import HapticDevice, Capabilities

WW_VID = 0x4098


class WinwingUrsaMinor(HapticDevice):
    """Holds an open HID handle to the Winwing and writes vibration frames."""
    ARM = bytes([0x02, 0x01, 0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])

    def __init__(self):
        self.handle = None
        self.path = None
        self._lock = threading.Lock()

    @property
    def capabilities(self):
        return Capabilities(name="Winwing Ursa Minor Fighter", max_level=255,
                            supports_intensity=True, needs_heartbeat=True,
                            heartbeat_interval=2.5)

    def open(self):
        path = hid_win.find_device_path(WW_VID, usage_page=0x0001, usage=0x0004)
        if not path:
            return False
        h = hid_win.open_path(path)
        if not h:
            return False
        self.handle = h
        self.path = path
        return True

    def close(self):
        with self._lock:
            if self.handle:
                hid_win.close(self.handle)
                self.handle = None

    def is_open(self):
        return self.handle is not None

    def _write(self, data):
        with self._lock:
            if not self.handle:
                return False
            return hid_win.write(self.handle, data)

    def arm(self):
        return self._write(self.ARM)

    def vib(self, intensity):
        """Set vibration at a native 0-255 intensity (legacy effects API)."""
        i = max(0, min(255, int(intensity)))
        frame = bytes([0x02, 0x0A, 0xBF, 0x00, 0x00, 0x03, 0x49, 0x00, i,
                       0x00, 0x00, 0x00, 0x00, 0x00])
        return self._write(frame)

    def set_level(self, level):
        """Normalized 0.0-1.0 intensity (HapticDevice interface)."""
        return self.vib(round(max(0.0, min(1.0, level)) * 255))


# Back-compat alias: the app + effects engine still construct/refer to `Stick`.
Stick = WinwingUrsaMinor
