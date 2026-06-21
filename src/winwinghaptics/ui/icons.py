"""Icon loader — vendored Lucide SVGs rendered to tinted Tk images.

Icons live as SVG source under ui/assets/icons/lucide/ (Lucide, ISC + MIT; see the LICENSE in
that folder). They draw with `stroke="currentColor"`, so we tint by substituting the color into
the SVG text and rendering it with tksvg. Results are cached by (name, color, size) because a
Tk PhotoImage is relatively expensive to build and the same icon/colour recurs across many rows.

A loader is bound to a Tk root (tksvg needs one). Keep a reference to this loader for the life of
the window: Tk images are garbage-collected if no Python reference survives, which blanks them.
"""
import os

try:
    import tksvg
    _TKSVG_OK = True
except Exception:                       # pragma: no cover - environment without tksvg
    tksvg = None
    _TKSVG_OK = False

_ICON_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "icons", "lucide")


class IconLoader:
    """Render + cache tinted Lucide icons as Tk images.

    Usage:
        icons = IconLoader(root)
        lbl = tk.Label(parent, image=icons.get("rocket", "#ff7a18", 18))
    """

    def __init__(self, root, icon_dir=_ICON_DIR):
        self._root = root
        self._dir = icon_dir
        self._cache = {}                 # (name, color, size) -> PhotoImage
        self._raw = {}                   # name -> svg text (read once)
        self.available = _TKSVG_OK

    def _svg_text(self, name):
        if name not in self._raw:
            path = os.path.join(self._dir, name + ".svg")
            with open(path, encoding="utf-8") as fh:
                self._raw[name] = fh.read()
        return self._raw[name]

    def has(self, name):
        """True if the named icon SVG exists on disk (cheap existence check)."""
        return os.path.exists(os.path.join(self._dir, name + ".svg"))

    def get(self, name, color="#e6edf3", size=18):
        """Return a tinted Tk image for `name` at `color`/`size`, or None if unavailable.

        Cached by (name, color, size). Returns None (rather than raising) when tksvg is missing
        or the icon can't be rendered, so the GUI can fall back to a text label."""
        if not self.available:
            return None
        key = (name, color, size)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        try:
            svg = self._svg_text(name).replace("currentColor", color)
            img = tksvg.SvgImage(master=self._root, data=svg, scaletowidth=size)
        except Exception:
            return None
        self._cache[key] = img
        return img
