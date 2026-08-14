"""The overlay widget: geometry, realization and the meter (spec §4).

The state machine is tested in test_pill.py without a screen. What is left
here is what only a real widget can answer.
"""

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication

from cadent import pill
from cadent.config import Config
from cadent.overlay import Overlay
from cadent.theme.tokens import tokens


@pytest.fixture
def overlay(styled, fake_platform):
    # `styled`, because the pill elides in the stylesheet's font — unstyled,
    # the label measures in whatever the machine's default font is (#143).
    # A fake platform, because these are app-logic tests: whether *this*
    # machine has animations enabled is the adapter's business, and a CI
    # runner answers differently than a desktop.
    widget = Overlay(Config(), tokens("dark"), platform=fake_platform)
    yield widget
    widget.hide()
    widget.deleteLater()


# ---- click-through and screen-reader posture -------------------------------

def test_the_pill_is_click_through_at_all_times(overlay):
    """PRD §5.7: there is never a dead zone that eats clicks or a stray drag
    that relocates the overlay. Moving it is a deliberate Settings mode."""
    assert overlay.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    overlay.show_recording()
    assert overlay.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)


def test_the_pill_never_takes_focus(overlay):
    assert overlay.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus
    assert overlay.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)


def test_the_pill_is_marked_as_a_tool_window_not_announced(overlay):
    """An always-on-top tool window with no focus and no interaction (§8.4)."""
    assert overlay.windowFlags() & Qt.WindowType.Tool
    assert overlay.accessibleName() == ""


def test_the_pill_survives_the_app_being_inactive(overlay):
    """macOS hides Qt.Tool windows whenever the application is inactive — and
    Cadent is always inactive while dictating (the target app owns focus),
    so without this attribute the pill never shows when it matters (#158).
    Documented as macOS-only and ignored elsewhere, so no platform gate."""
    assert overlay.testAttribute(
        Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow)


# ---- realization at startup ------------------------------------------------

def test_realize_creates_the_native_window_while_staying_hidden(overlay):
    """Without this the first pill of every session pays window-creation and
    first-paint cost no later one does."""
    overlay.realize()
    assert overlay.isVisible() is False
    assert overlay.windowOpacity() == 1.0
    assert overlay.winId()          # a native handle exists


def test_realizing_twice_is_harmless(overlay):
    overlay.realize()
    overlay.realize()
    assert overlay.isVisible() is False


# ---- geometry --------------------------------------------------------------

def test_the_top_level_exceeds_the_pill_by_the_shadow_margin(overlay):
    """A geometric consequence: the shadow needs room, so the translucent
    top-level is 96px tall for a 40px pill."""
    t = tokens("dark")
    overlay.show_recording()
    expected = int(t["pill_h"]) + 2 * int(t["pill_shadow_margin"])
    assert overlay.height() == expected


def test_place_offsets_by_the_shadow_margin_so_the_pill_sits_at_its_margin(overlay):
    """Without the offset the pill floats 28px above its bottom margin."""
    t = tokens("dark")
    overlay.show_recording()
    available = overlay._target_screen().availableGeometry()
    pill_bottom = overlay.geometry().bottom() - int(t["pill_shadow_margin"])
    assert abs((available.bottom() - pill_bottom)
               - int(t["pill_margin_bottom"])) <= 1


def test_width_is_clamped_between_min_and_max(overlay):
    t = tokens("dark")
    margin = 2 * int(t["pill_shadow_margin"])
    overlay.show_recording()
    assert overlay.width() - margin >= int(t["pill_min_w"])

    overlay.show_failure("Speech model still loading")
    assert overlay.width() - margin <= int(t["pill_max_w"])


