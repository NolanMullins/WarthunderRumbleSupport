"""Process / thread scheduling hints so the haptics app stays out of War Thunder's way.

The goal: never steal CPU the game wants. The right mechanism on modern Windows is NOT CPU
affinity ("pin to a core") -- a hardcoded mask risks contending with a game thread on that exact
core and ignores per-machine topology (AMD preferred-core, Intel hybrid P/E cores). Instead we
use the priority-preemptive scheduler + the power-throttling QoS API:

  * BELOW_NORMAL thread priority on the detection loop. The game runs at Normal/Above-Normal, so
    the scheduler always preempts our thread when the game is runnable. Our work is not
    OS-latency-critical (a ~45 ms poll budget hides any scheduling delay), so this costs us
    nothing perceptible while guaranteeing we yield.

  * EcoQoS (PROCESS_POWER_THROTTLING_EXECUTION_SPEED) on the process. This is the modern
    "I am a background app" signal. On Intel 12th-gen+ hybrid CPUs the Thread Director then
    parks us on EFFICIENCY (E) cores, leaving the performance (P) cores entirely for the game --
    exactly what manual affinity tries to achieve, but topology-correct and automatic.

All calls are best-effort: any failure (older Windows, non-Windows, denied) is swallowed and
reported via the return value, never raised, so scheduling hints can never break the app.
"""
import ctypes
from ctypes import wintypes

THREAD_PRIORITY_BELOW_NORMAL = -1
THREAD_PRIORITY_NORMAL = 0

# SetProcessInformation class + power-throttling flags (Win10 1709+ / 11)
_ProcessPowerThrottling = 4
_PROCESS_POWER_THROTTLING_CURRENT_VERSION = 1
_PROCESS_POWER_THROTTLING_EXECUTION_SPEED = 0x1


class _PROCESS_POWER_THROTTLING_STATE(ctypes.Structure):
    _fields_ = [("Version", wintypes.ULONG),
                ("ControlMask", wintypes.ULONG),
                ("StateMask", wintypes.ULONG)]


def _kernel32():
    k = ctypes.WinDLL("kernel32.dll")
    # Declare signatures so 64-bit HANDLEs are marshalled as pointers (not truncated to int).
    k.GetCurrentThread.restype = wintypes.HANDLE
    k.GetCurrentProcess.restype = wintypes.HANDLE
    k.SetThreadPriority.argtypes = [wintypes.HANDLE, ctypes.c_int]
    k.SetThreadPriority.restype = wintypes.BOOL
    k.GetThreadPriority.argtypes = [wintypes.HANDLE]
    k.GetThreadPriority.restype = ctypes.c_int
    return k


def lower_current_thread(below=True):
    """Set the CALLING thread's priority to BELOW_NORMAL (or back to NORMAL). Call this at the
    top of the detection worker so only that loop is de-prioritised, leaving the UI thread snappy.
    Returns True on success."""
    try:
        k = _kernel32()
        h = k.GetCurrentThread()
        prio = THREAD_PRIORITY_BELOW_NORMAL if below else THREAD_PRIORITY_NORMAL
        return bool(k.SetThreadPriority(h, prio))
    except Exception:
        return False


def current_thread_priority():
    """Return the calling thread's current priority value (for verification), or None."""
    try:
        k = _kernel32()
        return int(k.GetThreadPriority(k.GetCurrentThread()))
    except Exception:
        return None


def set_process_eco_qos(enable=True):
    """Apply (or clear) EcoQoS execution-speed throttling on the current process. On hybrid CPUs
    this biases the whole app onto efficiency cores. Best-effort; returns True on success and
    False on older Windows / non-Windows / denial (the app simply runs without the hint)."""
    try:
        k = _kernel32()
        st = _PROCESS_POWER_THROTTLING_STATE()
        st.Version = _PROCESS_POWER_THROTTLING_CURRENT_VERSION
        st.ControlMask = _PROCESS_POWER_THROTTLING_EXECUTION_SPEED
        st.StateMask = _PROCESS_POWER_THROTTLING_EXECUTION_SPEED if enable else 0
        k.SetProcessInformation.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                            ctypes.c_void_p, wintypes.DWORD]
        k.SetProcessInformation.restype = wintypes.BOOL
        ok = k.SetProcessInformation(k.GetCurrentProcess(), _ProcessPowerThrottling,
                                     ctypes.byref(st), ctypes.sizeof(st))
        return bool(ok)
    except Exception:
        return False


def apply_low_impact():
    """Convenience: apply the process-wide EcoQoS hint at app start. Returns True if it took."""
    return set_process_eco_qos(True)
