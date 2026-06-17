"""Read CPU and GPU temperatures from HWiNFO's shared memory.

On this machine (Ryzen 9950X + X870) HWiNFO is the only tool that reliably reads
the CPU temperature, so the app launches HWiNFO in the background (hidden,
sensors-only) via ``HWiNFOProcess``, reads the temperatures from its shared
memory block (``SharedMemoryReader``), and terminates HWiNFO again on exit.
No readings are available unless HWiNFO is running with "Shared Memory Support"
enabled (configured in tools/hwinfo/HWiNFO64.INI).

The shared-memory format (HWiNFO_SENSORS_SHARED_MEM2) is a packed structure:
header 44 bytes (signature "HWiS", version, revision, 8-byte poll time, then the
sensor- and reading-section offsets/sizes/counts), followed by fixed-size sensor
and reading elements. Each reading's value is a little-endian double at offset
284 within the element. Verified live against this machine.
"""

from __future__ import annotations

import ctypes
import math
import os
import struct
from ctypes import wintypes
from typing import Optional

HWINFO_EXE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "tools", "hwinfo", "HWiNFO64.exe"
)

_SM_NAME = "Global\\HWiNFO_SENS_SM2"
_SIGNATURE = b"HWiS"
_READING_TYPE_TEMPERATURE = 1

# Header field offsets (packed).
_H_SENSOR_OFFSET, _H_SENSOR_SIZE, _H_SENSOR_COUNT = 20, 24, 28
_H_READING_OFFSET, _H_READING_SIZE, _H_READING_COUNT = 32, 36, 40
# Sensor element field offsets.
_S_NAME_ORIG, _S_NAME_USER = 8, 136
# Reading element field offsets.
_R_TYPE, _R_SENSOR_INDEX = 0, 4
_R_LABEL_ORIG, _R_LABEL_USER, _R_VALUE = 12, 140, 284

# ---------------------------------------------------------------- Win32 setup
_FILE_MAP_READ = 0x0004
_SEE_MASK_NOCLOSEPROCESS = 0x00000040
_SW_HIDE = 0
_WAIT_TIMEOUT = 0x00000102
_GENERIC_ALL = 0x10000000
_CREATE_NO_WINDOW = 0x08000000
_STARTF_USESHOWWINDOW = 0x00000001
_HIDDEN_DESKTOP_NAME = "pctemp_monitor"

_k = ctypes.WinDLL("kernel32", use_last_error=True)
_k.OpenFileMappingW.restype = wintypes.HANDLE
_k.OpenFileMappingW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
_k.MapViewOfFile.restype = ctypes.c_void_p
_k.MapViewOfFile.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_size_t]
_k.UnmapViewOfFile.restype = wintypes.BOOL
_k.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
_k.CloseHandle.restype = wintypes.BOOL
_k.CloseHandle.argtypes = [wintypes.HANDLE]
_k.TerminateProcess.restype = wintypes.BOOL
_k.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
_k.WaitForSingleObject.restype = wintypes.DWORD
_k.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
_k.GetProcessId.restype = wintypes.DWORD
_k.GetProcessId.argtypes = [wintypes.HANDLE]

# user32: used to force-hide HWiNFO's windows (SW_HIDE in ShellExecute is only a
# hint that HWiNFO ignores, so we hide its windows explicitly by process id).
_user32 = ctypes.WinDLL("user32", use_last_error=True)
_WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
_user32.EnumWindows.restype = wintypes.BOOL
_user32.EnumWindows.argtypes = [_WNDENUMPROC, wintypes.LPARAM]
_user32.GetWindowThreadProcessId.restype = wintypes.DWORD
_user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
_user32.IsWindowVisible.restype = wintypes.BOOL
_user32.IsWindowVisible.argtypes = [wintypes.HWND]
_user32.ShowWindow.restype = wintypes.BOOL
_user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]

# Hidden-desktop launch: run HWiNFO on a desktop that is never displayed, so its
# window never flashes on the user's desktop.
_user32.CreateDesktopW.restype = wintypes.HANDLE
_user32.CreateDesktopW.argtypes = [
    wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_void_p,
    wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
]
_user32.CloseDesktop.restype = wintypes.BOOL
_user32.CloseDesktop.argtypes = [wintypes.HANDLE]


class _STARTUPINFOW(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD),
        ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD),
        ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD),
        ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD),
        ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.c_void_p),
        ("hStdInput", wintypes.HANDLE),
        ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


class _PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE),
        ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
    ]


_k.CreateProcessW.restype = wintypes.BOOL
_k.CreateProcessW.argtypes = [
    wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p, ctypes.c_void_p,
    wintypes.BOOL, wintypes.DWORD, ctypes.c_void_p, wintypes.LPCWSTR,
    ctypes.POINTER(_STARTUPINFOW), ctypes.POINTER(_PROCESS_INFORMATION),
]


def _cstr(data: bytes, off: int, maxlen: int = 128) -> str:
    raw = data[off:off + maxlen]
    z = raw.find(b"\x00")
    if z >= 0:
        raw = raw[:z]
    return raw.decode("latin-1", "replace")


