"""Low-level Windows HID plumbing (ctypes over hid.dll / setupapi.dll / kernel32.dll).

Pure device-transport layer: find a HID interface by VID + joystick usage, open/close a
handle, and write raw output reports. No effect/vibration semantics live here -- that is the
device class's job (winwing.py). Kept as zero-dependency stdlib ctypes so the app needs
nothing installed for HID.
"""
import ctypes
from ctypes import wintypes

# --- DLLs (built into Windows) ---
hid = ctypes.WinDLL("hid")
setupapi = ctypes.WinDLL("setupapi")
kernel32 = ctypes.WinDLL("kernel32")

# --- constants ---
DIGCF_PRESENT = 0x02
DIGCF_DEVICEINTERFACE = 0x10
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 1
FILE_SHARE_WRITE = 2
OPEN_EXISTING = 3
INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value
HIDP_STATUS_SUCCESS = 0x00110000


class GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort),
                ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]


class SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("InterfaceClassGuid", GUID),
                ("Flags", wintypes.DWORD), ("Reserved", ctypes.POINTER(ctypes.c_ulong))]


class HIDD_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Size", ctypes.c_ulong), ("VendorID", ctypes.c_ushort),
                ("ProductID", ctypes.c_ushort), ("VersionNumber", ctypes.c_ushort)]


class HIDP_CAPS(ctypes.Structure):
    _fields_ = [("Usage", ctypes.c_ushort), ("UsagePage", ctypes.c_ushort),
                ("InputReportByteLength", ctypes.c_ushort),
                ("OutputReportByteLength", ctypes.c_ushort),
                ("FeatureReportByteLength", ctypes.c_ushort),
                ("Reserved", ctypes.c_ushort * 17),
                ("NumberLinkCollectionNodes", ctypes.c_ushort),
                ("NumberInputButtonCaps", ctypes.c_ushort),
                ("NumberInputValueCaps", ctypes.c_ushort),
                ("NumberInputDataIndices", ctypes.c_ushort),
                ("NumberOutputButtonCaps", ctypes.c_ushort),
                ("NumberOutputValueCaps", ctypes.c_ushort),
                ("NumberOutputDataIndices", ctypes.c_ushort),
                ("NumberFeatureButtonCaps", ctypes.c_ushort),
                ("NumberFeatureValueCaps", ctypes.c_ushort),
                ("NumberFeatureDataIndices", ctypes.c_ushort)]


CreateFileW = kernel32.CreateFileW
CreateFileW.restype = wintypes.HANDLE
CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                        ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]

WriteFile = kernel32.WriteFile
WriteFile.restype = wintypes.BOOL
WriteFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                      ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]

ReadFile = kernel32.ReadFile
ReadFile.restype = wintypes.BOOL
ReadFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                     ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]

CloseHandle = kernel32.CloseHandle

# CRITICAL: declare SetupAPI signatures so 64-bit HANDLEs/pointers aren't truncated to
# 32-bit by ctypes' default c_int restype (that bug makes enumeration silently find
# nothing on 64-bit Windows).
setupapi.SetupDiGetClassDevsW.restype = wintypes.HANDLE
setupapi.SetupDiGetClassDevsW.argtypes = [ctypes.POINTER(GUID), wintypes.LPCWSTR,
                                          wintypes.HANDLE, wintypes.DWORD]
setupapi.SetupDiEnumDeviceInterfaces.restype = wintypes.BOOL
setupapi.SetupDiEnumDeviceInterfaces.argtypes = [
    wintypes.HANDLE, ctypes.c_void_p, ctypes.POINTER(GUID), wintypes.DWORD,
    ctypes.POINTER(SP_DEVICE_INTERFACE_DATA)]
