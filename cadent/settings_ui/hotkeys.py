"""WORKSPACE ▸ Hotkeys: one card, chords recorded rather than typed (spec §5.1, #63)."""

from __future__ import annotations

from PySide6.QtWidgets import QCheckBox, QComboBox, QSpinBox, QVBoxLayout, QWidget

from .. import a11y, settings
from .. import platform as platform_pkg
from ..chord import describe_combo
from .chord_recorder import ChordRecorderButton
from .context import PaneContext
from .widgets import Notice, card, label, page_title, row

# Where the desktop owns the binding (Linux's Wayland tiers, ADR 0008), the
# recorded chord is a suggestion and what fires is what the desktop reports.
DESKTOP_OWNS = "Your desktop owns this shortcut — change it in your desktop's settings."


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

        self.hotkey = recorder("hotkey")
        self.cleanup_hotkey = recorder("cleanup_hotkey")
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
        # What the desktop actually bound, where it owns the binding — and
        # which chord it substituted when the stored one cannot bind there
        # (a modifiers-only chord on Wayland, spec §4).
        self.desktop_binding = label("", "RowHint")
        self.desktop_binding.setVisible(False)
        layout.addWidget(self.desktop_binding)
        layout.addStretch()
        self.refresh_bindings()

        self.mode.currentIndexChanged.connect(
            lambda _i: ctx.set("hotkey_mode", self.mode.currentData()))
        # The one auto-repeating control in the app. Coalesced, because under
        # instant apply holding the arrow would rebuild the hotkey listener on
        # every tick — the coalesce gates the engine restart, not the disk.
        self.min_hold.valueChanged.connect(
            lambda value: ctx.set("min_hold_ms", value, coalesce=True))
        self.min_hold.editingFinished.connect(ctx.store.flush)

    def refresh_bindings(self) -> None:
        bindings = dict(platform_pkg.current().hotkey_tap.bindings())
        if not bindings:
            self.desktop_binding.setVisible(False)
            return
        caps = platform_pkg.current().capabilities
        parts = [DESKTOP_OWNS]
        dictate = bindings.get("dictate")
        if dictate:
            if dictate.startswith("<"):
                parts.append(f"The stored chord can't bind here, so this session uses "
                             f"{describe_combo(dictate, caps.modifier_captions)}.")
            else:
                parts.append(f"Dictation is bound to {dictate}.")
        cleanup = bindings.get("cleanup")
        if cleanup:
            if cleanup.startswith("<"):
                parts.append(f"Cleanup uses "
                             f"{describe_combo(cleanup, caps.modifier_captions)}.")
            else:
                parts.append(f"Cleanup is bound to {cleanup}.")
        self.desktop_binding.setText(" ".join(parts))
        self.desktop_binding.setVisible(True)

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