def _sane(value: Optional[float]) -> Optional[float]:
    """Reject implausible / non-finite temperatures (treat as 'no reading')."""
    if value is None or not math.isfinite(value) or value <= 0 or value > 150:
        return None
    return float(value)


class SharedMemoryReader:
    """Maps the HWiNFO shared-memory block and extracts CPU + GPU temps."""

    def __init__(self) -> None:
        self._handle = None
        self._addr = None

    def available(self) -> bool:
        """True if a valid HWiNFO shared-memory block is currently mapped."""
        if self._addr is not None:
            try:
                if ctypes.string_at(self._addr, 4) == _SIGNATURE:
                    return True
            except OSError:
                pass
        return self._open()

    def _open(self) -> bool:
        self._close()
        h = _k.OpenFileMappingW(_FILE_MAP_READ, False, _SM_NAME)
        if not h:
            return False
        addr = _k.MapViewOfFile(h, _FILE_MAP_READ, 0, 0, 0)
        if not addr:
            _k.CloseHandle(h)
            return False
        if ctypes.string_at(addr, 4) != _SIGNATURE:
            _k.UnmapViewOfFile(addr)
            _k.CloseHandle(h)
            return False
        self._handle, self._addr = h, addr
        return True

    def _close(self) -> None:
        if self._addr is not None:
            _k.UnmapViewOfFile(self._addr)
            self._addr = None
        if self._handle is not None:
            _k.CloseHandle(self._handle)
            self._handle = None

    def close(self) -> None:
        self._close()

    def read(self) -> dict:
        """Return {"cpu": float|None, "gpu": float|None} in degrees Celsius."""
        if not self.available():
            return {"cpu": None, "gpu": None}
        try:
            return self._parse()
        except Exception:
            # The block may have vanished (HWiNFO restarted); drop and retry next time.
            self._close()
            return {"cpu": None, "gpu": None}

    def _parse(self) -> dict:
        addr = self._addr
        header = ctypes.string_at(addr, 44)
        h32 = lambda o: int.from_bytes(header[o:o + 4], "little")
        off_s, sz_s, n_s = h32(_H_SENSOR_OFFSET), h32(_H_SENSOR_SIZE), h32(_H_SENSOR_COUNT)
        off_r, sz_r, n_r = h32(_H_READING_OFFSET), h32(_H_READING_SIZE), h32(_H_READING_COUNT)

        data = ctypes.string_at(addr, off_r + n_r * sz_r)

        sensor_names = []
        for i in range(n_s):
            b = off_s + i * sz_s
            sensor_names.append(_cstr(data, b + _S_NAME_USER) or _cstr(data, b + _S_NAME_ORIG))

        cpu = None
        gpu = None          # first temperature on an NVIDIA GPU sensor (= GPU core)
        gpu_fallback = None  # first temperature on any other GPU sensor
        for i in range(n_r):
            b = off_r + i * sz_r
            if int.from_bytes(data[b + _R_TYPE:b + _R_TYPE + 4], "little") != _READING_TYPE_TEMPERATURE:
                continue
            sidx = int.from_bytes(data[b + _R_SENSOR_INDEX:b + _R_SENSOR_INDEX + 4], "little")
            sname = sensor_names[sidx] if 0 <= sidx < len(sensor_names) else ""
            value = struct.unpack_from("<d", data, b + _R_VALUE)[0]

            # CPU: the Tctl/Tdie reading (token is not localized).
            if cpu is None:
                label = _cstr(data, b + _R_LABEL_USER)
                label_orig = _cstr(data, b + _R_LABEL_ORIG)
                if "Tctl" in label or "Tctl" in label_orig:
                    cpu = value

            # GPU: first temperature on the NVIDIA sensor (vendor name isn't localized).
            su = sname.upper()
            if "NVIDIA" in su or "GEFORCE" in su:
                if gpu is None:
                    gpu = value
            elif gpu_fallback is None and "GPU [" in sname:
                gpu_fallback = value

        return {"cpu": _sane(cpu), "gpu": _sane(gpu if gpu is not None else gpu_fallback)}


class _SHELLEXECUTEINFOW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("fMask", ctypes.c_ulong),
        ("hwnd", wintypes.HWND),
        ("lpVerb", wintypes.LPCWSTR),
        ("lpFile", wintypes.LPCWSTR),
        ("lpParameters", wintypes.LPCWSTR),
        ("lpDirectory", wintypes.LPCWSTR),
        ("nShow", ctypes.c_int),
        ("hInstApp", wintypes.HINSTANCE),
        ("lpIDList", ctypes.c_void_p),
        ("lpClass", wintypes.LPCWSTR),
        ("hkeyClass", wintypes.HKEY),
        ("dwHotKey", wintypes.DWORD),
        ("hIconOrMonitor", wintypes.HANDLE),
        ("hProcess", wintypes.HANDLE),
    ]


_shell32 = ctypes.WinDLL("shell32", use_last_error=True)
_shell32.ShellExecuteExW.restype = wintypes.BOOL
_shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(_SHELLEXECUTEINFOW)]


