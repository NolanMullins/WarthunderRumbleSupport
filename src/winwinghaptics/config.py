"""Configuration persistence — paths + JSON read/write.

Owns WHERE the config/calibration files live (next to the exe when frozen, else next to the
app script) and the resilient read/write (any error -> empty/no-op, matching the original
behaviour). The GUI still owns the mapping between these dicts and its Tk widgets/state.

`app_base_dir(app_file)` takes the APP entry file explicitly so the location is identical to
the original (which computed it from winwing_haptics.py's __file__) -- NOT this module's path.
"""
import os
import sys
import json

CONFIG_NAME = "winwing_haptics.json"
HUD_CALIB_NAME = "hud_calib.json"


def app_base_dir(app_file):
    """Directory for config files: the exe's dir when frozen (PyInstaller), else the
    directory of `app_file` (the app entry module)."""
    return os.path.dirname(os.path.abspath(
        sys.executable if getattr(sys, "frozen", False) else app_file))


def load(path):
    """Return the parsed config dict, or {} on any error (missing/corrupt) OR if the file
    contains valid JSON that is not an object (e.g. a list/number) -- callers treat the result
    as a dict, so a non-dict is normalised to {} to match the original resilient behaviour."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save(path, data):
    """Write the config dict as indented JSON. Returns True on success, False on any error."""
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
        return True
    except Exception:
        return False
