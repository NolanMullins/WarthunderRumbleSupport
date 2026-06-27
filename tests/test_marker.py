"""Unit tests for the keyboard fire-marker (ground-truth capture)."""
import os
import sys

sys.path.insert(0, os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "src")))
from winwinghaptics.sources import marker as M   # noqa: E402


def test_resolve_vk_named():
    assert M.resolve_vk("f12") == 0x7B
    assert M.resolve_vk("space") == 0x20
    assert M.resolve_vk("backslash") == 0xDC


def test_resolve_vk_single_char():
    assert M.resolve_vk("a") == ord("A")
    assert M.resolve_vk("Z") == ord("Z")
    assert M.resolve_vk("7") == ord("7")


def test_resolve_vk_int_passthrough():
    assert M.resolve_vk(0x42) == 0x42


def test_resolve_vk_unknown():
    assert M.resolve_vk("not-a-key") is None
    assert M.resolve_vk(None) is None


def test_rising_edge_detection(monkeypatch):
    # Drive a synthetic key-state sequence and confirm poll() fires once per press, on the edge.
    km = M.KeyMarker("f12")
    state = {"down": False}
    monkeypatch.setattr(km, "_is_down", lambda: state["down"])

    assert km.poll() is False          # up
    state["down"] = True
    assert km.poll() is True           # rising edge -> marker
    assert km.poll() is False          # held down -> no repeat
    assert km.poll() is False
    state["down"] = False
    assert km.poll() is False          # release -> nothing
    state["down"] = True
    assert km.poll() is True           # second press -> marker again


def test_unavailable_key_is_safe():
    km = M.KeyMarker("not-a-key")      # unresolved VK -> not available, never fires
    assert km.available is False
    assert km.poll() is False
