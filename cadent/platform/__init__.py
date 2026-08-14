"""`current()` — the one place `sys.platform` picks an implementation.

Everything else asks this factory (or is handed the result through
`CadentApp(platform=...)`, the single DI point). Per-OS modules are
imported here and nowhere else, so `winreg`/`ctypes.windll` (and later
pyobjc) load only where they exist (spec §1.4).
"""

from __future__ import annotations

import sys

from .base import (
    Autostart,
    Capabilities,
    Clipboard,
    DesktopEnv,
    FocusedApp,
    HardwareProbe,
    HotkeyTap,
    KeyboardOutput,
    KeycodeTable,
    Platform,
    SingleInstance,
)

__all__ = [
    "Autostart", "Capabilities", "Clipboard", "DesktopEnv", "FocusedApp",
    "HardwareProbe", "HotkeyTap", "KeyboardOutput", "KeycodeTable", "Platform",
    "SingleInstance", "current",
]

_current: Platform | None = None


def current() -> Platform:
    """This OS's platform, built once per process."""
    global _current
    if _current is None:
        if sys.platform == "win32":
            from . import win32 as impl
        elif sys.platform == "darwin":
            from . import darwin as impl
        else:
            from . import fallback as impl
        _current = impl.create()
    return _current
