"""The building blocks every pane is made of (spec §5.1, §8.3).

Stock Qt controls do not pass — every indicator is QSS-restyled, and three of
them are generated pixmaps. But the *semantics* underneath are untouched:
a restyled QCheckBox still reports the CheckBox role, which is what makes
screen-reader support correct by construction.

What is not free is the name. `row()` is the one card-row factory, and it
binds the visible label to its control as a buddy — so a control built here
can never report `name=''`. The enforcement is a test that walks every window
and asserts none does; the factory is what makes passing it the default.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QPalette, QShortcut
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStyle,
    QStyleOptionComboBox,
    QStylePainter,
    QVBoxLayout,
    QWidget,
)

from .. import a11y, platform
from ..theme.manager import tracked_caps

# How much of a row a combo whose items are *data* — device names, filenames —
# may claim, in characters. Characters rather than pixels so the cap scales
# with Windows' Text size setting exactly as the label beside it does (§8.5);
# a pixel maximum is the same bug at 225% that this is fixing at 100%.
COMBO_CHARS = 22


def open_in_explorer(path: Path) -> None:
    """Show a file in the user's own editor/shell. Never blocks the UI.

    Every "Open <file>" link in Settings goes through here — three panes want
    it, so it belongs with the shared widgets rather than in whichever pane
    happened to need it first.
    """
    platform.current().desktop.open_path(path)


def preview(text: str, limit: int = 90) -> str:
    """One flat line, ellipsised. Used wherever a multi-line body has to fit
    in a row."""
    flat = " ".join(text.split())
    return flat[:limit] + "…" if len(flat) > limit else flat


def label(text: str, name: str = "", wrap: bool = True) -> QLabel:
    widget = QLabel(text)
    if name:
        widget.setObjectName(name)
    widget.setWordWrap(wrap)
    return widget


def page_title(text: str) -> QLabel:
    """The pane's own name, in display type.

    Distinct from `caps()`, which names a *card* inside a pane. The sidebar
    already says which group you are in, so repeating the group here — as the
    first cut did — reads as the nav rail leaking into the page.
    """
    return label(text, "PageTitle", wrap=False)


def caps(text: str, t: dict, name: str = "Caps") -> QLabel:
    """Tracked capitals. Qt QSS has no letter-spacing and ignores it silently,
    so this cannot come from the stylesheet (§1.5)."""
    return tracked_caps(text, t, name)


class CappedCombo(QComboBox):
    """A combo that keeps to its share of the row.

    Stock `QComboBox` asks for the width of its widest **item** rather than of
    its current selection, and `row()` hands it what it asks for — so one real
    Windows device name (they run to 44 characters) took half the card and left
    the Microphone row's description and restart hint wrapping down a ~90px
    column (#106). Nine lines of hint reads as damage rather than as
    information, and the hint is the thing saying the change was not lost.

    **The combo's own text elides; the description does not wrap.** A truncated
    device name is still recognisable where a nine-line label is not. The cap
    is on the control and never on the model, so the whole name is still in the
    list — and it comes back as the tooltip whenever it doesn't fit.

    Named for the decision a caller has to make rather than for the slot: the
    question is *should this combo's width be capped*, and the answer is yes
    only where its items are **data** — device names, model filenames. A combo
    over a fixed set of short captions (Theme, Retention, Runtime) has nothing
    to cap and would only gain a tooltip it doesn't need.
    """

    def __init__(self) -> None:
        super().__init__()
        self.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.setMinimumContentsLength(COMBO_CHARS)
        self.currentIndexChanged.connect(lambda _i: self._update_tooltip())

    # ---- eliding -----------------------------------------------------------

    def _option(self) -> QStyleOptionComboBox:
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        return option

    def _elided(self, text: str, option: QStyleOptionComboBox) -> str:
        """`text` at the width the style leaves for it.

        The option is passed in rather than rebuilt because the caller in
        `paintEvent` already has one, and it is the same measurement either
        way — the field rect is where the label is actually drawn.
        """
        field = self.style().subControlRect(
            QStyle.ComplexControl.CC_ComboBox, option,
            QStyle.SubControl.SC_ComboBoxEditField, self)
        return self.fontMetrics().elidedText(
            text, Qt.TextElideMode.ElideRight, field.width())

    def _update_tooltip(self) -> None:
        """The whole name, but only when the row can't show it.

        A tooltip repeating text already on screen is noise, so this is a
        consequence of the eliding rather than a second decision.
        """
        text = self.currentText()
        self.setToolTip("" if self._elided(text, self._option()) == text else text)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._update_tooltip()

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt override
        """Elide rather than let the style cut the text off mid-glyph.

        `CE_ComboBoxLabel` *clips* to the edit field and draws no ellipsis, so
        the cap on its own would slice a device name in half with nothing to
        say it had. Drawn through `QStylePainter`, so the app stylesheet still
        owns every pixel of the frame and the arrow.
        """
        painter = QStylePainter(self)
        painter.setPen(self.palette().color(QPalette.ColorRole.Text))
        option = self._option()
        painter.drawComplexControl(QStyle.ComplexControl.CC_ComboBox, option)
        option.currentText = self._elided(option.currentText, option)
        painter.drawControl(QStyle.ControlElement.CE_ComboBoxLabel, option)


def row(t: dict, title: str, control: QWidget | None = None, *,
        desc: str = "", hint: str = "") -> QFrame:
    """One setting: title, optional subtitle, optional restart hint, control.

    The subtitle becomes the control's `accessibleDescription`, and a restart
    hint is appended to it — announced *before* the change, which is what a
    change-of-context warning has to do (§8.4).
    """
    frame = QFrame()
    frame.setObjectName("Row")
    layout = QHBoxLayout(frame)
    layout.setContentsMargins(int(t["row_pad_h"]), int(t["row_pad_v"]),
                              int(t["row_pad_h"]), int(t["row_pad_v"]))
    left = QVBoxLayout()
    left.setSpacing(2)
    title_label = label(title, "RowTitle", wrap=False)
    left.addWidget(title_label)
    if desc:
        left.addWidget(label(desc, "RowDesc"))
    # The restart hint sits *under* the description rather than in a column of
    # its own: given its own column it competes with the description for
    # width, and the description is what wraps.
    if hint:
        left.addWidget(label(hint, "RowHint"))
    layout.addLayout(left, 1)
    layout.addSpacing(int(t["sp_3"]))
    if control is not None:
        layout.addWidget(control, 0, Qt.AlignmentFlag.AlignVCenter)
        a11y.bind_label(title_label, control)
        description = " ".join(part for part in (desc, hint) if part)
        if description:
            a11y.describe(control, description)
    return frame


def card(rows: list[QWidget]) -> QFrame:
    """A rounded section card with hairline row separators."""
    frame = QFrame()
    frame.setObjectName("Card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(0, 4, 0, 4)
    layout.setSpacing(0)
    for i, child in enumerate(rows):
        if i:
            separator = QFrame()
            separator.setObjectName("RowSep")
            separator.setFixedHeight(1)
            layout.addWidget(separator)
        layout.addWidget(child)
    return frame


def link(text: str, on_click: Callable[[], None]) -> QPushButton:
    button = QPushButton(text)
    button.setObjectName("Link")
    button.clicked.connect(on_click)
    return button


class Notice(QFrame):
    """A persistent inline message with actions — never a modal.

    CONTEXT.md's "toast, never a prompt" holds throughout: the unreadable
    config, the malformed vocabulary file and the divergence line are all
    inline error states with actions rather than dialogs.
    """

    def __init__(self, t: dict, text: str, actions: list[tuple[str, Callable]],
                 kind: str = "Banner") -> None:
        super().__init__()
        self.setObjectName(kind)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(int(t["sp_3"]), int(t["sp_2"]),
                                  int(t["sp_3"]), int(t["sp_2"]))
        layout.setSpacing(int(t["sp_2"]))
        self.message = label(text, "RowDesc")
        layout.addWidget(self.message, 1)
        for caption, handler in actions:
            layout.addWidget(link(caption, handler))

    def set_text(self, text: str) -> None:
        self.message.setText(text)


class UndoStrip(QFrame):
    """The visible face of Ctrl+Z, not the only way back (§8.3).

    A strip that disappears on a timer is unusable if reaching it costs
    several Tab presses, so the shortcut is the real mechanism and this is
    what makes it discoverable. It fires an Alert when it appears and **never
    takes focus**.
    """

    def __init__(self, t: dict) -> None:
        super().__init__()
        self.setObjectName("Undo")
        self._undo: Callable[[], None] | None = None
        layout = QHBoxLayout(self)
        layout.setContentsMargins(int(t["sp_3"]), int(t["sp_2"]),
                                  int(t["sp_3"]), int(t["sp_2"]))
        self.message = label("", "RowDesc")
        layout.addWidget(self.message, 1)
        self.button = link("Undo", self._fire)
        layout.addWidget(self.button)
        self.setVisible(False)

    def offer(self, text: str, undo: Callable[[], None]) -> None:
        self._undo = undo
        self.message.setText(text)
        self.setVisible(True)
        a11y.announce(self.message, text)

    def dismiss(self) -> None:
        self._undo = None
        self.setVisible(False)

    def undo(self) -> None:
        """What Ctrl+Z and the strip's own button both call."""
        self._fire()

    def _fire(self) -> None:
        undo, self._undo = self._undo, None
        self.setVisible(False)
        if undo is not None:
            undo()


def bind_table_keys(pane: QWidget, table: QWidget, remove: Callable[[], None],
                    undo: UndoStrip) -> None:
    """Stock table semantics plus Delete and a real Ctrl+Z (§8.3).

    The Undo strip is the *visible face* of the shortcut rather than the only
    way back: a strip that disappears on a timer is unusable if reaching it
    costs several Tab presses.
    """
    delete = QShortcut(QKeySequence(Qt.Key.Key_Delete), table)
    delete.setContext(Qt.ShortcutContext.WidgetShortcut)
    delete.activated.connect(remove)

    undo_shortcut = QShortcut(QKeySequence.StandardKey.Undo, pane)
    undo_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
    undo_shortcut.activated.connect(undo.undo)
