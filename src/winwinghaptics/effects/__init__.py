"""Effects: data-driven haptic envelopes (library) played by a serialized motor engine.

The router (EventType -> effect binding) lands here in a later phase; for now the GUI dispatches
to the engine's named triggers / fire_effect directly.
"""
from .engine import EffectsEngine, Effects   # noqa: F401
from .library import EFFECTS                  # noqa: F401