def test_a_custom_anchor_is_read_as_a_fraction_of_available_geometry(qt_app):
    """Normalized, not absolute pixels, so a moved pill survives a monitor
    being unplugged (§4.6)."""
    config = Config(overlay_position_custom=True, overlay_anchor_x=0.2,
                    overlay_anchor_y=0.3)
    widget = Overlay(config, tokens("dark"))
    widget.show_recording()
    available = widget._target_screen().availableGeometry()
    t = tokens("dark")
    centre_x = widget.geometry().center().x()
    expected = available.left() + 0.2 * available.width()
    assert abs(centre_x - expected) <= int(t["pill_max_w"])
    assert centre_x < available.center().x()
    widget.hide()
    widget.deleteLater()


def test_the_pill_is_clamped_inside_the_screen(qt_app):
    """A pill dragged near an edge can't grow off-screen."""
    config = Config(overlay_position_custom=True, overlay_anchor_x=1.0,
                    overlay_anchor_y=1.0)
    widget = Overlay(config, tokens("dark"))
    widget.show_failure("Mic unavailable")
    available = widget._target_screen().availableGeometry()
    margin = int(tokens("dark")["pill_shadow_margin"])
    frame = widget.geometry().adjusted(margin, margin, -margin, -margin)
    assert frame.left() >= available.left()
    assert frame.right() <= available.right()
    assert frame.bottom() <= available.bottom()
    widget.hide()
    widget.deleteLater()


def test_the_screen_is_resolved_rather_than_assumed_primary(overlay):
    """`self.screen()` on a never-shown widget is always the *primary* screen
    — dictate into a window on a second monitor and the pill appeared where
    you weren't looking."""
    assert overlay._target_screen() in QGuiApplication.screens()


# ---- rendering the states --------------------------------------------------

def test_recording_shows_the_meter_and_the_mode_glyph(overlay):
    overlay.show_recording()
    assert overlay._meter.isVisible()
    assert overlay._glyph.isVisible()
    assert overlay._glyph.kind == pill.MIC
    assert overlay._label.isVisible() is False


def test_cleanup_changes_the_glyph(overlay):
    overlay.set_cleanup(True)
    overlay.show_recording()
    assert overlay._glyph.kind == pill.CLEANUP


def test_cleaning_up_names_the_second_phase(overlay):
    overlay.show_cleaning()
    assert overlay._label.text() == "Cleaning up"
    assert overlay._meter.mode == pill.INDETERMINATE


def test_cancelled_collapses_to_a_small_neutral_pill(overlay):
    overlay.show_cancelled()
    assert overlay._glyph.isVisible() is False
    assert overlay._meter.isVisible() is False
    assert overlay._label.text() == "Cancelled"


def test_failure_swaps_in_the_warning_glyph_and_danger_style(overlay):
    overlay.show_failure("Mic unavailable")
    assert overlay._glyph.kind == pill.WARNING
    assert overlay._label.objectName() == pill.DANGER


def test_hiding_stops_the_frame_timer(qt_app):
    widget = Overlay(Config(), {**tokens("dark"), "min_visible_ms": 0})
    widget.show_recording()
    assert widget._frame_timer.isActive()
    widget.hide_overlay()
    assert widget._frame_timer.isActive() is False
    widget.deleteLater()


def test_the_minimum_visible_floor_keeps_the_meter_running_until_it_expires(overlay):
    """The floor defers the exit rather than cancelling it, so the pill is
    still on screen and the meter is still information."""
    overlay.show_recording()
    overlay.hide_overlay()
    assert overlay.isVisible() is True
    assert overlay._frame_timer.isActive() is True


def test_the_hide_toggle_suppresses_activity_but_not_failure(qt_app):
    widget = Overlay(Config(show_overlay=False), tokens("dark"))
    widget.show_recording()
    assert widget.isVisible() is False
    widget.show_failure("Mic unavailable")
    assert widget._label.text() == "Mic unavailable"
    widget.hide()
    widget.deleteLater()


# ---- the meter -------------------------------------------------------------

def test_bars_never_fully_collapse(overlay):
    """A meter at literal zero is indistinguishable from a frozen one."""
    overlay.show_recording()
    overlay.level_source = lambda: 0.0
    overlay._meter.tick()
    assert all(bar >= 0.0 for bar in overlay._meter._bars)
    assert overlay._meter.height() == int(tokens("dark")["meter_span"])


