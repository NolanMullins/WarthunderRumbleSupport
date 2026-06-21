"""Effects engine — serialized motor output over a HapticDevice.

One heartbeat thread keeps the device armed and services the sustained gun rumble. One-shot
effects (missile/rocket/bomb/flare/kill/hit/death) run on a short worker thread and take
PRIORITY of the motor while playing, so their strong envelope is never stomped by the gun
rumble; when a one-shot ends, the gun rumble resumes if the trigger is still held.

The engine orchestrates (heartbeat, gun sustain, priority arbitration) but no longer hardcodes
how an effect is played: it asks the device's renderer to render an Effect descriptor (see
effects.model / effects.renderer). The default StreamingRenderer streams normalized levels via
set_level(0.0-1.0), so any HapticDevice works and a pattern-upload device can supply its own
renderer. On the Winwing the normalized levels map back to the original 0-255 values exactly, so
felt output is unchanged.
"""
import threading
import time

from .library import get_effect, GUN_LEVEL
from .renderer import renderer_for


class EffectsEngine:
    def __init__(self, device, logfn=lambda s: None):
        self.stick = device                # a HapticDevice; driven via set_level(0.0-1.0)
        self.renderer = renderer_for(device)  # per-device playback strategy (streaming by default)
        self.log = logfn
        self._stop = threading.Event()
        self._hb = None
        self._gun_until = 0.0
        self._gun_on = False
        self._oneshot_lock = threading.Lock()
        self._priority = False     # True while a one-shot owns the motor -> heartbeat must NOT
                                   # write the gun rumble over it

    # heartbeat keeps haptics armed
    def start_heartbeat(self):
        if self._hb and self._hb.is_alive():
            return
        self._stop.clear()
        self._hb = threading.Thread(target=self._hb_loop, daemon=True)
        self._hb.start()

    def stop(self):
        self._stop.set()
        try:
            self.stick.set_level(0.0)
        except Exception:
            pass

    def _hb_loop(self):
        # arm immediately, then every 2.5s; also services the continuous gun rumble
        try:
            self.stick.arm()
        except Exception:
            pass
        last_arm = time.time()
        while not self._stop.is_set():
            now = time.time()
            if now - last_arm >= 2.5:
                self.stick.arm()
                last_arm = now
            # continuous gun rumble while active -- BUT a one-shot effect takes priority and
            # owns the motor while it plays. When it finishes, the gun rumble resumes if the
            # trigger is still held.
            if self._priority:
                pass                                   # one-shot owns the motor
            elif now < self._gun_until:
                self.stick.set_level(GUN_LEVEL)
                self._gun_on = True
            elif self._gun_on:
                self.stick.set_level(0.0)
                self._gun_on = False
            time.sleep(0.05)

    # --- public effect triggers (each one-shot runs on its own short thread) ---
    def gun_active(self, dur=0.25):
        """Call repeatedly while weapon2==1; keeps the rumble alive `dur` seconds."""
        self._gun_until = max(self._gun_until, time.time() + dur)

    def _run_oneshot(self, fn):
        def wrap():
            with self._oneshot_lock:
                self._priority = True          # claim the motor so the gun rumble pauses
                try:
                    fn()
                finally:
                    self.stick.set_level(0.0)  # leave the motor quiet for the heartbeat
                    self._priority = False     # gun rumble resumes if trigger still held
        threading.Thread(target=wrap, daemon=True).start()

    def play(self, name):
        """Play a named one-shot Effect from the library via the device's renderer."""
        eff = get_effect(name)
        if not eff:
            return
        if eff.log:
            self.log(eff.log)
        self._run_oneshot(lambda: self.renderer.render(eff, self._stop.is_set))

    # --- named convenience triggers (kept for the existing call sites) ---
    def missile(self):
        self.play("missile")

    def rocket(self):
        self.play("rocket")

    def bomb(self):
        self.play("bomb")

    def flare(self):
        self.play("flare")

    def kill(self):
        self.play("kill")

    def hit(self):
        self.play("hit")

    def death(self):
        self.play("death")

    def fire_effect(self, name):
        """Dispatch by effect name used by the HUD detector / bindings."""
        if name in ("missile", "rocket", "bomb", "flare"):
            self.play(name)
        elif name == "gun":
            self.gun_active(0.4)


# Back-compat alias: the app + selftest construct/refer to `Effects`.
Effects = EffectsEngine
