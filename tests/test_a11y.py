"""Keyboard focus, naming and announcements (spec §8)."""

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QFocusEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from cadent import a11y


@pytest.fixture
def window(qt_app):
    w = QWidget()
    layout = QVBoxLayout(w)
    w.button = QPushButton("Download model")
    w.combo = QComboBox()
    w.toggle = QCheckBox()
    for child in (w.button, w.combo, w.toggle):
        layout.addWidget(child)
    yield w
    w.deleteLater()


# ---- focus-visible ---------------------------------------------------------

def focus(qt_app, widget, reason, kind=QEvent.Type.FocusIn):
    """Deliver a focus event the way Qt would, through installed filters."""
    qt_app.sendEvent(widget, QFocusEvent(kind, reason))


def test_tab_focus_rings_and_mouse_focus_does_not(qt_app, window):
    """QSS has no :focus-visible, so a plain :focus rule would ring on mouse
    clicks — which is not what Fusion does (measured: 284px changed on a
    QPushButton under keyboard focus, 0px under mouse focus)."""
    focus_filter = a11y.FocusVisibleFilter()
    window.button.installEventFilter(focus_filter)
    window.combo.installEventFilter(focus_filter)

    focus(qt_app, window.button, Qt.FocusReason.MouseFocusReason)
    assert window.button.property("kbdFocus") is False

    focus(qt_app, window.combo, Qt.FocusReason.TabFocusReason)
    assert window.combo.property("kbdFocus") is True


def test_backtab_and_shortcut_count_as_keyboard(qt_app):
    focus_filter = a11y.FocusVisibleFilter()
    for reason in (Qt.FocusReason.BacktabFocusReason,
                   Qt.FocusReason.ShortcutFocusReason):
        button = QPushButton("x")
        button.installEventFilter(focus_filter)
        focus(qt_app, button, reason)
        assert button.property("kbdFocus") is True


def test_the_ring_clears_when_focus_leaves(qt_app, window):
    focus_filter = a11y.FocusVisibleFilter()
    window.button.installEventFilter(focus_filter)
    focus(qt_app, window.button, Qt.FocusReason.TabFocusReason)
    assert window.button.property("kbdFocus") is True
    focus(qt_app, window.button, Qt.FocusReason.TabFocusReason,
          QEvent.Type.FocusOut)
    assert window.button.property("kbdFocus") is False


def test_install_returns_the_filter_so_it_stays_alive(qt_app):
    """An event filter that gets collected silently stops filtering."""
    focus_filter = a11y.install(qt_app)
    assert isinstance(focus_filter, a11y.FocusVisibleFilter)
    qt_app.removeEventFilter(focus_filter)


# ---- naming ----------------------------------------------------------------

def test_a_buddy_gives_a_bare_control_its_visible_label(qt_app):
    """A bare QComboBox reports name='' — the buddy is the whole fix, and it
    cannot drift from the label because it *is* the label."""
    label = QLabel("Microphone")
    combo = QComboBox()
    assert combo.accessibleName() == ""
    a11y.bind_label(label, combo)
    assert combo.accessibleName() == "Microphone"
    assert label.buddy() is combo


def test_a_buddy_strips_the_mnemonic_marker(qt_app):
    label = QLabel("&Theme")
    combo = QComboBox()
    a11y.bind_label(label, combo)
    assert combo.accessibleName() == "Theme"


def test_a_buddy_never_overwrites_an_explicit_name(qt_app):
    combo = QComboBox()
    combo.setAccessibleName("Speech model")
    a11y.bind_label(QLabel("Model"), combo)
    assert combo.accessibleName() == "Speech model"


def test_a_text_less_toggle_is_the_control_the_walker_catches(window):
    """The toggle is a text-less QCheckBox with its label in a separate
    QLabel, which is exactly how an unnamed control got in before."""
    assert window.toggle in a11y.unnamed(window)
    a11y.bind_label(QLabel("Clean up transcripts"), window.toggle)
    assert window.toggle not in a11y.unnamed(window)


def test_a_control_with_its_own_text_is_already_named(window):
    assert window.button not in a11y.unnamed(window)


def test_the_walker_ignores_controls_that_cannot_take_focus(qt_app):
    root = QWidget()
    layout = QVBoxLayout(root)
    layout.addWidget(QLabel("just a caption"))
    assert a11y.unnamed(root) == []


def test_focusables_finds_every_focusable_descendant(window):
    found = a11y.focusables(window)
    for child in (window.button, window.combo, window.toggle):
        assert child in found


def test_describe_carries_the_restart_warning(qt_app):
    """Announced *before* the change, which is what a change-of-context
    warning has to do."""
    combo = QComboBox()
    a11y.describe(combo, "Speech engine restarts when you pick a model (~2 s)")
    assert "restarts" in combo.accessibleDescription()


# ---- announcements ---------------------------------------------------------

def test_announce_sets_the_message_and_dispatches(qt_app):
    status = QLabel()
    a11y.announce(status, "Step 3 of 6 — GPU support pack")
    assert status.accessibleName() == "Step 3 of 6 — GPU support pack"


def test_announce_without_a_message_leaves_the_name_alone(qt_app):
    status = QLabel()
    status.setAccessibleName("Recording")
    a11y.announce(status)
    assert status.accessibleName() == "Recording"


def test_announce_never_raises_without_an_assistive_tool(qt_app):
    a11y.announce(QLineEdit(), "anything")


# ---- text scaling ----------------------------------------------------------

def test_text_scale_is_a_sane_multiplier():
    """Absent means 100%; the setting itself tops out at 225%. The probe
    lives on the platform's DesktopEnv now (ADR 0005)."""
    from cadent import platform

    scale = platform.current().desktop.text_scale_factor()
    assert 1.0 <= scale <= 2.25
