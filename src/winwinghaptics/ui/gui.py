"""Tkinter view — Concept A: a tabbed shell over the AppController.

Layout: a status strip (joystick / War Thunder) above a tab bar (Effects / Device / Activity).
The Effects tab renders a single, data-driven list from ui.effectspec (one row per trigger:
icon + name + Test + enable switch), which removes the old duplicate "Gun" and the artificial
weapons-vs-outcomes split into test-only / toggle-only cards. The Device tab holds the device
info, HUD auto-detect, callsign and the diagnostic tools; Activity holds the log.

The view owns ONLY presentation; all domain behaviour (workers, detector, calibration, recording,
config) lives in AppController. A small UiBridge marshals controller->UI callbacks (calibration
label, record button) onto the Tk main thread via root.after. Look-and-feel comes from ui.theme
tokens; icons are vendored Lucide SVGs rendered by ui.icons.
"""
import os
import sys
import ctypes

from .. import config
from ..app import AppController
from . import theme
from . import effectspec
from .icons import IconLoader
from .widgets import ToggleSwitch, RoundedButton, RoundedFrame, RoundedTile, ScrollFrame

C = theme.COLOR


class UiBridge:
    """Controller -> UI callbacks, marshaled onto the Tk main thread."""
    def __init__(self, root, get_calib_label, get_record_button):
        self._root = root
        self._get_calib_label = get_calib_label
        self._get_record_button = get_record_button
        self.green = C["status_ok"]
        self.muted = C["text_muted"]

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
                    btn.set_text(text)
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

    base_dir = config.app_base_dir(app_file)
    ctrl = AppController(base_dir)
    state = ctrl.state
    effects = ctrl.effects
    _HUD = ctrl.hud_available

    root = tk.Tk()
    root.title("Winwing Haptics")
    root.geometry("452x760")
    root.minsize(452, 720)
    root.configure(bg=C["bg_base"])

    icons = IconLoader(root)

    def ic(name, color, size):
        return icons.get(name, color, size)

    # fonts (named tk fonts pull from the token ramp)
    f_title = tkfont.Font(family=theme.FONT["title"][0], size=12)
    f_sub = tkfont.Font(family="Segoe UI", size=8)
    f_body = tkfont.Font(family=theme.FONT["body"][0], size=theme.FONT["body"][1])
    f_name = tkfont.Font(family="Segoe UI", size=10)
    f_small = tkfont.Font(family="Segoe UI", size=8)
    f_mono = tkfont.Font(family=theme.FONT["mono"][0], size=theme.FONT["mono"][1])
    f_strong = tkfont.Font(family="Segoe UI Semibold", size=10)

    refs = {"calib_lbl": None, "rec_btn": None}
    ctrl.ui = UiBridge(root, lambda: refs["calib_lbl"], lambda: refs["rec_btn"])

    def log(msg, tag=None):
        ctrl.log(msg, tag)

    # ---------------- Header ----------------
    header = tk.Frame(root, bg=C["bg_base"])
    header.pack(fill="x", padx=14, pady=(12, 8))
    bar = tk.Frame(header, bg=C["accent"], width=3, height=30)
    bar.pack(side="left", padx=(0, 9)); bar.pack_propagate(False)
    htext = tk.Frame(header, bg=C["bg_base"]); htext.pack(side="left")
    tk.Label(htext, text="Winwing Haptics", bg=C["bg_base"], fg=C["text"],
             font=f_title).pack(anchor="w")
    tk.Label(htext, text="War Thunder → controller rumble", bg=C["bg_base"],
             fg=C["text_muted"], font=f_sub).pack(anchor="w")

    # ---------------- Status strip ----------------
    strip = tk.Frame(root, bg=C["bg_base"]); strip.pack(fill="x", padx=12, pady=(0, 8))

    def stat_card(parent, icon_name, label):
        card = RoundedFrame(parent, radius=9, padx=10, pady=8)
        inner = card.inner
        dot = tk.Label(inner, image=ic(icon_name, C["status_idle"], theme.ICON["status"]),
                       bg=C["bg_card"])
        dot.image = ic(icon_name, C["status_idle"], theme.ICON["status"])
        dot.pack(side="left")
        tk.Label(inner, text=label, bg=C["bg_card"], fg=C["text"],
                 font=f_body).pack(side="left", padx=8)
        val = tk.Label(inner, text="—", bg=C["bg_card"], fg=C["text_muted"], font=f_small)
        val.pack(side="right")
        return card, dot, val, icon_name
    sc1, stick_dot, stick_val, stick_icn = stat_card(strip, "plug", "Joystick")
    sc1.pack(side="left", fill="x", expand=True, padx=(0, 4))
    sc2, game_dot, game_val, game_icn = stat_card(strip, "radio", "War Thunder")
    sc2.pack(side="left", fill="x", expand=True, padx=(4, 0))

    # ---------------- Tab bar + content ----------------
    tabbar = tk.Frame(root, bg=C["bg_base"]); tabbar.pack(fill="x", padx=12)
    underline = tk.Frame(root, bg=C["stroke"], height=1); underline.pack(fill="x")
    content = tk.Frame(root, bg=C["bg_base"]); content.pack(fill="both", expand=True)

    pages = {}
    tabs = {}
    current = {"name": None}

    def select_tab(name):
        for n, fr in pages.items():
            fr.pack_forget()
        pages[name].pack(fill="both", expand=True)
        for n, (lbl, icon_name) in tabs.items():
            on = (n == name)
            lbl.configure(fg=(C["text"] if on else C["text_muted"]),
                          image=ic(icon_name, C["accent"] if on else C["text_muted"],
                                   theme.ICON["tab"]))
            lbl.image = ic(icon_name, C["accent"] if on else C["text_muted"], theme.ICON["tab"])
            lbl.master.configure(bg=(C["accent"] if on else C["bg_base"]))
        current["name"] = name

    def add_tab(name, icon_name, scroll=True):
        wrap = tk.Frame(tabbar, bg=C["bg_base"])
        wrap.pack(side="left", padx=(0, 2))
        marker = tk.Frame(wrap, bg=C["bg_base"], height=2); marker.pack(side="bottom", fill="x")
        lbl = tk.Label(wrap, text="  " + name, bg=C["bg_base"], fg=C["text_muted"],
                       font=f_body, image=ic(icon_name, C["text_muted"], theme.ICON["tab"]),
                       compound="left", padx=8, pady=8, cursor="hand2")
        lbl.image = ic(icon_name, C["text_muted"], theme.ICON["tab"])
        lbl.pack()
        lbl.bind("<Button-1>", lambda _e, nm=name: select_tab(nm))
        tabs[name] = (lbl, icon_name)
        page = tk.Frame(content, bg=C["bg_base"])
        pages[name] = page
        # scrollable tabs return their inner frame so tall content (the effects list / device
        # settings) scrolls instead of being clipped by the window height.
        if scroll:
            sf = ScrollFrame(page, bg=C["bg_base"])
            sf.pack(fill="both", expand=True)
            return sf.inner
        return page

    page_effects = add_tab("Effects", "zap")
    page_device = add_tab("Device", "joystick")
    page_activity = add_tab("Activity", "scroll-text", scroll=False)

    # ============== EFFECTS TAB ==============
    enable_vars = {}
    switch_widgets = {}  # name -> ToggleSwitch
    row_widgets = {}     # name -> (icon_tile, desc_label)

    def make_test(spec):
        def run():
            try:
                if spec.test == "gun_active":
                    effects.gun_active(0.4)
                else:
                    getattr(effects, spec.test)()
            except Exception as e:
                log(f"test {spec.name} failed: {e}")
        return run

    def on_enable(name, value):
        state[f"en_{name}"] = bool(value)
        ctrl.save_cfg()

    def effect_row(parent, spec, last=False):
        row = tk.Frame(parent, bg=C["bg_card"])
        row.pack(fill="x")
        line = tk.Frame(row, bg=C["bg_card"]); line.pack(fill="x", padx=12, pady=8)
        # rounded icon tile
        tile = RoundedTile(line, ic(spec.icon, C["text_muted"], theme.ICON["row"]),
                           size=32, radius=8, fill=C["bg_subtle"], bg=C["bg_card"])
        tile.pack(side="left")
        # name + desc
        txt = tk.Frame(line, bg=C["bg_card"]); txt.pack(side="left", padx=11)
        tk.Label(txt, text=spec.label, bg=C["bg_card"], fg=C["text"],
                 font=f_name, anchor="w").pack(anchor="w")
        dlbl = tk.Label(txt, text=spec.desc, bg=C["bg_card"], fg=C["text_muted"],
                        font=f_small, anchor="w")
        if spec.desc:
            dlbl.pack(anchor="w")
        # switch (right), then Test pill
        var = tk.BooleanVar(value=state.get(f"en_{spec.name}", True))
        enable_vars[spec.name] = var
        sw = ToggleSwitch(line, var, on_toggle=lambda v, n=spec.name: on_enable(n, v))
        sw.pack(side="right", padx=(8, 0))
        switch_widgets[spec.name] = sw
        RoundedButton(line, "Test", make_test(spec),
                      icon=ic("play", C["text_muted"], theme.ICON["action"]),
                      bg=C["bg_card"]).pack(side="right")
        row_widgets[spec.name] = (tile, dlbl)
        if not last:
            tk.Frame(row, bg="#20272f", height=1).pack(fill="x", padx=12)
        return row

    def group_card(parent, title, specs):
        tk.Label(parent, text=title.upper(), bg=C["bg_base"], fg=C["text_muted"],
                 font=f_small).pack(anchor="w", padx=16, pady=(12, 6))
        card = RoundedFrame(parent, radius=10, padx=0, pady=0)
        card.pack(fill="x", padx=12, pady=(0, 2))
        for i, s in enumerate(specs):
            effect_row(card.inner, s, last=(i == len(specs) - 1))

    for gid, gtitle in effectspec.GROUPS:
        group_card(page_effects, gtitle, effectspec.specs_in_group(gid))

    # ============== DEVICE TAB ==============
    def card(parent):
        c = RoundedFrame(parent, radius=10, padx=12, pady=12)
        c.pack(fill="x", padx=12, pady=(0, 8))
        return c.inner

    tk.Label(page_device, text="", bg=C["bg_base"]).pack(pady=(4, 0))
    dev_card = card(page_device)
    dev_top = tk.Frame(dev_card, bg=C["bg_card"]); dev_top.pack(fill="x")
    dtile = RoundedTile(dev_top, ic("joystick", C["text"], 18), size=34, radius=8,
                        fill=C["bg_subtle"], bg=C["bg_card"])
    dtile.pack(side="left")
    dev_name = type(ctrl.stick).__name__
    try:
        dev_name = ctrl.stick.capabilities.name
    except Exception:
        pass
    dtxt = tk.Frame(dev_top, bg=C["bg_card"]); dtxt.pack(side="left", padx=10)
    tk.Label(dtxt, text=dev_name, bg=C["bg_card"], fg=C["text"], font=f_body).pack(anchor="w")
    dev_state_lbl = tk.Label(dtxt, text="searching…", bg=C["bg_card"], fg=C["text_muted"],
                             font=f_small)
    dev_state_lbl.pack(anchor="w")

    # callsign
    cs_card = card(page_device)
    tk.Label(cs_card, text="CALLSIGN", bg=C["bg_card"], fg=C["text_muted"],
             font=f_small).pack(anchor="w")
    tk.Label(cs_card, text="Your in-game name — kill / hit / death only fire for you.",
             bg=C["bg_card"], fg=C["text_muted"], font=f_small, wraplength=360,
             justify="left").pack(anchor="w", pady=(1, 6))
    callsign_var = tk.StringVar(value="")
    cs_entry = tk.Entry(cs_card, textvariable=callsign_var, bg=C["bg_subtle"], fg=C["text"],
                        font=f_small, insertbackground=C["text"], relief="flat", width=24)
    cs_entry.pack(anchor="w", ipady=3)

    def on_callsign(*_):
        state["callsign"] = callsign_var.get().strip(); ctrl.save_cfg()
    callsign_var.trace_add("write", on_callsign)

    # HUD auto-detect + diagnostics
    hud_card = card(page_device)
    hrow = tk.Frame(hud_card, bg=C["bg_card"]); hrow.pack(fill="x")
    tk.Label(hrow, text="HUD AUTO-DETECT", bg=C["bg_card"], fg=C["text_muted"],
             font=f_small).pack(side="left")
    hud_state_lbl = tk.Label(hrow, text="off", bg=C["bg_card"], fg=C["text_muted"], font=f_small)
    hud_state_lbl.pack(side="right")
    en_hud = tk.BooleanVar(value=state["hud_on"])

    def toggle_hud(v=None):
        state["hud_on"] = en_hud.get(); ctrl.save_cfg()
        if state["hud_on"] and not _HUD:
            log("HUD auto-detect unavailable (OCR engine/numpy missing).")
        else:
            log(f"HUD auto-detect {'enabled' if state['hud_on'] else 'disabled'}.")

    if _HUD:
        hud_toggle_row = tk.Frame(hud_card, bg=C["bg_card"]); hud_toggle_row.pack(fill="x", pady=(6, 2))
        tk.Label(hud_toggle_row, text="Read weapon counts from the screen",
                 bg=C["bg_card"], fg=C["text"], font=f_body).pack(side="left")
        ToggleSwitch(hud_toggle_row, en_hud, on_toggle=toggle_hud).pack(side="right")
        rg = state["hud_region"]
        hud_region_lbl = tk.Label(hud_card, text=f"region: {rg[0]},{rg[1]} {rg[2]}x{rg[3]}",
                                  bg=C["bg_card"], fg=C["text_muted"], font=f_small)
        hud_region_lbl.pack(anchor="w", pady=(6, 0))
        hud_calib_lbl = tk.Label(hud_card,
                                 text="Auto-learns your HUD the first time it sees the counters.",
                                 bg=C["bg_card"], fg=C["text_muted"], font=f_small,
                                 wraplength=360, justify="left")
        hud_calib_lbl.pack(anchor="w", pady=(2, 8))
        refs["calib_lbl"] = hud_calib_lbl

        tk.Label(hud_card, text="ADVANCED", bg=C["bg_card"], fg=C["text_muted"],
                 font=f_small).pack(anchor="w", pady=(2, 4))
        adv = tk.Frame(hud_card, bg=C["bg_card"]); adv.pack(fill="x")
        RoundedButton(adv, "Set Region", lambda: calibrate_hud(),
                      bg=C["bg_card"]).pack(side="left")
        RoundedButton(adv, "Re-learn HUD", lambda: ctrl.calibrate_detector(),
                      bg=C["bg_card"]).pack(side="left", padx=6)
        rec_btn = RoundedButton(adv, "Record 30s", lambda: ctrl.start_record(), bg=C["bg_card"])
        rec_btn.pack(side="left")
        refs["rec_btn"] = rec_btn
    else:
        tk.Label(hud_card, text="Unavailable in this build (needs OCR engine).",
                 bg=C["bg_card"], fg=C["text_muted"], font=f_small).pack(anchor="w", pady=4)
        hud_region_lbl = None

    # ============== ACTIVITY TAB ==============
    logcard = tk.Frame(page_activity, bg=C["bg_card"], highlightthickness=1,
                       highlightbackground=C["stroke"])
    logcard.pack(fill="both", expand=True, padx=12, pady=10)
    tk.Label(logcard, text="ACTIVITY", bg=C["bg_card"], fg=C["text_muted"],
             font=f_small).pack(anchor="w", padx=10, pady=(8, 2))
    logwrap = tk.Frame(logcard, bg=C["bg_card"]); logwrap.pack(fill="both", expand=True,
                                                               padx=8, pady=(0, 8))
    txt = tk.Text(logwrap, height=10, state="disabled", bg=C["bg_subtle"], fg="#aeb9c4",
                  insertbackground=C["text"], font=f_mono, relief="flat", bd=0,
                  padx=6, pady=4, wrap="word")
    sb = tk.Scrollbar(logwrap, command=txt.yview); txt.configure(yscrollcommand=sb.set)
    txt.pack(side="left", fill="both", expand=True); sb.pack(side="right", fill="y")
    txt.tag_config("kill", foreground=C["status_ok"])
    txt.tag_config("death", foreground=C["status_bad"])
    txt.tag_config("fx", foreground=C["accent"])
    txt.tag_config("wt", foreground=C["text_muted"])

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

    # ---------------- HUD region overlay ----------------
    def calibrate_hud():
        ov = tk.Toplevel(root)
        ov.attributes("-fullscreen", True)
        ov.attributes("-alpha", 0.25)
        ov.configure(bg="#000000", cursor="crosshair")
        ov.attributes("-topmost", True)
        cv = tk.Canvas(ov, bg="#101418", highlightthickness=0); cv.pack(fill="both", expand=True)
        cv.create_text(ov.winfo_screenwidth() // 2, 40,
                       text="Drag a box around the weapon counters. Esc to cancel.",
                       fill="#ffffff", font=f_body)
        sel = {"x0": 0, "y0": 0, "rect": None}

        def on_down(e):
            sel["x0"], sel["y0"] = e.x_root, e.y_root
            if sel["rect"]:
                cv.delete(sel["rect"])
            sel["rect"] = cv.create_rectangle(e.x, e.y, e.x, e.y, outline=C["accent"], width=2)
            sel["cx0"], sel["cy0"] = e.x, e.y

        def on_move(e):
            if sel["rect"]:
                cv.coords(sel["rect"], sel["cx0"], sel["cy0"], e.x, e.y)

        def on_up(e):
            x0, y0 = sel["x0"], sel["y0"]; x1, y1 = e.x_root, e.y_root
            l, t = min(x0, x1), min(y0, y1); w, h = abs(x1 - x0), abs(y1 - y0)
            ov.destroy()
            if w > 30 and h > 20:
                state["hud_region"] = (l, t, w, h); ctrl.save_cfg()
                if hud_region_lbl:
                    hud_region_lbl.config(text=f"region: {l},{t} {w}x{h}")
                log(f"HUD region set: {l},{t} {w}x{h}")

        cv.bind("<Button-1>", on_down); cv.bind("<B1-Motion>", on_move)
        cv.bind("<ButtonRelease-1>", on_up); ov.bind("<Escape>", lambda _e: ov.destroy())

    # ---------------- load saved config ----------------
    saved = ctrl.load_cfg()
    for name in effectspec.ENABLE_KEYS:
        if name in saved:
            state[f"en_{name}"] = bool(saved[name])
        if name in enable_vars:
            enable_vars[name].set(state.get(f"en_{name}", True))
            if name in switch_widgets:
                switch_widgets[name].refresh()
    callsign_var.set(state.get("callsign", ""))
    en_hud.set(state["hud_on"])
    if _HUD:
        _d0 = ctrl.get_det()
        if _d0 is not None and _d0.calibrated:
            ctrl.ui.set_calib_label(
                "HUD learned (%s). Re-learn only if the readout looks wrong."
                % ", ".join(sorted(_d0.calib.rows)), ok=True)

    # ---------------- status refresh ----------------
    def set_stat(dot, val, icon_name, ok, ok_text, idle_text, color_ok=None):
        color = (color_ok or C["status_ok"]) if ok else C["status_idle"]
        dot.configure(image=ic(icon_name, color, theme.ICON["status"]))
        dot.image = ic(icon_name, color, theme.ICON["status"])
        val.configure(text=ok_text if ok else idle_text,
                      fg=(C["status_ok"] if ok else C["text_muted"]))

    def refresh():
        set_stat(stick_dot, stick_val, stick_icn, state["stick_ok"], "connected", "not found")
        set_stat(game_dot, game_val, game_icn, state["game_ok"], "in match", "waiting")
        try:
            dev_state_lbl.config(
                text="connected · USB HID" if state["stick_ok"] else "searching…",
                fg=C["status_ok"] if state["stick_ok"] else C["text_muted"])
        except Exception:
            pass
        # live gun-firing row highlight
        gname = "gun"
        if gname in row_widgets:
            tile, dlbl = row_widgets[gname]
            firing = bool(state.get("firing_gun")) and state.get("en_gun", True)
            col = C["accent"] if firing else C["text_muted"]
            tile.set(image=ic("crosshair", col, theme.ICON["row"]),
                     fill=("#2a1a0c" if firing else C["bg_subtle"]))
            dlbl.configure(text="firing now" if firing else "cannon / MG",
                           fg=C["accent"] if firing else C["text_muted"])
        if _HUD:
            try:
                hud_state_lbl.config(
                    text=state["hud_status"],
                    fg=(C["status_ok"] if state["hud_on"] and "reading" in state["hud_status"]
                        else C["text_muted"]))
            except Exception:
                pass
        if state["running"]:
            root.after(300, refresh)

    select_tab("Effects")
    ctrl.start_workers()
    refresh()
    _drain_log()

    def on_close():
        ctrl.shutdown(); root.destroy()
    root.protocol("WM_DELETE_WINDOW", on_close)
    log("Ready. Enable HUD auto-detect on the Device tab — it learns your HUD automatically.")
    root.mainloop()


def _app_file():
    """Fallback app entry file for the (non-frozen) config base dir."""
    return os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), "winwing_haptics.py")


def run_gui_safe(app_file=None):
    """Run the GUI; on any boot/runtime error write crash_log.txt next to the exe and show a
    dialog so a boot failure is diagnosable instead of a silent exit (built --noconsole)."""
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
