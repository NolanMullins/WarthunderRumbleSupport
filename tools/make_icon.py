"""Generate the WT Haptics app icon — a flight-stick + rumble-waves mark on a dark badge in the
brand orange, exported as a multi-resolution .ico (for the exe + Windows taskbar) and a 256px
PNG (for the Tk window icon). Run: python tools/make_icon.py

Pure Pillow (already a runtime dep). Rendered large and downsampled for crisp small sizes.
"""
import os
from PIL import Image, ImageDraw, ImageFilter

OUT_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                        "..", "src", "winwinghaptics", "ui", "assets"))

# brand tokens (match ui/theme.py)
ACCENT = (255, 122, 24)        # #ff7a18
ACCENT_HI = (255, 165, 80)     # lighter highlight
BG_TOP = (28, 34, 42)          # #1c222a
BG_BOT = (13, 16, 20)          # #0d1014
STROKE = (44, 52, 62)          # subtle border

S = 1024                       # master render size


def _vgradient(size, top, bot):
    g = Image.new("RGB", (1, size), 0)
    for y in range(size):
        t = y / (size - 1)
        g.putpixel((0, y), tuple(int(top[i] + (bot[i] - top[i]) * t) for i in range(3)))
    return g.resize((size, size))


def render(size=S):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # --- badge: rounded square with a vertical gradient fill + subtle stroke ---
    inset = int(size * 0.06)
    rad = int(size * 0.235)
    box = [inset, inset, size - inset, size - inset]
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(box, radius=rad, fill=255)
    grad = _vgradient(size, BG_TOP, BG_BOT).convert("RGBA")
    img.paste(grad, (0, 0), mask)
    d.rounded_rectangle(box, radius=rad, outline=STROKE, width=max(2, size // 220))

    cx = size // 2

    # --- rumble waves: one BOLD arc each side of the grip (thick so it survives 16px), plus a
    #     fainter outer arc that only reads at larger sizes ---
    wave_c = (cx, int(size * 0.43))
    for r, alpha, wf in ((int(size * 0.255), 255, 0.060), (int(size * 0.340), 120, 0.030)):
        lw = max(7, int(size * wf))
        bbox = [wave_c[0] - r, wave_c[1] - r, wave_c[0] + r, wave_c[1] + r]
        layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        ld = ImageDraw.Draw(layer)
        ld.arc(bbox, start=-48, end=48, fill=ACCENT + (alpha,), width=lw)      # right
        ld.arc(bbox, start=132, end=228, fill=ACCENT + (alpha,), width=lw)     # left
        img.alpha_composite(layer)

    # --- flight stick: knob (ball) + grip capsule + small mount base, in accent ---
    grip_w = int(size * 0.150)
    knob_r = int(size * 0.130)
    knob_cy = int(size * 0.330)
    grip_top = knob_cy
    grip_bot = int(size * 0.620)
    # grip
    d.rounded_rectangle([cx - grip_w // 2, grip_top, cx + grip_w // 2, grip_bot],
                        radius=grip_w // 2, fill=ACCENT)
    # knob
    d.ellipse([cx - knob_r, knob_cy - knob_r, cx + knob_r, knob_cy + knob_r], fill=ACCENT)
    # knob highlight (upper-left sheen)
    hr = int(knob_r * 0.40)
    hx, hy = cx - int(knob_r * 0.40), knob_cy - int(knob_r * 0.40)
    d.ellipse([hx - hr, hy - hr, hx + hr, hy + hr], fill=ACCENT_HI)
    # mount base: a compact trapezoid plate, clearly separated from the grip
    base_y = int(size * 0.660)
    bw_top, bw_bot = int(size * 0.075), int(size * 0.235)
    plate_h = int(size * 0.070)
    d.polygon([(cx - bw_top, base_y), (cx + bw_top, base_y),
               (cx + bw_bot, base_y + plate_h), (cx - bw_bot, base_y + plate_h)],
              fill=ACCENT)
    d.rounded_rectangle([cx - bw_bot, base_y + plate_h,
                         cx + bw_bot, base_y + plate_h + int(size * 0.030)],
                        radius=int(size * 0.018), fill=ACCENT)

    return img


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    master = render(S)
    png = master.resize((256, 256), Image.LANCZOS)
    png_path = os.path.join(OUT_DIR, "wt_haptics.png")
    png.save(png_path)
    ico_path = os.path.join(OUT_DIR, "wt_haptics.ico")
    sizes = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)]
    master.save(ico_path, sizes=sizes)
    print("wrote", png_path)
    print("wrote", ico_path)


if __name__ == "__main__":
    main()
