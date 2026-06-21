"""Small custom Tk widgets for the Concept A UI.

Tkinter has no native rounded corners, toggle switch, or pill button, so these draw token-styled
controls on a Canvas (rounded rectangles via a smoothed polygon). Colours come from theme.COLOR so
the look stays centralized. Dependency-light: plain tk + the IconLoader, no ttk theme engine.
"""
import tkinter as tk
from tkinter import font as tkfont

from PIL import Image, ImageDraw, ImageTk

from . import theme

C = theme.COLOR


def _round_poly(cv, x1, y1, x2, y2, r, **kw):
    """Draw a rounded rectangle on a canvas using a smoothed polygon. Returns the item id."""
    r = max(0, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
    pts = [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]
    return cv.create_polygon(pts, smooth=True, **kw)


class RoundedFrame(tk.Frame):
    """A content-sized rounded card. Pack/grid children into `.inner`.

    Implemented as a REAL tk.Frame (so it participates in pack/grid/expand normally -- a canvas
    with an embedded window collapses to 1px under side-by-side `expand=True`). A PIL-rendered
    rounded-rectangle image is painted as the background behind the inset content and regenerated
    on resize. Keep pad >= radius so the square inner frame never covers the corner arcs.
    """
    def __init__(self, parent, radius=10, fill=None, outline=None, outerbg=None,
                 padx=12, pady=12):
        self.fill = fill or C["bg_card"]
        self.outline = outline or C["stroke"]
        self.outerbg = outerbg or C["bg_base"]
        self.radius = radius
        super().__init__(parent, bg=self.outerbg, bd=0, highlightthickness=0)
        self._bg = tk.Label(self, bg=self.outerbg, bd=0)
        self._bg.place(x=0, y=0, relwidth=1, relheight=1)
        self.inner = tk.Frame(self, bg=self.fill)
        self.inner.pack(fill="both", expand=True, padx=padx, pady=pady)
        self._img = None
        self._size = (0, 0)
        self.bind("<Configure>", self._render)

    def _render(self, e):
        w, h = e.width, e.height
        if w < 2 or h < 2 or (w, h) == self._size:
            return
        self._size = (w, h)
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([0, 0, w - 1, h - 1], radius=self.radius,
                            fill=self.fill, outline=self.outline, width=1)
        self._img = ImageTk.PhotoImage(img)
        self._bg.configure(image=self._img)
        self._bg.lower()


class RoundedTile(tk.Canvas):
    """A small rounded square holding a centered icon image (the per-row icon tile)."""
    def __init__(self, parent, image, size=32, radius=8, fill=None, bg=None):
        self.size = size
        self.radius = radius
        self.fill = fill or C["bg_subtle"]
        super().__init__(parent, width=size, height=size, highlightthickness=0,
                         bg=bg or C["bg_card"], bd=0)
        self._rect = _round_poly(self, 1, 1, size - 1, size - 1, radius,
                                 fill=self.fill, outline="")
        self._img_id = self.create_image(size // 2, size // 2, image=image)
        self._image = image

    def set(self, image=None, fill=None):
        if fill is not None:
            self.fill = fill
            self.itemconfig(self._rect, fill=fill)
        if image is not None:
            self._image = image
            self.itemconfig(self._img_id, image=image)


class ToggleSwitch(tk.Canvas):
    """A Fluent-style on/off switch backed by a tk.BooleanVar.

    on_toggle(value) is called (if given) after the user flips it. Reads/writes `variable` so the
    rest of the app can bind to it like any checkbox."""
    W, H = 38, 22
    PAD = 3

    def __init__(self, parent, variable, on_toggle=None, bg=None):
        super().__init__(parent, width=self.W, height=self.H, highlightthickness=0,
                         bg=bg or C["bg_card"], cursor="hand2", bd=0)
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

    def _redraw(self):
        self.delete("all")
        on = bool(self.var.get())
        track = C["accent"] if on else C["stroke_strong"]
        _round_poly(self, 1, 1, self.W - 1, self.H - 1, (self.H - 2) // 2,
                    fill=track, outline="")
        d = self.H - 2 * self.PAD
        x = (self.W - self.PAD - d) if on else self.PAD
        knob = "#ffffff" if on else "#c7ced6"
        self.create_oval(x, self.PAD, x + d, self.PAD + d, fill=knob, outline="")


class RoundedButton(tk.Canvas):
    """A small rounded pill button with an optional leading icon image + label, hover state.

    `accent=True` paints the primary action (orange fill); otherwise it's a bordered ghost pill."""
    def __init__(self, parent, text, command, icon=None, accent=False, bg=None,
                 padx=10, pady=5, radius=6):
        self.accent = accent
        self.bg_outer = bg or C["bg_card"]
        self.fill = C["accent"] if accent else self.bg_outer
        self.fill_hover = C["accent_hover"] if accent else "#232b34"
        self.outline = "" if accent else C["stroke"]
        self.fg = C["accent_ink"] if accent else C["text_muted"]
        self._command = command
        self._icon = icon
        super().__init__(parent, highlightthickness=0, bg=self.bg_outer, bd=0, cursor="hand2")
        self._tmpfont = tkfont.Font(family=theme.FONT["body"][0], size=theme.FONT["body"][1])
        tw = self._tmpfont.measure(text)
        iw = (icon.width() + 5) if icon is not None else 0
        self._cw = padx * 2 + iw + tw
        self._ch = pady * 2 + max(14, (icon.height() if icon is not None else 0))
        self.configure(width=self._cw, height=self._ch)
        self._radius = radius
        self._text = text
        self._padx = padx
        self._draw(self.fill)
        self.bind("<Enter>", lambda _e: self._draw(self.fill_hover))
        self.bind("<Leave>", lambda _e: self._draw(self.fill))
        self.bind("<Button-1>", lambda _e: self._command())

    def _draw(self, fill):
        self.delete("all")
        _round_poly(self, 1, 1, self._cw - 1, self._ch - 1, self._radius,
                    fill=fill, outline=self.outline, width=1)
        x = self._padx
        cy = self._ch // 2
        if self._icon is not None:
            self.create_image(x, cy, anchor="w", image=self._icon)
            x += self._icon.width() + 5
        self.create_text(x, cy, anchor="w", text=self._text, fill=self.fg, font=self._tmpfont)

    def set_text(self, text):
        """Update the label and resize the pill to fit (used for the Record button state)."""
        self._text = text.strip()
        tw = self._tmpfont.measure(self._text)
        iw = (self._icon.width() + 5) if self._icon is not None else 0
        self._cw = self._padx * 2 + iw + tw
        self.configure(width=self._cw)
        self._draw(self.fill)
