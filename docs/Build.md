# Building the standalone app

The app ships as a PyInstaller `--onedir` build. `--onefile` is avoided because WDAC
environments block it.

## Prerequisites

Windows, Python 3.10+ (64-bit), runtime deps installed, plus PyInstaller:

```powershell
python -m pip install -r requirements.txt
python -m pip install pyinstaller
```

## Build

```powershell
python -m PyInstaller --onedir --noconsole --name WinwingHaptics ^
  --distpath dist_final --workpath build --specpath build ^
  --collect-all winsdk --collect-submodules winwinghaptics --collect-submodules numpy ^
  run.py
```

The `--collect-*` flags matter: `winsdk` is needed for the Windows OCR used during HUD
calibration, and the package / numpy submodules have to be pulled in explicitly.

## Smoke test

Run the build headlessly to confirm the detector and OCR loaded inside the frozen app:

```powershell
.\dist_final\WinwingHaptics\WinwingHaptics.exe --hudtest
```

It writes `hudtest_result.txt`. A good build reports:

```
detector_ready=True ocr_ready=True
```

## CLI flags

`run.py` (and the frozen exe) take the same flags:

| Flag | What it does |
|---|---|
| (none) | Launch the GUI |
| `--selftest` | Open the stick, arm it, play the missile effect, then exit |
| `--hudtest` | Detector / OCR readiness check, writes `hudtest_result.txt` |
