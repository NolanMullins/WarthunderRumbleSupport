"""Keyboard fire-marker -- pixel-independent ground truth for the recorder.

The detector reads missile counts from pixels; validating it has meant hand-labelling frames,
which is slow and circular (the labels lean on the detector's own output). The user knows
exactly when they fire a missile, so during a focused test-flight recording they tap ONE
designated key at the instant of launch. We log those key-down edges with timestamps -> a clean,
pixel-independent record of real launches to score detection against (hits / misses / false
fires), with zero hand-labelling.

Privacy: we poll ONLY the single configured virtual-key via GetAsyncKeyState (which reads global
key state regardless of focus, so it works while War Thunder is foreground). This is NOT a
keylogger -- no other key is ever inspected or recorded. Polled at the recorder's frame rate
(~20 Hz, +/-50 ms), which is far finer than the detector's multi-frame confirmation latency.

Pick a marker key that is UNBOUND in your War Thunder controls so every press cleanly means "I
just launched" with no in-game side effect.
"""
import ctypes

try:
    _user32 = ctypes.windll.user32
except Exception:                     # non-Windows / no user32 -> markers degrade to no-op
    _user32 = None

# Friendly name -> Windows virtual-key code. Function keys / backslash are usually unbound in WT.
VK_CODES = {
    "space": 0x20, "backslash": 0xDC, "grave": 0xC0, "rbracket": 0xDD, "lbracket": 0xDB,
    "f9": 0x78, "f10": 0x79, "f11": 0x7A, "f12": 0x7B,
    "insert": 0x2D, "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
    "numpad0": 0x60, "numpad1": 0x61, "numpad_mul": 0x6A, "numpad_dot": 0x6E,
}
DEFAULT_MARKER = "f12"               # an unbound key by default -> pure ground-truth marker


def resolve_vk(key):
    """Map a friendly name or an int VK to a virtual-key code. Returns None if unknown."""
    if isinstance(key, int):
        return key
    if isinstance(key, str):
        k = key.strip().lower()
        if k in VK_CODES:
            return VK_CODES[k]
        if len(k) == 1:               # a single character: its uppercase ASCII is its VK
            return ord(k.upper())
    return None


class KeyMarker:
    """Edge-detects key-down on a single designated key. poll() returns True once per press."""

    def __init__(self, key=DEFAULT_MARKER):
        self.vk = resolve_vk(key)
        self.key = key
        self._was_down = False

    @property
    def available(self):
        return _user32 is not None and self.vk is not None

    def _is_down(self):
        if not self.available:
            return False
        # high-order bit set => key currently down. We never inspect any other key.
        return bool(_user32.GetAsyncKeyState(self.vk) & 0x8000)

    def poll(self):
        """Return True on a RISING edge (key newly pressed since the last poll), else False."""
        down = self._is_down()
        edge = down and not self._was_down
        self._was_down = down
        return edge

    def reset(self):
        self._was_down = False
