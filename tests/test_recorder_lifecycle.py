"""Tests for the diagnostic recorder lifecycle (controller) -- specifically that a recording
finalizes when its capture window elapses, regardless of length.

Regression guard for a bug where the footer/cleanup/button-reset lived INSIDE the worker's
`if recording:` block (recording == now < until), so the nested `now >= until` stop check was
unreachable with the same `now` -- the recording never finalized and the Record button stuck on
"Recording…". This was latent at 30s and very visible once long sessions shipped.
"""
import os
import sys
import json
import tempfile

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "src")))

from winwinghaptics.app.controller import AppController, record_button_label  # noqa: E402


class _RecUi:
    """Captures the Record button text the controller sets."""
    def __init__(self):
        self.button_text = None

    def set_calib_label(self, text, ok=False):
        pass

    def set_record_button(self, text):
        self.button_text = text


def _ctrl():
    base = tempfile.mkdtemp()
    c = AppController(base)
    c.ui = _RecUi()
    return c, base


def test_finalize_noop_when_not_recording():
    c, _ = _ctrl()
    # No active recording -> nothing to finalize at any time.
    assert c._finalize_recording_if_due(now=10_000.0) is False


def test_finalize_waits_until_window_elapses():
    c, base = _ctrl()
    recdir = os.path.join(base, "hud_rec_test")
    os.makedirs(recdir, exist_ok=True)
    c.state["hud_rec_dir"] = recdir
    c.state["hud_rec_until"] = 1000.0
    c.state["hud_rec_n"] = 42
    c.state["hud_rec_marks"] = 3
    c.state["record_seconds"] = 300
    # Before the deadline: must NOT finalize.
    assert c._finalize_recording_if_due(now=999.0) is False
    assert c.state["hud_rec_dir"] == recdir


def test_finalize_writes_footer_and_resets_button():
    c, base = _ctrl()
    recdir = os.path.join(base, "hud_rec_test2")
    os.makedirs(recdir, exist_ok=True)
    # seed a header line like the real recorder does
    with open(os.path.join(recdir, "telemetry.jsonl"), "w", encoding="utf-8") as f:
        f.write(json.dumps({"type": "header"}) + "\n")
    c.state["hud_rec_dir"] = recdir
    c.state["hud_rec_until"] = 1000.0
    c.state["hud_rec_n"] = 100
    c.state["hud_rec_marks"] = 5
    c.state["record_seconds"] = 300
    c._marker = object()   # stand-in marker; finalize must clear it

    # At/after the deadline: finalize exactly once.
    assert c._finalize_recording_if_due(now=1000.0) is True
    assert c.state["hud_rec_dir"] is None          # session cleared
    assert c._marker is None                        # marker released
    assert c.ui.button_text == record_button_label(300)   # button reset to the chosen length

    # Footer line written with the frame + mark counts.
    rows = [json.loads(l) for l in open(os.path.join(recdir, "telemetry.jsonl"), encoding="utf-8")]
    footer = [r for r in rows if r.get("type") == "footer"]
    assert len(footer) == 1
    assert footer[0]["frames"] == 100
    assert footer[0]["marks"] == 5

    # Idempotent: a second call does nothing (session already closed).
    c.ui.button_text = None
    assert c._finalize_recording_if_due(now=1001.0) is False
    assert c.ui.button_text is None


def test_finalize_button_label_matches_configured_length():
    # The reset label reflects the configured length, not a hardcoded "30s".
    for secs, label in [(30, "Record 30s"), (300, "Record 5min"), (600, "Record 10min")]:
        c, base = _ctrl()
        recdir = os.path.join(base, f"rec_{secs}")
        os.makedirs(recdir, exist_ok=True)
        c.state["hud_rec_dir"] = recdir
        c.state["hud_rec_until"] = 500.0
        c.state["record_seconds"] = secs
        c._finalize_recording_if_due(now=600.0)
        assert c.ui.button_text == label
