"""The Windows platform: every Win32-touching primitive, behind the seam.

Only the `current()` factory imports this module, and only on Windows —
`winreg` and the LP64-vs-LLP64 struct trap (#129) never load anywhere else.
Moved here, not re-exported: `cadent.winput`, `cadent.autostart`, the
clipboard privates in `inject.py`, the foreground probes, the pynput wiring
in `hotkey.py`, the hardware probes and the named mutex all died as import
paths (ADR 0005).
"""

from __future__ import annotations

import ctypes
import logging
import sys
import threading
import winreg
from collections.abc import Callable
from ctypes import wintypes
from pathlib import Path

from .. import config
from .base import Capabilities, Platform
from .keycodes import WIN32_KEYCODES

log = logging.getLogger(__name__)

# ---- SendInput plumbing (formerly cadent/winput.py) ----------------------

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
VK_MASK = 0xFF  # unassigned VK: breaks the Win-down→Win-up sequence, nothing else

MODIFIER_VKS = (0x10, 0x11, 0x12, 0x5B, 0x5C)  # shift, ctrl, alt, lwin, rwin

_ULONG_PTR = ctypes.POINTER(ctypes.c_ulong)


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", _ULONG_PTR)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", _ULONG_PTR)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD),
                ("wParamH", wintypes.WORD)]


class INPUT(ctypes.Structure):
    # The union MUST carry all three arms: SendInput validates cbSize against
    # the full struct (40 bytes on x64) and rejects every call otherwise.
    class _U(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


def _send(events: list[tuple[int, int, int]]) -> int:
    """events: (wVk, wScan, dwFlags) triples. Returns the number accepted."""
    inputs = []
    for wvk, wscan, flags in events:
        inp = INPUT(type=INPUT_KEYBOARD)
        inp.ki = KEYBDINPUT(wvk, wscan, flags, 0, None)
        inputs.append(inp)
    arr = (INPUT * len(inputs))(*inputs)
    sent = ctypes.windll.user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))
    if sent != len(inputs):
        log.debug("SendInput short send: %d/%d, GetLastError=%d",
                  sent, len(inputs), ctypes.windll.kernel32.GetLastError())
    return sent


class Win32Keyboard:
    def send_text_units(self, units: list[int]) -> bool:
        """KEYEVENTF_UNICODE down/up per UTF-16 unit. False on a short send."""
        events = []
        for unit in units:
            events.append((0, unit, KEYEVENTF_UNICODE))
            events.append((0, unit, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP))
        return _send(events) == len(events)

    def send_chord(self, keys: list[int]) -> None:
        events = [(vk, 0, 0) for vk in keys]
        events += [(vk, 0, KEYEVENTF_KEYUP) for vk in reversed(keys)]
        _send(events)

    def send_mask_key(self) -> None:
        """Dummy VK down/up — the MenuMaskKey technique."""
        _send([(VK_MASK, 0, 0), (VK_MASK, 0, KEYEVENTF_KEYUP)])

    def modifiers_down(self) -> bool:
        return any(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000
                   for vk in MODIFIER_VKS)


# ---- clipboard (formerly inject.py's privates) -------------------------------

CF_UNICODETEXT = 13
_EXCLUDE_FORMAT_NAME = "ExcludeClipboardContentFromMonitorProcessing"


def _open_clipboard_retrying(attempts: int = 10, delay_s: float = 0.05) -> None:
    import time

    for _ in range(attempts):
        if ctypes.windll.user32.OpenClipboard(None):
            return
        time.sleep(delay_s)
    raise OSError("could not open clipboard")


