"""The rendered stylesheet, the generated indicators, and the theme manager.

These need a QApplication, so they share the one in `qt_app` (conftest) —
Qt permits exactly one per process.
"""

import re

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap

from cadent.theme import manager, pixmaps, qss
from cadent.theme.tokens import tokens


@pytest.fixture(autouse=True)
def _clean_pixmaps():
    pixmaps.clear()
    yield
    pixmaps.clear()


@pytest.fixture
def make_manager(qt_app):
    """Build ThemeManagers that are actually torn down.

    A manager is parented to the application and stays connected to
    colorSchemeChanged, so a leaked one keeps re-rendering the whole sheet on
    every later theme change — measurably, minutes per test.
    """
    built = []

    def make(preference="system", **kwargs):
        built.append(manager.ThemeManager(qt_app, preference, **kwargs))
        return built[-1]

    yield make
    for mgr in built:
        mgr.setParent(None)
    

# ---- pixmaps ---------------------------------------------------------------

def test_generated_indicators_cover_the_set_qss_cannot_draw(qt_app):
    t = tokens("dark")
    generated = pixmaps.generate("dark", t)
    assert QPixmap(generated.chevron).size().width() == t["chevron_w"]
    assert QPixmap(generated.toggle_off).size().width() == t["toggle_w"]
    assert QPixmap(generated.toggle_on).size().height() == t["toggle_h"]
    assert QPixmap(generated.radio_off).size().width() == t["radio_outer"]
    assert QPixmap(generated.radio_on).size().width() == t["radio_outer"]


def test_each_indicator_ships_its_at_nx_ladder(qt_app):
    """QSS `image:` clips rather than scales, so a 2x pixmap in a 1x box is a
    smear. Qt's @Nx convention is what keeps that from happening at any DPI."""
    t = tokens("dark")
    base = pixmaps.generate("dark", t).toggle_on
    for scale in (2, 3):
        variant = base.replace(".png", f"@{scale}x.png")
        assert QPixmap(variant).width() == round(float(t["toggle_w"]) * scale)


def test_generation_is_cached_per_theme(qt_app):
    t = tokens("dark")
    assert pixmaps.generate("dark", t) is pixmaps.generate("dark", t)
    assert pixmaps.generate("light", tokens("light")).toggle_on != \
        pixmaps.generate("dark", t).toggle_on


# ---- stylesheet ------------------------------------------------------------

@pytest.mark.parametrize("theme", ["dark", "light"])
def test_stylesheet_renders_with_every_token_substituted(qt_app, theme):
    t = tokens(theme)
    sheet = qss.render(t, pixmaps.generate(theme, t))
    assert "$" not in sheet          # no unsubstituted placeholder survived
    assert t["bg"] in sheet
    assert t["hud_surface"] in sheet


def test_stylesheet_never_uses_letter_spacing(qt_app):
    """Qt QSS ignores it silently — tracked capitals come from tracked_caps()."""
    t = tokens("dark")
    assert "letter-spacing" not in qss.render(t, pixmaps.generate("dark", t))


def test_combo_and_scrollbar_are_styled_whole_not_partially(qt_app):
    """QSS styling of these two is all-or-nothing; a partial rule yields a
    broken control (§1.1)."""
    t = tokens("dark")
    sheet = qss.render(t, pixmaps.generate("dark", t))
    for sub in ("QComboBox::drop-down", "QComboBox::down-arrow",
                "QComboBox QAbstractItemView"):
        assert sub in sheet
    for sub in ("QScrollBar:vertical", "QScrollBar::handle:vertical",
                "QScrollBar:horizontal", "QScrollBar::handle:horizontal",
                "QScrollBar::add-line", "QScrollBar::add-page"):
        assert sub in sheet


def test_focus_ring_is_scoped_to_keyboard_focus_only(qt_app):
    """A plain :focus rule would ring on mouse clicks, which is not what
    Fusion does (§8.2)."""
    t = tokens("dark")
    sheet = qss.render(t, pixmaps.generate("dark", t))
    assert 'kbdFocus="true"' in sheet
    assert not re.search(r"[^-\w]:focus\s*\{[^}]*outline", sheet)
    # The accent CTA gets the near-white ring; the plain one is invisible on it.
    assert 'QPushButton#Accent[kbdFocus="true"]' in sheet
    assert t["focus_ring_on_accent"] in sheet


def test_stylesheet_applies_to_a_real_application(qt_app):
    t = tokens("dark")
    sheet = qss.render(t, pixmaps.generate("dark", t))
    qt_app.setStyleSheet(sheet)
    assert qt_app.styleSheet() == sheet


# ---- manager ---------------------------------------------------------------

def test_manager_forces_fusion(qt_app, make_manager):
    """Fusion unconditionally: the design was proven on it, and it collapses
    the Windows 10 divergence where windowsvista cannot render dark at all."""
    make_manager("dark")
    # With a sheet installed `style()` is Qt's stylesheet proxy, which reports
    # no object name; drop the sheet and the base style shows through.
    qt_app.setStyleSheet("")
    assert "fusion" in qt_app.style().objectName().lower()


def test_override_beats_the_system_scheme(qt_app, make_manager):
    mgr = make_manager("light")
    assert mgr.theme == "light"
    mgr.set_preference("dark")
    assert mgr.theme == "dark"
    assert mgr.tokens["bg"] == tokens("dark")["bg"]


def test_system_preference_lands_unknown_on_dark(qt_app, make_manager):
    """Qt reports ColorScheme.Unknown under a contrast theme; the branch must
    serve dark there, not light (§1.3)."""
    mgr = make_manager("system")
    mgr._scheme = Qt.ColorScheme.Unknown
    assert mgr.theme == "dark"
    mgr._scheme = Qt.ColorScheme.Light
    assert mgr.theme == "light"
    mgr._scheme = Qt.ColorScheme.Dark
    assert mgr.theme == "dark"


def test_high_contrast_drops_the_sheet_entirely(qt_app, make_manager):
    """A contrast theme changes the palette, not our stylesheet, and a QSS
    hard-coded colour beats the palette on every widget it matches — so the
    only correct posture is to remove the sheet (§1.3)."""
    mgr = make_manager("dark")
    assert qt_app.styleSheet() != ""
    mgr._high_contrast = True
    mgr.apply()
    assert qt_app.styleSheet() == ""
    mgr._high_contrast = False
    mgr.apply()
    assert qt_app.styleSheet() != ""


def test_high_contrast_outranks_the_light_dark_override(qt_app, make_manager):
    mgr = make_manager("light")
    mgr._high_contrast = True
    mgr.apply()
    assert qt_app.styleSheet() == ""


def test_text_scale_multiplies_the_type_scale_only(qt_app, make_manager):
    """Windows' Text size setting is honoured by no Qt mechanism at all, so
    the type tokens are multiplied at startup (§8.5)."""
    mgr = make_manager("dark", text_scale=1.5)
    plain = tokens("dark")
    assert mgr.tokens["fs_body"] == pytest.approx(float(plain["fs_body"]) * 1.5)
    assert mgr.tokens["sp_3"] == plain["sp_3"]
    assert mgr.tokens["bg"] == plain["bg"]


def test_tracked_caps_applies_tracking_qss_cannot(qt_app):
    t = tokens("dark")
    label = manager.tracked_caps("workspace", t)
    assert label.text() == "WORKSPACE"
    assert label.font().letterSpacing() == pytest.approx(float(t["tracking_caps"]))


def test_high_contrast_probe_answers_without_raising(qt_app):
    assert isinstance(manager.high_contrast_active(qt_app.styleHints()), bool)
