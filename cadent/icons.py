"""The Cadent mark: one silhouette, three colours, six sizes (spec §2).

One mic-derived mark is the app's entire visual identity — the tray's Ready
state, the exe, the installer, and every window's title-bar icon. Paused and
Needs-attention are recolourings of it; the geometry never changes.

Loaded via `QIcon.addFile()` on the pre-rasterised PNG set so Windows picks
the exact size it asks for (16 @100% DPI, 20 @125%, 24 @150%, 32 @200%) and
**never scales**. That also avoids shipping Qt's SVG image-format plugin — a
classic PyInstaller silent failure where the icon works in dev and the built
exe shows a blank.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtGui import QIcon

# Availability only (spec §3.1). Whether cleanup is on is deliberately absent: it
# lives in the tooltip and the menu checkbox, because a 16px icon Windows
# frequently collapses into the overflow flyout is the wrong surface for a
# mode you check *while dictating*.
STATES = ("ready", "paused", "attention")
SIZES = (16, 20, 24, 32, 48, 256)

_cache: dict[tuple[str, bool], QIcon] = {}


def icons_dir() -> Path:
    """Where the rasterised mark lives, frozen or from a checkout."""
    bundled = getattr(sys, "_MEIPASS", None)
    if bundled:
        return Path(bundled) / "icons"
    return Path(__file__).resolve().parent.parent / "packaging" / "icons"


def png_path(state: str, size: int, template: bool = False) -> Path:
    suffix = "-template" if template else ""
    return icons_dir() / "png" / f"mark-{state}{suffix}-{size}.png"


def ico_path() -> Path:
    return icons_dir() / "cadent.ico"


def _mark(state: str, template: bool) -> QIcon:
    if state not in STATES:
        state = "ready"
    key = (state, template)
    if key not in _cache:
        icon = QIcon()
        for size in SIZES:
            path = png_path(state, size, template)
            if path.exists():
                icon.addFile(str(path))
        # A mask lets the OS recolour the mark to the menu bar it sits in;
        # the states then differ by silhouette, not colour (#164).
        icon.setIsMask(template)
        _cache[key] = icon
    return _cache[key]


def state_icon(state: str) -> QIcon:
    """The tray mark in one availability state, at every size the OS asks for.

    Colour carries the state except where the fact table says the OS adapts
    template images to the menu bar (#164) — there the mark is a mask and the
    states are shapes."""
    from . import platform

    return _mark(state, platform.current().capabilities.tray_icon_is_template)


def app_icon() -> QIcon:
    """The window / taskbar / Alt-Tab icon: the Ready mark, unrecoloured.

    Never the template — windows and installers want the brand mark, and a
    mask outside the menu bar renders as a black blot."""
    return _mark("ready", False)
