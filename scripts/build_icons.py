"""Rasterise the Cadent mark from SVG to the PNG set and the .ico.

The mark is authored once per state as SVG (`packaging/icons/mark-*.svg`) and
rasterised here at build time to the exact sizes Windows asks for, so
`QIcon.addFile()` never has to scale and Qt's SVG image-format plugin never
has to survive PyInstaller (#69).

Two grids per state: the 24-unit master serves 24 and up, and a
hand-corrected 16-unit source (`mark-*-16.svg`) serves 16 and 20, where the
cradle has to land on whole pixel rows. Per-size correction is the whole
reason this runs at build time rather than scaling one source (#73).

Run:  uv run python scripts/build_icons.py
Out:  packaging/icons/png/mark-<state>-<size>.png   (18 files)
      packaging/icons/cadent.ico                 (multi-size, Ready)
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication

ICONS = Path(__file__).resolve().parent.parent / "packaging" / "icons"
STATES = ["ready", "paused", "attention"]
SIZES = [16, 20, 24, 32, 48, 256]

# Below this the hand-corrected 16-unit source wins; at and above it the
# 24-unit master does.
HINT_CEILING = 20


def render(state: str, size: int, template: bool = False) -> QImage:
    """One raster. `template` picks the darwin menu-bar set (#164): the same
    per-size correction discipline, but silhouette-only sources — macOS
    recolours a template image, so the states differ by shape."""
    suffix = "-template" if template else ""
    src = ICONS / (f"mark-{state}{suffix}-16.svg" if size <= HINT_CEILING
                   else f"mark-{state}{suffix}.svg")
    img = QImage(size, size, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    QSvgRenderer(str(src)).render(p)
    p.end()
    return img


def png_bytes(img: QImage) -> bytes:
    # The QByteArray must outlive the QBuffer: QBuffer holds a bare pointer to
    # it, so passing a temporary crashes the interpreter at teardown.
    store = QByteArray()
    buf = QBuffer(store)
    buf.open(QBuffer.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    buf.close()
    return bytes(store)


def write_ico(images: list[QImage], dest: Path) -> None:
    """Assemble a PNG-compressed multi-size .ico by hand.

    Qt's ICO handler is read-only, and the format is small enough to emit
    directly: a 6-byte header, a 16-byte directory entry per image, then the
    payloads. PNG-in-ICO is what Windows has read since Vista, and it keeps
    the 256px entry from ballooning the file the way a BMP entry would.
    """
    payloads = [png_bytes(i) for i in images]
    offset = 6 + 16 * len(images)
    header = struct.pack("<HHH", 0, 1, len(images))
    directory, body = b"", b""
    for img, data in zip(images, payloads, strict=True):
        # 0 means 256 in the ICO directory's single-byte size fields.
        dim = 0 if img.width() >= 256 else img.width()
        directory += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32,
                                 len(data), offset)
        body += data
        offset += len(data)
    dest.write_bytes(header + directory + body)


def main() -> None:
    app = QApplication(sys.argv)  # noqa: F841 - QImage/QPainter need one
    out = ICONS / "png"
    out.mkdir(parents=True, exist_ok=True)

    # The runtime module owns the raster naming convention; the builder only
    # follows it, so the two can never drift apart.
    from cadent import icons

    ready: list[QImage] = []
    for state in STATES:
        for size in SIZES:
            img = render(state, size)
            img.save(str(icons.png_path(state, size)), "PNG")
            if state == "ready":
                ready.append(img)
            render(state, size, template=True).save(
                str(icons.png_path(state, size, template=True)), "PNG")

    ico = ICONS / "cadent.ico"
    write_ico(ready, ico)

    print(f"  {2 * len(STATES) * len(SIZES)} PNGs -> {out}")
    print(f"  {ico.name} ({ico.stat().st_size:,} bytes, "
          f"{len(SIZES)} sizes) -> {ico.parent}")


if __name__ == "__main__":
    main()
