"""WORKSPACE ▸ Hotkeys: one card, with inline validity (spec §5.1)."""

from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QLineEdit, QSpinBox, QVBoxLayout, QWidget

from .. import settings
from ..chord import parse_combo
from .context import PaneContext
from .widgets import Notice, card, label, page_title, row


class HotkeysPane(QWidget):
    def __init__(self, ctx: PaneContext) -> None:
        super().__init__()
        self.ctx = ctx
        self.setObjectName("Pane")
        t = ctx.tokens
        layout = QVBoxLayout(self)
        layout.setContentsMargins(int(t["sp_6"]), int(t["sp_5"]),
                                  int(t["sp_6"]), int(t["sp_5"]))
        layout.setSpacing(int(t["sp_3"]))

        self.hotkey = QLineEdit(ctx.config.hotkey)
        self.cleanup_hotkey = QLineEdit(ctx.config.cleanup_hotkey)
        self.mode = QComboBox()
        self.mode.addItem("Hold to dictate", "hold")
        self.mode.addItem("Tap to start / tap to stop", "toggle")
        self.mode.setCurrentIndex(max(self.mode.findData(ctx.config.hotkey_mode), 0))
        self.min_hold = QSpinBox()
        self.min_hold.setRange(0, 2000)
        self.min_hold.setSingleStep(50)
        self.min_hold.setSuffix(" ms")
        self.min_hold.setValue(ctx.config.min_hold_ms)

        self.error = label("", "Danger")
        self.error.setVisible(False)

        # A quiet line for anything config.json disagrees with us
        # about in this pane's own fields (§7.4, §7.5).
        self.notice = Notice(t, "", [])
        self.notice.setVisible(False)
        layout.addWidget(self.notice)
        layout.addWidget(page_title("Hotkeys"))
        layout.addWidget(card([
            row(t, "Dictation hotkey", self.hotkey,
                desc="Hold this and speak; release to insert",
                hint=settings.restart_hint("hotkey")),
            row(t, "Hotkey mode", self.mode,
                desc="Whether the key is held or tapped",
                hint=settings.restart_hint("hotkey_mode")),
            row(t, "Cleanup hotkey", self.cleanup_hotkey,
                desc="Tap this to turn cleanup on or off",
                hint=settings.restart_hint("cleanup_hotkey")),
            row(t, "Minimum hold", self.min_hold,
                desc="Shorter presses are discarded rather than dictated",
                hint=settings.restart_hint("min_hold_ms")),
        ]))
        layout.addWidget(self.error)
        layout.addStretch()

        # Writes fire on **field commit** — focus-out or Enter — never per
        # keystroke: a half-typed chord would tear down the listener on every
        # character.
        self.hotkey.editingFinished.connect(lambda: self._commit_chord("hotkey"))
        self.cleanup_hotkey.editingFinished.connect(
            lambda: self._commit_chord("cleanup_hotkey"))
        self.hotkey.textChanged.connect(self._validate)
        self.cleanup_hotkey.textChanged.connect(self._validate)
        self.mode.currentIndexChanged.connect(
            lambda _i: ctx.set("hotkey_mode", self.mode.currentData()))
        # The one auto-repeating control in the app. Coalesced, because under
        # instant apply holding the arrow would rebuild the hotkey listener on
        # every tick — the coalesce gates the engine restart, not the disk.
        self.min_hold.valueChanged.connect(
            lambda value: ctx.set("min_hold_ms", value, coalesce=True))
        self.min_hold.editingFinished.connect(ctx.store.flush)

    # ---- validity ---------------------------------------------------------

    def _problem(self) -> str | None:
        for caption, field in (("Dictation hotkey", self.hotkey),
                               ("Cleanup hotkey", self.cleanup_hotkey)):
            try:
                parse_combo(field.text().strip())
            except ValueError:
                return f"{caption} isn't a valid chord (e.g. <ctrl>+<cmd>)"
        return None

    def _validate(self) -> None:
        problem = self._problem()
        self.error.setText(problem or "")
        self.error.setVisible(problem is not None)

    def _commit_chord(self, field: str) -> None:
        """An invalid chord is not written — it would disarm the hotkey.

        This is the one place the pane withholds a write, and it is not the
        §5.4 "nothing is refused under the user's hands" case: an unparseable
        chord has no meaning to preserve, and writing it would leave the user
        with no way to dictate and no way back except a hand edit.
        """
        if self._problem() is not None:
            return
        widget = self.hotkey if field == "hotkey" else self.cleanup_hotkey
        value = widget.text().strip()
        if value != getattr(self.ctx.config, field):
            self.ctx.set(field, value)
