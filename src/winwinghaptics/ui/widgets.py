"""Small custom Tk widgets for the Concept A UI.

Tkinter has no native toggle switch or rounded button, so these draw token-styled controls on a
Canvas. They take colours from theme.COLOR so the look stays centralized. Kept dependency-light
(plain tk + the IconLoader); no ttk theme engine required.
"""
import tkinter as tk

from . import theme

C = theme.COLOR


class ToggleSwitch(tk.Canvas):
    """A Fluent-style on/off switch backed by a tk.BooleanVar.

    on_toggle(value) is called (if given) after the user flips it. Reads/writes `variable` so the
    rest of the app can bind to it like any checkbox."""
    W, H = 38, 22
    PAD = 3

    def __init__(self, parent, variable, on_toggle=None, bg=None):
        super().__init__(parent, width=self.W, height=self.H, highlightthickness=0,
                         bg=bg or C["bg_card"], cursor="hand2")
        self.var = variable
        self._on_toggle = on_toggle
        self.bind("<Button-1>", self._click)
        self._redraw()

    def _click(self, _e):
        self.var.set(not self.var.get())
        self._redraw()
        if self._on_toggle:
            self._on_toggle(self.var.get())

    def refresh(self):
        self._redraw()

    def _round_rect(self, x0, y0, x1, y1, r, **kw):
        self.create_oval(x0, y0, x0 + 2 * r, y1, **kw)
        self.create_oval(x1 - 2 * r, y0, x1, y1, **kw)
        self.create_rectangle(x0 + r, y0, x1 - r, y1, **kw)

    def _redraw(self):
        self.delete("all")
        on = bool(self.var.get())
        track = C["accent"] if on else C["stroke_strong"]
        self._round_rect(1, 1, self.W - 1, self.H - 1, (self.H - 2) // 2, fill=track, outline="")
        d = self.H - 2 * self.PAD
        x = (self.W - self.PAD - d) if on else self.PAD
        knob = "#ffffff" if on else "#c7ced6"
        self.create_oval(x, self.PAD, x + d, self.PAD + d, fill=knob, outline="")


class FlatButton(tk.Label):
    """A clickable token-styled button (Label-based, like the original UI) with hover + optional
    leading icon image. `accent=True` paints the primary action."""
    def __init__(self, parent, text, command, icon=None, accent=False, small=True, bg=None):
        self._base = C["accent"] if accent else (bg or C["bg_subtle"])
        self._hover = C["accent_hover"] if accent else "#27303a"
        fg = C["accent_ink"] if accent else C["text"]
        super().__init__(parent, text=text, image=icon, compound="left" if icon else "none",
                         bg=self._base, fg=fg, font=theme.FONT["body"],
                         padx=(8 if small else 11), pady=(4 if small else 6), cursor="hand2")
        if icon is not None:
            self._icon = icon                     # keep a ref so Tk doesn't GC the image
            self.configure(text=" " + text if text else "")
        self.bind("<Enter>", lambda _e: self.configure(bg=self._hover))
        self.bind("<Leave>", lambda _e: self.configure(bg=self._base))
        self.bind("<Button-1>", lambda _e: command())
