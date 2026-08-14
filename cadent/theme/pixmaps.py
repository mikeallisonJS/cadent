"""The three indicators QSS cannot draw, generated from the token dict (§1.5).

QSS can draw a toggle's *track* but not a knob offset inside it; the ring
radio's border-width trick renders as a crescent; and there is no chevron
primitive. All three are painted into PNGs at startup and referenced as
`image: url(...)` — derived from the tokens, never a parallel palette.

**The DPI trap and how this module avoids it.** `QSS image:` draws a pixmap at
its natural pixel size and *clips* to the indicator box; it does not scale. A
2x pixmap dropped into a 38x20 indicator renders as a clipped smear. Measured
on PySide6 6.11: QSS honours Qt's `@Nx` high-DPI file convention — with
`url(toggle-on.png)` referenced and `toggle-on@2x.png` on disk, a DPR-2 window
loads the @2x file and fills the box exactly. So rather than tracking the live
devicePixelRatio and regenerating on every DPI change (which leaves a window
dragged between monitors briefly wrong), each indicator is written once as a
1x/2x/3x ladder and Qt picks per-screen. `qt_findAtNxFile` rounds a fractional
DPR up, so 1.25 and 1.5 both land on the crisp @2x and are scaled down.
"""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPen, QPixmap

# The @Nx variants Qt will look for. qt_findAtNxFile caps its search at 3x.
_SCALES = (1, 2, 3)


@dataclass(frozen=True)
class PixmapSet:
    """Filesystem paths for one theme's indicators, ready for `url(...)`."""

    chevron: str
    toggle_off: str
    toggle_on: str
    radio_off: str
    radio_on: str


_dir: Path | None = None
_cache: dict[str, PixmapSet] = {}


def _out_dir() -> Path:
    global _dir
    if _dir is None:
        _dir = Path(tempfile.mkdtemp(prefix="cadent-theme-"))
    return _dir


def _write(name: str, paint, w: float, h: float) -> str:
    """Paint one indicator at every @Nx scale; return the 1x path for QSS."""
    base = _out_dir() / f"{name}.png"
    for scale in _SCALES:
        pm = QPixmap(round(w * scale), round(h * scale))
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.scale(scale, scale)
        paint(p)
        p.end()
        suffix = "" if scale == 1 else f"@{scale}x"
        pm.save(str(base.with_name(f"{name}{suffix}.png")), "PNG")
    return str(base).replace("\\", "/")


def _paint_chevron(t: dict, colour: str):
    w, h = float(t["chevron_w"]), float(t["chevron_h"])

    def paint(p: QPainter) -> None:
        pen = QPen(QColor(colour))
        pen.setWidthF(1.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawPolyline([QPointF(2, 2.5), QPointF(w / 2, h - 2.5), QPointF(w - 2, 2.5)])

    return paint


def _paint_toggle(t: dict, on: bool):
    w, h = float(t["toggle_w"]), float(t["toggle_h"])
    knob = float(t["toggle_knob"])

    def paint(p: QPainter) -> None:
        if on:
            grad = QLinearGradient(0, 0, w, 0)
            grad.setColorAt(0, QColor(t["accent_fill_a"]))
            grad.setColorAt(1, QColor(t["accent_fill_b"]))
            p.setBrush(grad)
            p.setPen(QPen(QColor(t["accent_fill_a"]), 1))
        else:
            p.setBrush(QColor(t["field"]))
            p.setPen(QPen(QColor(t["border_strong"]), 1))
        p.drawRoundedRect(QRectF(0.5, 0.5, w - 1, h - 1), h / 2, h / 2)
        pad = (h - knob) / 2
        x = w - knob - pad if on else pad
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(t["on_accent"] if on else t["text_dim"]))
        p.drawEllipse(QRectF(x, pad, knob, knob))

    return paint


def _paint_radio(t: dict, on: bool):
    d = float(t["radio_outer"])

    def paint(p: QPainter) -> None:
        p.setBrush(Qt.BrushStyle.NoBrush if on else QColor(t["field"]))
        p.setPen(QPen(QColor(t["accent_fill_a"] if on else t["border_strong"]), 2))
        p.drawEllipse(QRectF(1, 1, d - 2, d - 2))
        if on:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(t["accent_fill_a"]))
            r = d / 2 - 4
            p.drawEllipse(QRectF(d / 2 - r, d / 2 - r, r * 2, r * 2))

    return paint


def generate(theme: str, t: dict) -> PixmapSet:
    """The indicator set for a theme, generated once and cached.

    Keyed on the theme alone because the other axis — a Windows contrast theme
    — drops the stylesheet entirely (§1.3), so no rule references these paths
    while it is active.
    """
    if theme in _cache:
        return _cache[theme]
    chev_w, chev_h = float(t["chevron_w"]), float(t["chevron_h"])
    tog_w, tog_h = float(t["toggle_w"]), float(t["toggle_h"])
    radio = float(t["radio_outer"])
    result = PixmapSet(
        chevron=_write(f"chevron-{theme}", _paint_chevron(t, str(t["text_dim"])),
                       chev_w, chev_h),
        toggle_off=_write(f"toggle-off-{theme}", _paint_toggle(t, False), tog_w, tog_h),
        toggle_on=_write(f"toggle-on-{theme}", _paint_toggle(t, True), tog_w, tog_h),
        radio_off=_write(f"radio-off-{theme}", _paint_radio(t, False), radio, radio),
        radio_on=_write(f"radio-on-{theme}", _paint_radio(t, True), radio, radio),
    )
    _cache[theme] = result
    return result


def clear() -> None:
    """Drop the cache and the files behind it (tests; app shutdown)."""
    global _dir
    _cache.clear()
    if _dir is not None:
        shutil.rmtree(_dir, ignore_errors=True)
        _dir = None
