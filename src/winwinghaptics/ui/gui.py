"""Tkinter view — builds the window and wires widgets to the AppController.

The view owns ONLY presentation: palette/fonts, widgets, the region-select overlay, the log
pump and the status refresh. All domain behaviour (workers, detector, calibration, recording,
config) lives in AppController. A small UiBridge marshals the few controller->UI callbacks
(calibration label, record button) onto the Tk main thread via root.after.
"""
import os
import sys
import ctypes

from .. import config
from ..app import AppController


class UiBridge:
    """Controller -> UI callbacks, marshaled onto the Tk main thread."""
    def __init__(self, root, get_calib_label, get_record_button):
        self._root = root
        self._get_calib_label = get_calib_label
        self._get_record_button = get_record_button
        self.green = "#33d17a"
        self.muted = "#8b97a4"

    def set_calib_label(self, text, ok=False):
        def apply():
            lbl = self._get_calib_label()
            if lbl is not None:
                try:
                    lbl.config(text=text, fg=(self.green if ok else self.muted))
                except Exception:
                    pass
        try:
            self._root.after(0, apply)
        except Exception:
            pass

    def set_record_button(self, text):
        def apply():
            btn = self._get_record_button()
            if btn is not None:
                try:
                    btn.config(text=text)
                except Exception:
                    pass
        try:
            self._root.after(0, apply)
        except Exception:
            pass


