"""Tests for the hardware device registry (discovery + selection)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from winwinghaptics.hardware import registry                # noqa: E402
from winwinghaptics.hardware import WinwingUrsaMinor         # noqa: E402


def test_winwing_is_registered():
    assert WinwingUrsaMinor in registry.backends()


def test_detect_picks_present_backend(monkeypatch):
    class Present:
        @staticmethod
        def probe():
            return True

    class Absent:
        @staticmethod
        def probe():
            return False

    monkeypatch.setattr(registry, "_BACKENDS", [Absent, Present])
    assert registry.detect() is Present


def test_detect_returns_none_when_nothing_present(monkeypatch):
    class Absent:
        @staticmethod
        def probe():
            return False

    monkeypatch.setattr(registry, "_BACKENDS", [Absent])
    assert registry.detect() is None


def test_select_device_returns_instance_of_detected(monkeypatch):
    class Present:
        @staticmethod
        def probe():
            return True

    monkeypatch.setattr(registry, "_BACKENDS", [Present])
    assert isinstance(registry.select_device(), Present)


def test_select_device_falls_back_when_none_detected(monkeypatch):
    instances = []

    class Absent:
        @staticmethod
        def probe():
            return False

        def __init__(self):
            instances.append(self)

    monkeypatch.setattr(registry, "_BACKENDS", [Absent])
    # nothing detected -> still returns an (unopened) instance so the worker can retry open()
    dev = registry.select_device()
    assert isinstance(dev, Absent)
    assert len(instances) == 1


def test_probe_swallows_exceptions(monkeypatch):
    class Boom:
        @staticmethod
        def probe():
            raise RuntimeError("no driver")

    monkeypatch.setattr(registry, "_BACKENDS", [Boom])
    assert registry.detect() is None        # a throwing probe is treated as "not present"


def test_register_is_idempotent(monkeypatch):
    monkeypatch.setattr(registry, "_BACKENDS", [])

    class Dev:
        @staticmethod
        def probe():
            return False

    registry.register(Dev)
    registry.register(Dev)
    assert registry.backends().count(Dev) == 1
