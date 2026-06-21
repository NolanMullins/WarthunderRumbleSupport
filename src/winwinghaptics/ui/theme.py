"""Design tokens — the single source of truth for the UI's look.

Widgets reference NAMED tokens (theme.COLOR["accent"], theme.SPACE["md"], ...) instead of raw
hex/sizes, so the whole look can be retuned or a light theme added by editing this one file. This
is the "design language" layer for the Concept A Fluent-style UI; the brand orange is the accent,
status colours (green/red) are reserved for connection state, and an accent "firing" tint marks a
live haptic so no colour does double duty.
"""

# ---- colour tokens (dark theme) ----
COLOR = {
    "accent":        "#ff7a18",   # primary actions, switch-on, focus, selection, live tint
    "accent_hover":  "#ff9442",
    "accent_press":  "#e06a10",
    "accent_ink":    "#1a1109",   # text/!icon ON an accent fill

    "bg_base":       "#0f1216",   # window
    "bg_card":       "#171c22",   # grouped surfaces
    "bg_subtle":     "#1e252d",   # hover / selected rows, icon tiles, inputs
    "bg_titlebar":   "#0c0f13",

    "stroke":        "#262d36",   # card / control borders
    "stroke_strong": "#39424d",   # switch track (off), slider track

    "text":          "#e6edf3",
    "text_muted":    "#8b97a4",
    "text_on_accent": "#ffffff",

    "status_ok":     "#33d17a",   # connected (status only)
    "status_bad":    "#e5484d",   # error (status only)
    "status_idle":   "#566270",   # waiting / disconnected dot
}

# ---- spacing scale (4px grid) ----
SPACE = {"xs": 4, "sm": 8, "md": 12, "lg": 16, "xl": 20, "xxl": 24}

# ---- corner radii ----
RADIUS = {"control": 5, "card": 9, "pill": 11}

# ---- type ramp (family, size, weight) -- Fluent-ish on Segoe UI ----
FONT = {
    "caption":     ("Segoe UI", 8),
    "body":        ("Segoe UI", 9),
    "body_strong": ("Segoe UI Semibold", 9),
    "subtitle":    ("Segoe UI Semibold", 11),
    "title":       ("Segoe UI Semibold", 13),
    "mono":        ("Consolas", 8),
}

# ---- icon sizing ----
ICON = {"row": 17, "tab": 15, "status": 15, "action": 12}
