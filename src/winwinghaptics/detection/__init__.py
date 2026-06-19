"""HUD detection: screen-read War Thunder weapon-counter rows and decide weapon-fire events.

Public surface (unchanged from the legacy flat module):
  read_counts, TemporalTracker, HudDetector, Calib, calibrate_from_grays, save_gray_png,
  capture_gray, text_feature, WEAPON_CLASS, WEAPON_EFFECT, _init_ocr
"""
from .hud_detect import *          # noqa: F401,F403
from .hud_detect import (          # noqa: F401  (explicit re-export of names tools rely on)
    read_counts, TemporalTracker, HudDetector, Calib, calibrate_from_grays,
    save_gray_png, capture_gray, text_feature, WEAPON_CLASS, WEAPON_EFFECT, _init_ocr,
)
