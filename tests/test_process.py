"""Tests for War Thunder process detection and the controller's wt_open gating logic."""
import os
import sys
import time

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "src")))

from winwinghaptics.sources import process as P          # noqa: E402
from winwinghaptics.app.controller import AppController  # noqa: E402


# ----------------------------- process util -----------------------------
def test_is_running_matches_case_insensitive():
    enum = lambda: iter(["chrome.exe", "ACES.EXE", "explorer.exe"])
    assert P.is_running(("aces.exe",), _enum=enum) is True


def test_is_running_no_match():
    enum = lambda: iter(["chrome.exe", "explorer.exe"])
    assert P.is_running(("aces.exe",), _enum=enum) is False


def test_is_warthunder_running_true():
    assert P.is_warthunder_running(_enum=lambda: iter(["aces.exe"])) is True


def test_is_warthunder_running_false():
    assert P.is_warthunder_running(_enum=lambda: iter(["notepad.exe"])) is False


def test_is_running_swallows_enum_error():
    def boom():
        raise RuntimeError("snapshot failed")
        yield  # pragma: no cover
    assert P.is_running(("aces.exe",), _enum=boom) is False


def test_iter_process_names_live_runs():
    # On this Windows host the live enumeration must see at least the python process.
    names = list(P.iter_process_names())
    assert any(n.endswith(".exe") for n in names)


# ----------------------- controller wt_open gating ----------------------
def _bare_controller():
    """A controller instance WITHOUT running __init__ (no device/Effects side effects), with
    just the fields _refresh_wt_open touches."""
    c = AppController.__new__(AppController)
    c.state = {"wt_open": False}
    c._wt_proc_next = 0.0
    c._wt_proc_open = False
    return c


def test_wt_open_true_when_server_up(monkeypatch):
    c = _bare_controller()
    monkeypatch.setattr(P, "is_warthunder_running", lambda: False)
    c._refresh_wt_open(server_up=True)
    assert c.state["wt_open"] is True


def test_wt_open_true_when_process_running_even_if_server_down(monkeypatch):
    # The key case: HUD-only users with the telemetry server disabled must still be detected.
    c = _bare_controller()
    monkeypatch.setattr(P, "is_warthunder_running", lambda: True)
    c._refresh_wt_open(server_up=False)
    assert c.state["wt_open"] is True


def test_wt_open_false_when_both_down(monkeypatch):
    c = _bare_controller()
    monkeypatch.setattr(P, "is_warthunder_running", lambda: False)
    c._refresh_wt_open(server_up=False)
    assert c.state["wt_open"] is False


def test_wt_process_check_is_throttled(monkeypatch):
    # The process check must only run on the slow cadence, not every call.
    calls = {"n": 0}

    def counting():
        calls["n"] += 1
        return False
    c = _bare_controller()
    monkeypatch.setattr(P, "is_warthunder_running", counting)
    c._refresh_wt_open(server_up=False)   # first call -> checks process
    c._refresh_wt_open(server_up=False)   # immediately again -> throttled, no new check
    assert calls["n"] == 1
    # force the cadence window open
    c._wt_proc_next = time.time() - 1
    c._refresh_wt_open(server_up=False)
    assert calls["n"] == 2


def test_hud_on_defaults_true():
    # New installs (no saved config key) must have auto-detect ON.
    import inspect
    src = inspect.getsource(AppController.load_cfg)
    assert 'cfg.get("hud_on", True)' in src
