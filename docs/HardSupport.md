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

The effects engine drives every device through `set_level(0.0-1.0)`, so that's the method that
matters. The Winwing backend (`hardware/winwing.py`) also keeps a native `vib(0..255)` helper and
has `set_level()` scale into it; that's a convenient pattern for any single-motor device, but it's
optional. Keep-alive timing is owned by the device: the base class's `start_keepalive()` /
`keepalive()` re-arm on your `Capabilities.heartbeat_interval`, so you don't write a heartbeat loop.

## Steps

1. **Create the backend.** Add `src/winwinghaptics/hardware/<yourdevice>.py` with a class that
   subclasses `HapticDevice` and implements the methods above. Use `hardware/hid_win.py` for
   raw USB HID transport (find by vendor ID + HID usage, open, write output reports) if your
   device is HID. Add a static `probe()` that cheaply returns whether the device is present (the
   registry uses it for discovery).

2. **Describe it.** Return a `Capabilities` from the `capabilities` property so the engine can
   adapt (for example, `needs_heartbeat` / `heartbeat_interval` control re-arming).

3. **Register it.** Add your class to `src/winwinghaptics/hardware/__init__.py` and call
   `register(YourDevice)` there, the same way `WinwingUrsaMinor` is registered. Discovery and
   selection then pick it up automatically:

   ```python
   from .registry import register
   register(YourDevice)
   ```

   The controller calls `select_device()`, which returns the first backend whose `probe()` reports
   present, so you do NOT edit the controller to add hardware.

4. **Custom playback (optional).** If your device uploads a whole pattern and owns its own timing
   (rather than being driven level-by-level), give it a `make_renderer()` that returns a renderer
   for it; `renderer_for()` uses it instead of the default `StreamingRenderer`. See
   `docs/Architecture.md` for the effect-descriptor / renderer model.

5. **Update the docs.** Add the device to the Supported hardware table in the root `README.md`.

## Reference

Use `hardware/winwing.py` as the template. It's a small, complete example: HID discovery by
vendor ID and joystick usage, a `probe()`, an arm packet, and a `set_level()` that scales the
normalized intensity to the device's native range. The HID plumbing it relies on lives in
`hardware/hid_win.py`.