class Win32Clipboard:
    def get_text(self) -> str | None:
        user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
        # Without this, ctypes truncates the 64-bit HANDLE to c_int and GlobalLock
        # dereferences garbage (access violation seen live on x64).
        user32.GetClipboardData.restype = ctypes.c_void_p
        _open_clipboard_retrying()
        try:
            if not user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
                return None
            handle = user32.GetClipboardData(CF_UNICODETEXT)
            if not handle:
                return None
            kernel32.GlobalLock.restype = ctypes.c_void_p
            ptr = kernel32.GlobalLock(ctypes.c_void_p(handle))
            if not ptr:
                return None
            try:
                return ctypes.wstring_at(ptr)
            finally:
                kernel32.GlobalUnlock(ctypes.c_void_p(handle))
        finally:
            user32.CloseClipboard()

    def set_text(self, text: str, exclude_from_history: bool) -> None:
        user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
        GMEM_MOVEABLE = 0x0002
        kernel32.GlobalAlloc.restype = ctypes.c_void_p
        kernel32.GlobalLock.restype = ctypes.c_void_p

        _open_clipboard_retrying()
        try:
            user32.EmptyClipboard()

            data = text.encode("utf-16-le") + b"\x00\x00"
            handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
            ptr = kernel32.GlobalLock(ctypes.c_void_p(handle))
            ctypes.memmove(ptr, data, len(data))
            kernel32.GlobalUnlock(ctypes.c_void_p(handle))
            user32.SetClipboardData(CF_UNICODETEXT, ctypes.c_void_p(handle))

            if exclude_from_history:
                fmt = user32.RegisterClipboardFormatW(_EXCLUDE_FORMAT_NAME)
                if fmt:
                    zero = (0).to_bytes(4, "little")
                    h2 = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(zero))
                    p2 = kernel32.GlobalLock(ctypes.c_void_p(h2))
                    ctypes.memmove(p2, zero, len(zero))
                    kernel32.GlobalUnlock(ctypes.c_void_p(h2))
                    user32.SetClipboardData(fmt, ctypes.c_void_p(h2))
        finally:
            user32.CloseClipboard()

    def sequence_number(self) -> int:
        return ctypes.windll.user32.GetClipboardSequenceNumber()


# ---- focused app (formerly inject.py + overlay.py probes) --------------------

class Win32FocusedApp:
    def name(self) -> str:
        """Executable name of the foreground window's process."""
        try:
            import psutil  # optional; degrade gracefully if absent

            hwnd = ctypes.windll.user32.GetForegroundWindow()
            pid = wintypes.DWORD()
            ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            return psutil.Process(pid.value).name()
        except Exception:
            return "unknown"

    def window_rect(self) -> tuple[int, int, int, int] | None:
        try:
            hwnd = ctypes.windll.user32.GetForegroundWindow()
            if not hwnd:
                return None
            rect = wintypes.RECT()
            if not ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                return None
            return (rect.left, rect.top, rect.right, rect.bottom)
        except Exception:
            return None

    def injection_blocked(self) -> str | None:
        """Elevated foreground under UIPI: both SendInput and a synthetic
        paste chord are silently swallowed, so failure would look like
        success. Never block injection on a failed pre-flight."""
        try:
            if ctypes.windll.shell32.IsUserAnAdmin():
                return None  # we're elevated too; UIPI won't block us

            user32, kernel32, advapi32 = (ctypes.windll.user32, ctypes.windll.kernel32,
                                          ctypes.windll.advapi32)
            hwnd = user32.GetForegroundWindow()
            if not hwnd:
                return None
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION,
                                          False, pid.value)
            blocked = "focused window is elevated (admin)"
            if not handle:
                ERROR_ACCESS_DENIED = 5
                return blocked if kernel32.GetLastError() == ERROR_ACCESS_DENIED else None
            try:
                TOKEN_QUERY = 0x0008
                token = wintypes.HANDLE()
                if not advapi32.OpenProcessToken(handle, TOKEN_QUERY, ctypes.byref(token)):
                    return blocked  # can see the process but not its token: elevated
                try:
                    TokenElevation = 20
                    elevation = wintypes.DWORD()
                    size = wintypes.DWORD()
                    ok = advapi32.GetTokenInformation(
                        token, TokenElevation, ctypes.byref(elevation),
                        ctypes.sizeof(elevation), ctypes.byref(size))
                    return blocked if bool(ok) and elevation.value != 0 else None
                finally:
                    kernel32.CloseHandle(token)
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            return None

    def permission_granted(self) -> bool:
        return True     # no permission preflight on Windows

    def running_apps(self) -> list[tuple[str, str]]:
        return []       # `app_picker` is False: the pane lists process names

    def display_name(self, identity: str) -> str | None:
        return None     # executable names *are* the display names here


# ---- hotkey tap (formerly hotkey.py's pynput wiring) --------------------------

_WM_KEYDOWN = 0x0100
_WM_SYSKEYDOWN = 0x0104
_LLKHF_INJECTED = 0x10


