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
from ..hardware import select_device
from ..effects import Effects
from ..effects import dispatch
from ..sources import WarThunder
from ..sources import killfeed
from ..sources import process as wt_process
from ..sources.marker import KeyMarker, DEFAULT_MARKER
from ..events import EventType

try:
    from ..detection import hud_detect
    HUD_AVAILABLE = True
except Exception:
    hud_detect = None
    HUD_AVAILABLE = False


# Canonical list of toggleable effects (state key is "en_<name>"). Mirrors the effects engine
# triggers; the UI's effect spec presents the same names. All default ON, so adding a key here
# without a saved-config value keeps the original "always fires" behavior.
EFFECT_ENABLE_KEYS = ["gun", "missile", "rocket", "bomb", "flare", "kill", "hit", "death"]


class NullUiBridge:
    """Default no-op UI bridge so the controller works headless / before a GUI attaches.
    The GUI replaces this with one that marshals onto the Tk main thread via root.after."""
    def set_calib_label(self, text, ok=False):
        pass

    def set_record_button(self, text):
        pass


def _screen_size():
    """Primary screen pixel size [w, h] for the recording manifest (display context), or None."""
    try:
        import ctypes
        u = ctypes.windll.user32
        return [int(u.GetSystemMetrics(0)), int(u.GetSystemMetrics(1))]
    except Exception:
        return None


def record_button_label(seconds):
    """Human label for the record button, e.g. 30 -> 'Record 30s', 300 -> 'Record 5min'."""
    s = int(seconds)
    if s % 60 == 0 and s >= 60:
        return f"Record {s // 60}min"
    return f"Record {s}s"


