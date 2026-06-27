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
import threading
import webbrowser

from .. import config
from ..app import AppController
from ..app.controller import record_button_label
from . import theme
from . import effectspec
from .icons import IconLoader
from .widgets import ToggleSwitch, RoundedButton, RoundedFrame, RoundedTile, ScrollFrame
from .. import __version__
from ..update import UpdateChecker
from ..update.checker import releases_page
from ..update.installer import WindowsUpdater

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
    root.title("WT Haptics")
    root.geometry("452x760")
    root.minsize(452, 720)
    root.configure(bg=C["bg_base"])

    # Window + taskbar icon. The .ico drives the title-bar and taskbar on Windows; the PNG is a
    # cross-version fallback via iconphoto. Setting an explicit AppUserModelID makes Windows group
    # the app under -- and show -- OUR icon in the taskbar instead of the generic python host icon.
    _assets = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("WTHaptics.App")
    except Exception:
        pass
    try:
        root.iconbitmap(default=os.path.join(_assets, "wt_haptics.ico"))
    except Exception:
        pass
    try:
        _icon_img = tk.PhotoImage(file=os.path.join(_assets, "wt_haptics.png"))
        root.iconphoto(True, _icon_img)
        root._wt_icon_ref = _icon_img            # keep a reference so Tk doesn't GC the image
    except Exception:
        pass

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
    tk.Label(htext, text="WT Haptics", bg=C["bg_base"], fg=C["text"],
             font=f_title).pack(anchor="w")
    tk.Label(htext, text=f"War Thunder → controller rumble · v{__version__}", bg=C["bg_base"],
             fg=C["text_muted"], font=f_sub).pack(anchor="w")

    # ---------------- Update banner (hidden until an update is found) ----------------
    update_state = {"info": None, "busy": False, "status": ""}
    banner = RoundedFrame(root, radius=9, fill="#2a1a0c", outline=C["accent"], padx=12, pady=9)
    banner_lbl = tk.Label(banner.inner, text="", bg="#2a1a0c", fg=C["accent"], font=f_body,
                          image=ic("download", C["accent"], 16), compound="left", padx=0,
                          cursor="hand2")
    banner_lbl.image = ic("download", C["accent"], 16)
    banner_lbl.pack(side="left", padx=(2, 8))
    banner_btns = tk.Frame(banner.inner, bg="#2a1a0c"); banner_btns.pack(side="right")

    def show_banner():
        info = update_state["info"]
        if not info or not info.available:
            banner.pack_forget(); return
        banner_lbl.config(text="  Update available — v%s" % info.version)
        banner.pack(fill="x", padx=12, pady=(0, 8), after=header)

    def hide_banner():
        update_state["info"] = None
        banner.pack_forget()

    # update orchestration (shared by the banner and the Device-tab Updates card)
    _updater = WindowsUpdater()

    def apply_update():
        """Apply the available update: self-replace+relaunch on a frozen Windows build, else open
        the Releases page so the user can download it."""
        info = update_state["info"]
        if not info or update_state["busy"]:
            return
        if _updater.is_supported() and info.asset_url:
            update_state["busy"] = True
            set_update_status("Downloading update…")
            log(f"Downloading update v{info.version}…", "fx")

            def finish_exit():
                # On the Tk MAIN thread: release the device + close the window, then FORCE the
                # process to terminate. The helper .bat waits for THIS pid to exit before it swaps
                # the app folder and relaunches; just returning from mainloop is not enough on a
                # frozen --noconsole build (a lingering thread or Tk teardown can keep the process
                # alive, leaving the helper spinning in its wait loop forever — the "black window
                # stuck on find <pid>" symptom). os._exit guarantees the file locks are released.
                set_update_status("Installing… the app will restart.")
                log("Update downloaded — restarting to install…", "fx")
                try:
                    ctrl.shutdown()
                except Exception:
                    pass
                try:
                    root.destroy()
                except Exception:
                    pass
                os._exit(0)

            def request_exit():
                # Called by update() from the worker thread once the swap helper is launched. Marshal
                # the graceful exit onto the Tk main thread, but ALSO arm a watchdog that force-exits
                # no matter what: root.after() from a worker thread is not Tk-thread-safe and may
                # never run, and ctrl.shutdown() could block — either way the process MUST die so the
                # update helper can stop waiting and apply the swap.
                wd = threading.Timer(2.0, lambda: os._exit(0))
                wd.daemon = True
                wd.start()
                try:
                    root.after(0, finish_exit)
                except Exception:
                    pass

            def work():
                ok = _updater.update(
                    info,
                    on_progress=lambda r, t: root.after(
                        0, lambda: set_update_status(
                            "Downloading… %d%%" % (int(r * 100 / t) if t else 0))),
                    _exit=request_exit)
                if not ok:
                    update_state["busy"] = False
                    root.after(0, lambda: (set_update_status("Update failed — opening Releases"),
                                           log("Update failed; opening Releases page.", "fx"),
                                           webbrowser.open(info.html_url)))
            threading.Thread(target=work, daemon=True).start()
        else:
            webbrowser.open(info.html_url or releases_page())
            log("Opened the Releases page to download the update.", "fx")

    def view_notes():
        info = update_state["info"]
        webbrowser.open((info.html_url if info else None) or releases_page())

    RoundedButton(banner_btns, "Update", apply_update, accent=True, bg="#2a1a0c",
                  icon=ic("download", C["accent_ink"], 12)).pack(side="left")
    _close = tk.Label(banner_btns, image=ic("x", C["text_muted"], 14), bg="#2a1a0c",
                      cursor="hand2")
    _close.image = ic("x", C["text_muted"], 14)
    _close.pack(side="left", padx=(8, 0))
    _close.bind("<Button-1>", lambda _e: hide_banner())
    banner_lbl.bind("<Button-1>", lambda _e: view_notes())

    def set_update_status(text):
        update_state["status"] = text
        try:
            upd_status_lbl.config(text=text)
        except Exception:
            pass

    def run_update_check(manual=False):
        """Check GitHub Releases on a background thread; render the banner/card on the UI thread."""
        if update_state["busy"]:
            return
        if manual:
            set_update_status("Checking…")

        def work():
            info = UpdateChecker().check()
            def render():
                update_state["info"] = info
                if info is None:
                    set_update_status("Couldn't check (offline?)" if manual else "")
                elif info.available:
                    set_update_status(f"Update available: v{info.version}")
                    show_banner()
                else:
                    set_update_status("You're on the latest version.")
            root.after(0, render)
        threading.Thread(target=work, daemon=True).start()

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

    # updates card
    upd_card = card(page_device)
    upd_head = tk.Frame(upd_card, bg=C["bg_card"]); upd_head.pack(fill="x")
    tk.Label(upd_head, text="UPDATES", bg=C["bg_card"], fg=C["text_muted"],
             font=f_small).pack(side="left")
    tk.Label(upd_head, text=f"v{__version__}", bg=C["bg_card"], fg=C["text_muted"],
             font=f_small).pack(side="right")
    upd_status_lbl = tk.Label(upd_card, text="", bg=C["bg_card"], fg=C["text_muted"],
                              font=f_small, anchor="w", wraplength=360, justify="left")
    upd_status_lbl.pack(anchor="w", pady=(4, 6))
    upd_btns = tk.Frame(upd_card, bg=C["bg_card"]); upd_btns.pack(fill="x")
    RoundedButton(upd_btns, "Check for updates", lambda: run_update_check(manual=True),
                  icon=ic("refresh-cw", C["text_muted"], 12), bg=C["bg_card"]).pack(side="left")
    RoundedButton(upd_btns, "Update now", apply_update, accent=True, bg=C["bg_card"],
                  icon=ic("download", C["accent_ink"], 12)).pack(side="left", padx=6)
    if update_state.get("status"):
        upd_status_lbl.config(text=update_state["status"])

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
        rec_btn = RoundedButton(adv, record_button_label(state.get("record_seconds", 30)),
                                lambda: ctrl.start_record(), bg=C["bg_card"])
        rec_btn.pack(side="left")
        refs["rec_btn"] = rec_btn

        # Recording length selector (persisted). Longer sessions capture far more real launches
        # for ground-truth scoring; full-frame PNGs are ~1 MB/s so the choice has a disk cost.
        rec_row = tk.Frame(hud_card, bg=C["bg_card"]); rec_row.pack(fill="x", pady=(6, 0))
        tk.Label(rec_row, text="Length", bg=C["bg_card"], fg=C["text_muted"],
                 font=f_small).pack(side="left")
        _dur_presets = [("30s", 30), ("1min", 60), ("2min", 120), ("5min", 300), ("10min", 600)]
        _dur_by_label = dict(_dur_presets)
        _cur_secs = int(state.get("record_seconds", 30))
        _cur_label = next((lbl for lbl, s in _dur_presets if s == _cur_secs), f"{_cur_secs}s")
        dur_var = tk.StringVar(value=_cur_label)

        def on_duration(*_):
            secs = _dur_by_label.get(dur_var.get(), _cur_secs)
            state["record_seconds"] = secs
            ctrl.save_cfg()
            try:
                rec_btn.set_text(record_button_label(secs))
            except Exception:
                pass

        dur_menu = tk.OptionMenu(rec_row, dur_var, *[lbl for lbl, _ in _dur_presets],
                                 command=lambda *_: on_duration())
        dur_menu.configure(bg=C["bg_subtle"], fg=C["text"], font=f_small, relief="flat",
                           highlightthickness=0, activebackground=C["bg_subtle"],
                           activeforeground=C["text"], bd=0)
        try:
            dur_menu["menu"].configure(bg=C["bg_subtle"], fg=C["text"])
        except Exception:
            pass
        dur_menu.pack(side="left", padx=6)
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
        # Reflect the persisted recording length in the dropdown + Record button. The widgets
        # were built from defaults BEFORE load_cfg() ran above, so without this a saved length
        # (e.g. 5min) would show as "30s" while start_record() actually used the saved value.
        try:
            _secs = int(state.get("record_seconds", 30))
            _lbl = next((lbl for lbl, s in _dur_presets if s == _secs), f"{_secs}s")
            dur_var.set(_lbl)
            rec_btn.set_text(record_button_label(_secs))
        except Exception:
            pass
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
        # War Thunder: three states -- in a match (green), open but in menu/hangar (amber-ish),
        # or closed (idle). wt_open is process/server presence; game_ok is actively flying.
        if state["game_ok"]:
            set_stat(game_dot, game_val, game_icn, True, "in match", "waiting")
        elif state.get("wt_open"):
            game_val.configure(text="in menu", fg=C["text"])
            try:
                game_dot.configure(image=ic(game_icn, C["accent"], theme.ICON["status"]))
                game_dot.image = ic(game_icn, C["accent"], theme.ICON["status"])
            except Exception:
                pass
        else:
            set_stat(game_dot, game_val, game_icn, False, "in match", "closed")
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
    # check for updates shortly after boot (background thread; banner appears if one is found)
    root.after(1500, lambda: run_update_check(manual=False))

    def on_close():
        ctrl.shutdown(); root.destroy()
    root.protocol("WM_DELETE_WINDOW", on_close)
    log("Ready. HUD auto-detect is on — it learns your HUD automatically when War Thunder is open.")
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
                fh.write("WT Haptics crash:\n\n" + tb)
        except Exception:
            pass
        try:
            import tkinter as tk
            from tkinter import messagebox
            r = tk.Tk(); r.withdraw()
            messagebox.showerror(
                "WT Haptics — startup error",
                "The app hit an error and had to stop.\n\n"
                "A crash_log.txt was written next to the app. Please send it.\n\n"
                + tb.strip().splitlines()[-1])
            r.destroy()
        except Exception:
            pass
        sys.exit(1)
