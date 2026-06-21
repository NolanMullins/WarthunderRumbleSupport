"""Lightweight Windows process detection (stdlib only, no extra dependency).

Used to tell whether War Thunder is actually OPEN so the HUD scanner only runs when it can
possibly see the game. HUD detection is pure screen capture and does NOT require the game's
localhost web server (some players disable it), so process presence -- not the telemetry
port -- is the authoritative "the game is running" signal.

Enumeration uses the Win32 Toolhelp snapshot via ctypes: no pip package, works inside the
frozen PyInstaller exe. Every call is wrapped so a failure simply reports "not running"
rather than ever breaking the worker loop.
"""
import ctypes
from ctypes import wintypes

# War Thunder's client executable. Lower-cased for case-insensitive comparison.
WARTHUNDER_PROCESSES = ("aces.exe",)

TH32CS_SNAPPROCESS = 0x00000002


class _PROCESSENTRY32(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", ctypes.c_char * 260),
    ]


def iter_process_names():
    """Yield the lower-cased exe name of every running process. Empty on any failure or on
    non-Windows. Kept as its own function so tests can inject a fake enumerator."""
    try:
        kernel32 = ctypes.windll.kernel32          # AttributeError off-Windows -> handled
    except Exception:
        return
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap == -1 or snap == 0:
        return
    try:
        entry = _PROCESSENTRY32()
        entry.dwSize = ctypes.sizeof(_PROCESSENTRY32)
        if not kernel32.Process32First(snap, ctypes.byref(entry)):
            return
        while True:
            try:
                name = entry.szExeFile.decode("utf-8", "ignore").lower()
            except Exception:
                name = ""
            if name:
                yield name
            if not kernel32.Process32Next(snap, ctypes.byref(entry)):
                break
    finally:
        try:
            kernel32.CloseHandle(snap)
        except Exception:
            pass


def is_running(targets=WARTHUNDER_PROCESSES, _enum=None):
    """True if any process whose exe name is in `targets` is currently running.

    `targets` are matched case-insensitively. `_enum` lets tests pass a fake name iterator;
    in production it defaults to the live Toolhelp enumeration. Never raises."""
    wanted = {t.lower() for t in targets}
    try:
        names = _enum() if _enum is not None else iter_process_names()
        for name in names:
            if name and name.lower() in wanted:
                return True
    except Exception:
        return False
    return False


def is_warthunder_running(_enum=None):
    """True if the War Thunder client process is running."""
    return is_running(WARTHUNDER_PROCESSES, _enum=_enum)
