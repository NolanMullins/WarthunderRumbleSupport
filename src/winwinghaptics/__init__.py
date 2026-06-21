"""WinwingHaptics — War Thunder haptic-feedback bridge for the Winwing Ursa Minor Fighter.

Package layout (refactor in progress):
  detection/   HUD weapon-counter detector (read_counts, TemporalTracker, calibration)
  (future)     hardware/, sources/, effects/, config, ui — see plan.

The legacy single-file app (src/winwing_haptics.py) is being decomposed into this package
phase by phase; each phase is gated green by the tests/ A/B suite.
"""

# Single source of truth for the app version. The auto-updater compares this against the latest
# GitHub release tag; bump it for each release (and tag the release `v<this>`).
__version__ = "0.1.0"