class Win32HotkeyTap:
    """WH_KEYBOARD_LL via pynput, pass-through. The filter only forwards —
    LL hooks that block past the OS timeout get silently removed."""

    def __init__(self) -> None:
        self._listener = None

    def start(self, on_event, chords=()) -> None:
        # `chords` is ignored: a low-level hook sees the whole keyboard.
        if self._listener is not None:
            return
        from pynput import keyboard

        def win32_event_filter(msg: int, data) -> bool:
            on_event(data.vkCode, msg in (_WM_KEYDOWN, _WM_SYSKEYDOWN),
                     bool(data.flags & _LLKHF_INJECTED))
            return True  # pass every event through; never suppress

        self._listener = keyboard.Listener(win32_event_filter=win32_event_filter)
        self._listener.start()

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None

    def bound_shortcuts(self):
        return None    # the hook sees the whole keyboard; nobody else binds

    def available(self) -> bool:
        return True


# ---- hardware probes (formerly hardware.py + gpu_pack.py) --------------------

class Win32Hardware:
    def cuda_total_memory(self) -> float | None:
        """Total VRAM in GB via the CUDA driver API, or None.

        Honest caveat: `cuDeviceTotalMem` is *total* VRAM, not free. Acceptable
        because stt.py's encode probe already falls back to CPU int8 at load
        time, so an over-optimistic suggestion self-corrects.
        """
        try:
            cuda = ctypes.WinDLL("nvcuda.dll")
            if cuda.cuInit(0) != 0:
                return None
            device = ctypes.c_int()
            if cuda.cuDeviceGet(ctypes.byref(device), 0) != 0:
                return None
            total = ctypes.c_size_t()
            get_mem = getattr(cuda, "cuDeviceTotalMem_v2", None) or cuda.cuDeviceTotalMem
            if get_mem(ctypes.byref(total), device) != 0:
                return None
            return total.value / (1024 ** 3)
        except Exception:
            log.debug("CUDA driver probe failed", exc_info=True)
            return None

    def dx12_gpu_present(self) -> bool:
        """Can a Direct3D 12 device be created? The gate for the Parakeet
        engine — DirectML is happy on AMD and Intel GPUs too (#72). A null
        riid and null out-pointer ask whether D3D12 *would* create a device;
        S_FALSE means yes, and leaves nothing to release."""
        try:
            create = ctypes.WinDLL("d3d12.dll").D3D12CreateDevice
            create.restype = ctypes.c_long
            create.argtypes = (ctypes.c_void_p, ctypes.c_uint,
                               ctypes.c_void_p, ctypes.c_void_p)
            # 0xb000 is D3D_FEATURE_LEVEL_11_0, the floor DirectML needs.
            return create(None, 0xb000, None, None) in (0, 1)   # S_OK | S_FALSE
        except Exception:
            log.debug("Direct3D 12 probe failed", exc_info=True)
            return False

    def processor_name(self) -> str:
        try:
            key = r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key) as handle:
                return str(winreg.QueryValueEx(handle, "ProcessorNameString")[0]).strip()
        except Exception:
            log.debug("ProcessorNameString read failed", exc_info=True)
            return ""

    def nvidia_driver_present(self) -> bool:
        """nvcuda.dll ships with every NVIDIA driver — loadable means GPU
        hardware."""
        try:
            ctypes.WinDLL("nvcuda.dll")
            return True
        except OSError:
            return False

    def metal_gpu_present(self) -> bool:
        return False


# ---- autostart (formerly cadent/autostart.py) -----------------------------

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "Cadent"


class Win32Autostart:
    """Run-at-login: the Cadent value under HKCU's Run key (M3 charter 5).

    `key_path` is parameterized so tests write to a throwaway subkey and never
    touch the real Run key.
    """

    def __init__(self, key_path: str = RUN_KEY) -> None:
        self._key_path = key_path

    def run_command(self) -> str:
        """What the OS runs at login: the installed exe when packaged, else the
        dev entrypoint (pythonw so no console window flashes up)."""
        if getattr(sys, "frozen", False):
            return f'"{sys.executable}"'
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        interpreter = pythonw if pythonw.exists() else Path(sys.executable)
        return f'"{interpreter}" -m cadent'

    def set_enabled(self, enabled: bool, command: str | None = None) -> None:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, self._key_path, 0,
                                winreg.KEY_SET_VALUE) as key:
            if enabled:
                winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ,
                                  command or self.run_command())
            else:
                try:
                    winreg.DeleteValue(key, VALUE_NAME)
                except FileNotFoundError:
                    pass

    def is_enabled(self) -> bool:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self._key_path) as key:
                winreg.QueryValueEx(key, VALUE_NAME)
            return True
        except FileNotFoundError:
            return False


