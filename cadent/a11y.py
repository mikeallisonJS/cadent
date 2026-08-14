"""Keyboard and screen reader support (spec §8).

The bar: keyboard-complete, correctly named, High-Contrast-safe. Every
function reachable and operable by keyboard with visible focus; every control
carrying a correct accessible name and role; a contrast theme never leaving
the user staring at violet (that last one lives in `theme.manager`).

This is greenfield, not a repair — there was no accessibility code at all in
`cadent/*.py` before M4: no setAccessibleName, no setTabOrder, no setBuddy,
no setFocusPolicy, no mnemonics.

Screen-reader support is correct-by-construction from stock Qt widgets:
restyling a QCheckBox into a toggle pill repaints the indicator, it does not
touch the CheckBox role underneath. What is *not* free is the name, which is
what `bind_label` and `assert_named` are for.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QAccessible, QAccessibleEvent
from PySide6.QtWidgets import QLabel, QWidget

from .theme.manager import repolish

log = logging.getLogger(__name__)

# Focus arriving for one of these reasons is keyboard focus and gets a ring.
# A mouse click does not — measured: Fusion changes 284px on a QPushButton
# under keyboard focus and **0px** under mouse focus, so ringing on click
# would be a behaviour change, not a styling one.
_KEYBOARD_REASONS = (
    Qt.FocusReason.TabFocusReason,
    Qt.FocusReason.BacktabFocusReason,
    Qt.FocusReason.ShortcutFocusReason,
)


class FocusVisibleFilter(QObject):
    """`:focus-visible` for Qt, which QSS does not have.

    Sets a `kbdFocus` dynamic property that the stylesheet's focus rules
    select on, and repolishes so the change takes effect — a dynamic property
    used in a selector does nothing until the widget is unpolished/polished.
    """

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        kind = event.type()
        if kind == QEvent.Type.FocusIn and isinstance(obj, QWidget):
            self._mark(obj, event.reason() in _KEYBOARD_REASONS)
        elif kind == QEvent.Type.FocusOut and isinstance(obj, QWidget):
            self._mark(obj, False)
        return False

    @staticmethod
    def _mark(widget: QWidget, keyboard: bool) -> None:
        if widget.property("kbdFocus") == keyboard:
            return
        widget.setProperty("kbdFocus", keyboard)
        repolish(widget)


def install(app) -> FocusVisibleFilter:
    """Install the focus-visible filter app-wide. Returns it so the caller can
    keep it alive — an event filter that gets collected stops filtering."""
    focus_filter = FocusVisibleFilter(app)
    app.installEventFilter(focus_filter)
    return focus_filter


# ---- naming ----------------------------------------------------------------

def bind_label(label: QLabel, control: QWidget) -> None:
    """Give a control its visible label as its accessible name.

    `setBuddy` works without a mnemonic, so it costs nothing visually, and it
    strips the `&` when one is present. The decisive property is that the name
    cannot drift from the visible label, because it *is* the label — there is
    no second copy of the string.

    Today a bare QComboBox, a bare QLineEdit and every toggle report `name=''`
    — the toggle because it is a text-less QCheckBox with its label in a
    separate QLabel.
    """
    label.setBuddy(control)
    if not control.accessibleName():
        control.setAccessibleName(label.text().replace("&", ""))


def describe(widget: QWidget, description: str) -> None:
    """Subtitles, restart warnings and the reason a control is behaving oddly.

    A heavy field's `restarts <engine>` text goes here so it is announced
    *before* the change — which is what a change-of-context warning has to do.
    """
    widget.setAccessibleDescription(description)


def focusables(root: QWidget) -> list[QWidget]:
    """Every control in a window that can take keyboard focus.

    Qt's compound controls are counted once, as themselves: a QComboBox owns
    an internal QLineEdit and a popup QListView, and both inherit the combo's
    accessible identity rather than needing names of their own.
    """
    found = []
    for child in root.findChildren(QWidget):
        if (child.focusPolicy() != Qt.FocusPolicy.NoFocus
                and child.isEnabled()
                and not child.isWindow()
                and not _inside_compound_control(child, root)):
            found.append(child)
    return found


def _inside_compound_control(widget: QWidget, root: QWidget) -> bool:
    from PySide6.QtWidgets import QAbstractSpinBox, QComboBox

    parent = widget.parentWidget()
    while parent is not None and parent is not root:
        if isinstance(parent, QComboBox | QAbstractSpinBox):
            return True
        parent = parent.parentWidget()
    return False


def unnamed(root: QWidget) -> list[QWidget]:
    """Focusable controls reporting an empty accessible name.

    The enforcement seam for §8.3: a test walks every window and asserts this
    is empty. That is what catches the control built outside the card-row
    factory — exactly how the text-less toggle got in.
    """
    return [w for w in focusables(root) if not _name_of(w)]


def _name_of(widget: QWidget) -> str:
    name = widget.accessibleName()
    if name:
        return name
    # Qt derives a name from the widget's own text when it has one.
    text = getattr(widget, "text", None)
    return (text() or "").replace("&", "") if callable(text) else ""


# ---- announcements ---------------------------------------------------------

def announce(widget: QWidget, message: str | None = None) -> None:
    """Say something to a screen reader, if one is listening.

    One primitive covers every case in the spec — wizard page changes, engine
    restarts, try-it results, the Undo strip appearing. Free when no AT is
    attached: `QAccessible.isActive()` is false and Qt no-ops the update.
    """
    if message is not None:
        widget.setAccessibleName(message)
    try:
        QAccessible.updateAccessibility(
            QAccessibleEvent(widget, QAccessible.Event.Alert))
    except Exception:       # pragma: no cover - never break the UI to announce
        log.debug("accessibility alert failed", exc_info=True)