def test_the_meter_decays_rather_than_snapping_to_zero(overlay):
    """Without decay the bars flicker at 30 fps; with it the meter looks alive
    between syllables."""
    overlay.show_recording()
    overlay.level_source = lambda: 0.3
    overlay._meter.tick()
    loud = list(overlay._meter._bars)
    overlay.level_source = lambda: 0.0
    overlay._meter.tick()
    quiet = list(overlay._meter._bars)
    assert any(q > 0 for q in quiet)
    assert all(q <= loud_bar for q, loud_bar in zip(quiet, loud, strict=True))


def test_silence_is_only_called_after_the_hold_time(overlay):
    overlay.show_recording()
    overlay.level_source = lambda: 0.0
    overlay._meter.tick()
    assert overlay._meter.silent is False        # not yet — 2 s has to pass


def test_the_meter_morphs_into_the_sweep_in_the_same_geometry(overlay):
    overlay.show_recording()
    size = overlay._meter.size()
    overlay.show_transcribing()
    assert overlay._meter.mode == pill.INDETERMINATE
    assert overlay._meter.size() == size


def test_the_sweep_moves_between_frames(overlay):
    overlay.show_transcribing()
    overlay._meter.tick()
    first = list(overlay._meter._bars)
    overlay._meter._phase += 0.25
    overlay._meter.tick()
    assert list(overlay._meter._bars) != first


# ---- theming ---------------------------------------------------------------

def test_the_pill_keeps_its_own_chrome_so_it_survives_high_contrast(overlay):
    """It is a HUD over other apps with its own non-inverting surface; forcing
    it into the system contrast palette would put a bright slab over the
    user's document (§4.6)."""
    assert "overlayPill" in overlay.pill.styleSheet()
    assert tokens("dark")["hud_surface"] in overlay.pill.styleSheet()


def test_switching_theme_gives_the_pill_a_different_dark_never_a_light_one(overlay):
    overlay.set_tokens(tokens("light"))
    assert tokens("light")["hud_surface"] in overlay.pill.styleSheet()
    assert tokens("dark")["hud_surface"] not in overlay.pill.styleSheet()


# ---- motion (§4.6, §4.7) ---------------------------------------------------

def test_the_pill_is_near_opaque_not_translucent(overlay):
    """It sits over white pages, video and code: legibility beats
    translucency, and there is no subpixel crispness left to trade away."""
    assert overlay.pill.alpha == int(tokens("dark")["hud_alpha"])
    assert 240 <= overlay.pill.alpha < 255


def test_the_alpha_follows_the_theme(overlay):
    overlay.set_tokens(tokens("light"))
    assert overlay.pill.alpha == int(tokens("light")["hud_alpha"])


def test_a_width_change_tweens_rather_than_snapping(overlay):
    """A failure label must not snap the pill wider under your eye."""
    overlay.show_recording()
    narrow = overlay.pill.width()
    overlay.show_failure("Speech model still loading")
    assert overlay._width_anim is not None
    assert overlay._width_anim.startValue() == narrow
    assert overlay._width_anim.duration() == int(tokens("dark")["width_tween_ms"])


def test_the_first_appearance_never_tweens(overlay):
    """There is no previous width to grow from."""
    overlay.show_failure("Mic unavailable")
    assert overlay._width_anim is None


def test_a_long_failure_label_is_elided_rather_than_growing_the_pill(overlay):
    """If a failure label doesn't fit, the copy is wrong, not the pill."""
    overlay.show_failure("A failure message far longer than any copy anyone "
                         "has ever proposed for this surface")
    margin = 2 * int(tokens("dark")["pill_shadow_margin"])
    assert overlay.width() - margin <= int(tokens("dark")["pill_max_w"])
    assert overlay._label.text().endswith("…")