# ---- single instance (formerly app.py's named mutex) --------------------------

_MUTEX_NAME = "Local\\Cadent-single-instance"


class Win32SingleInstance:
    def acquire(self) -> bool:
        """Named-mutex guard; the OS releases it on process death."""
        ERROR_ALREADY_EXISTS = 183
        ctypes.windll.kernel32.CreateMutexW(None, False, _MUTEX_NAME)
        return ctypes.windll.kernel32.GetLastError() != ERROR_ALREADY_EXISTS


# ---- desktop environment ------------------------------------------------------

# The tray-ink watcher's Win32 surface, declared once at import.
#
# ctypes defaults every foreign return to `c_int`, and a `HANDLE` is
# pointer-sized: taking `CreateEventW`'s result unprototyped truncates it on
# 64-bit Windows. That is the same LP64-vs-LLP64 trap as #129, one layer down,
# and it fails silently — handle values are usually small enough to survive the
# cut, right up until one isn't.
#
# Loaded privately rather than through the cached `ctypes.windll`, so setting
# these prototypes cannot reach any other user of kernel32 in the process
# (`ctypes.WinDLL("d3d12.dll")` above does the same for the same reason).
_kernel32 = ctypes.WinDLL("kernel32")
_advapi32 = ctypes.WinDLL("advapi32")

_kernel32.CreateEventW.argtypes = (wintypes.LPVOID, wintypes.BOOL,
                                   wintypes.BOOL, wintypes.LPCWSTR)
_kernel32.CreateEventW.restype = wintypes.HANDLE
_kernel32.ResetEvent.argtypes = (wintypes.HANDLE,)
_kernel32.ResetEvent.restype = wintypes.BOOL
_kernel32.SetEvent.argtypes = (wintypes.HANDLE,)
_kernel32.SetEvent.restype = wintypes.BOOL
_kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
_kernel32.CloseHandle.restype = wintypes.BOOL
_kernel32.WaitForMultipleObjects.argtypes = (wintypes.DWORD,
                                             ctypes.POINTER(wintypes.HANDLE),
                                             wintypes.BOOL, wintypes.DWORD)
_kernel32.WaitForMultipleObjects.restype = wintypes.DWORD
_advapi32.RegNotifyChangeKeyValue.argtypes = (wintypes.HKEY, wintypes.BOOL,
                                              wintypes.DWORD, wintypes.HANDLE,
                                              wintypes.BOOL)
_advapi32.RegNotifyChangeKeyValue.restype = wintypes.LONG


