"""Renderers — the per-device strategy for turning an Effect into physical output.

A renderer is HOW an Effect is played on a particular kind of device. Splitting this out is what
lets very different hardware coexist behind one Effect definition and one engine:

  * StreamingRenderer (here): the HOST owns timing. It walks the Effect's segments and streams
    normalized levels to device.set_level() over the timeline -- the model for ERM/LRA motors
    driven by a level (e.g. the Winwing). This reproduces the original engine playback loop.

  * A pattern/effect device (DualSense, bHaptics, an FFB stick) would provide its OWN renderer
    that uploads the whole Effect once and lets the DEVICE own timing, sleeping effect.duration_ms
    so the engine's priority arbitration still knows when playback ends.

render() is synchronous and cooperative: it returns when the effect finishes OR when is_stopped()
becomes true, so the engine can run it inside its one-shot priority thread unchanged.
"""
import time


class StreamingRenderer:
    """Play an Effect by streaming normalized levels to device.set_level over its timeline."""
    TICK = 0.003   # seconds between level writes while holding a segment (re-asserts the level)

    def __init__(self, device):
        self.device = device

    def render(self, effect, is_stopped=lambda: False):
        for seg in effect.segments:
            end = time.time() + seg.duration_ms / 1000.0
            while time.time() < end and not is_stopped():
                self.device.set_level(seg.level)
                time.sleep(self.TICK)
        self.device.set_level(0.0)


def renderer_for(device):
    """Pick the renderer for a device: its own make_renderer() if it provides one (a pattern
    device), else the default host-timed StreamingRenderer."""
    make = getattr(device, "make_renderer", None)
    if callable(make):
        return make()
    return StreamingRenderer(device)
