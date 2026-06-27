"""
check_input.py -- one-shot input diagnostic you run in YOUR OWN terminal.

Why: the goal is pixel-independent ground truth for the recorder (so we can validate missile
detection against your REAL inputs instead of hand-labelling). This checks which input sources
a normal process in your session can actually see while you interact, so we pick the right one.

HOW TO RUN (with the Winwing plugged in):
    cd <repo>
    python tools\\check_input.py

Then, during the 25-second window, do ALL of:
  * move the stick around (roll + pitch),
  * press several buttons, especially your MISSILE-FIRE button and the trigger,
  * tap the \\ (backslash) key a few times, and the F12 key a few times.

At the end it prints a clear summary of what registered. Paste that summary back.
"""
import os
import sys
import time
import threading

sys.path.insert(0, os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "src")))

DUR = 25.0


def hid_probe(stop, out):
    try:
        from winwinghaptics.hardware import hid_win as H
        from winwinghaptics.hardware.winwing import WW_VID
        path = H.find_device_path(WW_VID, 0x0001, 0x0004)
        if not path:
            out["hid"] = "NOT FOUND (stick not detected as HID)"
            return
        h = H.open_path(path); rl = H.input_report_length(h) or 30
        distinct = set(); reads = 0
        while not stop["v"]:
            d = H.read(h, rl)
            if d is None:
                break
            reads += 1; distinct.add(d.hex())
        H.close(h)
        out["hid"] = f"found; {reads} reads, {len(distinct)} DISTINCT reports " \
                     f"({'LIVE INPUT' if len(distinct) > 1 else 'STATIC -- no live input'})"
    except Exception as e:
        out["hid"] = f"error: {e!r}"


def pygame_probe(stop, out):
    try:
        os.environ["SDL_VIDEODRIVER"] = "dummy"
        import pygame
        pygame.init(); pygame.joystick.init()
        if pygame.joystick.get_count() == 0:
            out["pygame"] = "no joystick via DirectInput/SDL"
            return
        j = pygame.joystick.Joystick(0); j.init()
        nax = j.get_numaxes(); base = [j.get_axis(a) for a in range(nax)]
        moved = 0.0; btns = set()
        while not stop["v"]:
            for e in pygame.event.get():
                if e.type == pygame.JOYBUTTONDOWN:
                    btns.add(int(e.button))
            for a in range(nax):
                moved = max(moved, abs(j.get_axis(a) - base[a]))
            time.sleep(0.01)
        out["pygame"] = f"'{j.get_name()}'; axis_moved_max={moved:.2f}; " \
                        f"buttons_pressed={sorted(btns) if btns else 'NONE'}"
    except Exception as e:
        out["pygame"] = f"error: {e!r}"


def keyboard_probe(stop, out):
    try:
        import ctypes
        u = ctypes.windll.user32
        u.GetAsyncKeyState.restype = ctypes.c_short
        u.GetAsyncKeyState.argtypes = [ctypes.c_int]
        cands = {"backslash": 0xDC, "f12": 0x7B, "space": 0x20}
        was = {k: False for k in cands}; edges = {k: 0 for k in cands}
        while not stop["v"]:
            for k, vk in cands.items():
                down = bool(u.GetAsyncKeyState(vk) & 0x8000)
                if down and not was[k]:
                    edges[k] += 1
                was[k] = down
            time.sleep(0.008)
        out["keyboard"] = ", ".join(f"{k}={n}" for k, n in edges.items())
    except Exception as e:
        out["keyboard"] = f"error: {e!r}"


def main():
    out = {}
    stop = {"v": False}
    ts = [threading.Thread(target=f, args=(stop, out), daemon=True)
          for f in (hid_probe, pygame_probe, keyboard_probe)]
    for t in ts:
        t.start()
    print("=" * 64)
    print(f"INPUT CHECK -- interact for {DUR:.0f} seconds NOW:")
    print("  * move the stick (roll+pitch)")
    print("  * press buttons, esp. your MISSILE-FIRE button + trigger")
    print("  * tap the \\ key a few times, and F12 a few times")
    print("=" * 64)
    for i in range(int(DUR), 0, -1):
        print(f"  ...{i:2d}s left", end="\r", flush=True)
        time.sleep(1.0)
    stop["v"] = True
    time.sleep(0.8)
    print("\n" + "=" * 64)
    print("RESULTS (paste this back):")
    print(f"  HID raw   : {out.get('hid')}")
    print(f"  DirectInput: {out.get('pygame')}")
    print(f"  Keyboard  : {out.get('keyboard')}")
    print("=" * 64)


if __name__ == "__main__":
    main()
