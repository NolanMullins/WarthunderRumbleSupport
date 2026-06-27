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
python -m PyInstaller --onedir --noconsole --name WTHaptics ^
  --distpath dist_final --workpath build --specpath build ^
  --paths src ^
  --icon ../src/winwinghaptics/ui/assets/wt_haptics.ico ^
  --collect-all winsdk --collect-all tksvg ^
  --collect-submodules winwinghaptics --collect-submodules numpy ^
  --add-data "../src/winwinghaptics/ui/assets;winwinghaptics/ui/assets" ^
  run.py
```

Flag notes (each fixes a real frozen-build failure):

* `--paths src` is REQUIRED: `run.py` adds `src/` to `sys.path` at runtime and imports the
  top-level `winwing_haptics` entry-point shim, which PyInstaller's static analysis cannot see
  through — without it the shim is left out and the exe crashes at launch with
  `ModuleNotFoundError: No module named 'winwing_haptics'`.
* `--collect-all winsdk` pulls in the Windows OCR used during HUD calibration.
* `--collect-all tksvg` bundles the tksvg Tcl extension (`libtksvg.dll` + `pkgIndex.tcl`) that
  renders the vendored Lucide SVG icons. Bundling only the Python wrapper is not enough —
  `tksvg.SvgImage` does `package require tksvg` at runtime, so without the Tcl files every UI
  icon silently renders blank.
* `--add-data "../src/winwinghaptics/ui/assets;winwinghaptics/ui/assets"` bundles the package
  data — the Lucide icon SVGs and the app icon (`wt_haptics.ico`/`.png`). `--collect-data
  winwinghaptics` does NOT work here: the package lives only under `src/` (added via `--paths`),
  which PyInstaller's data collector does not honour, so it collects nothing and the icons go
  blank. The explicit `--add-data` is deterministic. Its SRC half (like `--icon`) resolves
  relative to `--specpath` (here `build/`), hence the `../`; the `;DEST` half is the in-bundle
  path.
* `--icon` sets the exe's own icon — its path also resolves relative to `--specpath` (here
  `build/`), hence the leading `../`.

Build from a checkout that matches the version you intend to ship (see Releases below).

## Installer (Inno Setup)

The release also ships a one-click Windows installer (`WTHaptics-Setup-v<version>.exe`) built from
`installer/WTHaptics.iss` with [Inno Setup 6](https://jrsoftware.org/isinfo.php). It packages the
`dist_final\WTHaptics` `--onedir` build into a setup that creates Start-menu (and optional desktop)
shortcuts and an Add/Remove Programs entry with an uninstaller.

**It installs PER-USER to `%LOCALAPPDATA%\Programs\WT Haptics` (no admin / no UAC).** This is
deliberate: the app self-updates by overwriting its own `--onedir` folder in place
(`src/winwinghaptics/update/installer.py`). A user-writable location lets that keep working with no
elevation — install once, then every future GitHub release lands automatically via the in-app
updater. A Program Files install would need admin to overwrite and would silently break self-update.

Build the installer locally (after the PyInstaller build above):

```powershell
# ISCC is the Inno Setup command-line compiler; install Inno Setup 6 first.
& "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe" /DMyAppVersion=0.1.1 installer\WTHaptics.iss
# -> dist_installer\WTHaptics-Setup-v0.1.1.exe
```

`MyAppVersion` defaults to `0.0.0-dev` if omitted. A silent install/uninstall (for testing) is
`WTHaptics-Setup-v<ver>.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART`. Uninstall removes only what
setup installed, so user data next to the exe (`winwing_haptics.json`, `hud_calib.json`, `hud_rec_*`
recordings) is left behind.

## Smoke test

Run the build headlessly to confirm the detector and OCR loaded inside the frozen app:

```powershell
.\dist_final\WTHaptics\WTHaptics.exe --hudtest
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

The app self-updates from GitHub Releases (see `src/winwinghaptics/update/`).

### Automated pipeline (recommended)

`.github/workflows/release.yml` builds, smoke-tests, and publishes a release automatically. To
ship a version:

1. **Bump the version on `main`.** Edit `__version__` in `src/winwinghaptics/__init__.py` (single
   source of truth) and merge it to `main` via a PR.
2. **Tag and push.** Create a tag that matches the version and push it:
   ```powershell
   git tag v0.2.0      # must equal "v" + __version__
   git push origin v0.2.0
   ```

The pipeline (on `windows-latest`) then: verifies the tag commit is on `main`, verifies the tag
matches `__version__` (fails loudly on a mismatch), runs the test suite, builds the `--onedir`
app, smoke-tests the frozen exe (`--hudtest` must report `detector_ready=True`), then publishes a
GitHub release with **two assets**:

* `WTHaptics-v<version>-win64.zip` — the zipped `--onedir` build. **This is what the in-app
  self-updater downloads and swaps in place** (it matches assets by the `.zip` suffix).
* `WTHaptics-Setup-v<version>.exe` — the one-click Inno Setup installer for a fresh install.

A pre-release tag (e.g. `v0.2.0-rc1`) is marked as a GitHub pre-release, which the in-app updater
ignores by default.

Run the workflow manually (Actions tab → Release → Run workflow) to build + smoke-test the
current `main` and download the zip **and installer** as build artifacts **without** cutting a
release — useful for verifying a build before tagging.

### Manual fallback

If you build locally instead: bump `__version__`, build the `--onedir` app (above), zip
`dist_final/WTHaptics` into a single `.zip`, build the installer (see Installer section), then
create a release tagged `v<version>` and attach both the `.zip` and the `Setup .exe`.

### How the updater consumes it

The updater picks the first non-draft, non-prerelease release, compares its tag to `__version__`,
and (on a frozen Windows build) downloads the `.zip` asset, swaps the app folder, and relaunches.
A release with no `.zip` asset still drives the "update available" banner, but the button falls
back to opening the Releases page. User data next to the exe (`winwing_haptics.json`,
`hud_calib.json`, recordings) is preserved across a swap.
