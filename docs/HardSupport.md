# Adding hardware support

The app is device-agnostic at the effects layer. Effects are authored once in normalized
intensity (0.0 to 1.0) and each device maps that to its own native range, so adding a new
controller is mostly about writing one backend class.

## What a device has to provide

Every backend implements the `HapticDevice` interface in
`src/winwinghaptics/hardware/base.py`:

| Method / property | Job |
|---|---|
| `capabilities` | A `Capabilities` describing the device (name, native max level, whether it needs a periodic re-arm heartbeat, etc.) |
| `open()` | Find and open the device. Return `True` on success |
| `close()` | Close the handle |
| `is_open()` | Whether a handle is currently open |
| `arm()` | Send the keep-alive / arm packet (return `True`; make it a no-op if the device doesn't need one) |
| `set_level(level)` | Set vibration from a normalized `0.0`-`1.0` value |

The existing Winwing backend (`hardware/winwing.py`) also keeps a legacy native `vib(0..255)`
method because the effects engine currently calls `vib()` directly. If your device follows the
same pattern, the simplest path is to expose a native `vib()` and have `set_level()` scale into
it, exactly like `WinwingUrsaMinor` does.

## Steps

1. **Create the backend.** Add `src/winwinghaptics/hardware/<yourdevice>.py` with a class that
   subclasses `HapticDevice` and implements the methods above. Use `hardware/hid_win.py` for
   raw USB HID transport (find by vendor ID + HID usage, open, write output reports) if your
   device is HID.

2. **Describe it.** Return a `Capabilities` from the `capabilities` property so the engine can
   adapt (for example, `needs_heartbeat` controls whether it gets periodically re-armed).

3. **Export it.** Add your class to `src/winwinghaptics/hardware/__init__.py` so it's importable
   from `winwinghaptics.hardware`.

4. **Wire it up.** The controller currently constructs the Winwing directly in
   `src/winwinghaptics/app/controller.py`:

   ```python
   self.stick = Stick()
   ```

   To bring up a new device, swap that for your class (or add device selection / auto-probe
   logic there). There is no device registry yet, so this is a manual edit for now.

5. **Update the docs.** Add the device to the Supported hardware table in the root `README.md`.

## Reference

Use `hardware/winwing.py` as the template. It's a small, complete example: HID discovery by
vendor ID and joystick usage, an arm packet resent on a heartbeat, and a vibration frame whose
last byte is the 0-255 intensity. The HID plumbing it relies on lives in `hardware/hid_win.py`.