setupapi.SetupDiGetDeviceInterfaceDetailW.restype = wintypes.BOOL
setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [
    wintypes.HANDLE, ctypes.POINTER(SP_DEVICE_INTERFACE_DATA), ctypes.c_void_p,
    wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
setupapi.SetupDiDestroyDeviceInfoList.restype = wintypes.BOOL
setupapi.SetupDiDestroyDeviceInfoList.argtypes = [wintypes.HANDLE]
hid.HidD_GetPreparsedData.restype = wintypes.BOOL
hid.HidD_GetPreparsedData.argtypes = [wintypes.HANDLE, ctypes.POINTER(ctypes.c_void_p)]
hid.HidD_FreePreparsedData.argtypes = [ctypes.c_void_p]
hid.HidP_GetCaps.argtypes = [ctypes.c_void_p, ctypes.POINTER(HIDP_CAPS)]
hid.HidD_GetAttributes.restype = wintypes.BOOL
hid.HidD_GetAttributes.argtypes = [wintypes.HANDLE, ctypes.POINTER(HIDD_ATTRIBUTES)]


def find_device_path(vendor_id, usage_page=0x0001, usage=0x0004):
    """Return the device path of the first HID interface matching `vendor_id` and the given
    HID usage (defaults to a joystick: usage page 0x01, usage 0x04), or None."""
    guid = GUID()
    hid.HidD_GetHidGuid(ctypes.byref(guid))
    hdev = setupapi.SetupDiGetClassDevsW(ctypes.byref(guid), None, None,
                                         DIGCF_PRESENT | DIGCF_DEVICEINTERFACE)
    if hdev == INVALID_HANDLE_VALUE or hdev is None:
        return None
    try:
        idx = 0
        while True:
            ifc = SP_DEVICE_INTERFACE_DATA()
            ifc.cbSize = ctypes.sizeof(SP_DEVICE_INTERFACE_DATA)
            if not setupapi.SetupDiEnumDeviceInterfaces(hdev, None, ctypes.byref(guid),
                                                        idx, ctypes.byref(ifc)):
                break
            idx += 1
            req = wintypes.DWORD(0)
            setupapi.SetupDiGetDeviceInterfaceDetailW(hdev, ctypes.byref(ifc), None, 0,
                                                      ctypes.byref(req), None)
            if req.value == 0:
                continue
            buf = ctypes.create_string_buffer(req.value)
            # cbSize of SP_DEVICE_INTERFACE_DETAIL_DATA_W: 8 on 64-bit, 6 on 32-bit
            cb = 8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6
            ctypes.memmove(buf, ctypes.byref(wintypes.DWORD(cb)), 4)
            if not setupapi.SetupDiGetDeviceInterfaceDetailW(hdev, ctypes.byref(ifc), buf,
                                                            req.value, None, None):
                continue
            # path is a wide string starting at offset 4
            path = ctypes.wstring_at(ctypes.addressof(buf) + 4)
            if check_device(path, vendor_id, usage_page, usage):
                return path
    finally:
        setupapi.SetupDiDestroyDeviceInfoList(hdev)
    return None


def check_device(path, vendor_id, usage_page=0x0001, usage=0x0004):
    """Open `path`, verify the VID and the HID usage (page/usage)."""
    h = CreateFileW(path, GENERIC_READ | GENERIC_WRITE,
                    FILE_SHARE_READ | FILE_SHARE_WRITE, None, OPEN_EXISTING, 0, None)
    if h == INVALID_HANDLE_VALUE or not h:
        return False
    ok = False
    try:
        attrs = HIDD_ATTRIBUTES()
        attrs.Size = ctypes.sizeof(HIDD_ATTRIBUTES)
        if hid.HidD_GetAttributes(h, ctypes.byref(attrs)) and attrs.VendorID == vendor_id:
            pp = ctypes.c_void_p()
            if hid.HidD_GetPreparsedData(h, ctypes.byref(pp)):
                caps = HIDP_CAPS()
                if hid.HidP_GetCaps(pp, ctypes.byref(caps)) == HIDP_STATUS_SUCCESS:
                    ok = (caps.UsagePage == usage_page and caps.Usage == usage)
                hid.HidD_FreePreparsedData(pp)
    finally:
        CloseHandle(h)
    return ok


def open_path(path):
    """Open a device path for read+write. Returns a handle or None."""
    h = CreateFileW(path, GENERIC_READ | GENERIC_WRITE,
                    FILE_SHARE_READ | FILE_SHARE_WRITE, None, OPEN_EXISTING, 0, None)
    if h == INVALID_HANDLE_VALUE or not h:
        return None
    return h


def write(handle, data):
    """Write a raw output report. Returns True on success."""
    written = wintypes.DWORD(0)
    buf = ctypes.create_string_buffer(bytes(data), len(data))
    return bool(WriteFile(handle, buf, len(data), ctypes.byref(written), None))


def input_report_length(handle):
    """Return the device's HID InputReportByteLength (bytes per input report), or 0."""
    n = 0
    pp = ctypes.c_void_p()
    if hid.HidD_GetPreparsedData(handle, ctypes.byref(pp)):
        caps = HIDP_CAPS()
        if hid.HidP_GetCaps(pp, ctypes.byref(caps)) == HIDP_STATUS_SUCCESS:
            n = int(caps.InputReportByteLength)
        hid.HidD_FreePreparsedData(pp)
    return n


def read(handle, length):
    """Blocking read of one HID input report (`length` bytes). Returns the bytes actually read
    (may be shorter), or None on error. ReadFile blocks until the device sends a report, so this
    is meant to run on a dedicated thread that simply tracks the latest report."""
    if length <= 0:
        return None
    buf = ctypes.create_string_buffer(length)
    got = wintypes.DWORD(0)
    if not ReadFile(handle, buf, length, ctypes.byref(got), None):
        return None
    return buf.raw[:got.value]


def close(handle):
    try:
        CloseHandle(handle)
    except Exception:
        pass
