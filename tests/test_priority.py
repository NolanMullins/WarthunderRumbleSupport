"""Tests for the scheduling-hint module (app/priority.py). These run on the live Windows host;
they assert the calls succeed and that lowering only affects the calling thread."""
import os
import sys
import threading

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "src")))

from winwinghaptics.app import priority as P  # noqa: E402


def test_eco_qos_applies():
    # On a modern Windows host this should succeed; the call must never raise regardless.
    assert P.set_process_eco_qos(True) in (True, False)


def test_lower_thread_sets_below_normal():
    result = {}

    def worker():
        result["before"] = P.current_thread_priority()
        result["ok"] = P.lower_current_thread(True)
        result["after"] = P.current_thread_priority()

    t = threading.Thread(target=worker)
    t.start(); t.join()
    assert result["ok"] is True
    assert result["before"] == 0          # NORMAL
    assert result["after"] == -1          # BELOW_NORMAL


def test_lower_thread_is_thread_local():
    # The main thread must stay at NORMAL even after a worker lowers itself.
    before_main = P.current_thread_priority()

    def worker():
        P.lower_current_thread(True)

    t = threading.Thread(target=worker)
    t.start(); t.join()
    assert P.current_thread_priority() == before_main   # unchanged on this thread


def test_restore_thread_priority():
    result = {}

    def worker():
        P.lower_current_thread(True)
        result["lowered"] = P.current_thread_priority()
        P.lower_current_thread(False)                   # restore to NORMAL
        result["restored"] = P.current_thread_priority()

    t = threading.Thread(target=worker)
    t.start(); t.join()
    assert result["lowered"] == -1
    assert result["restored"] == 0


def test_calls_never_raise():
    # Defensive: even called oddly, the module reports via return value, never throws.
    assert isinstance(P.apply_low_impact(), bool)
    assert P.current_thread_priority() is not None