def run_gui(app_file):
    import tkinter as tk
    from tkinter import font as tkfont

    # Make the process DPI-aware so text renders crisp at native resolution.
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_AWARE
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

    # --- palette ---
    BG       = "#0f1216"
    PANEL    = "#171c22"
    PANEL2   = "#1e252d"
    FG       = "#e6edf3"
    MUTED    = "#8b97a4"
    ACCENT   = "#ff7a18"
    GREEN    = "#33d17a"
    RED      = "#e5484d"
    GREYDOT  = "#566270"

    base_dir = config.app_base_dir(app_file)
    ctrl = AppController(base_dir)
    state = ctrl.state
    effects = ctrl.effects
    _HUD_AVAILABLE = ctrl.hud_available

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

    # widget refs the UiBridge needs (filled in below; bridge reads them lazily)
    refs = {"calib_lbl": None, "rec_btn": None}
    ctrl.ui = UiBridge(root, lambda: refs["calib_lbl"], lambda: refs["rec_btn"])

    def log(msg, tag=None):
        ctrl.log(msg, tag)

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
        ctrl.save_cfg()
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
        style_btn(hud_btns, "Re-learn HUD", lambda: ctrl.calibrate_detector(),
                  small=True).pack(side="left", padx=(6, 0))
        hud_rec_btn = style_btn(hud_btns, "Record 30s", lambda: ctrl.start_record(), small=True)
        hud_rec_btn.pack(side="left", padx=(6, 0))
        refs["rec_btn"] = hud_rec_btn
        rg = state["hud_region"]
        hud_region_lbl = tk.Label(hud_card, text=f"region: {rg[0]},{rg[1]} {rg[2]}x{rg[3]}",
                                  bg=PANEL, fg=MUTED, font=f_small)
        hud_region_lbl.pack(anchor="w", pady=(2, 0))
        hud_calib_lbl = tk.Label(hud_card,
                                 text="Auto-learns your HUD the first time it sees the counters in a match. "
                                      "Use 'Re-learn HUD' only if the readout looks wrong.",
                                 bg=PANEL, fg=MUTED, font=f_small, wraplength=380, justify="left")
        hud_calib_lbl.pack(anchor="w", pady=(2, 0))
        refs["calib_lbl"] = hud_calib_lbl
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
        """Mirror the Tk enable vars into the controller's state dict (UI thread only)."""
        state["en_gun"] = bool(en_gun.get())
        state["en_kill"] = bool(en_kill.get())
        state["en_hit"] = bool(en_hit.get())
        state["en_death"] = bool(en_death.get())

    def chk(parent, text, var, r, col):
        cb = tk.Checkbutton(parent, text=text, variable=var, bg=PANEL, fg=FG,
                            activebackground=PANEL, activeforeground=FG,
                            selectcolor=PANEL2, font=f_body, anchor="w",
                            highlightthickness=0, bd=0, padx=2,
                            command=lambda: (sync_enables(), ctrl.save_cfg()))
        cb.grid(row=r, column=col, sticky="w", padx=(0, 10), pady=1)

    grid = tk.Frame(ef, bg=PANEL); grid.pack(fill="x")
    chk(grid, "Gun rumble", en_gun, 0, 0)
    chk(grid, "Kill confirm", en_kill, 0, 1)
    chk(grid, "Took a hit", en_hit, 1, 0)
    chk(grid, "Death", en_death, 1, 1)

    # callsign (so kill/death only fire for YOU, not every player in the match)
    csrow = tk.Frame(ef, bg=PANEL); csrow.pack(fill="x", pady=(6, 0))
    tk.Label(csrow, text="Your callsign:", bg=PANEL, fg=MUTED, font=f_small).pack(side="left")
    callsign_var = tk.StringVar(value="")
    cs_entry = tk.Entry(csrow, textvariable=callsign_var, bg=PANEL2, fg=FG, font=f_small,
                        insertbackground=FG, relief="flat", width=18)
    cs_entry.pack(side="left", padx=6, ipady=2)

    def on_callsign(*_):
        state["callsign"] = callsign_var.get().strip()
        ctrl.save_cfg()
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

    # log pump: drain the controller's thread-safe queue onto the Tk Text on the UI thread.
    def _drain_log():
        pending = ctrl.drain_log()
        if pending:
            txt.configure(state="normal")
            for ts, msg, tag in pending:
                txt.insert("end", f"{ts}  ", "")
                txt.insert("end", f"{msg}\n", (tag,))
            txt.see("end")
            txt.configure(state="disabled")
        if state["running"]:
            root.after(80, _drain_log)

    # ---------- HUD region calibration overlay (drag a box over the counters) ----------
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
            sel["rect"] = cv.create_rectangle(e.x, e.y, e.x, e.y, outline="#ff7a18", width=2)
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
                ctrl.save_cfg()
                if hud_region_lbl:
                    hud_region_lbl.config(text=f"region: {l},{t} {w}x{h}")
                log(f"HUD region set: {l},{t} {w}x{h}")

        def on_esc(_):
            ov.destroy()

        cv.bind("<Button-1>", on_down)
        cv.bind("<B1-Motion>", on_move)
        cv.bind("<ButtonRelease-1>", on_up)
        ov.bind("<Escape>", on_esc)

    # load saved config now that controls exist
    saved = ctrl.load_cfg()
    if saved:
        en_gun.set(saved.get("gun", True))
        en_kill.set(saved.get("kill", True))
        en_hit.set(saved.get("hit", True))
        en_death.set(saved.get("death", True))
    sync_enables()   # seed the worker-visible mirrors from the loaded checkbox states
    callsign_var.set(state.get("callsign", ""))
    en_hud.set(state["hud_on"])
    if _HUD_AVAILABLE:
        _d0 = ctrl.get_det()
        if _d0 is not None and _d0.calibrated:
            ctrl.ui.set_calib_label(
                "HUD learned (%s). Re-learn only if readout looks wrong."
                % ", ".join(sorted(_d0.calib.rows)), ok=True)

    # ---------- status refresh ----------
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

    ctrl.start_workers()
    refresh()
    _drain_log()   # start the UI-thread log pump

    def on_close():
        ctrl.shutdown()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    log("Ready. Enable HUD auto-detect — it learns your HUD automatically once you're in a match.")
    root.mainloop()


def _app_file():
    """Fallback app entry file for the (non-frozen) config base dir, used when run_gui_safe
    is called without an explicit app_file. Resolves to src/winwing_haptics.py so the config
    path is unchanged from earlier versions."""
    return os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), "winwing_haptics.py")


def run_gui_safe(app_file=None):
    """Run the GUI; on any boot/runtime error write crash_log.txt next to the exe and show a
    dialog -- so a boot failure is diagnosable instead of a silent exit (built --noconsole)."""
    if app_file is None:
        app_file = _app_file()
    try:
        run_gui(app_file)
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
