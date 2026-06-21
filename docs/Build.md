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
  --collect-data winwinghaptics ^
  run.py
```

The `--collect-*` flags matter: `winsdk` is needed for the Windows OCR used during HUD
calibration, the package / numpy submodules have to be pulled in explicitly, and
`--collect-data winwinghaptics` bundles the package's data files — the vendored Lucide UI icons
under `ui/assets/` (without it the icons silently fall back to blank). Build from a checkout that
matches the version you intend to ship (see Releases below).

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

## Releases (auto-update)

The app self-updates from GitHub Releases (see `src/winwinghaptics/update/`). To publish a build
the in-app updater will pick up:

1. **Bump the version.** Edit `__version__` in `src/winwinghaptics/__init__.py`. This is the single
   source of truth the updater compares against the latest release tag.
2. **Build** the `--onedir` app (above) and **zip the output folder**
   (`dist_final/WinwingHaptics`) into a single `.zip`.
3. **Create a release** tagged `v<version>` (e.g. `v0.2.0`) and **attach the `.zip`** as a release
   asset. The release notes become the in-app changelog.

The updater picks the first non-draft, non-prerelease release, compares its tag to `__version__`,
and (on a frozen Windows build) downloads the `.zip` asset, swaps the app folder, and relaunches.
A release with no `.zip` asset still drives the "update available" banner, but the button falls
back to opening the Releases page. User data next to the exe (`winwing_haptics.json`,
`hud_calib.json`, recordings) is preserved across a swap.