class HWiNFOProcess:
    """Launches HWiNFO64.exe hidden (elevated) and terminates it on stop.

    Uses ShellExecuteEx so the process inherits our elevation without a second
    UAC prompt (HWiNFO's manifest requires administrator). Keeps the process
    handle so only our own instance is terminated.
    """

    def __init__(self, exe: str = HWINFO_EXE) -> None:
        self.exe = exe
        self._hprocess = None
        self._pid = 0
        self._hdesk = None

    @property
    def exe_exists(self) -> bool:
        return os.path.isfile(self.exe)

    def start(self) -> bool:
        if not self.exe_exists:
            return False
        # Preferred: launch on a hidden desktop so the window never appears at all.
        if self._start_hidden_desktop():
            return True
        # Fallback: visible-desktop launch (brief flash, hidden by hide_windows()).
        sei = _SHELLEXECUTEINFOW()
        sei.cbSize = ctypes.sizeof(sei)
        sei.fMask = _SEE_MASK_NOCLOSEPROCESS
        sei.lpVerb = "open"
        sei.lpFile = self.exe
        sei.lpDirectory = os.path.dirname(self.exe)
        sei.nShow = _SW_HIDE
        if _shell32.ShellExecuteExW(ctypes.byref(sei)) and sei.hProcess:
            self._hprocess = sei.hProcess
            self._pid = _k.GetProcessId(sei.hProcess)
            return True
        return False

    def _start_hidden_desktop(self) -> bool:
        """Launch HWiNFO on a non-visible desktop via CreateProcess.

        HWiNFO is requireAdministrator, so CreateProcess would normally fail with
        ERROR_ELEVATION_REQUIRED; setting __COMPAT_LAYER=RunAsInvoker lets it
        succeed, and because this process is already elevated, HWiNFO inherits
        the elevated token. Its window renders on the hidden desktop, never on
        the user's. Returns False on any failure so start() can fall back.
        """
        try:
            hdesk = _user32.CreateDesktopW(
                _HIDDEN_DESKTOP_NAME, None, None, 0, _GENERIC_ALL, None
            )
            if not hdesk:
                return False
            prev = os.environ.get("__COMPAT_LAYER")
            os.environ["__COMPAT_LAYER"] = "RunAsInvoker"
            try:
                si = _STARTUPINFOW()
                si.cb = ctypes.sizeof(si)
                si.lpDesktop = _HIDDEN_DESKTOP_NAME
                si.dwFlags = _STARTF_USESHOWWINDOW
                si.wShowWindow = _SW_HIDE
                pi = _PROCESS_INFORMATION()
                ok = _k.CreateProcessW(
                    self.exe, None, None, None, False, _CREATE_NO_WINDOW,
                    None, os.path.dirname(self.exe),
                    ctypes.byref(si), ctypes.byref(pi),
                )
            finally:
                if prev is None:
                    os.environ.pop("__COMPAT_LAYER", None)
                else:
                    os.environ["__COMPAT_LAYER"] = prev
            if not ok:
                _user32.CloseDesktop(hdesk)
                return False
            if pi.hThread:
                _k.CloseHandle(pi.hThread)
            self._hprocess = pi.hProcess
            self._pid = pi.dwProcessId
            self._hdesk = hdesk
            return True
        except Exception:
            return False

    def hide_windows(self) -> int:
        """Force-hide any visible top-level windows owned by HWiNFO.

        Call repeatedly just after start() (HWiNFO opens its window a moment
        later) and periodically afterwards to keep it hidden.
        """
        if not self._pid:
            return 0
        pid, hidden = self._pid, 0

        def _enum(hwnd, _lparam):
            nonlocal hidden
            wpid = wintypes.DWORD(0)
            _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(wpid))
            if wpid.value == pid and _user32.IsWindowVisible(hwnd):
                _user32.ShowWindow(hwnd, _SW_HIDE)
                hidden += 1
            return True

        try:
            _user32.EnumWindows(_WNDENUMPROC(_enum), 0)
        except Exception:
            pass
        return hidden

    def is_running(self) -> bool:
        if not self._hprocess:
            return False
        return _k.WaitForSingleObject(self._hprocess, 0) == _WAIT_TIMEOUT

    def stop(self) -> None:
        if self._hprocess:
            try:
                _k.TerminateProcess(self._hprocess, 0)
            finally:
                _k.CloseHandle(self._hprocess)
                self._hprocess = None
                self._pid = 0
        if self._hdesk:
            _user32.CloseDesktop(self._hdesk)
            self._hdesk = None


if __name__ == "__main__":
    # Quick standalone check (requires admin + HWiNFO present):
    #   python sensors.py
    import time

    reader = SharedMemoryReader()
    proc = HWiNFOProcess()
    started = False
    if not reader.available():
        print("starting HWiNFO:", proc.start())
        started = True
        for _ in range(25):
            time.sleep(1.0)
            if reader.available():
                break
    try:
        for _ in range(5):
            print(reader.read())
            time.sleep(1.0)
    finally:
        reader.close()
        if started:
            proc.stop()
