"""Tests for the effects engine + normalized library.

The engine now drives devices through the normalized set_level(0.0-1.0) interface instead of the
legacy native vib(0-255). These tests pin that the normalized library round-trips to the ORIGINAL
native 0-255 envelope exactly (so felt output on the Winwing is unchanged), and that the engine
emits via set_level.
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from winwinghaptics.effects import library                 # noqa: E402
from winwinghaptics.effects.engine import EffectsEngine    # noqa: E402
from winwinghaptics.effects.renderer import StreamingRenderer  # noqa: E402
from winwinghaptics.hardware import WinwingUrsaMinor        # noqa: E402


# The original hardcoded native 0-255 envelopes (pre-normalization), as the byte-identical
# contract the normalized library must reproduce through a 0-255 device mapping.
EXPECTED_NATIVE = {
    "missile": [255, 0, 255, 0, 190, 0, 140, 0, 90, 0, 50],
    "rocket":  [255, 0, 210, 0, 140],
    "bomb":    [255, 120],
    "flare":   [160],
    "kill":    [255, 0, 255],
    "hit":     [200, 0, 150],
    "death":   [255] + list(range(255, 0, -10)),
}


def _to_native(level):
    """Map a normalized 0.0-1.0 level the way a 0-255 device does (matches set_level)."""
    return round(max(0.0, min(1.0, level)) * 255)


def test_every_effect_round_trips_to_original_native():
    for name, expected in EXPECTED_NATIVE.items():
        natives = [_to_native(seg.level) for seg in library.EFFECTS[name].segments]
        assert natives == expected, name


def test_durations_unchanged():
    # the normalization touched levels only, never timings
    assert [seg.duration_ms for seg in library.EFFECTS["missile"].segments] == \
        [360, 40, 70, 30, 55, 35, 50, 40, 45, 45, 40]


def test_effect_duration_is_segment_sum():
    eff = library.EFFECTS["missile"]
    assert eff.duration_ms == sum(seg.duration_ms for seg in eff.segments)


def test_gun_level_maps_to_135():
    assert _to_native(library.GUN_LEVEL) == 135


def test_device_set_level_matches_mapping():
    d = WinwingUrsaMinor()
    # Capture the native 0-255 value set_level passes to the device's vib() so this guards the
    # real WinwingUrsaMinor mapping (round(clamp(level)*255)), not just the local helper.
    seen = []
    d.vib = lambda native: seen.append(native)
    for level in (0.0, 5 / 255, library.GUN_LEVEL, 245 / 255, 1.0):
        d.set_level(level)
    assert seen == [0, 5, 135, 245, 255]


class _FakeDevice:
    """Records the native levels written, so we can assert the engine emits via set_level."""
    def __init__(self):
        self.levels = []

    def set_level(self, level):
        self.levels.append(_to_native(level))
        return True

    def arm(self):
        return True


def test_engine_emits_via_set_level():
    dev = _FakeDevice()
    eng = EffectsEngine(dev)
    eng.play("flare")          # shortest effect (single 45 ms segment)
    time.sleep(0.2)            # let the one-shot thread finish
    assert 160 in dev.levels   # the flare level was emitted
    assert dev.levels[-1] == 0  # motor left quiet at the end


def test_streaming_renderer_plays_segment_levels_then_zero():
    dev = _FakeDevice()
    r = StreamingRenderer(dev)
    r.render(library.EFFECTS["flare"])     # synchronous, single 45 ms segment at 160
    assert 160 in dev.levels
    assert dev.levels[-1] == 0


def test_streaming_renderer_stops_when_signalled():
    dev = _FakeDevice()
    r = StreamingRenderer(dev)
    # is_stopped True from the start: no segment levels emitted, only the final quiet write.
    r.render(library.EFFECTS["missile"], is_stopped=lambda: True)
    assert dev.levels == [0]


def test_engine_uses_streaming_renderer_by_default():
    eng = EffectsEngine(_FakeDevice())
    assert isinstance(eng.renderer, StreamingRenderer)


# ---- device-owned keep-alive ----
from winwinghaptics.hardware.base import HapticDevice, Capabilities   # noqa: E402


class _KeepaliveDevice(HapticDevice):
    """Minimal HapticDevice that just counts arm() calls, to test the base keepalive cadence."""
    def __init__(self, needs_heartbeat=True, interval=2.5):
        self._caps = Capabilities(name="test", needs_heartbeat=needs_heartbeat,
                                  heartbeat_interval=interval)
        self.arms = 0

    @property
    def capabilities(self):
        return self._caps

    def open(self):
        return True

    def close(self):
        pass

    def is_open(self):
        return True

    def arm(self):
        self.arms += 1
        return True

    def set_level(self, level):
        return True


def test_keepalive_rearms_on_interval():
    d = _KeepaliveDevice(interval=2.5)
    d.start_keepalive()            # arms immediately
    assert d.arms == 1
    d.keepalive(now=d._last_arm + 1.0)   # too soon -> no re-arm
    assert d.arms == 1
    d.keepalive(now=d._last_arm + 2.5)   # interval elapsed -> re-arm
    assert d.arms == 2


def test_keepalive_noop_when_not_needed():
    d = _KeepaliveDevice(needs_heartbeat=False)
    d.start_keepalive()
    d.keepalive(now=10_000.0)
    assert d.arms == 0             # device that doesn't need a heartbeat never arms


def test_keepalive_clock_reset_even_if_initial_arm_fails():
    # If the very first arm() throws, the clock must still be set so keepalive() doesn't then
    # re-arm every tick (the original engine set last_arm unconditionally).
    d = _KeepaliveDevice(interval=2.5)
    d.arm = lambda: (_ for _ in ()).throw(RuntimeError("device gone"))
    try:
        d.start_keepalive()
    except RuntimeError:
        pass
    assert d._last_arm != 0.0                       # clock was set before the failed arm
    d.arm = lambda: setattr(d, "arms", d.arms + 1) or True
    d.keepalive(now=d._last_arm + 1.0)              # within interval -> must NOT arm
    assert d.arms == 0