class Win32Desktop:
    # The taskbar's colour mode. `AppsUseLightTheme` sits beside it and is the
    # one Qt reports through colorScheme(); the two disagree often enough that
    # reading the wrong one puts a black icon on a black taskbar.
    _PERSONALIZE = (r"SOFTWARE\Microsoft\Windows\CurrentVersion\Themes"
                    r"\Personalize")

    def __init__(self) -> None:
        self._ink_thread: threading.Thread | None = None
        self._ink_stop: int | None = None

    def open_path(self, path: Path) -> None:
        try:
            import os

            os.startfile(str(path))     # noqa: S606 - the user asked for it
        except Exception:
            pass

    def request_permission(self) -> None:
        pass    # no permission preflight, nothing to ask for

    def text_scale_factor(self) -> float:
        """Windows' accessibility **Text size** setting (100-225%) as a
        multiplier. Honoured by no Qt mechanism at all, so the registry is
        the only route; an absent value means 100%."""
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"SOFTWARE\Microsoft\Accessibility") as handle:
                percent = int(winreg.QueryValueEx(handle, "TextScaleFactor")[0])
        except Exception:
            return 1.0
        return max(1.0, min(percent / 100, 2.25))

    def high_contrast(self) -> bool:
        """SPI_GETHIGHCONTRAST fallback for Qt below 6.10, which the >=6.8
        floor permits."""
        try:
            class HIGHCONTRAST(ctypes.Structure):
                _fields_ = [("cbSize", wintypes.UINT), ("dwFlags", wintypes.DWORD),
                            ("lpszDefaultScheme", wintypes.LPWSTR)]

            SPI_GETHIGHCONTRAST = 0x0042
            HCF_HIGHCONTRASTON = 0x00000001
            info = HIGHCONTRAST()
            info.cbSize = ctypes.sizeof(HIGHCONTRAST)
            ok = ctypes.windll.user32.SystemParametersInfoW(
                SPI_GETHIGHCONTRAST, info.cbSize, ctypes.byref(info), 0)
            return bool(ok) and bool(info.dwFlags & HCF_HIGHCONTRASTON)
        except Exception:
            log.debug("SPI_GETHIGHCONTRAST probe failed", exc_info=True)
            return False

    def animations_enabled(self) -> bool:
        """Windows' "Show animations" setting (§4.6): when off, fades and
        tweens snap — but the meter keeps animating, because it is
        information, not decoration. That policy stays portable in overlay
        code; this is only the fact."""
        try:
            SPI_GETCLIENTAREAANIMATION = 0x1042
            enabled = ctypes.c_int(1)
            ctypes.windll.user32.SystemParametersInfoW(
                SPI_GETCLIENTAREAANIMATION, 0, ctypes.byref(enabled), 0)
            return bool(enabled.value)
        except Exception:
            return True

    def tray_ink(self) -> str:
        """Black on a light taskbar, white on a dark one — and whatever the
        contrast theme draws text with when one is on."""
        if self.high_contrast():
            # A contrast theme paints the taskbar from its own palette, so
            # neither black nor white is safe. COLOR_WINDOWTEXT is what that
            # theme writes text in, and the tray sits among text.
            try:
                COLOR_WINDOWTEXT = 8
                # COLORREF is 0x00BBGGRR — the red channel is the low byte.
                bgr = int(ctypes.windll.user32.GetSysColor(COLOR_WINDOWTEXT))
                r, g, b = bgr & 0xFF, (bgr >> 8) & 0xFF, (bgr >> 16) & 0xFF
                return f"#{r:02x}{g:02x}{b:02x}"
            except Exception:
                log.debug("GetSysColor(COLOR_WINDOWTEXT) failed", exc_info=True)
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                self._PERSONALIZE) as handle:
                light = int(winreg.QueryValueEx(
                    handle, "SystemUsesLightTheme")[0])
        except Exception:
            # Absent on a fresh Windows 11, whose taskbar is dark. Guessing
            # light here would paint black on black.
            light = 0
        return "#000000" if light else "#ffffff"

    def watch_tray_ink(self, on_change: Callable[[], None]) -> None:
        """Watch the Personalize key on a thread of our own.

        `RegNotifyChangeKeyValue` rather than a `WM_SETTINGCHANGE` filter: the
        message route needs a window and a Qt event loop, and this package
        stays Qt-free (ADR 0005). The callback lands on this thread, exactly
        as `HotkeyTap.start`'s does."""
        # `is_alive`, not `is not None`: a worker that exited early — no
        # Personalize key, a refused registration — must not wedge the door
        # shut against every later attempt to start one.
        if self._ink_thread is not None and self._ink_thread.is_alive():
            return
        stop = _kernel32.CreateEventW(None, True, False, None)
        if not stop:
            log.debug("CreateEventW failed (%s); the tray ink will not follow "
                      "the taskbar", ctypes.windll.kernel32.GetLastError())
            return
        self._ink_stop = stop
        # The handle is passed in as well as stored: `stop_watching_tray_ink`
        # clears the attribute while the worker is still running, and the
        # worker must not be reading it at that moment.
        self._ink_thread = threading.Thread(
            target=self._watch_ink, args=(on_change, stop), daemon=True,
            name="cadent-tray-ink")
        self._ink_thread.start()

    def _watch_ink(self, on_change: Callable[[], None], stop: int) -> None:
        REG_NOTIFY_CHANGE_LAST_SET = 0x00000004
        WAIT_OBJECT_0 = 0
        INFINITE = 0xFFFFFFFF
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, self._PERSONALIZE)
        except OSError:
            log.debug("no Personalize key to watch; the tray ink will not "
                      "follow the taskbar", exc_info=True)
            return
        with key:
            event = _kernel32.CreateEventW(None, True, False, None)
            if not event:
                log.debug("CreateEventW failed (%s)",
                          ctypes.windll.kernel32.GetLastError())
                return
            # Both handles are locals for the life of the wait. The stop event
            # shares it so a quit never blocks on a theme change that may not
            # come for hours.
            handles = (wintypes.HANDLE * 2)(wintypes.HANDLE(event),
                                            wintypes.HANDLE(stop))
            try:
                while True:
                    _kernel32.ResetEvent(event)
                    # Re-armed every pass: one registration yields one
                    # notification and then forgets us.
                    status = _advapi32.RegNotifyChangeKeyValue(
                        wintypes.HKEY(int(key)), False,
                        REG_NOTIFY_CHANGE_LAST_SET, wintypes.HANDLE(event),
                        True)
                    if status != 0:
                        log.debug("RegNotifyChangeKeyValue failed (%s)", status)
                        return
                    if _kernel32.WaitForMultipleObjects(
                            2, handles, False, INFINITE) != WAIT_OBJECT_0:
                        return          # stop signalled, or the wait failed
                    on_change()
            except Exception:
                log.debug("tray ink watch ended", exc_info=True)
            finally:
                _kernel32.CloseHandle(event)

    def stop_watching_tray_ink(self) -> None:
        thread, self._ink_thread = self._ink_thread, None
        stop, self._ink_stop = self._ink_stop, None
        if not stop:
            return
        _kernel32.SetEvent(stop)
        if thread is not None:
            thread.join(timeout=1.0)
            if thread.is_alive():
                # The worker still holds this handle in its wait array, and
                # Windows recycles handle values — closing it now could leave
                # that wait pointing at somebody else's object. One leaked
                # event on a shutdown that already went wrong is the cheaper
                # mistake.
                log.debug("tray ink watcher did not stop; leaving its stop "
                          "event open")
                return
        _kernel32.CloseHandle(stop)


