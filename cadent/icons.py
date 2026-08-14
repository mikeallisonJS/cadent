"""The Cadent mark: one silhouette, three shapes, six sizes (spec §2).

One mic-derived mark is the app's entire visual identity — the tray on both
platforms, the exe, the installer, and every window's title-bar icon. The
three availability states differ by *shape*: flow lines, pause bars, the
exclamation. Colour says nothing about state anywhere (this supersedes #164,
where only darwin worked this way and Windows recoloured one silhouette).

The rasters are alpha only. Where the OS repaints a mask to suit the surface
it sits on — the macOS menu bar — the mark ships as a mask and the OS picks
the ink. Where it does not, we paint the silhouette ourselves in an ink
derived from the tray's own surface, which on Windows is the taskbar's colour
mode rather than the app's (see `platform.DesktopEnv.tray_ink`).

Loaded via `QIcon.addFile()`/`addPixmap()` on the pre-rasterised PNG set so
Windows picks the exact size it asks for (16 @100% DPI, 20 @125%, 24 @150%,
32 @200%) and **never scales**. That also avoids shipping Qt's SVG
image-format plugin — a classic PyInstaller silent failure where the icon
works in dev and the built exe shows a blank.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap

from .theme.tokens import BASE

# Availability only (spec §3.1). Whether cleanup is on is deliberately absent: it
# lives in the tooltip and the menu checkbox, because a 16px icon Windows
# frequently collapses into the overflow flyout is the wrong surface for a
# mode you check *while dictating*.
STATES = ("ready", "paused", "attention")
SIZES = (16, 20, 24, 32, 48, 256)

# The window / taskbar / Alt-Tab mark is the brand colour; only the tray takes
# its ink from the surface underneath it. Read from the token so the value the
# taskbar contrast audit checks is the value that gets painted.
BRAND_INK = str(BASE["brand"])

# Keyed by ink as well as state: a taskbar that flips from light to dark asks
# for the same state in a new colour, and a key that ignored the ink would
# serve the old one back for the life of the process.
_cache: dict[tuple[str, str | None], QIcon] = {}


def icons_dir() -> Path:
    """Where the rasterised mark lives, frozen or from a checkout."""
    bundled = getattr(sys, "_MEIPASS", None)
    if bundled:
        return Path(bundled) / "icons"
    return Path(__file__).resolve().parent.parent / "packaging" / "icons"


def png_path(state: str, size: int) -> Path:
    return icons_dir() / "png" / f"mark-{state}-{size}.png"


def ico_path() -> Path:
    return icons_dir() / "cadent.ico"


def icns_path() -> Path:
    """The .app bundle's icon (#171) — packaging only; nothing loads it at
    runtime, since macOS reads it from the bundle itself."""
    return icons_dir() / "cadent.icns"


def _tinted(path: Path, ink: str) -> QPixmap:
    """One raster painted in `ink`, keeping its alpha.

    `SourceIn` fills only where the silhouette already is, which is why the
    shipped PNGs can be a single colourless set: the shape is the asset, the
    colour is a runtime decision."""
    source = QPixmap(str(path))
    out = QPixmap(source.size())
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.drawPixmap(0, 0, source)
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    p.fillRect(out.rect(), QColor(ink))
    p.end()
    return out


def _mark(state: str, ink: str | None) -> QIcon:
    """The mark in one state. `ink` None means ship it as a mask and let the
    OS paint it; a colour means paint it here."""
    if state not in STATES:
        state = "ready"
    key = (state, ink)
    if key not in _cache:
        icon = QIcon()
        for size in SIZES:
            path = png_path(state, size)
            if not path.exists():
                continue
            if ink is None:
                icon.addFile(str(path))
            else:
                icon.addPixmap(_tinted(path, ink))
        # A mask lets the OS recolour the mark to the menu bar it sits in.
        icon.setIsMask(ink is None)
        _cache[key] = icon
    return _cache[key]


def state_icon(state: str) -> QIcon:
    """The tray mark in one availability state, at every size the OS asks for.

    Shape carries the state on both platforms. Only the ink differs: macOS
    repaints a mask to its menu bar, and everywhere else we paint it to the
    tray surface ourselves."""
    from . import platform

    current = platform.current()
    if current.capabilities.tray_icon_painted_by_os:
        return _mark(state, None)
    return _mark(state, current.desktop.tray_ink())


def app_icon() -> QIcon:
    """The window / taskbar / Alt-Tab icon: the Ready mark in brand colour.

    Never a mask — windows and installers want the brand mark, and a mask
    outside the menu bar renders as a black blot."""
    return _mark("ready", BRAND_INK)