class AppController:
    def __init__(self, base_dir):
        self.base_dir = base_dir
        self.CONFIG = os.path.join(base_dir, config.CONFIG_NAME)
        self.HUD_CALIB = os.path.join(base_dir, config.HUD_CALIB_NAME)
        self.hud_available = HUD_AVAILABLE

        # Flag the whole process as a background / efficiency workload (EcoQoS). On hybrid CPUs
        # Windows then biases us onto efficiency cores, leaving the performance cores for the
        # game. Best-effort: silently no-ops on older Windows. (The detection thread is also set
        # to below-normal priority in hud_worker; together these keep us out of the game's way.)
        try:
            from . import priority
            priority.apply_low_impact()
        except Exception:
            pass

        self.stick = select_device()
        self.effects = Effects(self.stick)
        self.wt = WarThunder()
        self.ui = NullUiBridge()

        self.state = {
            "stick_ok": False, "game_ok": False,
            "wt_open": False,           # War Thunder process/server detected as running
            "last_evt": 0, "last_dmg": 0,
            "hud_on": True,             # auto-detect ON by default (learns the HUD on its own)
            "hud_region": (0, 0, 400, 400),
            "hud_status": "off",
            "hud_det": None,
            "hud_calibrating": False,
            "hud_auto_next": 0.0,
            "hud_loadout_next": 0.0,
            "hud_rec_until": 0.0,
            "hud_rec_dir": None,
            "hud_rec_n": 0,
            # Ground-truth fire marker: while recording, the user taps this (unbound) key at the
            # instant they fire a missile -> a pixel-independent launch record to score detection
            # against. record_seconds is the capture length (longer than 30s for match sessions).
            "marker_key": DEFAULT_MARKER,
            "record_seconds": 30,
            "hud_rec_marks": 0,
            "callsign": "",
            "running": True,
            "firing_gun": False,   # set by the HUD worker; lets the UI light the live gun row
            # plain-bool mirrors of the Tk enable checkboxes, updated from the UI thread.
            # Worker threads read THESE (never the Tk BooleanVars, which aren't thread-safe).
            # All effects default ON; the GUI applies any saved overrides from config.
            **{f"en_{k}": True for k in EFFECT_ENABLE_KEYS},
        }
        self._wt_proc_next = 0.0          # next time to re-check the WT process (slow cadence)
        self._wt_proc_open = False        # last WT process-presence result
        self._loadout_missing = set()     # calibrated rows missing on the PREVIOUS loadout check
                                          # (debounces disappear/move-driven re-learns vs cloud)
        self._marker = None               # KeyMarker, created per recording from marker_key

        self._log_q = queue.Queue()
        # Serialises calibration: the manual 'Re-learn' button and the HUD worker both call
        # calibrate_core() on different threads. A plain bool guard is check-then-set (a TOCTOU
        # race) so both callers could enter at once. This lock makes entry atomic; the loser
        # simply skips (non-blocking acquire) instead of running a second overlapping calibration.
        self._calib_lock = threading.Lock()
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
        self.state["hud_on"] = bool(cfg.get("hud_on", True))
        self.state["callsign"] = cfg.get("callsign", "")
        self.state["marker_key"] = cfg.get("marker_key", DEFAULT_MARKER)
        try:
            self.state["record_seconds"] = max(10, int(cfg.get("record_seconds", 30)))
        except Exception:
            self.state["record_seconds"] = 30
        return cfg.get("enables") or {}

    def save_cfg(self):
        data = {
            "enables": {k: self.state[f"en_{k}"] for k in EFFECT_ENABLE_KEYS},
            "hud_on": self.state["hud_on"],
            "hud_region": list(self.state["hud_region"]),
            "callsign": self.state.get("callsign", ""),
            "record_seconds": int(self.state.get("record_seconds", 30)),
            "marker_key": self.state.get("marker_key", DEFAULT_MARKER),
        }
        config.save(self.CONFIG, data)

    def enabled(self, name):
        """Whether the effect `name` is enabled (defaults True for unknown names)."""
        return bool(self.state.get(f"en_{name}", True))

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
        auto-calibrator and the manual 'Re-learn' button. Guarded by a lock so the two never
        overlap. On success the calibration is saved and fast detection is live."""
        det = self.get_det()
        if det is None:
            return False
        # Non-blocking acquire: if a calibration is already running on another thread, skip
        # rather than queue up a redundant second pass.
        if not self._calib_lock.acquire(blocking=False):
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
            self._calib_lock.release()

    def calibrate_detector(self):
        """Manual 'Re-learn HUD' button -> run calibration in a worker thread."""
        threading.Thread(target=lambda: self.calibrate_core(True), daemon=True).start()

    def start_record(self, duration=None):
        """Begin a diagnostic recording: every polled frame is saved as a PNG and a telemetry
        line is written to telemetry.jsonl. The HUD worker does the actual capture so we record
        exactly what detection sees. `duration` (seconds) defaults to state['record_seconds'].

        While recording, the configured fire-marker key is polled each frame: a key-down is
        logged as a {"type":"marker"} line -- pixel-independent ground truth for offline scoring
        of missile detection (see sources/marker.py)."""
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
        dur = float(duration if duration is not None else self.state.get("record_seconds", 30))
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
        marker_key = self.state.get("marker_key", DEFAULT_MARKER)
        self._marker = KeyMarker(marker_key)
        self._marker.reset()
        header = {
            "type": "header", "time": ts,
            "region": list(self.state["hud_region"]),
            "screen": _screen_size(),
            "duration_s": dur,
            "marker_key": marker_key,
            "marker_available": self._marker.available,
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
        self.state["hud_rec_marks"] = 0
        self.state["hud_rec_until"] = time.time() + dur
        mk = marker_key if self._marker.available else f"{marker_key}(?)"
        # Rough disk estimate: full-frame PNGs dominate (~56 KB each at ~18 Hz ~= 1 MB/s);
        # telemetry is negligible. Shown so a long session's footprint is no surprise.
        est_mb = int(dur * 1.0)
        self.log(f"Recording {dur:.0f}s (~{est_mb} MB, mark key: {mk}) → "
                 f"{os.path.basename(rec_dir)} …", "fx")
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

    def _refresh_wt_open(self, server_up):
        """Decide whether War Thunder is OPEN. The localhost telemetry server being reachable
        (server_up) proves it, but some players disable that server while HUD detection (pure
        screen capture) still works -- so we ALSO check the client process on a slow cadence.
        wt_open = (server reachable) OR (aces.exe running). Process polling is cheap but not
        free, so it runs every ~2s, not every fast tick."""
        now = time.time()
        if now >= self._wt_proc_next:
            self._wt_proc_next = now + 2.0
            try:
                self._wt_proc_open = wt_process.is_warthunder_running()
            except Exception:
                self._wt_proc_open = False
        self.state["wt_open"] = bool(server_up or self._wt_proc_open)

    def wt_worker(self):
        hud_seeded = False
        cyc = 0
        while self.state["running"]:
            # GUN: poll the trigger indicator FAST. weapon2 is the actual trigger-input state
            # (zero visual lag, unlike reading the HUD ammo counter), so the lowest-latency,
            # most reliable gun signal is this localhost value polled quickly.
            ind = self.wt.indicators()
            server_up = ind is not None            # any response => WT's web server is up
            if isinstance(ind, dict) and ind.get("valid"):
                self.state["game_ok"] = True
                w2 = ind.get("weapon2", 0.0) or 0.0
                if self.state["en_gun"] and w2 >= 1.0:
                    self.effects.gun_active(0.18)
            else:
                self.state["game_ok"] = False
            # "WT open" (process or server) gates HUD scanning so it only runs when the game can
            # actually be on screen -- never churning calibration on the desktop/other windows.
            self._refresh_wt_open(server_up)
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
        # Run the detection loop at BELOW_NORMAL priority so War Thunder (Normal/Above-Normal)
        # always preempts it -- the haptics never steal CPU the game wants. Only THIS thread is
        # de-prioritised; the UI thread stays at normal priority so the window stays responsive.
        try:
            from . import priority
            if priority.lower_current_thread(True):
                self.log("Detection running at low priority (won't steal CPU from the game).", "fx")
        except Exception:
            pass
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
            # Only scan when War Thunder is actually open. Off-game (desktop / other windows)
            # there is nothing to read, and probing/calibrating there would churn or mis-learn.
            # While recording we keep going regardless (the user is deliberately capturing).
            if not self.state["wt_open"] and not (time.time() < self.state["hud_rec_until"]):
                self.state["hud_status"] = "waiting for War Thunder…"
                det.reset()
                time.sleep(0.5)
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
            # Loadout / row-change detection: periodically compare the VISIBLE weapon set to the
            # calibrated one and re-learn so the system tracks rows as they appear, disappear, or
            # move. Checked every ~3s for responsiveness.
            #   * APPEAR  (a visible weapon we never calibrated) -> re-learn IMMEDIATELY. A freshly
            #     OCR'd valid weapon token is high-confidence (cloud rarely forms one), and it
            #     means a real loadout add / a row that just became available.
            #   * DISAPPEAR/MOVE (a calibrated weapon no longer seen while others still are) ->
            #     re-learn only if it PERSISTS across two consecutive checks. A single washed-out
            #     frame can transiently hide a row, so debouncing avoids churning calibration
            #     (which is blocking and resets the tracker) on mere cloud flicker.
            if (not (now < self.state["hud_rec_until"]) and now >= self.state["hud_loadout_next"]
                    and not self.state["hud_calibrating"]):
                self.state["hud_loadout_next"] = now + 3.0
                try:
                    vis = det.visible_labels()
                except Exception:
                    vis = set()
                known = set(det.calib.rows) if det.calib else set()
                new_weapons = vis - known
                missing = (known - vis) if vis else set()      # ignore "all gone" (menu/blank)
                if new_weapons:
                    self._loadout_missing = set()
                    self.log(f"New weapon(s) on HUD ({', '.join(sorted(new_weapons))}) — "
                             f"re-learning…", "fx")
                    self.calibrate_core(False)
                    continue
                if missing and missing == self._loadout_missing:
                    self._loadout_missing = set()
                    self.log(f"HUD rows changed ({', '.join(sorted(missing))} moved/gone) — "
                             f"re-learning…", "fx")
                    self.calibrate_core(False)
                    continue
                self._loadout_missing = missing                # remember for the debounce
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
                    if self.enabled("flare"):
                        self.effects.flare()
                else:                              # ("fire_effect", effect_name)
                    if self.enabled(action[1]):
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
            if gun_firing and self.enabled("gun"):
                self.effects.gun_active(0.18)
            self.state["firing_gun"] = bool(gun_firing)
            if recording and rec_info is not None:
                n = self.state["hud_rec_n"]
                self.state["hud_rec_n"] = n + 1
                # Ground-truth fire marker: a key-down on the configured (unbound) key means the
                # user just fired. Log it as its own line AND tag the frame, so offline scoring
                # can align real launches to detector fires without any hand-labelling.
                marked = False
                if self._marker is not None:
                    try:
                        marked = self._marker.poll()
                    except Exception:
                        marked = False
                if marked:
                    self.state["hud_rec_marks"] += 1
                    self.rec_write({"type": "marker", "n": n, "t": round(now, 3),
                                    "idx": self.state["hud_rec_marks"]})
                if frame is not None:
                    try:
                        hud_detect.save_gray_png(
                            os.path.join(self.state["hud_rec_dir"], f"f{n:06d}.png"), frame)
                    except Exception:
                        pass
                rec_info["type"] = "frame"
                rec_info["n"] = n
                rec_info["t"] = round(now, 3)
                rec_info["counts"] = counts
                rec_info["dispatched"] = dispatched
                rec_info["mark"] = marked
                self.rec_write(rec_info)
                if now >= self.state["hud_rec_until"]:
                    d = self.state["hud_rec_dir"]
                    # write the footer BEFORE clearing hud_rec_dir -- rec_write reads
                    # state["hud_rec_dir"] and early-returns when it's None.
                    self.rec_write({"type": "footer", "frames": self.state["hud_rec_n"],
                                    "marks": self.state["hud_rec_marks"], "t": round(now, 3)})
                    self.state["hud_rec_dir"] = None
                    self._marker = None
                    self.log(f"Recording done: {self.state['hud_rec_n']} frames, "
                             f"{self.state['hud_rec_marks']} marks → "
                             f"{os.path.basename(d)}", "fx")
                    self.ui.set_record_button(
                        record_button_label(self.state.get("record_seconds", 30)))
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
