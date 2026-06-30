"""Apply an update to the frozen --onedir build (Windows) and relaunch.

A running Windows .exe and its loaded DLLs are locked, so we can't overwrite the app folder
in-process. Instead:

  1. download the release .zip asset to a temp file,
  2. extract it to a staging folder OUTSIDE the app folder,
  3. write a helper .bat that waits for THIS process to exit, mirrors the staged build over the app
     folder (robocopy), relaunches the exe, and deletes the staging + itself,
  4. launch that .bat detached and exit the app.

This is meaningful only for a frozen (PyInstaller) build: `is_supported()` is False when running
from source, where the GUI instead opens the Releases page. Everything except the final swap is
unit-testable; the swap is delegated to the OS helper so the app can exit cleanly.
"""
import os
import sys
import shutil
import zipfile
import tempfile
import subprocess
import urllib.request

from ..config import CONFIG_NAME, HUD_CALIB_NAME   # user-data filenames to protect during a swap


def is_frozen():
    return getattr(sys, "frozen", False)


class WindowsUpdater:
    """Download + stage + swap-and-relaunch a frozen --onedir build."""

    def __init__(self, app_dir=None, exe_path=None, opener=None):
        # When frozen, the exe lives at sys.executable and the --onedir folder is its directory.
        self.exe_path = exe_path or sys.executable
        self.app_dir = app_dir or os.path.dirname(os.path.abspath(self.exe_path))
        self._open = opener or self._default_open

    # ---- support gate ----
    def is_supported(self):
        """True only for a frozen build on Windows (the case the swap-and-relaunch is built for)."""
        return is_frozen() and os.name == "nt"

    # ---- download ----
    @staticmethod
    def _default_open(url, timeout=30.0):
        req = urllib.request.Request(url, headers={"User-Agent": "WinwingHaptics-Updater"})
        return urllib.request.urlopen(req, timeout=timeout)

    def download(self, asset_url, dest_path, on_progress=None, chunk=65536):
        """Stream the asset to dest_path. on_progress(read, total) is called as it downloads."""
        resp = self._open(asset_url)
        total = 0
        try:
            total = int(resp.headers.get("Content-Length", 0) or 0)
        except Exception:
            total = 0
        read = 0
        with open(dest_path, "wb") as fh:
            while True:
                buf = resp.read(chunk)
                if not buf:
                    break
                fh.write(buf)
                read += len(buf)
                if on_progress:
                    on_progress(read, total)
        try:
            resp.close()
        except Exception:
            pass
        return dest_path

    # ---- stage ----
    def stage(self, zip_path, staging_dir):
        """Extract the update zip into staging_dir and return the folder that holds the build.

        The release zip may contain the build directly OR nested in a single top-level folder
        (e.g. WinwingHaptics/...); this returns whichever directory actually contains the exe, or
        None if the extracted contents don't look like the app build (no exe) -- so a malformed or
        wrong asset can't be swapped over the install."""
        if os.path.isdir(staging_dir):
            shutil.rmtree(staging_dir, ignore_errors=True)
        os.makedirs(staging_dir, exist_ok=True)
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(staging_dir)
        return self._build_root(staging_dir)

    def _build_root(self, staging_dir):
        """Find the directory inside staging that contains the app exe (handles a nested top folder).
        Returns None when the expected exe is nowhere to be found -- the caller MUST treat that as a
        failed/invalid download and NOT swap, rather than guessing a root and overwriting the install."""
        exe_name = os.path.basename(self.exe_path)
        if os.path.exists(os.path.join(staging_dir, exe_name)):
            return staging_dir
        entries = [os.path.join(staging_dir, e) for e in os.listdir(staging_dir)]
        dirs = [d for d in entries if os.path.isdir(d)]
        for d in dirs:
            if os.path.exists(os.path.join(d, exe_name)):
                return d
        return None

    # ---- helper script ----
    def _helper_script(self, build_root, staging_dir, log_path=None):
        """Generate the .bat that waits for this PID, copies build_root over app_dir, relaunches,
        and cleans up.

        Uses robocopy /E (copy the new build, overwriting changed files) NOT /MIR: /MIR PURGES
        destination files absent from the source, which would delete the user's config, calibration
        and recordings that live next to the exe in a frozen build. /XF also protects the user's
        settings files from being overwritten by anything a release archive might accidentally carry.
        Robocopy exit codes < 8 are success. The log is written to the temp work dir, never into the
        app dir (so it isn't left behind in the install)."""
        pid = os.getpid()
        exe = self.exe_path
        log = log_path or os.path.join(os.path.dirname(staging_dir), "update_log.txt")
        excl_files = " ".join('"%s"' % n for n in (CONFIG_NAME, HUD_CALIB_NAME))
        return (
            "@echo off\r\n"
            "setlocal\r\n"
            f'set "PID={pid}"\r\n'
            f'set "SRC={build_root}"\r\n'
            f'set "DST={self.app_dir}"\r\n'
            f'set "STAGE={staging_dir}"\r\n'
            f'set "EXE={exe}"\r\n'
            f'set "LOG={log}"\r\n'
            "echo WinwingHaptics update started %DATE% %TIME% > \"%LOG%\"\r\n"
            # Wait for THIS app's pid to exit so its files unlock, THEN swap. `if errorlevel 1` is
            # true when find did NOT match -> the process is gone -> safe to swap (goto swap). A
            # TRIES cap (~3 min) guarantees we can NEVER spin forever; but on the cap we do NOT
            # swap (goto giveup) -- the app is still alive, so robocopy over its locked files could
            # leave a partial, mixed-version install. The app force-exits in ~2 s, so the cap is a
            # pure safety net. `ping` is the delay (needs no console stdin, unlike `timeout`).
            # All paths converge on a single :cleanup so the self-delete is always the LAST line.
            "set /a TRIES=0\r\n"
            ":waitloop\r\n"
            'tasklist /FI "PID eq %PID%" 2>NUL | find "%PID%" >NUL\r\n'
            "if errorlevel 1 goto swap\r\n"
            "set /a TRIES+=1\r\n"
            "if %TRIES% GEQ 180 goto giveup\r\n"
            "ping -n 2 127.0.0.1 >NUL\r\n"
            "goto waitloop\r\n"
            ":giveup\r\n"
            'echo app still running after timeout; update not applied >> "%LOG%"\r\n'
            "goto cleanup\r\n"
            ":swap\r\n"
            f'robocopy "%SRC%" "%DST%" /E /XF {excl_files} /R:3 /W:2 /NFL /NDL /NJH /NJS /NP '
            '>> "%LOG%" 2>&1\r\n'
            "if errorlevel 8 goto roborfail\r\n"
            'start "" "%EXE%"\r\n'
            "goto cleanup\r\n"
            ":roborfail\r\n"
            'echo robocopy failed, aborting relaunch >> "%LOG%"\r\n'
            ":cleanup\r\n"
            'rmdir /S /Q "%STAGE%" 2>NUL\r\n'
            'del "%~f0"\r\n'
        )

    def _write_helper(self, build_root, staging_dir, helper_path):
        with open(helper_path, "w", encoding="ascii", newline="") as fh:
            fh.write(self._helper_script(build_root, staging_dir))
        return helper_path

    def _launch_helper(self, helper_path):
        """Launch the helper so it survives this process exiting, WITHOUT a visible window.

        Flags: CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP. We must NOT use DETACHED_PROCESS: a
        detached process has no console, and the helper's `tasklist | find` pid-wait then BLOCKS
        forever with no console -- the helper spins, shows a black window that never closes, and the
        swap never happens (the exact bug users hit). CREATE_NO_WINDOW instead gives the helper a
        valid but hidden console, so `tasklist`/`find` work and no window is shown; the child is
        independent so it outlives us to overwrite the app folder and relaunch.
        """
        CREATE_NO_WINDOW = 0x08000000
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        subprocess.Popen(["cmd", "/c", helper_path], close_fds=True,
                         creationflags=CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP,
                         cwd=os.path.dirname(helper_path))

    # ---- orchestration ----
    def update(self, info, on_progress=None, work_dir=None, _exit=None):
        """Download `info` (an UpdateInfo with an asset_url), stage it, and launch the swap helper.

        Aborts (returns False, no swap) if unsupported, there's no asset, the download/extract fails,
        or the staged contents don't contain the app exe (a malformed/wrong asset). On success it
        invokes `_exit` so this process releases its file locks and the helper can swap + relaunch.

        `_exit` MUST actually terminate the process. The default is sys.exit(0), which is only valid
        on the MAIN thread; a GUI caller running this on a worker thread must pass an `_exit` that
        marshals a real shutdown/exit onto the main thread (sys.exit() in a worker only ends the
        worker, leaving the helper waiting forever).
        """
        if not self.is_supported():
            return False
        if not info or not info.asset_url:
            return False
        # Unique work dir per attempt so concurrent/retried updates can't collide on the same paths.
        work_dir = work_dir or tempfile.mkdtemp(prefix="winwinghaptics_update_")
        os.makedirs(work_dir, exist_ok=True)
        zip_path = os.path.join(work_dir, info.asset_name or "update.zip")
        staging_dir = os.path.join(work_dir, "staged")
        try:
            self.download(info.asset_url, zip_path, on_progress=on_progress)
            build_root = self.stage(zip_path, staging_dir)
            # Guard: only swap if the staged build actually contains our exe. Otherwise a corrupt or
            # wrong .zip would be copied over the install.
            exe_name = os.path.basename(self.exe_path)
            if not build_root or not os.path.exists(os.path.join(build_root, exe_name)):
                shutil.rmtree(work_dir, ignore_errors=True)
                return False
            helper = self._write_helper(build_root, staging_dir,
                                        os.path.join(work_dir, "apply_update.bat"))
            self._launch_helper(helper)
        except Exception:
            return False
        # release file locks so the helper can overwrite the app folder, then relaunch us
        (_exit or (lambda: sys.exit(0)))()
        return True
