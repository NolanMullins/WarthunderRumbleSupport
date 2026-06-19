"""
WinwingHaptics — lightweight War Thunder -> Winwing Ursa Minor vibration bridge.

Pure Python standard library + NumPy + winsdk (Windows OCR). Windows built-in DLLs via
ctypes:
  * HID (find/open/write the stick)  -> hid.dll, setupapi.dll, kernel32.dll  (built into Windows)
  * War Thunder telemetry             -> http.client                         (stdlib)
  * GUI                               -> tkinter                             (stdlib)
  * HUD weapon-counter detection      -> hud_detect.py (numpy + winsdk OCR)

Packaged to a standalone app with PyInstaller --onedir (NOT --onefile: WDAC environments
block single-file extraction). See README for the exact build command.

Vibration protocol (decoded from SimApp Pro capture):
  ARM/heartbeat  : 02 01 00 00 00 01 00 ...           (resend ~every 2.5s)
  Set intensity  : 02 0A BF 00 00 03 49 00 <0..255> ...(device holds level; 0 = stop)

Signal sources:
  HUD ammo counters (hud_detect)  -> gun / missile / rocket / bomb / flare / chaff fires
                                     (a counter ticking down = that weapon fired)
  /indicators weapon2 == 1.0      -> cannon/gun trigger (fast, low-latency rumble onset)
  /hudmsg damage[]                -> kills / hits / death (text feed, callsign-matched)

CLI:
  python winwing_haptics.py            -> launch GUI
  python winwing_haptics.py --selftest -> open stick, arm, play missile effect, exit
  python winwing_haptics.py --hudtest  -> verify HUD detector + OCR deps (for the build)
"""

import sys
import os
import time
import json
import queue
import threading
import ctypes

# Optional HUD auto-detect (numpy + winsdk OCR). Imported lazily/guarded so the app
# still runs if these aren't present.
try:
    import winwinghaptics.detection.hud_detect as hud_detect
    _HUD_AVAILABLE = True
except Exception:
    hud_detect = None
    _HUD_AVAILABLE = False

# ----------------------------------------------------------------------------------------
# HID layer — extracted to winwinghaptics.hardware. `Stick` is the Winwing device (kept as an
# alias so the effects engine + app are unchanged this phase).
# ----------------------------------------------------------------------------------------

from winwinghaptics.hardware import Stick  # noqa: E402


# ----------------------------------------------------------------------------------------
# War Thunder telemetry poller — extracted to winwinghaptics.sources.
# ----------------------------------------------------------------------------------------

from winwinghaptics.sources import WarThunder  # noqa: E402


# ----------------------------------------------------------------------------------------
# Effect engine — envelopes over Stick.vib(), serialized on one worker thread
# ----------------------------------------------------------------------------------------

# ----------------------------------------------------------------------------------------
# Effect engine — extracted to winwinghaptics.effects (data-driven library + engine).
# `Effects` is an alias of EffectsEngine so the app + selftest are unchanged.
# ----------------------------------------------------------------------------------------

from winwinghaptics.effects import Effects  # noqa: E402


# ----------------------------------------------------------------------------------------
# GUI
# ----------------------------------------------------------------------------------------

