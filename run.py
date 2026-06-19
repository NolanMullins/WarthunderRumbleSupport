"""Thin launcher for WinwingHaptics.

Keeps a stable entry point while the app is decomposed into the winwinghaptics package.
Adds src/ to the path and delegates to the GUI app, preserving its CLI flags.

    python run.py            # launch GUI
    python run.py --selftest # stick self-test
    python run.py --hudtest  # detector/OCR readiness check (writes hudtest_result.txt)
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

import winwing_haptics  # noqa: E402


def _hudtest():
    import winwinghaptics.detection.hud_detect as hud_detect
    ok = getattr(winwing_haptics, "_HUD_AVAILABLE", False)
    det_ok = ocr_ok = False
    if ok:
        try:
            d = hud_detect.HudDetector(region=(0, 0, 300, 200))
            det_ok = d.available
            d.poll()
            ocr_ok = hud_detect._init_ocr()
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


def main():
    if "--selftest" in sys.argv:
        return winwing_haptics.selftest()
    if "--hudtest" in sys.argv:
        return _hudtest()
    winwing_haptics.run_gui_safe()
    return 0


if __name__ == "__main__":
    sys.exit(main())
