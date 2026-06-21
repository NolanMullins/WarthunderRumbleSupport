"""WT Haptics — War Thunder haptic-feedback bridge for the Winwing Ursa Minor Fighter joystick.

This module is now a thin entry-point SHIM. The implementation lives in the winwinghaptics
package:
  winwinghaptics.app.controller   headless app logic + worker threads
  winwinghaptics.ui.gui           Tkinter view (run_gui / run_gui_safe)
  winwinghaptics.hardware         HID device (Stick / HapticDevice)
  winwinghaptics.effects          effect engine + data-driven library + dispatch
  winwinghaptics.sources          telemetry client + kill-feed classifier
  winwinghaptics.detection        HUD detector + TemporalTracker

CLI (also reachable via ../run.py):
  python winwing_haptics.py            -> launch GUI
  python winwing_haptics.py --selftest -> open stick, arm, play missile effect, exit
  python winwing_haptics.py --hudtest  -> detector/OCR readiness check (for the build)
"""
import os
import sys
import time

# Make the package importable whether run as a script (src/ on path) or imported.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from winwinghaptics.hardware import Stick           # noqa: E402  (back-compat re-export)
from winwinghaptics.effects import Effects          # noqa: E402  (back-compat re-export)
from winwinghaptics.sources import WarThunder       # noqa: E402  (back-compat re-export)
from winwinghaptics.ui import run_gui as _run_gui, run_gui_safe as _run_gui_safe  # noqa: E402

try:
    from winwinghaptics.detection import hud_detect
    _HUD_AVAILABLE = True
except Exception:
    hud_detect = None
    _HUD_AVAILABLE = False


def run_gui():
    """Launch the GUI, anchoring config base_dir to this file's directory (src/)."""
    _run_gui(__file__)


def run_gui_safe():
    """Launch the GUI with crash handling, anchored to this file's directory for config."""
    _run_gui_safe(__file__)


def selftest():
    s = Stick()
    if not s.open():
        print("Stick NOT found.")
        return 1
    print(f"Stick opened: {s.path}")
    eff = Effects(s, print)
    eff.start_heartbeat()
    time.sleep(0.2)
    eff.missile()
    time.sleep(2.0)
    eff.stop()
    s.close()
    print("selftest done.")
    return 0


def run_hudtest():
    """Definitive check that the HUD detector + deps work inside the (frozen) build."""
    ok = _HUD_AVAILABLE
    det_ok = ocr_ok = False
    if ok:
        try:
            d = hud_detect.HudDetector(region=(0, 0, 300, 200))
            det_ok = d.available
            d.poll()
            ocr_ok = hud_detect._init_ocr()  # calibration depends on Windows OCR
        except Exception as e:
            print("HUD error:", e)
    msg = f"HUD_AVAILABLE={ok} detector_ready={det_ok} ocr_ready={ocr_ok}"
    try:
        with open("hudtest_result.txt", "w") as fh:
            fh.write(msg)
    except Exception:
        pass
    print(msg)
    return 0 if (det_ok and ocr_ok) else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    if "--hudtest" in sys.argv:
        sys.exit(run_hudtest())
    run_gui_safe()
