"""Thin launcher for WT Haptics.

Adds src/ to the path and delegates to the entry-point shim, preserving its CLI flags.

    python run.py            # launch GUI
    python run.py --selftest # stick self-test
    python run.py --hudtest  # detector/OCR readiness check (writes hudtest_result.txt)
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

import winwing_haptics  # noqa: E402


def main():
    if "--selftest" in sys.argv:
        return winwing_haptics.selftest()
    if "--hudtest" in sys.argv:
        return winwing_haptics.run_hudtest()
    winwing_haptics.run_gui_safe()
    return 0


if __name__ == "__main__":
    sys.exit(main())
