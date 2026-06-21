"""Tkinter view + entry helpers.

The heavy GUI imports (the Tkinter view plus the PIL/tksvg-backed widgets and icons) are loaded
LAZILY, inside these functions, so importing winwinghaptics.ui -- which winwing_haptics.py does for
every entry point, including --selftest and --hudtest -- does NOT pull in the GUI imaging libraries.
run_gui_safe also catches a failure to import the GUI itself (e.g. a missing or unbundled PIL /
tksvg) and reports it the same way as a runtime boot error, so a packaged --noconsole build never
dies silently before a handler can run.
"""
import os
import sys


def run_gui(app_file):
    """Launch the GUI (raises on error). Imports the GUI module lazily."""
    from .gui import run_gui as _impl
    return _impl(app_file)


def run_gui_safe(app_file=None):
    """Launch the GUI, writing crash_log.txt + showing a dialog on ANY boot error -- including a
    failure to import the GUI module (missing PIL / tksvg)."""
    try:
        from .gui import run_gui_safe as _impl
    except Exception:
        _report_crash()
        sys.exit(1)
    return _impl(app_file)


def _report_crash():
    """Write crash_log.txt next to the exe and show a dialog (best-effort). Used when the GUI
    cannot even be imported, mirroring gui.run_gui_safe's runtime crash reporting."""
    import traceback
    tb = traceback.format_exc()
    try:
        base = os.path.dirname(os.path.abspath(
            sys.executable if getattr(sys, "frozen", False) else __file__))
    except Exception:
        base = os.getcwd()
    try:
        with open(os.path.join(base, "crash_log.txt"), "w", encoding="utf-8") as fh:
            fh.write("WT Haptics crash:\n\n" + tb)
    except Exception:
        pass
    try:
        import tkinter as tk
        from tkinter import messagebox
        r = tk.Tk(); r.withdraw()
        messagebox.showerror(
            "WT Haptics — startup error",
            "The app hit an error and had to stop.\n\n"
            "A crash_log.txt was written next to the app. Please send it.\n\n"
            + (tb.strip().splitlines()[-1] if tb.strip() else ""))
        r.destroy()
    except Exception:
        pass
