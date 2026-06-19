"""Signal sources — observers that watch the game and surface raw signals.

Currently: the War Thunder localhost telemetry client (telemetry_client.WarThunder). The HUD
detector (winwinghaptics.detection) is the other signal source. The worker LOOPS that poll
these and translate them into Events still live in the GUI app for now; they move behind a
SignalSource interface alongside the UI/controller decomposition.
"""
from .telemetry_client import WarThunder   # noqa: F401
