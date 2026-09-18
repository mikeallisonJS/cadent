"""Render the Microsoft Store logo art from the same sources as the app icon.

Partner Center wants Store logos separate from the installer's icon:

    1:1 box art      2160 x 2160   required for MSI/EXE listings
    2:3 poster art   1440 x 2160   recommended; the main logo where shown
    1:1 app tile      300 x  300   recommended; overrides the package icon

All three are built from `app_icon()` in build_icons.py — the gradient tile
with the Ready mark knocked out of it — rather than drawn again here, so the
Store art cannot drift from the icon on the exe. That is the same
single-source rule ADR 0006 applies between the tray and the app icon.

Two of Microsoft's rules are checked in code rather than trusted to the eye,
because both are easy to break by nudging a number:

- The Store may overlay text on the **bottom third** of box and poster art, so
  nothing but background may sit there. `assert_bottom_third_clear` fails the
  run if any pixel there strays from the canvas.
- Text on the art must meet **4.5:1** contrast. `assert_contrast` measures the
  actual rendered background under each line, not the nominal token.

Run:  uv run python scripts/build_store_art.py
Out:  packaging/store/box-art-2160.png
      packaging/store/poster-art-1440x2160.png
      packaging/store/app-tile-300.png

packaging/store/ rather than packaging/icons/: the spec collects icons/ into
the frozen app, and none of this belongs in the installer.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QPainter
from PySide6.QtWidgets import QApplication

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_icons import app_icon, png_bytes  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "packaging" / "store"

# From cadent/theme/tokens.py: the dark canvas, primary and dim text. The art
# sits on the app's own background, so a listing looks like the product.
CANVAS = QColor("#14111a")
TEXT = QColor("#edeaf6")
TEXT_DIM = QColor("#a79fc0")

FONT_FAMILY = "Segoe UI"  # tokens.py's first choice; Windows ships it
NAME = "Cadent"
TAGLINE = "Private, offline dictation"

# WCAG AA for normal text, which is the bar Microsoft names for poster art.
MIN_CONTRAST = 4.5


def canvas(width: int, height: int) -> QImage:
    img = QImage(width, height, QImage.Format.Format_ARGB32)
    img.fill(CANVAS)
    return img


def place_tile(img: QImage, size: int, centre_x: float, top: float) -> None:
    """Draw the app icon, rendered at its final size rather than scaled."""
    p = QPainter(img)
    p.drawImage(round(centre_x - size / 2), round(top), app_icon(size))
    p.end()


def text_line(img: QImage, text: str, px: float, weight: QFont.Weight,
              colour: QColor, top: float) -> QRectF:
    """Draw one centred line with its cap top at `top`; return its ink box."""
    font = QFont(FONT_FAMILY)
    font.setPixelSize(round(px))
    font.setWeight(weight)
    metrics = QFontMetricsF(font)
    width = metrics.horizontalAdvance(text)
    x = (img.width() - width) / 2
    baseline = top + metrics.capHeight()
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    p.setFont(font)
    p.setPen(colour)
    p.drawText(round(x), round(baseline), text)
    p.end()
    return QRectF(x, baseline - metrics.ascent(), width,
                  metrics.ascent() + metrics.descent())


def luminance(c: QColor) -> float:
    def channel(v: int) -> float:
        s = v / 255
        return s / 12.92 if s <= 0.04045 else ((s + 0.055) / 1.055) ** 2.4
    return 0.2126 * channel(c.red()) + 0.7152 * channel(c.green()) + 0.0722 * channel(c.blue())


def contrast(a: QColor, b: QColor) -> float:
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def assert_contrast(background: QImage, box: QRectF, ink: QColor, label: str) -> None:
    """Worst-case contrast of `ink` against the background *under* the text.

    Sampled from an image rendered without the text, so the measurement is of
    what the letters actually sit on — not the canvas token, which would pass
    even if a glow or the tile had crept underneath.
    """
    worst = float("inf")
    x0, y0 = max(0, int(box.left())), max(0, int(box.top()))
    x1 = min(background.width(), int(box.right()) + 1)
    y1 = min(background.height(), int(box.bottom()) + 1)
    for y in range(y0, y1, 2):
        for x in range(x0, x1, 2):
            worst = min(worst, contrast(ink, background.pixelColor(x, y)))
    if worst < MIN_CONTRAST:
        raise SystemExit(f"{label}: contrast {worst:.2f}:1 is under {MIN_CONTRAST}:1")
    print(f"  {label}: worst-case contrast {worst:.1f}:1")


def assert_bottom_third_clear(img: QImage, label: str) -> None:
    """Nothing but canvas below two-thirds height, where overlays may land."""
    start = (img.height() * 2) // 3
    for y in range(start, img.height(), 3):
        for x in range(0, img.width(), 3):
            if img.pixelColor(x, y).rgb() != CANVAS.rgb():
                raise SystemExit(f"{label}: content at ({x}, {y}) is inside the bottom "
                                 "third, where the Store may overlay text")
    print(f"  {label}: bottom third clear")


def box_art(size: int = 2160) -> QImage:
    """Icon only. At the sizes box art is shown, a wordmark would be noise —
    and the listing prints the name beside it anyway. Centred in the top two
    thirds, the only region Microsoft guarantees stays unobstructed."""
    img = canvas(size, size)
    tile = round(size * 0.52)
    top = (size * 2 / 3 - tile) / 2
    place_tile(img, tile, size / 2, top)
    assert_bottom_third_clear(img, "box art")
    return img


def poster_art(width: int = 1440, height: int = 2160) -> QImage:
    """Icon, name and one line of positioning, all above the two-thirds line."""
    img = canvas(width, height)
    tile = round(width * 0.50)
    tile_top = height * 0.12
    place_tile(img, tile, width / 2, tile_top)

    background = img.copy()  # what the text will sit on, for the contrast check
    name_top = tile_top + tile + height * 0.055
    name_box = text_line(img, NAME, width * 0.135, QFont.Weight.DemiBold, TEXT, name_top)
    tag_top = name_box.bottom() + height * 0.02
    tag_box = text_line(img, TAGLINE, width * 0.052, QFont.Weight.Normal, TEXT_DIM, tag_top)

    if tag_box.bottom() >= height * 2 / 3:
        raise SystemExit("poster art: the tagline runs into the bottom third")
    assert_contrast(background, name_box, TEXT, "poster name")
    assert_contrast(background, tag_box, TEXT_DIM, "poster tagline")
    assert_bottom_third_clear(img, "poster art")
    return img


def main() -> int:
    QApplication(sys.argv)
    installed = QFont(FONT_FAMILY).exactMatch()
    if not installed:
        print(f"warning: {FONT_FAMILY} is not installed; the poster will use a "
              "substitute face. Run this on Windows for the real one.")
    OUT.mkdir(parents=True, exist_ok=True)
    outputs = {
        "box-art-2160.png": box_art(),
        "poster-art-1440x2160.png": poster_art(),
        "app-tile-300.png": app_icon(300),
    }
    for name, img in outputs.items():
        (OUT / name).write_bytes(png_bytes(img))
        print(f"wrote {OUT / name}  ({img.width()}x{img.height()})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
