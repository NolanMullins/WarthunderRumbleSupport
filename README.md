# Warthunder Rumble Support (WinwingHaptics)

Haptic feedback for **War Thunder** on the **Winwing Ursa Minor Fighter** joystick.
The app drives the stick's built-in vibration motor so you *feel* in-game weapon events —
cannon fire, missile/rocket/bomb launches, countermeasures, kills and deaths — without any
official SimApp Pro install.

> Windows-only. Talks to the stick over raw USB HID and to War Thunder over its local
> telemetry server. No game files are modified.

---

## How it works

The app fuses two signal sources and renders effects on one serialized motor thread:

1. **HUD screen-reading** (`hud_detect.py`) — captures the on-screen weapon readout and
   reads each weapon's ammo counter (RKT / BMB / AAM / FLR / CHFF / CNN) via fast NumPy
   template matching against glyphs harvested during a one-time calibration. When a counter
   ticks **down**, that weapon fired → emit the matching effect. A temporal tracker rejects
   OCR noise (digit flicker, truncation misreads, respawn resets) so a number wobbling
   `248↔242` never buzzes, while a real burst fires within ~1–2 frames.

2. **War Thunder telemetry** (`localhost:8111`) — polled by the app for the live
   trigger-input state (`/indicators` → `weapon2`) for the lowest-latency gun rumble, and
   for the kill/death feed (`/hudmsg`) to drive callsign-based kill/death effects.

3. **HID vibration** — ARM + SET packets over USB HID to the Ursa Minor Fighter
   (VID `0x4098`, PID `0xBC2A`). Effects are short ERM envelopes; the gun is a sustained
   rumble while the trigger is held; one-shot effects take motor priority so a launch is
   never flattened by the gun rumble.

---

## Project layout

```
run.py                 Thin launcher (python run.py [--selftest|--hudtest])
src/
  winwing_haptics.py   Main Tkinter app: workers, effect engine, HID I/O, WT telemetry
  winwinghaptics/
    detection/
      hud_detect.py    HUD detector + TemporalTracker (calibration, read_counts, fire logic)
tools/                 Offline analysis + the A/B test platform drivers (see tests/README.md)
tests/                 Regression suite: detector / tracker / calibration A/B (pytest)
recordings/            (gitignored) Drop Record-30s captures here for the tools
datasets/              (gitignored) Static frame sets + ground_truth.json for hud_eval
```

> The app is being decomposed from the flat `winwing_haptics.py` into the `winwinghaptics`
> package, one phase at a time; each phase stays green on the `tests/` suite.

---

## Setup

Requires Python 3.10+ (64-bit) on Windows.

```powershell
python -m pip install -r requirements.txt
```

Run from source:

```powershell
python run.py
```

In the app: connect the stick, enable HUD auto-detect (it self-calibrates), and the status
panel shows the live read. Use **Record 30s** to capture a clip (frames + telemetry +
`calib.json`) into a `recordings/` folder for offline analysis with the tools.

---

## Building the standalone app

PyInstaller `--onedir` (WDAC environments block `--onefile`):

```powershell
python -m PyInstaller --onedir --noconsole --name WinwingHaptics ^
  --distpath dist_final --workpath build --specpath build ^
  --collect-all winsdk --collect-submodules winwinghaptics --collect-submodules numpy ^
  run.py
```

Smoke-test the build headlessly:

```powershell
..\dist_final\WinwingHaptics\WinwingHaptics.exe --hudtest   # writes hudtest_result.txt
```

Expected: `detector_ready=True ocr_ready=True`.

---

## Running the analysis tools

The tools read clips from `recordings/` (and `hud_eval.py` reads `datasets/`). Place a
captured clip so the path is `recordings/<clip>/<clip>/` (the recorder nests it that way),
then:

```powershell
python tools\live_vs_current.py     # current tracker vs live in-game fires
python tools\miss_audit.py          # missed / late detections
python tools\all_frames_audit.py    # faithful detector A/B (clips with calib.json)
```

`all_frames_audit.py` is only faithful for clips recorded with a `calib.json` sidecar
(captured automatically by current builds); older clips are flagged non-faithful.

---

## Notes

- The HID protocol and vibration packet format were reverse-engineered; no vendor SDK is
  used or required.
- The detector is tuned against real gameplay recordings; the `tools/` harnesses exist to
  catch regressions and quantify latency / false-positive / miss rates before shipping.
