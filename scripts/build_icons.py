"""Rasterise the Cadent mark from SVG to the PNG set, the .ico and the .icns.

The mark is authored once per state as SVG (`packaging/icons/mark-*.svg`) and
rasterised here at build time to the exact sizes the tray asks for, so
`QIcon.addFile()` never has to scale and Qt's SVG image-format plugin never
has to survive PyInstaller (#69).

Two grids per state: the 24-unit master serves 24 and up, and a
hand-corrected 16-unit source (`mark-*-16.svg`) serves 16 and 20, where the
cradle has to land on whole pixel rows. Per-size correction is the whole
reason this runs at build time rather than scaling one source (#73).

One set, both platforms. The tray rasters are **alpha only**: the mark is
painted at runtime in an ink the OS picks (darwin masks it to the menu bar)
or one derived from the taskbar (win32), so what ships is a silhouette and
the SVG's nominal black never reaches a screen. The states differ by shape,
not colour, everywhere — superseding the darwin-only composition of #164.

The `.ico` and `.icns` are a different picture entirely: the exe, the
installer, the .app bundle and the Dock get the **tile** — `app-tile.svg`'s
gradient rounded square with the Ready mark knocked out of it in white, at
62.5% of the canvas. That is composited here rather than authored as one SVG
so the mark keeps a single source; a copy of the geometry inside a tile file
would be the parallel-copy drift ADR 0006 exists to undo.

Run:  uv run python scripts/build_icons.py
Out:  packaging/icons/png/mark-<state>-<size>.png   (18 files)
      packaging/icons/cadent.ico                 (multi-size, Ready)
      packaging/icons/cadent.icns                (multi-size, Ready)
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

# The nominal fill every mark SVG is authored with; the app icon knocks the
# mark out of its tile by rendering it white. Keeping the sources black means
# an untinted render is a legible silhouette rather than a stray brand colour.
NOMINAL_INK = b"#000"
KNOCKOUT_INK = b"#ffffff"

# How much of the tile the mark fills. 640 on the 1024 master — enough to read
# as the subject at 32px in a taskbar, short of crowding the corners at 512.
# The mark is placed by its 24-unit grid box, not by its ink: the ink sits
# right of centre (the flow lines run out of the mic), and centring *that*
# pushes the mic off-centre, which is the thing the eye actually tracks.
MARK_FILL = 0.625

# Below 32px the proportional mark runs out of pixels — at a 16px tile it gets
# ten, and the mic stops being a mic. The small entries trade tile margin for
# glyph, which is what every platform's own icon set does at this size; the
# discontinuity is invisible because nothing ever shows 24 and 32 together.
SMALL_TILE = 24
SMALL_MARK_FILL = 0.80

# The .icns entries, as (OSType, pixel size) — the PNG-capable types `iconutil`
# emits for a full .iconset (#171). macOS addresses an icon by point size *and*
# retina scale, so 32, 256 and 512 each appear twice under different types: a
# 32px raster is both "32pt @1x" (icp5) and "16pt @2x" (ic11). Same bytes, two
# names; dropping either leaves the Dock or Finder picking a size it has to
# scale.
ICNS_TYPES = [
    (b"icp4", 16), (b"ic11", 32), (b"icp5", 32), (b"ic12", 64),
    (b"ic07", 128), (b"ic13", 256), (b"ic08", 256), (b"ic14", 512),
    (b"ic09", 512), (b"ic10", 1024),
]
ICNS_SIZES = sorted({size for _, size in ICNS_TYPES})


def source(state: str, size: int) -> Path:
    """Which grid serves this size."""
    suffix = "-16" if size <= HINT_CEILING else ""
    return ICONS / f"mark-{state}{suffix}.svg"


def render(state: str, size: int, ink: bytes | None = None) -> QImage:
    """One raster. `ink` substitutes the SVG's nominal black — used only for
    the app-icon containers; the tray set keeps the alpha and no colour."""
    data = source(state, size).read_bytes()
    if ink is not None:
        data = data.replace(NOMINAL_INK, ink)
    img = QImage(size, size, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    QSvgRenderer(QByteArray(data)).render(p)
    p.end()
    return img


def app_icon(size: int) -> QImage:
    """The tile with the Ready mark knocked out of it, at one size.

    Composited rather than authored: `app-tile.svg` owns the tile and
    `mark-ready.svg` owns the mark, so neither can drift from the other. The
    mark is rendered at its own target size rather than scaled after the fact,
    which keeps the `HINT_CEILING` routing intact — a 32px tile draws a 20px
    mark, and 20 still comes off the hand-corrected 16-unit grid.
    """
    img = QImage(size, size, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    QSvgRenderer(QByteArray((ICONS / "app-tile.svg").read_bytes())).render(p)
    fill = SMALL_MARK_FILL if size <= SMALL_TILE else MARK_FILL
    mark_px = round(size * fill)
    if mark_px:
        # Centred by the mark's grid box — see MARK_FILL.
        offset = (size - mark_px) // 2
        p.drawImage(offset, offset, render("ready", mark_px, KNOCKOUT_INK))
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


def write_icns(images: dict[int, QImage], dest: Path) -> None:
    """Assemble a PNG-in-ICNS by hand, for the same reason `write_ico` does it
    (#171): Qt's ICNS handler writes a single size, and `iconutil` — the tool
    that would do this properly — only exists on macOS, where this build step
    never runs. The container is trivial: the magic and a big-endian total
    length, then one chunk per entry (4-byte OSType, big-endian length
    *including* its own 8-byte header, payload).

    Emitting the file here rather than on the runner keeps the mark a
    committed artefact on both OSes — the macOS build consumes `cadent.icns`
    exactly as the Windows one consumes `cadent.ico`.
    """
    payloads = {size: png_bytes(img) for size, img in images.items()}
    body = b""
    for ostype, size in ICNS_TYPES:
        data = payloads[size]
        body += ostype + struct.pack(">I", 8 + len(data)) + data
    dest.write_bytes(b"icns" + struct.pack(">I", 8 + len(body)) + body)


def main() -> None:
    app = QApplication(sys.argv)  # noqa: F841 - QImage/QPainter need one
    out = ICONS / "png"
    out.mkdir(parents=True, exist_ok=True)

    # The runtime module owns the raster naming convention; the builder only
    # follows it, so the two can never drift apart.
    from cadent import icons

    for state in STATES:
        for size in SIZES:
            render(state, size).save(str(icons.png_path(state, size)), "PNG")

    # The containers carry the tile, not the tray silhouette: an icon sitting
    # among filled shapes in a Dock or a Start menu has a different job from
    # one sitting among monochrome glyphs in a tray.
    ico = ICONS / "cadent.ico"
    write_ico([app_icon(size) for size in SIZES], ico)

    # The .app icon (#171). Its own size ladder: the Dock and Finder ask up to
    # 1024, four sizes past anything the tray or the .ico needs, and none of
    # them belong in the runtime PNG set.
    icns = icons.icns_path()
    write_icns({size: app_icon(size) for size in ICNS_SIZES}, icns)

    print(f"  {len(STATES) * len(SIZES)} PNGs -> {out}")
    print(f"  {ico.name} ({ico.stat().st_size:,} bytes, "
          f"{len(SIZES)} sizes) -> {ico.parent}")
    print(f"  {icns.name} ({icns.stat().st_size:,} bytes, "
          f"{len(ICNS_TYPES)} entries) -> {icns.parent}")


if __name__ == "__main__":
    main()