# ---- the assembled platform ---------------------------------------------------

CAPABILITIES = Capabilities(
    keycode_table=WIN32_KEYCODES,
    default_injection_strategy="type",
    injection_rungs=("type", "paste"),
    paste_chord="ctrl+v",
    default_overrides=tuple(config._default_overrides()),
    default_override_reasons=config.DEFAULT_OVERRIDE_REASONS,
    auto_learn_overrides=True,
    stt_runtimes=config.STT_RUNTIMES,
    gpu_only_engines=frozenset({"parakeet"}),
    show_runtime_combo=True,
    gpu_pack_available=True,
    permission=None,
    autostart_label="Start with Windows",
    app_identity_placeholder="app.exe",
    app_picker=False,
    mic_permission_hint=None,
    tray_click_toggles_pause=True,
    # "option" too: hand-edited configs cross OSes with their users, and a
    # Mac-authored "<option>" must read as the key it lands on here.
    modifier_captions={"ctrl": "Ctrl", "shift": "Shift", "alt": "Alt",
                       "option": "Alt", "win": "Win", "cmd": "Win"},
    tray_icon_painted_by_os=False,
    # Windows has two theme settings and Qt's colorScheme reports the *app*
    # one, so a user running a dark taskbar with light apps who picks System
    # correctly gets a light Cadent. Without this line that reads as a bug.
    theme_subtitle="Follows your Windows app colour mode",
    high_contrast_reason=("Windows is using a contrast theme, so Cadent is "
                          "following your system colours"),
    overlay="windowed",
    per_app_overrides=True,
    support_tier=None,
    support_tier_summary=None,
    default_combo="<ctrl>+<cmd>",
    default_cleanup_combo="<ctrl>+<shift>+<alt>",
)


def create() -> Platform:
    return Platform(
        capabilities=CAPABILITIES,
        keyboard=Win32Keyboard(),
        clipboard=Win32Clipboard(),
        focused_app=Win32FocusedApp(),
        hotkey_tap=Win32HotkeyTap(),
        hardware=Win32Hardware(),
        autostart=Win32Autostart(),
        single_instance=Win32SingleInstance(),
        desktop=Win32Desktop(),
    )