def run_gui():
    import tkinter as tk
    from tkinter import font as tkfont

    # Make the process DPI-aware so text renders crisp at native resolution
    # (otherwise Windows bitmap-stretches the window and it looks blurry).
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_AWARE
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

    # --- palette ---
    BG       = "#0f1216"   # window background
    PANEL    = "#171c22"   # card background
    PANEL2   = "#1e252d"   # nested / log background
    FG       = "#e6edf3"   # primary text
    MUTED    = "#8b97a4"   # secondary text
    ACCENT   = "#ff7a18"   # winwing-ish orange
    GREEN    = "#33d17a"
    RED      = "#e5484d"
    GREYDOT  = "#566270"

    stick = Stick()
    effects = Effects(stick)
    wt = WarThunder()

    root = tk.Tk()
    root.title("Winwing Haptics")
    root.geometry("452x740")
    root.minsize(452, 700)
    root.configure(bg=BG)

    f_title = tkfont.Font(family="Segoe UI Semibold", size=13)
    f_sub   = tkfont.Font(family="Segoe UI", size=8)
    f_body  = tkfont.Font(family="Segoe UI", size=9)
    f_small = tkfont.Font(family="Segoe UI", size=8)
    f_mono  = tkfont.Font(family="Consolas", size=8)

    state = {
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

    # ---- config persistence (next to the exe/script) ----
    base_dir = os.path.dirname(os.path.abspath(
        sys.executable if getattr(sys, "frozen", False) else __file__))
    CONFIG = os.path.join(base_dir, "winwing_haptics.json")
    HUD_CALIB = os.path.join(base_dir, "hud_calib.json")

    def load_cfg():
        try:
            with open(CONFIG, encoding="utf-8") as fh:
                cfg = json.load(fh)
            if cfg.get("hud_region"):
                state["hud_region"] = tuple(cfg["hud_region"])
            state["hud_on"] = bool(cfg.get("hud_on", False))
            state["callsign"] = cfg.get("callsign", "")
            return cfg.get("enables") or {}
        except Exception:
            return {}

    def save_cfg():
        try:
            data = {
                "enables": {"gun": en_gun.get(), "kill": en_kill.get(),
                            "hit": en_hit.get(), "death": en_death.get()},
                "hud_on": state["hud_on"],
                "hud_region": list(state["hud_region"]),
                "callsign": state.get("callsign", ""),
            }
            with open(CONFIG, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2)
        except Exception:
            pass

    def card(parent, pad=10):
        c = tk.Frame(parent, bg=PANEL, highlightthickness=1, highlightbackground="#262d36")
        c.pack(fill="x", padx=12, pady=(0, 8))
        inner = tk.Frame(c, bg=PANEL)
        inner.pack(fill="x", padx=pad, pady=pad)
        return inner

    # ---------- Header ----------
    header = tk.Frame(root, bg=BG)
    header.pack(fill="x", padx=14, pady=(12, 10))
    bar = tk.Frame(header, bg=ACCENT, width=3, height=30)
    bar.pack(side="left", padx=(0, 9))
    bar.pack_propagate(False)
    htext = tk.Frame(header, bg=BG)
    htext.pack(side="left")
    tk.Label(htext, text="Winwing Haptics", bg=BG, fg=FG, font=f_title).pack(anchor="w")
    tk.Label(htext, text="War Thunder → Ursa Minor rumble", bg=BG, fg=MUTED,
             font=f_sub).pack(anchor="w")

    # ---------- Status card ----------
    st = card(root)
    def status_row(parent, label):
        row = tk.Frame(parent, bg=PANEL)
        row.pack(fill="x", pady=2)
        dot = tk.Canvas(row, width=12, height=12, bg=PANEL, highlightthickness=0)
        dot.pack(side="left")
        oid = dot.create_oval(2, 2, 11, 11, fill=GREYDOT, outline="")
        tk.Label(row, text=label, bg=PANEL, fg=FG, font=f_body).pack(side="left", padx=7)
        val = tk.Label(row, text="—", bg=PANEL, fg=MUTED, font=f_small)
        val.pack(side="right")
        return dot, oid, val
    stick_dot, stick_oid, stick_val = status_row(st, "Joystick")
    game_dot, game_oid, game_val = status_row(st, "War Thunder")

    # ---------- helpers (log + buttons) defined early so cards can use them ----------
    def style_btn(parent, text, cmd, primary=False, small=False):
        bgc = ACCENT if primary else PANEL2
        fgc = "#1a1109" if primary else FG
        b = tk.Label(parent, text=text, bg=bgc, fg=fgc, font=f_body,
                     padx=(8 if small else 11), pady=(4 if small else 6), cursor="hand2")
        def on_enter(_): b.configure(bg=("#ff9442" if primary else "#27303a"))
        def on_leave(_): b.configure(bg=bgc)
        b.bind("<Enter>", on_enter); b.bind("<Leave>", on_leave)
        b.bind("<Button-1>", lambda _e: cmd())
        return b

    # ---------- Weapon haptics (HUD-driven) info ----------
    wb = card(root)
    tk.Label(wb, text="WEAPON HAPTICS", bg=PANEL, fg=MUTED, font=f_small).pack(anchor="w")
    tk.Label(wb, text="Missiles, rockets, bombs, guns and countermeasures are detected "
             "automatically from the HUD (enable below). Test the effects here:",
             bg=PANEL, fg=MUTED, font=f_small, anchor="w", wraplength=400,
             justify="left").pack(anchor="w", pady=(2, 6))
    testrow = tk.Frame(wb, bg=PANEL); testrow.pack(fill="x")
    style_btn(testrow, "Missile", lambda: effects.missile(), small=True).pack(side="left")
    style_btn(testrow, "Rocket", lambda: effects.rocket(), small=True).pack(side="left", padx=4)
    style_btn(testrow, "Bomb", lambda: effects.bomb(), small=True).pack(side="left")
    style_btn(testrow, "Gun", lambda: effects.gun_active(0.4), small=True).pack(side="left", padx=4)
    style_btn(testrow, "Flare", lambda: effects.flare(), small=True).pack(side="left")

    # ---------- HUD auto-detect card ----------
    hud_card = card(root)
    hrow = tk.Frame(hud_card, bg=PANEL); hrow.pack(fill="x")
    tk.Label(hrow, text="HUD AUTO-DETECT", bg=PANEL, fg=MUTED, font=f_small).pack(side="left")
    hud_state_lbl = tk.Label(hrow, text="off", bg=PANEL, fg=MUTED, font=f_small)
    hud_state_lbl.pack(side="right")
    en_hud = tk.BooleanVar(value=state["hud_on"])

    def toggle_hud():
        state["hud_on"] = en_hud.get()
        save_cfg()
        if state["hud_on"] and not _HUD_AVAILABLE:
            log("HUD auto-detect unavailable (OCR engine/numpy missing).")
        else:
            log(f"HUD auto-detect {'enabled' if state['hud_on'] else 'disabled'}.")

    if _HUD_AVAILABLE:
        tk.Checkbutton(hud_card, text="Read weapon counts from screen (AAM/RKT/BMB/FLR/CHFF)",
                       variable=en_hud, command=toggle_hud, bg=PANEL, fg=FG,
                       activebackground=PANEL, activeforeground=FG, selectcolor=PANEL2,
                       font=f_body, anchor="w", highlightthickness=0, bd=0,
                       wraplength=380, justify="left").pack(anchor="w", pady=(4, 2))
        hud_btns = tk.Frame(hud_card, bg=PANEL); hud_btns.pack(fill="x")
        style_btn(hud_btns, "Set Region", lambda: calibrate_hud(), small=True).pack(side="left")
        style_btn(hud_btns, "Re-learn HUD", lambda: calibrate_detector(), small=True).pack(side="left", padx=(6, 0))
        hud_rec_btn = style_btn(hud_btns, "Record 30s", lambda: start_record(), small=True)
        hud_rec_btn.pack(side="left", padx=(6, 0))
        rg = state["hud_region"]
        hud_region_lbl = tk.Label(hud_card, text=f"region: {rg[0]},{rg[1]} {rg[2]}x{rg[3]}",
                                  bg=PANEL, fg=MUTED, font=f_small)
        hud_region_lbl.pack(anchor="w", pady=(2, 0))
        hud_calib_lbl = tk.Label(hud_card,
                                 text="Auto-learns your HUD the first time it sees the counters in a match. "
                                      "Use 'Re-learn HUD' only if the readout looks wrong.",
                                 bg=PANEL, fg=MUTED, font=f_small, wraplength=380, justify="left")
        hud_calib_lbl.pack(anchor="w", pady=(2, 0))
    else:
        tk.Label(hud_card, text="Unavailable in this build (needs OCR engine).",
                 bg=PANEL, fg=MUTED, font=f_small).pack(anchor="w", pady=2)
        hud_region_lbl = None
        hud_calib_lbl = None
        hud_rec_btn = None

    # ---------- Effects (outcome) card ----------
    ef = card(root)
    tk.Label(ef, text="OUTCOME EFFECTS", bg=PANEL, fg=MUTED, font=f_small).pack(anchor="w", pady=(0, 4))
    en_gun = tk.BooleanVar(value=True)
    en_kill = tk.BooleanVar(value=True)
    en_hit = tk.BooleanVar(value=True)
    en_death = tk.BooleanVar(value=True)

    def sync_enables():
        """Mirror the Tk enable vars into the plain `state` dict. Called only from the UI
        thread (checkbox command + after config load). Worker threads read state[...] so
        they never touch the Tk vars (which are not thread-safe)."""
        state["en_gun"] = bool(en_gun.get())
        state["en_kill"] = bool(en_kill.get())
        state["en_hit"] = bool(en_hit.get())
        state["en_death"] = bool(en_death.get())

    def chk(parent, text, var, r, col):
        cb = tk.Checkbutton(parent, text=text, variable=var, bg=PANEL, fg=FG,
                            activebackground=PANEL, activeforeground=FG,
                            selectcolor=PANEL2, font=f_body, anchor="w",
                            highlightthickness=0, bd=0, padx=2,
                            command=lambda: (sync_enables(), save_cfg()))
        cb.grid(row=r, column=col, sticky="w", padx=(0, 10), pady=1)

    grid = tk.Frame(ef, bg=PANEL); grid.pack(fill="x")
    chk(grid, "Gun rumble", en_gun, 0, 0)
    chk(grid, "Kill confirm", en_kill, 0, 1)
    chk(grid, "Took a hit", en_hit, 1, 0)
    chk(grid, "Death", en_death, 1, 1)

    # callsign (needed so kill/death only fire for YOU, not every player in the match)
    csrow = tk.Frame(ef, bg=PANEL); csrow.pack(fill="x", pady=(6, 0))
    tk.Label(csrow, text="Your callsign:", bg=PANEL, fg=MUTED, font=f_small).pack(side="left")
    callsign_var = tk.StringVar(value="")
    cs_entry = tk.Entry(csrow, textvariable=callsign_var, bg=PANEL2, fg=FG, font=f_small,
                        insertbackground=FG, relief="flat", width=18)
    cs_entry.pack(side="left", padx=6, ipady=2)

    def on_callsign(*_):
        state["callsign"] = callsign_var.get().strip()
        save_cfg()
    callsign_var.trace_add("write", on_callsign)
    tk.Label(ef, text="(in-game name — kill/death effects only fire for you)",
             bg=PANEL, fg=MUTED, font=f_small).pack(anchor="w")

    # ---------- Log ----------
    logcard = tk.Frame(root, bg=PANEL, highlightthickness=1, highlightbackground="#262d36")
    logcard.pack(fill="both", expand=True, padx=12, pady=(0, 12))
    tk.Label(logcard, text="ACTIVITY", bg=PANEL, fg=MUTED, font=f_small).pack(anchor="w", padx=10, pady=(8, 2))
    logwrap = tk.Frame(logcard, bg=PANEL); logwrap.pack(fill="both", expand=True, padx=8, pady=(0, 8))
    txt = tk.Text(logwrap, height=6, state="disabled", bg=PANEL2, fg="#aeb9c4",
                  insertbackground=FG, font=f_mono, relief="flat", bd=0,
                  padx=6, pady=4, wrap="word")
    sb = tk.Scrollbar(logwrap, command=txt.yview)
    txt.configure(yscrollcommand=sb.set)
    txt.pack(side="left", fill="both", expand=True)
    sb.pack(side="right", fill="y")
    txt.tag_config("kill", foreground=GREEN)
    txt.tag_config("death", foreground=RED)
    txt.tag_config("fx", foreground=ACCENT)
    txt.tag_config("wt", foreground=MUTED)

    # Thread-safe logging: workers/effect threads must NOT touch the Tk Text widget directly
    # (Tkinter is single-threaded). They push lines onto a queue; a pump on the UI thread
    # (_drain_log, scheduled via root.after) renders them. log() is therefore safe to call
    # from any thread.
    _log_q = queue.Queue()

    def log(msg, tag=None):
        ts = time.strftime("%H:%M:%S")
        _log_q.put((ts, msg, tag or ""))

    def _drain_log():
        try:
            pending = []
            while True:
                pending.append(_log_q.get_nowait())
        except queue.Empty:
            pass
        if pending:
            txt.configure(state="normal")
            for ts, msg, tag in pending:
                txt.insert("end", f"{ts}  ", "")
                txt.insert("end", f"{msg}\n", (tag,))
            txt.see("end")
            txt.configure(state="disabled")
        if state["running"]:
            root.after(80, _drain_log)

    effects.log = lambda m: log(m, "fx")

    # --- HUD region calibration: drag a rectangle over the weapon counters ---
    def calibrate_hud():
        ov = tk.Toplevel(root)
        ov.attributes("-fullscreen", True)
        ov.attributes("-alpha", 0.25)
        ov.configure(bg="#000000", cursor="crosshair")
        ov.attributes("-topmost", True)
        cv = tk.Canvas(ov, bg="#101418", highlightthickness=0)
        cv.pack(fill="both", expand=True)
        cv.create_text(ov.winfo_screenwidth() // 2, 40,
                       text="Drag a box around the weapon counters (RKT/BMB/AAM/FLR/CHFF/CNN). "
                            "Esc to cancel.", fill="#ffffff", font=f_body)
        sel = {"x0": 0, "y0": 0, "rect": None}

        def on_down(e):
            sel["x0"], sel["y0"] = e.x_root, e.y_root
            if sel["rect"]:
                cv.delete(sel["rect"])
            sel["rect"] = cv.create_rectangle(e.x, e.y, e.x, e.y,
                                              outline="#ff7a18", width=2)
            sel["cx0"], sel["cy0"] = e.x, e.y

        def on_move(e):
            if sel["rect"]:
                cv.coords(sel["rect"], sel["cx0"], sel["cy0"], e.x, e.y)

        def on_up(e):
            x0, y0 = sel["x0"], sel["y0"]
            x1, y1 = e.x_root, e.y_root
            l, t = min(x0, x1), min(y0, y1)
            w, h = abs(x1 - x0), abs(y1 - y0)
            ov.destroy()
            if w > 30 and h > 20:
                state["hud_region"] = (l, t, w, h)
                save_cfg()
                if hud_region_lbl:
                    hud_region_lbl.config(text=f"region: {l},{t} {w}x{h}")
                log(f"HUD region set: {l},{t} {w}x{h}")

        def on_esc(_):
            ov.destroy()

        cv.bind("<Button-1>", on_down)
        cv.bind("<B1-Motion>", on_move)
        cv.bind("<ButtonRelease-1>", on_up)
        ov.bind("<Escape>", on_esc)

    # --- shared detector + one-time template calibration ---
    def get_det():
        if state["hud_det"] is None and _HUD_AVAILABLE:
            try:
                d = hud_detect.HudDetector(region=state["hud_region"])
                if os.path.exists(HUD_CALIB):
                    try:
                        d.load(HUD_CALIB)
                        if d.calibrated:
                            d.region = state["hud_region"]
                    except Exception:
                        # stale/incompatible calibration from an older version -> discard,
                        # the app will simply auto-recalibrate. Never let it break boot.
                        try:
                            os.remove(HUD_CALIB)
                        except Exception:
                            pass
                state["hud_det"] = d
            except Exception as e:
                log(f"HUD detector unavailable: {e}")
                return None
        return state["hud_det"]

    def _set_calib_lbl(txt, ok=False):
        try:
            if hud_calib_lbl:
                hud_calib_lbl.config(text=txt, fg=(GREEN if ok else MUTED))
        except Exception:
            pass

    def _calibrate_core(manual):
        """Run the one-time OCR calibration (blocking ~4s). Shared by the silent
        auto-calibrator and the manual 'Re-learn' button. Guarded so the two never
        overlap. On success the calibration is saved and fast detection is live."""
        det = get_det()
        if det is None or state["hud_calibrating"]:
            return False
        state["hud_calibrating"] = True
        try:
            det.region = state["hud_region"]
            state["hud_status"] = "learning HUD…"
            if manual:
                root.after(0, lambda: _set_calib_lbl("learning your HUD… keep counters visible (~3s)"))
            ok, msg = det.calibrate(n_frames=12, interval=0.25)
            if ok:
                det.save(HUD_CALIB)
                root.after(0, lambda: _set_calib_lbl(msg, ok=True))
                log(f"HUD learned ({msg}). Fast detection active.", "fx")
            elif manual:
                root.after(0, lambda: _set_calib_lbl("couldn't find counters: " + msg))
                log("HUD calibration failed: " + msg)
            return ok
        finally:
            state["hud_calibrating"] = False

    def calibrate_detector():
        """Manual 'Re-learn HUD' button -> run calibration in a worker thread."""
        threading.Thread(target=lambda: _calibrate_core(True), daemon=True).start()

    def start_record():
        """Begin a 30s diagnostic recording: every polled frame is saved as a PNG and a
        telemetry line (reads+confidence, baselines, pending state, events, dispatched
        effects, timing) is written to telemetry.jsonl. The HUD worker does the actual
        capture so we record exactly what detection sees."""
        if not _HUD_AVAILABLE:
            return
        if state["hud_rec_until"] > time.time():
            return  # already recording
        det = get_det()
        if det is None:
            return
        if not state["hud_on"]:
            log("Enable HUD auto-detect before recording.")
            return
        ts = time.strftime("%Y%m%d_%H%M%S")
        rec_dir = os.path.join(base_dir, f"hud_rec_{ts}")
        try:
            os.makedirs(rec_dir, exist_ok=True)
        except Exception as e:
            log(f"Record failed: {e}")
            return
        # Dump the FULL calibration (geometry + harvested glyph/label templates) to a
        # sidecar calib.json so the recording can be RE-DETECTED offline with the exact
        # live calibration -- the header geometry alone is not enough to reproduce reads
        # (the OCR-harvested templates differ each calibration). This makes the recording a
        # faithful detector A/B fixture.
        calib_saved = False
        if det.calibrated and det.calib is not None:
            try:
                with open(os.path.join(rec_dir, "calib.json"), "w", encoding="utf-8") as cf:
                    json.dump(det.calib.to_dict(), cf)
                calib_saved = True
            except Exception as e:
                log(f"calib dump failed: {e}", "fx")
        # header with calibration + region so the recording is self-describing
        header = {
            "type": "header", "time": ts,
            "region": list(state["hud_region"]),
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
            log(f"Record failed: {e}")
            return
        state["hud_rec_dir"] = rec_dir
        state["hud_rec_n"] = 0
        state["hud_rec_until"] = time.time() + 30.0
        log(f"Recording 30s → {os.path.basename(rec_dir)} …", "fx")
        try:
            hud_rec_btn.config(text="● Recording…")
        except Exception:
            pass

    def _rec_write(line):
        d = state["hud_rec_dir"]
        if not d:
            return
        try:
            with open(os.path.join(d, "telemetry.jsonl"), "a", encoding="utf-8") as fh:
                fh.write(json.dumps(line) + "\n")
        except Exception:
            pass

    # load saved config now that controls exist
    saved = load_cfg()
    if saved:
        en_gun.set(saved.get("gun", True))
        en_kill.set(saved.get("kill", True))
        en_hit.set(saved.get("hit", True))
        en_death.set(saved.get("death", True))
    sync_enables()   # seed the worker-visible mirrors from the loaded checkbox states
    callsign_var.set(state.get("callsign", ""))
    if _HUD_AVAILABLE:
        _d0 = get_det()
        if _d0 is not None and _d0.calibrated:
            _set_calib_lbl("HUD learned (%s). Re-learn only if readout looks wrong."
                           % ", ".join(sorted(_d0.calib.rows)), ok=True)

    # --- worker: stick connection + heartbeat ---
    def stick_worker():
        while state["running"]:
            if not stick.is_open():
                if stick.open():
                    state["stick_ok"] = True
                    effects.start_heartbeat()
                    log("Stick connected.")
                else:
                    state["stick_ok"] = False
            time.sleep(1.0)

    # --- worker: War Thunder telemetry ---
    def wt_worker():
        hud_seeded = False
        cyc = 0
        while state["running"]:
            # GUN: poll the trigger indicator FAST. weapon2 is the actual trigger-input state
            # (zero visual lag, unlike reading the HUD ammo counter), so the lowest-latency,
            # most reliable gun signal is this localhost value polled quickly. At ~50 ms the
            # rumble starts within one tick of pulling the trigger instead of up to the old
            # 300 ms. A short 0.18 s sustain (re-armed every fast tick while held) keeps the
            # buzz continuous yet stops promptly on release.
            ind = wt.indicators()
            if isinstance(ind, dict) and ind.get("valid"):
                state["game_ok"] = True
                w2 = ind.get("weapon2", 0.0) or 0.0
                if state["en_gun"] and w2 >= 1.0:
                    effects.gun_active(0.18)
            else:
                state["game_ok"] = False
            # KILL/DEATH feed: only needs ~300 ms cadence. Poll the text feed INDEPENDENTLY of
            # indicators validity (/hudmsg works whenever WT's web server is up, even when the
            # cockpit indicators don't populate), every ~6th fast tick.
            if cyc % 6 == 0:
                hud = wt.hudmsg(state["last_evt"], state["last_dmg"])
                if isinstance(hud, dict):
                    dmgs = hud.get("damage", []) or []
                    for d in dmgs:
                        if isinstance(d, dict) and "id" in d:
                            state["last_dmg"] = max(state["last_dmg"], int(d["id"]))
                        msg = (d.get("msg") or "") if isinstance(d, dict) else ""
                        # On the FIRST poll WT replays the whole backlog (lastDmg started at 0);
                        # absorb it as baseline so we don't fire for kills that happened before
                        # the app was watching.
                        if hud_seeded and msg:
                            handle_damage(msg)
                    if not hud_seeded:
                        hud_seeded = True
                    # also drain the 'events' list ids so the cursor advances cleanly
                    for e in hud.get("events", []) or []:
                        if isinstance(e, dict) and "id" in e:
                            state["last_evt"] = max(state["last_evt"], int(e["id"]))
            cyc += 1
            time.sleep(0.05)

    def handle_damage(msg):
        if not msg:
            return
        # Always surface the raw kill-feed line so it's visible WHAT the game sent and
        # whether matching worked -- this is the fastest way to diagnose "callsign effects
        # don't fire" (wrong callsign, squadron tag, unexpected verb, localisation, etc.).
        log(f"WT: {msg}", "wt")
        cs_raw = (state.get("callsign") or "").strip().lower()
        if not cs_raw:
            return
        # Robust name matching: pull alphanumeric tokens (len>=3) from the entered callsign
        # so a squadron prefix on either side (e.g. "=GRIND= DEERSLUG") still matches when
        # the user typed just "DEERSLUG".
        import re as _re
        cs_tokens = [t for t in _re.findall(r"[a-z0-9]+", cs_raw) if len(t) >= 3]
        if not cs_tokens:
            cs_tokens = [cs_raw]

        def is_me(segment):
            seg = segment.lower()
            return any(t in seg for t in cs_tokens)

        low = msg.lower()
        # WT kill-feed verbs vary by mode/locale. Cover the common English ones; the verb
        # splits the line into attacker (left) and victim (right).
        kill_verbs = (" destroyed ", " shot down ", " has shot down ", " wrecked ",
                      " set afire ", " severely damaged ", " has destroyed ")
        crash_terms = ("has crashed", "has been wrecked", "wasted", "crashed")

        for verb in kill_verbs:
            if verb in low:
                attacker, victim = low.split(verb, 1)
                if is_me(attacker):
                    if state["en_kill"]:
                        effects.kill()
                    log(f"KILL  {msg}", "kill")
                elif is_me(victim):
                    if state["en_death"]:
                        effects.death()
                    log(f"DEATH  {msg}", "death")
                return
        # self-inflicted / crash with no attacker verb
        if any(t in low for t in crash_terms) and is_me(low):
            if state["en_death"]:
                effects.death()
            log(f"DEATH  {msg}", "death")
            return
        # Non-fatal hit: a milder damage verb where YOU are the victim. Runs only after the
        # kill/death checks (severe "destroyed/shot down/severely damaged" already returned),
        # so this fires the lighter "took a hit" bump rather than a death. Verbs vary by mode/
        # locale; the raw WT: line above lets the user confirm/extend these.
        hit_verbs = (" hit ", " damaged ", " has damaged ", " set on fire ")
        for verb in hit_verbs:
            if verb in low:
                attacker, victim = low.split(verb, 1)
                if is_me(victim):
                    if state["en_hit"]:
                        effects.hit()
                    log(f"HIT  {msg}", "fx")
                return

    # --- worker: HUD auto-detect (screen OCR of weapon counters) ---
    def hud_worker():
        if not _HUD_AVAILABLE:
            return
        det = get_det()
        if det is None:
            state["hud_status"] = "unavailable"
            return
        last_counter_knock = 0.0
        empty_streak = 0          # consecutive live frames that read nothing
        while state["running"]:
            if not state["hud_on"]:
                state["hud_status"] = "off"
                det.reset()
                time.sleep(0.5)
                continue
            if not det.available:
                state["hud_status"] = "unavailable"
                time.sleep(1.0)
                continue
            if not det.calibrated:
                # Auto-calibrate silently: when the player is in a match with counters
                # visible, learn the HUD once in the background. A cheap one-frame probe
                # gates the expensive OCR so we don't churn in menus/clear sky.
                now = time.time()
                if state["hud_calibrating"]:
                    state["hud_status"] = "learning HUD…"
                    time.sleep(0.4)
                    continue
                if now < state["hud_auto_next"]:
                    state["hud_status"] = "waiting for HUD…"
                    time.sleep(0.4)
                    continue
                det.region = state["hud_region"]
                try:
                    seen = det.probe()
                except Exception:
                    seen = 0
                if seen >= 1:
                    _calibrate_core(False)              # blocking ~4s in this worker
                    state["hud_auto_next"] = time.time() + 3
                else:
                    state["hud_status"] = "waiting for HUD…"
                    state["hud_auto_next"] = now + 4    # re-probe in a few seconds
                continue
            det.region = state["hud_region"]
            now = time.time()
            # --- loadout-change detection: periodically check the visible weapon set. If a
            # weapon row appears that wasn't calibrated (loadout swap, or a column the
            # one-time calibration missed), re-learn so the new weapon is tracked. Cheap-ish
            # (one OCR pass) and throttled, and skipped while recording so it can't stall it.
            if (not (now < state["hud_rec_until"]) and now >= state["hud_loadout_next"]
                    and not state["hud_calibrating"]):
                state["hud_loadout_next"] = now + 8.0
                try:
                    vis = det.visible_labels()
                except Exception:
                    vis = set()
                known = set(det.calib.rows) if det.calib else set()
                new_weapons = vis - known
                if new_weapons:
                    log(f"New weapon(s) on HUD ({', '.join(sorted(new_weapons))}) — "
                        f"re-learning…", "fx")
                    _calibrate_core(False)
                    continue
            recording = now < state["hud_rec_until"]
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
            state["hud_status"] = (("● REC " if recording else "") +
                                   (f"reading {len(counts)}" if counts else "no read"))
            # --- self-healing: a saved/auto calibration can be stale or poisoned (e.g. a
            # transient frame mislocated the count column). If labels are clearly on screen
            # but we keep reading nothing, the calibration is bad -> drop it and re-learn.
            if not recording:
                if counts:
                    empty_streak = 0
                else:
                    empty_streak += 1
                    if empty_streak >= 40 and not state["hud_calibrating"]:  # ~2s of nothing
                        try:
                            seen = det.probe()
                        except Exception:
                            seen = 0
                        if seen >= 1:        # HUD really is visible -> calibration is bad
                            log("HUD readout stalled — re-learning calibration…", "fx")
                            det.calib = None
                            try:
                                if os.path.exists(HUD_CALIB):
                                    os.remove(HUD_CALIB)
                            except Exception:
                                pass
                            empty_streak = 0
                            state["hud_auto_next"] = 0.0
                            continue
                        empty_streak = 0     # no HUD visible (menu/clear) -> not a fault
            dispatched = []
            for wp, effect, kind, delta, old, new in events:
                if kind == "rapid":
                    # gun: handled below via a sustained firing state (not per-event), so the
                    # rumble is one continuous buzz across the burst instead of pulsing.
                    dispatched.append({"weapon": wp, "effect": "gun_active", "kind": kind,
                                       "old": old, "new": new, "delta": delta})
                elif kind == "counter":
                    # flares/chaff: subtle knock, throttled so a dump = a couple of knocks
                    if now - last_counter_knock >= 0.30:
                        effects.flare()
                        last_counter_knock = now
                        dispatched.append({"weapon": wp, "effect": "flare", "kind": kind,
                                           "old": old, "new": new, "delta": delta})
                    else:
                        dispatched.append({"weapon": wp, "effect": "flare_throttled",
                                           "kind": kind, "old": old, "new": new, "delta": delta})
                else:
                    effects.fire_effect(effect)
                    dispatched.append({"weapon": wp, "effect": effect, "kind": kind,
                                       "old": old, "new": new, "delta": delta})
                log(f"HUD {wp} {old}->{new}  →  {effect}", "fx")
            # Sustain ONE continuous gun rumble while any rapid weapon is actively firing
            # (count still dropping). Polling at ~17-20 Hz, we extend the rumble a little past
            # the next poll so it never gaps between confirmed ticks -> steady, not pulsing.
            try:
                gun_firing = any(det.tracker.is_firing(w)
                                 for w, c in hud_detect.WEAPON_CLASS.items() if c == "rapid")
            except Exception:
                gun_firing = False
            if gun_firing:
                effects.gun_active(0.18)
            if recording and rec_info is not None:
                n = state["hud_rec_n"]
                state["hud_rec_n"] = n + 1
                if frame is not None:
                    try:
                        hud_detect.save_gray_png(
                            os.path.join(state["hud_rec_dir"], f"f{n:04d}.png"), frame)
                    except Exception:
                        pass
                rec_info["type"] = "frame"
                rec_info["n"] = n
                rec_info["t"] = round(now, 3)
                rec_info["counts"] = counts
                rec_info["dispatched"] = dispatched
                _rec_write(rec_info)
                if now >= state["hud_rec_until"]:
                    d = state["hud_rec_dir"]
                    state["hud_rec_dir"] = None
                    _rec_write({"type": "footer", "frames": state["hud_rec_n"],
                                "t": round(now, 3)})
                    log(f"Recording done: {state['hud_rec_n']} frames → "
                        f"{os.path.basename(d)}", "fx")
                    try:
                        root.after(0, lambda: hud_rec_btn.config(text="Record 30s"))
                    except Exception:
                        pass
            time.sleep(0.02)   # ~20+ Hz poll: faster frames -> quicker confirmation/feel

    # --- UI refresh ---
    def refresh():
        if state["stick_ok"]:
            stick_dot.itemconfig(stick_oid, fill=GREEN)
            stick_val.config(text="connected", fg=GREEN)
        else:
            stick_dot.itemconfig(stick_oid, fill=RED)
            stick_val.config(text="not found", fg=MUTED)
        if state["game_ok"]:
            game_dot.itemconfig(game_oid, fill=GREEN)
            game_val.config(text="in match", fg=GREEN)
        else:
            game_dot.itemconfig(game_oid, fill=GREYDOT)
            game_val.config(text="waiting", fg=MUTED)
        try:
            hud_state_lbl.config(text=state["hud_status"],
                                 fg=(GREEN if state["hud_on"] and "reading" in state["hud_status"]
                                     else MUTED))
        except Exception:
            pass
        if state["running"]:
            root.after(300, refresh)

    workers = [stick_worker, wt_worker]
    if _HUD_AVAILABLE:
        workers.append(hud_worker)
    for w in workers:
        threading.Thread(target=w, daemon=True).start()
    refresh()
    _drain_log()   # start the UI-thread log pump

    def on_close():
        state["running"] = False
        save_cfg()
        effects.stop()
        time.sleep(0.1)
        stick.close()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    log("Ready. Enable HUD auto-detect — it learns your HUD automatically once you're in a match.")
    root.mainloop()


# ----------------------------------------------------------------------------------------
# entry
# ----------------------------------------------------------------------------------------

def selftest():
    s = Stick()
    if not s.open():
        print("Stick NOT found."); return 1
    print(f"Stick opened: {s.path}")
    eff = Effects(s, print)
    eff.start_heartbeat()
    time.sleep(0.2)
    eff.missile()
    time.sleep(2.0)
    eff.stop(); s.close()
    print("selftest done.")
    return 0


def run_gui_safe():
    """Run the GUI, but if anything fails during boot or runtime, write a full traceback
    to crash_log.txt next to the exe and show a dialog -- so a boot failure is diagnosable
    instead of a silent exit (the app is built --noconsole, so stderr is otherwise lost)."""
    try:
        run_gui()
    except Exception:
        import traceback
        tb = traceback.format_exc()
        try:
            base = os.path.dirname(os.path.abspath(
                sys.executable if getattr(sys, "frozen", False) else __file__))
        except Exception:
            base = os.getcwd()
        try:
            with open(os.path.join(base, "crash_log.txt"), "w", encoding="utf-8") as fh:
                fh.write("WinwingHaptics crash:\n\n" + tb)
        except Exception:
            pass
        try:
            import tkinter as tk
            from tkinter import messagebox
            r = tk.Tk(); r.withdraw()
            messagebox.showerror(
                "Winwing Haptics — startup error",
                "The app hit an error and had to stop.\n\n"
                "A crash_log.txt was written next to the app. Please send it.\n\n"
                + tb.strip().splitlines()[-1])
            r.destroy()
        except Exception:
            pass
        sys.exit(1)


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    if "--hudtest" in sys.argv:
        # Definitive check that the HUD detector + deps work inside the (frozen) build.
        ok = _HUD_AVAILABLE
        det_ok = False
        ocr_ok = False
        if ok:
            try:
                d = hud_detect.HudDetector(region=(0, 0, 300, 200))
                det_ok = d.available
                d.poll()
                ocr_ok = hud_detect._init_ocr()  # calibration depends on Windows OCR
            except Exception as e:
                print("HUD error:", e)
        msg = f"HUD_AVAILABLE={ok} detector_ready={det_ok} ocr_ready={ocr_ok}"
        # write to a file so the windowed/console state doesn't matter
        try:
            with open("hudtest_result.txt", "w") as fh:
                fh.write(msg)
        except Exception:
            pass
        print(msg)
        sys.exit(0 if (det_ok and ocr_ok) else 1)
    run_gui_safe()
