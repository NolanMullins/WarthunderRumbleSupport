"""Effects: data-driven haptic envelopes played by a serialized motor engine.

Effects are device-independent descriptors (model.Effect / Segment) defined in the library; a
renderer (renderer.StreamingRenderer by default) turns a descriptor into device output. The
router (EventType -> effect binding) lands here in a later phase; for now the GUI dispatches to
the engine's named triggers / fire_effect directly.
"""
from .engine import EffectsEngine, Effects   # noqa: F401
from .library import EFFECTS, get_effect      # noqa: F401
from .model import Effect, Segment, Channel   # noqa: F401
from .renderer import StreamingRenderer, renderer_for   # noqa: F401
