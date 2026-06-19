"""Application controller — owns shared state, dependencies, and the worker threads.

The controller is the headless heart of the app: it holds the device/effects/telemetry/detector,
the shared `state` dict, config persistence, calibration + recording, and the three worker loops
(stick / telemetry / HUD). It has NO Tkinter dependency. The GUI builds widgets, sets a small
`ui` bridge for the handful of UI-thread callbacks (log render, calib label, record button), and
calls start_workers() / shutdown().

This separation is what makes the rest of the work easier: worker logic is now reachable and
mockable without standing up a window, and the UI is a thin view over this controller.
"""
import os
import time
import json
import queue
import threading

from .. import config
from ..hardware import Stick
from ..effects import Effects
from ..effects import dispatch
from ..sources import WarThunder
from ..sources import killfeed
from ..events import EventType

try:
    from ..detection import hud_detect
    HUD_AVAILABLE = True
except Exception:
    hud_detect = None
    HUD_AVAILABLE = False


class NullUiBridge:
    """Default no-op UI bridge so the controller works headless / before a GUI attaches.
    The GUI replaces this with one that marshals onto the Tk main thread via root.after."""
    def set_calib_label(self, text, ok=False):
        pass

    def set_record_button(self, text):
        pass


class AppController:
    def __init__(self, base_dir):
        self.base_dir = base_dir
        self.CONFIG = os.path.join(base_dir, config.CONFIG_NAME)
        self.HUD_CALIB = os.path.join(base_dir, config.HUD_CALIB_NAME)
        self.hud_available = HUD_AVAILABLE

        self.stick = Stick()
        self.effects = Effects(self.stick)
        self.wt = WarThunder()
        self.ui = NullUiBridge()

        self.state = {
            "stick_ok": False, "game_ok": False,
            "last_evt": 0, "last_dmg": 0,
            "hud_on": False,
            "hud_region": (0, 0, 400, 400),
            "hud_status": "off",
            "hud_det": None,
            "hud_calibrating": False,
            "hud_auto_next": 0.0,
            "hud_loadout_next": 0.0,
            "hud_rec_until": 0.0,
            "hud_rec_dir": None,
            "hud_rec_n": 0,
            "callsign": "",
            "running": True,
            # plain-bool mirrors of the Tk enable checkboxes, updated from the UI thread.
            # Worker threads read THESE (never the Tk BooleanVars, which aren't thread-safe).
            "en_gun": True, "en_kill": True, "en_hit": True, "en_death": True,
        }

        self._log_q = queue.Queue()
        self.effects.log = lambda m: self.log(m, "fx")

    # ---- thread-safe logging (UI drains the queue) ----
    def log(self, msg, tag=None):
        self._log_q.put((time.strftime("%H:%M:%S"), msg, tag or ""))

    def drain_log(self):
        """Return + clear all pending (ts, msg, tag) log records. Called on the UI thread."""
        pending = []
        try:
            while True:
                pending.append(self._log_q.get_nowait())
        except queue.Empty:
            pass
        return pending

    # ---- config persistence (state-based; UI mirrors are kept current by the GUI) ----
    def load_cfg(self):
        """Load config into state; return the saved 'enables' dict for the GUI to apply."""
        cfg = config.load(self.CONFIG)
        if cfg.get("hud_region"):
            self.state["hud_region"] = tuple(cfg["hud_region"])
        self.state["hud_on"] = bool(cfg.get("hud_on", False))
        self.state["callsign"] = cfg.get("callsign", "")
        return cfg.get("enables") or {}

    def save_cfg(self):
        data = {
            "enables": {"gun": self.state["en_gun"], "kill": self.state["en_kill"],
                        "hit": self.state["en_hit"], "death": self.state["en_death"]},
            "hud_on": self.state["hud_on"],
            "hud_region": list(self.state["hud_region"]),
            "callsign": self.state.get("callsign", ""),
        }
        config.save(self.CONFIG, data)

    # ---- shared detector + one-time template calibration ----
    def get_det(self):
        if self.state["hud_det"] is None and self.hud_available:
            try:
                d = hud_detect.HudDetector(region=self.state["hud_region"])
                if os.path.exists(self.HUD_CALIB):
                    try:
                        d.load(self.HUD_CALIB)
                        if d.calibrated:
                            d.region = self.state["hud_region"]
                    except Exception:
                        # stale/incompatible calibration from an older version -> discard,
                        # the app will simply auto-recalibrate. Never let it break boot.
                        try:
                            os.remove(self.HUD_CALIB)
                        except Exception:
                            pass
                self.state["hud_det"] = d
            except Exception as e:
                self.log(f"HUD detector unavailable: {e}")
                return None
        return self.state["hud_det"]

    def calibrate_core(self, manual):
        """Run the one-time OCR calibration (blocking ~4s). Shared by the silent
        auto-calibrator and the manual 'Re-learn' button. Guarded so the two never
        overlap. On success the calibration is saved and fast detection is live."""
        det = self.get_det()
        if det is None or self.state["hud_calibrating"]:
            return False
        self.state["hud_calibrating"] = True
        try:
            det.region = self.state["hud_region"]
            self.state["hud_status"] = "learning HUD…"
            if manual:
                self.ui.set_calib_label("learning your HUD… keep counters visible (~3s)")
            ok, msg = det.calibrate(n_frames=12, interval=0.25)
            if ok:
                det.save(self.HUD_CALIB)
                self.ui.set_calib_label(msg, ok=True)
                self.log(f"HUD learned ({msg}). Fast detection active.", "fx")
            elif manual:
                self.ui.set_calib_label("couldn't find counters: " + msg)
                self.log("HUD calibration failed: " + msg)
            return ok
        finally:
            self.state["hud_calibrating"] = False

    def calibrate_detector(self):
        """Manual 'Re-learn HUD' button -> run calibration in a worker thread."""
        threading.Thread(target=lambda: self.calibrate_core(True), daemon=True).start()

    def start_record(self):
        """Begin a 30s diagnostic recording: every polled frame is saved as a PNG and a
        telemetry line is written to telemetry.jsonl. The HUD worker does the actual capture
        so we record exactly what detection sees."""
        if not self.hud_available:
            return
        if self.state["hud_rec_until"] > time.time():
            return  # already recording
        det = self.get_det()
        if det is None:
            return
        if not self.state["hud_on"]:
            self.log("Enable HUD auto-detect before recording.")
            return
        ts = time.strftime("%Y%m%d_%H%M%S")
        rec_dir = os.path.join(self.base_dir, f"hud_rec_{ts}")
        try:
            os.makedirs(rec_dir, exist_ok=True)
        except Exception as e:
            self.log(f"Record failed: {e}")
            return
        # Dump the FULL calibration (geometry + harvested glyph/label templates) to a sidecar
        # calib.json so the recording can be RE-DETECTED offline with the exact live
        # calibration -- the header geometry alone is not enough to reproduce reads.
        calib_saved = False
        if det.calibrated and det.calib is not None:
            try:
                with open(os.path.join(rec_dir, "calib.json"), "w", encoding="utf-8") as cf:
                    json.dump(det.calib.to_dict(), cf)
                calib_saved = True
            except Exception as e:
                self.log(f"calib dump failed: {e}", "fx")
        header = {
            "type": "header", "time": ts,
            "region": list(self.state["hud_region"]),
            "calibrated": det.calibrated,
            "calib_file": "calib.json" if calib_saved else None,
            "weapons": sorted(det.calib.rows) if det.calibrated else [],
            "count_x": getattr(det.calib, "count_x", None) if det.calibrated else None,
            "pitch": getattr(det.calib, "pitch", None) if det.calibrated else None,
            "rows": det.calib.rows if det.calibrated else {},
        }
        try:
            with open(os.path.join(rec_dir, "telemetry.jsonl"), "w", encoding="utf-8") as fh:
                fh.write(json.dumps(header) + "\n")
        except Exception as e:
            self.log(f"Record failed: {e}")
            return
        self.state["hud_rec_dir"] = rec_dir
        self.state["hud_rec_n"] = 0
        self.state["hud_rec_until"] = time.time() + 30.0
        self.log(f"Recording 30s → {os.path.basename(rec_dir)} …", "fx")
        self.ui.set_record_button("● Recording…")

    def rec_write(self, line):
        d = self.state["hud_rec_dir"]
        if not d:
            return
        try:
            with open(os.path.join(d, "telemetry.jsonl"), "a", encoding="utf-8") as fh:
                fh.write(json.dumps(line) + "\n")
        except Exception:
            pass

    # ---- workers ----
    def stick_worker(self):
        while self.state["running"]:
            if not self.stick.is_open():
                if self.stick.open():
                    self.state["stick_ok"] = True
                    self.effects.start_heartbeat()
                    self.log("Stick connected.")
                else:
                    self.state["stick_ok"] = False
            time.sleep(1.0)

    def wt_worker(self):
        hud_seeded = False
        cyc = 0
        while self.state["running"]:
            # GUN: poll the trigger indicator FAST. weapon2 is the actual trigger-input state
            # (zero visual lag, unlike reading the HUD ammo counter), so the lowest-latency,
            # most reliable gun signal is this localhost value polled quickly.
            ind = self.wt.indicators()
            if isinstance(ind, dict) and ind.get("valid"):
                self.state["game_ok"] = True
                w2 = ind.get("weapon2", 0.0) or 0.0
                if self.state["en_gun"] and w2 >= 1.0:
                    self.effects.gun_active(0.18)
            else:
                self.state["game_ok"] = False
            # KILL/DEATH feed: only needs ~300 ms cadence. Polled independently of indicators
            # validity (/hudmsg works whenever WT's web server is up), every ~6th fast tick.
            if cyc % 6 == 0:
                hud = self.wt.hudmsg(self.state["last_evt"], self.state["last_dmg"])
                if isinstance(hud, dict):
                    dmgs = hud.get("damage", []) or []
                    for d in dmgs:
                        if isinstance(d, dict) and "id" in d:
                            self.state["last_dmg"] = max(self.state["last_dmg"], int(d["id"]))
                        msg = (d.get("msg") or "") if isinstance(d, dict) else ""
                        # On the FIRST poll WT replays the whole backlog -> absorb it as
                        # baseline so we don't fire for kills before the app was watching.
                        if hud_seeded and msg:
                            self.handle_damage(msg)
                    if not hud_seeded:
                        hud_seeded = True
                    for e in hud.get("events", []) or []:
                        if isinstance(e, dict) and "id" in e:
                            self.state["last_evt"] = max(self.state["last_evt"], int(e["id"]))
            cyc += 1
            time.sleep(0.05)

    def handle_damage(self, msg):
        if not msg:
            return
        # Always surface the raw kill-feed line so it's visible WHAT the game sent and whether
        # matching worked -- fastest way to diagnose "callsign effects don't fire".
        self.log(f"WT: {msg}", "wt")
        outcome = killfeed.classify(msg, self.state.get("callsign"))
        if outcome == EventType.KILL:
            if self.state["en_kill"]:
                self.effects.kill()
            self.log(f"KILL  {msg}", "kill")
        elif outcome == EventType.DEATH:
            if self.state["en_death"]:
                self.effects.death()
            self.log(f"DEATH  {msg}", "death")
        elif outcome == EventType.HIT:
            if self.state["en_hit"]:
                self.effects.hit()
            self.log(f"HIT  {msg}", "fx")

    def hud_worker(self):
        if not self.hud_available:
            return
        det = self.get_det()
        if det is None:
            self.state["hud_status"] = "unavailable"
            return
        last_counter_knock = 0.0
        empty_streak = 0          # consecutive live frames that read nothing
        while self.state["running"]:
            if not self.state["hud_on"]:
                self.state["hud_status"] = "off"
                det.reset()
                time.sleep(0.5)
                continue
            if not det.available:
                self.state["hud_status"] = "unavailable"
                time.sleep(1.0)
                continue
            if not det.calibrated:
                # Auto-calibrate silently when counters are visible. A cheap one-frame probe
                # gates the expensive OCR so we don't churn in menus/clear sky.
                now = time.time()
                if self.state["hud_calibrating"]:
                    self.state["hud_status"] = "learning HUD…"
                    time.sleep(0.4)
                    continue
                if now < self.state["hud_auto_next"]:
                    self.state["hud_status"] = "waiting for HUD…"
                    time.sleep(0.4)
                    continue
                det.region = self.state["hud_region"]
                try:
                    seen = det.probe()
                except Exception:
                    seen = 0
                if seen >= 1:
                    self.calibrate_core(False)              # blocking ~4s in this worker
                    self.state["hud_auto_next"] = time.time() + 3
                else:
                    self.state["hud_status"] = "waiting for HUD…"
                    self.state["hud_auto_next"] = now + 4   # re-probe in a few seconds
                continue
            det.region = self.state["hud_region"]
            now = time.time()
            # loadout-change detection: periodically check the visible weapon set. If a weapon
            # row appears that wasn't calibrated (loadout swap / a missed column), re-learn.
            if (not (now < self.state["hud_rec_until"]) and now >= self.state["hud_loadout_next"]
                    and not self.state["hud_calibrating"]):
                self.state["hud_loadout_next"] = now + 8.0
                try:
                    vis = det.visible_labels()
                except Exception:
                    vis = set()
                known = set(det.calib.rows) if det.calib else set()
                new_weapons = vis - known
                if new_weapons:
                    self.log(f"New weapon(s) on HUD ({', '.join(sorted(new_weapons))}) — "
                             f"re-learning…", "fx")
                    self.calibrate_core(False)
                    continue
            recording = now < self.state["hud_rec_until"]
            rec_info = None
            if recording:
                try:
                    events, counts, frame, rec_info = det.poll_debug()
                except Exception:
                    events, counts, frame, rec_info = [], {}, None, None
            else:
                try:
                    events, counts = det.poll()
                except Exception:
                    events, counts = [], {}
            self.state["hud_status"] = (("● REC " if recording else "") +
                                        (f"reading {len(counts)}" if counts else "no read"))
            # self-healing: if labels are clearly on screen but we keep reading nothing, the
            # calibration is bad -> drop it and re-learn.
            if not recording:
                if counts:
                    empty_streak = 0
                else:
                    empty_streak += 1
                    if empty_streak >= 40 and not self.state["hud_calibrating"]:  # ~2s
                        try:
                            seen = det.probe()
                        except Exception:
                            seen = 0
                        if seen >= 1:        # HUD really is visible -> calibration is bad
                            self.log("HUD readout stalled — re-learning calibration…", "fx")
                            det.calib = None
                            try:
                                if os.path.exists(self.HUD_CALIB):
                                    os.remove(self.HUD_CALIB)
                            except Exception:
                                pass
                            empty_streak = 0
                            self.state["hud_auto_next"] = 0.0
                            continue
                        empty_streak = 0     # no HUD visible (menu/clear) -> not a fault
            dispatch_plan = dispatch.plan(events, now, last_counter_knock)
            for action in dispatch_plan.actions:
                if action[0] == "flare":
                    self.effects.flare()
                else:                              # ("fire_effect", effect_name)
                    self.effects.fire_effect(action[1])
            for line in dispatch_plan.logs:
                self.log(line, "fx")
            last_counter_knock = dispatch_plan.last_counter_knock
            dispatched = dispatch_plan.dispatched
            # Sustain ONE continuous gun rumble while any rapid weapon is actively firing.
            try:
                gun_firing = any(det.tracker.is_firing(w)
                                 for w, c in hud_detect.WEAPON_CLASS.items() if c == "rapid")
            except Exception:
                gun_firing = False
            if gun_firing:
                self.effects.gun_active(0.18)
            if recording and rec_info is not None:
                n = self.state["hud_rec_n"]
                self.state["hud_rec_n"] = n + 1
                if frame is not None:
                    try:
                        hud_detect.save_gray_png(
                            os.path.join(self.state["hud_rec_dir"], f"f{n:04d}.png"), frame)
                    except Exception:
                        pass
                rec_info["type"] = "frame"
                rec_info["n"] = n
                rec_info["t"] = round(now, 3)
                rec_info["counts"] = counts
                rec_info["dispatched"] = dispatched
                self.rec_write(rec_info)
                if now >= self.state["hud_rec_until"]:
                    d = self.state["hud_rec_dir"]
                    # write the footer BEFORE clearing hud_rec_dir -- rec_write reads
                    # state["hud_rec_dir"] and early-returns when it's None.
                    self.rec_write({"type": "footer", "frames": self.state["hud_rec_n"],
                                    "t": round(now, 3)})
                    self.state["hud_rec_dir"] = None
                    self.log(f"Recording done: {self.state['hud_rec_n']} frames → "
                             f"{os.path.basename(d)}", "fx")
                    self.ui.set_record_button("Record 30s")
            time.sleep(0.02)   # ~20+ Hz poll: faster frames -> quicker confirmation/feel

    # ---- lifecycle ----
    def start_workers(self):
        workers = [self.stick_worker, self.wt_worker]
        if self.hud_available:
            workers.append(self.hud_worker)
        for w in workers:
            threading.Thread(target=w, daemon=True).start()

    def shutdown(self):
        self.state["running"] = False
        self.save_cfg()
        self.effects.stop()
        time.sleep(0.1)
        self.stick.close()
