"""Tests for the UI effect spec and the controller's per-effect enable wiring.

These guard the data-driven effects list (one row per trigger) and the new generalized enables:
every spec maps to a real engine trigger, a vendored icon, and a controller enable key, and the
controller gates/persists all of them (not just the original gun/kill/hit/death).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from winwinghaptics.ui import effectspec                       # noqa: E402
from winwinghaptics.effects.engine import EffectsEngine        # noqa: E402
from winwinghaptics.app.controller import EFFECT_ENABLE_KEYS   # noqa: E402

_ICON_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src",
                         "winwinghaptics", "ui", "assets", "icons", "lucide")


def test_specs_cover_all_enable_keys():
    assert sorted(effectspec.ENABLE_KEYS) == sorted(EFFECT_ENABLE_KEYS)


def test_every_spec_has_an_engine_trigger():
    eng_methods = dir(EffectsEngine)
    for s in effectspec.SPECS:
        assert s.test in eng_methods, f"{s.name} -> {s.test} missing on engine"


def test_every_spec_icon_is_vendored():
    for s in effectspec.SPECS:
        assert os.path.exists(os.path.join(_ICON_DIR, s.icon + ".svg")), s.icon


def test_groups_partition_specs():
    grouped = []
    for gid, _title in effectspec.GROUPS:
        grouped += [s.name for s in effectspec.specs_in_group(gid)]
    assert sorted(grouped) == sorted(s.name for s in effectspec.SPECS)


def test_by_name_lookup():
    assert effectspec.BY_NAME["gun"].label == "Gun"
    assert effectspec.BY_NAME["flare"].label == "Countermeasures"


# ---- controller enable wiring (no GUI / hardware needed) ----
class _Dummy:
    pass


def _make_controller(tmp_path):
    from winwinghaptics.app.controller import AppController
    return AppController(str(tmp_path))


def test_controller_defaults_all_enabled(tmp_path):
    c = _make_controller(tmp_path)
    for k in EFFECT_ENABLE_KEYS:
        assert c.enabled(k) is True


def test_controller_enabled_reflects_state(tmp_path):
    c = _make_controller(tmp_path)
    c.state["en_missile"] = False
    assert c.enabled("missile") is False
    assert c.enabled("rocket") is True


def test_enables_round_trip_through_config(tmp_path):
    c = _make_controller(tmp_path)
    c.state["en_bomb"] = False
    c.state["en_flare"] = False
    c.save_cfg()
    c2 = _make_controller(tmp_path)
    saved = c2.load_cfg()
    assert saved.get("bomb") is False and saved.get("flare") is False
    assert saved.get("missile") is True
