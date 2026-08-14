"""The mark's build artefacts (spec §2, §10).

`build_icons.py` assembles the .ico by hand because Qt's ICO handler is
read-only. That makes the container our code rather than a library's, so the
bytes it writes are worth asserting on.
"""

import importlib.util
import struct
from pathlib import Path

import pytest
from PySide6.QtGui import QImage

from cadent import icons

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def build_icons():
    spec = importlib.util.spec_from_file_location(
        "build_icons", ROOT / "scripts" / "build_icons.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---- the rasterised set ----------------------------------------------------

@pytest.mark.parametrize("state", icons.STATES)
@pytest.mark.parametrize("size", icons.SIZES)
def test_every_state_ships_every_size_at_exact_dimensions(qt_app, state, size):
    """QIcon.addFile picks by exact size and never scales, so a missing or
    mis-sized raster shows up as a blurry tray icon, not an error."""
    img = QImage(str(icons.png_path(state, size)))
    assert not img.isNull(), f"missing raster for {state} at {size}"
    assert (img.width(), img.height()) == (size, size)


@pytest.mark.parametrize("state", icons.STATES)
def test_the_mark_actually_has_ink(qt_app, state):
    """A transparent PNG is the failure mode a size check alone misses."""
    img = QImage(str(icons.png_path(state, 24)))
    opaque = sum(1 for y in range(img.height()) for x in range(img.width())
                 if img.pixelColor(x, y).alpha() > 128)
    assert opaque > 0.10 * img.width() * img.height()


def test_state_icon_serves_each_requested_size_without_scaling(qt_app):
    """Every size Windows asks for has an exact raster behind it.

    The pixmap requests pin devicePixelRatio to 1.0 (#118): the two-int
    overload applies the screen's ratio, and at 150% scale a 24pt request is
    served by the 32px raster at DPR 1.33 — an exact source image, but a
    deviceIndependentSize of 24.06 that an exact `== 24` rejects. Pinned to
    1.0, each request must come back as the raster of exactly that size.
    """
    from PySide6.QtCore import QSize

    icon = icons.state_icon("ready")
    available = {size.width() for size in icon.availableSizes()}
    assert available == set(icons.SIZES)
    for size in icons.SIZES:
        served = icon.pixmap(QSize(size, size), 1.0)
        assert (served.width(), served.height()) == (size, size)


def test_unknown_state_falls_back_to_ready(qt_app):
    assert not icons.state_icon("nonsense").isNull()


# ---- the hand-corrected 16-unit grid ---------------------------------------

def test_the_seam_sits_between_20_and_24(qt_app, build_icons):
    """At 16px the 24-unit master puts the cradle across three rows at half
    alpha and reads as a grey smudge, so 16 and 20 come from a hand-corrected
    16-unit source instead. The discontinuity there is the correction."""
    assert build_icons.HINT_CEILING == 20
    for state in icons.STATES:
        assert (icons.icons_dir() / f"mark-{state}-16.svg").exists()
        assert (icons.icons_dir() / f"mark-{state}.svg").exists()


# ---- the hand-assembled .ico -----------------------------------------------

def _parse_ico(data: bytes):
    reserved, kind, count = struct.unpack_from("<HHH", data, 0)
    assert (reserved, kind) == (0, 1)
    entries = []
    for i in range(count):
        w, h, colours, res, planes, bpp, length, offset = struct.unpack_from(
            "<BBBBHHII", data, 6 + 16 * i)
        entries.append({"w": w, "h": h, "length": length, "offset": offset,
                        "payload": data[offset:offset + length]})
    return entries


def test_ico_contains_every_size_with_256_encoded_as_zero(qt_app):
    """The ICO directory's size fields are a single byte each, so 256 — the
    size Windows uses for the largest shell views — is written as 0."""
    entries = _parse_ico(icons.ico_path().read_bytes())
    assert [e["w"] for e in entries] == [16, 20, 24, 32, 48, 0]
    assert all(e["w"] == e["h"] for e in entries)


def test_ico_payloads_are_png_and_read_back_at_exact_dimensions(qt_app):
    """PNG-in-ICO is what Windows has read since Vista, and it keeps the 256px
    entry from ballooning the file the way a BMP entry would."""
    entries = _parse_ico(icons.ico_path().read_bytes())
    for entry, expected in zip(entries, icons.SIZES, strict=True):
        assert entry["payload"].startswith(b"\x89PNG\r\n\x1a\n")
        img = QImage.fromData(entry["payload"], "PNG")
        assert (img.width(), img.height()) == (expected, expected)


def test_qt_reads_the_ico_back_at_every_size(qt_app):
    """Qt's ICO handler is read-only but it is the reader that matters most
    here — Qt is what loads the icon at runtime."""
    from PySide6.QtGui import QIcon

    icon = QIcon(str(icons.ico_path()))
    assert {size.width() for size in icon.availableSizes()} == set(icons.SIZES)


def test_write_ico_round_trips_what_it_was_given(qt_app, build_icons, tmp_path):
    images = [build_icons.render("ready", size) for size in (16, 256)]
    dest = tmp_path / "out.ico"
    build_icons.write_ico(images, dest)
    entries = _parse_ico(dest.read_bytes())
    assert [e["w"] for e in entries] == [16, 0]
    assert QImage.fromData(entries[1]["payload"], "PNG").width() == 256


# ---- the hand-assembled .icns (#171) ---------------------------------------

def _parse_icns(data: bytes):
    assert data[:4] == b"icns"
    total = struct.unpack_from(">I", data, 4)[0]
    assert total == len(data), "declared length must match the file"
    entries, offset = [], 8
    while offset < total:
        ostype = data[offset:offset + 4]
        length = struct.unpack_from(">I", data, offset + 4)[0]
        entries.append({"type": ostype, "length": length,
                        "payload": data[offset + 8:offset + length]})
        offset += length
    return entries


def test_icns_carries_every_type_the_dock_and_finder_ask_for(qt_app, build_icons):
    """A missing OSType is a size macOS has to scale — the .app icon equivalent
    of the blurry-tray failure the PNG set exists to prevent."""
    entries = _parse_icns(icons.icns_path().read_bytes())
    assert [e["type"] for e in entries] == [t for t, _ in build_icons.ICNS_TYPES]


def test_icns_payloads_are_png_at_the_size_their_type_declares(qt_app, build_icons):
    """32, 256 and 512 each appear under two types (point size and retina
    scale); both entries must really hold a raster of that size."""
    entries = _parse_icns(icons.icns_path().read_bytes())
    for entry, (_, size) in zip(entries, build_icons.ICNS_TYPES, strict=True):
        assert entry["payload"].startswith(b"\x89PNG\r\n\x1a\n")
        img = QImage.fromData(entry["payload"], "PNG")
        assert (img.width(), img.height()) == (size, size)


def test_qt_reads_the_icns_back_at_every_size(qt_app, build_icons):
    """The reader check the .ico gets too: our container, so someone else's
    parser has to accept it. macOS itself is the parser that matters, and
    Qt's ICNS handler is the closest stand-in a Windows build machine has."""
    from PySide6.QtGui import QIcon

    icon = QIcon(str(icons.icns_path()))
    assert {size.width() for size in icon.availableSizes()} == set(build_icons.ICNS_SIZES)


def test_write_icns_round_trips_what_it_was_given(qt_app, build_icons, tmp_path):
    dest = tmp_path / "out.icns"
    build_icons.write_icns(
        {size: build_icons.render("ready", size) for size in build_icons.ICNS_SIZES},
        dest)
    entries = _parse_icns(dest.read_bytes())
    assert len(entries) == len(build_icons.ICNS_TYPES)
    assert QImage.fromData(entries[-1]["payload"], "PNG").width() == 1024


# ---- the darwin template set (#164) ----------------------------------------

def test_every_template_state_ships_every_size(qt_app):
    """The menu-bar mask set: same size discipline as the coloured set."""
    for state in icons.STATES:
        for size in icons.SIZES:
            img = QImage(str(icons.png_path(state, size, template=True)))
            assert not img.isNull(), f"missing template raster {state}@{size}"
            assert (img.width(), img.height()) == (size, size)


def test_template_states_differ_by_shape_not_colour(qt_app):
    """A template icon has only alpha to speak with, so the three states must
    have three silhouettes — flow lines, pause bars, the exclamation."""
    def alpha_map(state):
        img = QImage(str(icons.png_path(state, 24, template=True)))
        return tuple(img.pixelColor(x, y).alpha() > 128
                     for y in range(img.height()) for x in range(img.width()))

    maps = {state: alpha_map(state) for state in icons.STATES}
    assert sum(maps["ready"]) > 0.05 * 24 * 24
    assert maps["ready"] != maps["paused"]
    assert maps["ready"] != maps["attention"]
    assert maps["paused"] != maps["attention"]


def test_darwin_tray_icon_is_a_mask_the_colour_columns_are_not(qt_app, monkeypatch):
    """The fact decides (#164): where the OS adapts template images to the
    menu bar, the mark is a mask; elsewhere the colours carry the state."""
    from conftest import make_platform, pin_darwin_ui_platform

    from cadent import platform as platform_pkg

    pin_darwin_ui_platform(monkeypatch)
    for state in icons.STATES:
        icon = icons.state_icon(state)
        assert not icon.isNull()
        assert icon.isMask() is True

    monkeypatch.setattr(platform_pkg, "_current", make_platform())
    assert icons.state_icon("ready").isMask() is False


def test_the_app_icon_stays_coloured_even_on_darwin(qt_app, monkeypatch):
    """Windows, taskbars and installers want the brand mark, not a mask —
    only the menu bar gets the template treatment."""
    from conftest import pin_darwin_ui_platform

    pin_darwin_ui_platform(monkeypatch)
    assert icons.app_icon().isMask() is False
