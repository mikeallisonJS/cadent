"""WORKSPACE ▸ Hotkeys: one card, chords recorded rather than typed (spec §5.1, #63)."""

from __future__ import annotations

from PySide6.QtWidgets import QCheckBox, QComboBox, QSpinBox, QVBoxLayout, QWidget

from .. import a11y, settings
from .. import platform as platform_pkg
from ..chord import describe_combo
from ..hotkey import runnable_combo
from .chord_recorder import ChordRecorderButton
from .context import PaneContext
from .widgets import Notice, card, label, page_title, row


class HotkeysPane(QWidget):
    def __init__(self, ctx: PaneContext) -> None:
        super().__init__()
        self.ctx = ctx
        self.setObjectName("Pane")
        t = ctx.tokens
        caps = platform_pkg.current().capabilities
        layout = QVBoxLayout(self)
        layout.setContentsMargins(int(t["sp_6"]), int(t["sp_5"]),
                                  int(t["sp_6"]), int(t["sp_5"]))
        layout.setSpacing(int(t["sp_3"]))

        # Not a config field: it only says how the *next* recording names a
        # modifier, and the chord itself shows which it got.
        self.sided = QCheckBox()

        def recorder(field: str) -> ChordRecorderButton:
            return ChordRecorderButton(
                getattr(ctx.config, field),
                capture_keys=ctx.capture_keys, table=caps.keycode_table,
                on_recorded=lambda chord: self._commit_chord(field, chord),
                on_problem=self._show_problem, sided=self.sided.isChecked)

        self.hotkey_note = caps.hotkey_note
        if self.hotkey_note is None:
            self.hotkey = recorder("hotkey")
            self.cleanup_hotkey = recorder("cleanup_hotkey")
        else:
            # The desktop owns the binding: say what it reports, not what the
            # file asks for, and never offer a recorder that cannot hear keys.
            self.hotkey = self._owned("dictate", "hotkey", caps)
            self.cleanup_hotkey = self._owned("cleanup", "cleanup_hotkey", caps)
        self.mode = QComboBox()
        self.mode.addItem("Hold to dictate", "hold")
        self.mode.addItem("Tap to start / tap to stop", "toggle")
        self.mode.addItem("Tap or hold", "tap_or_hold")
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
        if self.hotkey_note is not None:
            layout.addWidget(label(self.hotkey_note, "RowDesc"))
            changed = [field for field in ("hotkey", "cleanup_hotkey")
                       if self._run_combo(field, caps) != getattr(ctx.config, field)]
            if changed and caps.hotkey_available:
                self.config_note = label(
                    "Your config.json names a chord a desktop shortcut can't "
                    "use, so Cadent is asking for its default this session. "
                    "The file is left as it is.", "RowHint")
                layout.addWidget(self.config_note)
        win = caps.modifier_captions.get("cmd", "Win")
        layout.addWidget(card([
            row(t, "Dictation hotkey", self.hotkey,
                desc="Click, then press the keys you'll tap or hold to dictate",
                hint=settings.restart_hint("hotkey")),
            row(t, "Hotkey mode", self.mode,
                desc="Held, tapped, or either: a tap latches, a hold releases",
                hint=settings.restart_hint("hotkey_mode")),
            row(t, "Cleanup hotkey", self.cleanup_hotkey,
                desc="Click, then tap the keys that turn cleanup on or off",
                hint=settings.restart_hint("cleanup_hotkey")),
            row(t, "Tell left from right", self.sided,
                desc=f"Record the side you press — Right Ctrl rather than "
                     f"either Ctrl, Left {win} rather than either {win}"),
            row(t, "Minimum hold", self.min_hold,
                desc="Shorter presses are discarded rather than dictated",
                hint=settings.restart_hint("min_hold_ms")),
        ]))
        layout.addWidget(self.error)
        layout.addStretch()

        self.mode.currentIndexChanged.connect(
            lambda _i: ctx.set("hotkey_mode", self.mode.currentData()))
        # The one auto-repeating control in the app. Coalesced, because under
        # instant apply holding the arrow would rebuild the hotkey listener on
        # every tick — the coalesce gates the engine restart, not the disk.
        self.min_hold.valueChanged.connect(
            lambda value: ctx.set("min_hold_ms", value, coalesce=True))
        self.min_hold.editingFinished.connect(ctx.store.flush)

    def _run_combo(self, field: str, caps) -> str:
        default = (caps.default_combo if field == "hotkey"
                   else caps.default_cleanup_combo)
        return runnable_combo(getattr(self.ctx.config, field), default, caps)

    def _owned(self, shortcut: str, field: str, caps):
        """A read-only line for a chord the desktop binds."""
        if not caps.hotkey_available:
            return label("Off", "RowDesc")
        reported = platform_pkg.current().hotkey_tap.bindings().get(shortcut)
        return label(reported or describe_combo(
            self._run_combo(field, caps), caps.modifier_captions), "RowDesc")

    # ---- recording outcomes -------------------------------------------------

    def _commit_chord(self, field: str, chord: str) -> None:
        """A recorded chord is parseable by construction — the recorder only
        spells parts the table knows — so the write is unconditional. The
        listener is rebuilt on the commit (`hotkeys` engine), never mid-press."""
        self.error.setVisible(False)
        widget = self.hotkey if field == "hotkey" else self.cleanup_hotkey
        widget.set_chord(chord)
        if chord != getattr(self.ctx.config, field):
            self.ctx.set(field, chord)

    def _show_problem(self, problem: str) -> None:
        """The keys pressed are not a hotkey; say why, keep the old chord."""
        self.error.setText(problem)
        self.error.setVisible(True)
        a11y.announce(self.error, problem)
