"""TEXT ▸ App overrides (spec §5.5).

**The pane *is* `app_overrides` — flat, complete, and the only place per-app
injection is ever explained.** The 10 rows `_default_overrides()` writes on
first run are not noise: they are the answer to *"why does dictation paste
into my terminal?"*

No stored provenance flag beyond `learned`, which is how a row was *born*, not
a claim about its contents — any flag we invented would become a lie the first
time someone hand-edits a shipped row. Rows sort alphabetically in the view
and keep file order on disk; order here is semantically inert, because
`resolve_override` is first-match-wins and duplicates are prevented, so **no
drag handle is offered — reordering would be theater.**

> **Hard constraint.** This pane mutates the existing list in place and never
> rebinds `config.app_overrides`. Rebinding leaves the injector holding the
> old list, every edit becomes a no-op until restart, and the symptom ("my
> override does nothing") is indistinguishable from a typo'd executable name.
"""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import a11y, platform, settings
from ..config import AppOverride
from ..inject import parse_paste_chord
from .context import PaneContext
from .widgets import (
    Notice,
    UndoStrip,
    bind_table_keys,
    card,
    label,
    link,
    open_in_explorer,
    page_title,
    row,
)

LEARNED_TOOLTIP = "Added automatically after typing failed in this app."

EMPTY_STATE = ("No app overrides. Cadent types into every app, and adds "
               "one automatically if typing fails.")

# Notepad's 500 ms is the one number in the defaults that reads as a mystery.
NOTEPAD_SETTLE_NOTE = ("A cold Notepad can take over 150 ms to process a "
                       "paste; restoring the clipboard before then pastes the "
                       "wrong thing.")


class OverridesPane(QWidget):
    def __init__(self, ctx: PaneContext) -> None:
        super().__init__()
        self.ctx = ctx
        self.setObjectName("Pane")
        t = ctx.tokens
        layout = QVBoxLayout(self)
        layout.setContentsMargins(int(t["sp_6"]), int(t["sp_5"]),
                                  int(t["sp_6"]), int(t["sp_5"]))
        layout.setSpacing(int(t["sp_2"]))

        # A quiet line for anything config.json disagrees with us
        # about in this pane's own fields (§7.4, §7.5).
        self.notice = Notice(t, "", [])
        self.notice.setVisible(False)
        layout.addWidget(self.notice)
        layout.addWidget(page_title("App overrides"))
        layout.addWidget(label(
            "How Cadent inserts text into each app. Overrides Cadent "
            "ships, ones it learned, and ones you wrote all look alike here.",
            "RowDesc"))

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["App", "Strategy", ""])
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch)
        # The chip column fits the chip. Plain items, so the delegate's size
        # hint is the truth here — which is exactly what it is not for the
        # Strategy column below.
        self.table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAccessibleName("App overrides")
        self.table.verticalHeader().setVisible(False)
        self.table.currentCellChanged.connect(lambda *_: self._show_detail())
        layout.addWidget(self.table, 1)

        self.empty = label(EMPTY_STATE, "RowHint")
        layout.addWidget(self.empty)

        self.undo = UndoStrip(t)
        layout.addWidget(self.undo)

        # The strategy-scoped disclosure, mirroring the branch in inject.py.
        self.strategy = QComboBox()
        for value, caption in settings.STRATEGY_LABELS:
            self.strategy.addItem(caption, value)
        self.strategy.activated.connect(lambda _i: self._commit_strategy())

        # Wide enough for the identical combo that goes in the row (#101).
        # A too-narrow column does not clip a cell widget, it *moves* it: Qt
        # widens an editor narrower than its own minimum size hint rightward,
        # over the chip in the next column — which is how the only durable
        # explanation of auto-learn (§5.5) came to be invisible. Sized from
        # the combo rather than from `ResizeToContents`, which asks the
        # delegate, and the delegate knows nothing about a cell widget — the
        # same blind spot that makes row heights manual below.
        self.table.setColumnWidth(
            1, self.strategy.minimumSizeHint().width() + int(t["sp_3"]) * 2)

        self.chunk_size = QSpinBox()
        self.chunk_size.setRange(0, 4096)
        self.chunk_size.setSpecialValueText("Whole utterance")
        self.chunk_size.setSuffix(" characters")
        self.chunk_delay = QSpinBox()
        self.chunk_delay.setRange(0, 500)
        self.chunk_delay.setSuffix(" ms")
        self.paste_chord = QLineEdit()
        # "" in the config means the platform's own chord; show which one that is.
        self.paste_chord.setPlaceholderText(
            platform.current().capabilities.paste_chord)
        self.settle_delay = QSpinBox()
        self.settle_delay.setRange(0, 5000)
        self.settle_delay.setSingleStep(50)
        self.settle_delay.setSuffix(" ms")
        self.restore_clipboard = QCheckBox()

        self.type_rows = card([
            row(t, "Send in chunks of", self.chunk_size,
                desc="Some apps drop very large synthetic input"),
            row(t, "Delay between chunks", self.chunk_delay),
        ])
        self.paste_rows = card([
            row(t, "Paste shortcut", self.paste_chord,
                desc="The shortcut this app uses to paste — e.g. ctrl+shift+v"),
            row(t, "Wait before restoring the clipboard", self.settle_delay),
            row(t, "Put my clipboard back afterwards", self.restore_clipboard),
        ])
        self.notify_note = label(
            "Nothing is inserted into this app. The transcript still lands in "
            "History.", "RowHint")

        self.chord_warning = label("", "Warning")
        self.chord_warning.setVisible(False)
        self.settle_note = label(NOTEPAD_SETTLE_NOTE, "RowHint")
        self.settle_note.setVisible(False)

        self.detail = QWidget()
        detail_layout = QVBoxLayout(self.detail)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        detail_layout.setSpacing(int(t["sp_2"]))
        detail_layout.addWidget(card([row(t, "Strategy", self.strategy)]))
        detail_layout.addWidget(self.type_rows)
        detail_layout.addWidget(self.paste_rows)
        detail_layout.addWidget(self.chord_warning)
        detail_layout.addWidget(self.settle_note)
        detail_layout.addWidget(self.notify_note)
        layout.addWidget(self.detail)

        actions = QHBoxLayout()
        self.add_combo = QComboBox()
        self.add_combo.setEditable(True)    # free text is always accepted
        self.add_combo.setAccessibleName("Add app")
        self.add_combo.lineEdit().setPlaceholderText(
            platform.current().capabilities.app_identity_placeholder)
        # Otherwise the combo sizes itself to the longest running process name
        # and drags the whole pane wider than the window.
        self.add_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.add_combo.setMinimumContentsLength(14)
        actions.addWidget(self.add_combo)
        self.add_button = _button("Add app", self._add)
        actions.addWidget(self.add_button)
        self.remove_button = _button("Remove", self._remove, "Danger")
        actions.addWidget(self.remove_button)
        actions.addStretch()
        layout.addLayout(actions)

        footer = QHBoxLayout()
        self.count = label("", "RowHint", wrap=False)
        footer.addWidget(self.count)
        footer.addStretch()
        # Undo expires on the next edit, so there has to be a durable way back
        # from deleting a shipped rule.
        self.restore_button = link("Restore built-in overrides",
                                   self._restore_defaults)
        footer.addWidget(self.restore_button)
        footer.addWidget(link("Open config.json",
                              lambda: open_in_explorer(ctx.config_path)))
        layout.addLayout(footer)

        self.paste_chord.editingFinished.connect(self._commit_knobs)
        for spin in (self.chunk_size, self.chunk_delay, self.settle_delay):
            spin.editingFinished.connect(self._commit_knobs)
        self.restore_clipboard.toggled.connect(lambda _on: self._commit_knobs())

        bind_table_keys(self, self.table, self._remove, self.undo)
        self._populate_add_suggestions()
        self.reload()

    def showEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        # The picker relists on every visit; the win32 suggestions don't —
        # they are append-only into an editable combo, and re-appending on
        # each show would duplicate the list under the user.
        if platform.current().capabilities.app_picker:
            self._populate_add_suggestions()
        super().showEvent(event)

    # ---- the list ----------------------------------------------------------

    @property
    def overrides(self) -> list[AppOverride]:
        """The live list the injector holds. Never rebound, only mutated."""
        return self.ctx.config.app_overrides

    def _sorted(self) -> list[AppOverride]:
        """Alphabetical in the *view*; file order is untouched on disk."""
        return sorted(self.overrides, key=lambda o: o.process.lower())

    def reload(self) -> None:
        rows = self._sorted()
        self.table.setRowCount(len(rows))
        for i, override in enumerate(rows):
            # The display name when the live lookup resolves, the raw identity
            # otherwise (§5.2) — the identity stays the row's key either way,
            # and the tooltip keeps it readable when the name displaced it.
            display = settings.app_display_name(override.process)
            name = QTableWidgetItem(display)
            name.setData(Qt.ItemDataRole.UserRole, override.process)
            if display != override.process:
                name.setToolTip(override.process)
            self.table.setItem(i, 0, name)
            # Strategy is editable *in the row* (§5.5) as well as in the
            # disclosure below, because the row is where you are looking when
            # you decide a rule is wrong.
            strategy = QComboBox()
            for value, caption in settings.STRATEGY_LABELS:
                strategy.addItem(caption, value)
            current = ("clipboard" if override.strategy == "clipboard-no-restore"
                       else override.strategy)
            strategy.setCurrentIndex(max(strategy.findData(current), 0))
            strategy.setAccessibleName(f"Strategy for {display}")
            strategy.activated.connect(
                lambda _i, o=override, c=strategy: self._set_strategy(o, c))
            self.table.setCellWidget(i, 1, strategy)
            # Row height comes from the delegate's size hint, which knows
            # nothing about a cell *widget* — without this the combo is
            # clipped to a text row.
            self.table.setRowHeight(i, strategy.sizeHint().height() + 6)
            chip = QTableWidgetItem("learned" if override.learned else "")
            if override.learned:
                # The only durable explanation of auto-learn anywhere in the
                # app — the toast is transient and fires once.
                chip.setToolTip(LEARNED_TOOLTIP)
            self.table.setItem(i, 2, chip)
        self.count.setText(
            f"{len(rows)} app override{'' if len(rows) == 1 else 's'}")
        self.empty.setVisible(not rows)
        self.detail.setVisible(bool(rows))
        if rows and self.table.currentRow() < 0:
            self.table.setCurrentCell(0, 0)
        self._show_detail()

    def select(self, process: str, flash: bool = False) -> None:
        """Scroll a row into view and select it — used by the clickable
        auto-learn toast, since right after "typing failed in this app" is the
        only moment anyone cares about per-app injection."""
        for i in range(self.table.rowCount()):
            identity = self.table.item(i, 0).data(Qt.ItemDataRole.UserRole)
            if identity.lower() == process.lower():
                self.table.setCurrentCell(i, 0)
                self.table.scrollToItem(self.table.item(i, 0))
                if flash:
                    self._flash(i)
                return

    def _flash(self, row: int) -> None:
        """Say "it is already here" without a dialog: a duplicate add selects
        and flashes the existing row rather than creating a second, dead one."""
        item = self.table.item(row, 0)
        if item is None:
            return
        item.setBackground(QColor(self.ctx.tokens["accent_soft_bg"]))
        a11y.announce(self.table, f"{item.text()} already has an override.")
        # Re-resolved on the way out, and scoped to the pane: any reload
        # between now and then destroys the item, and clearing a dangling
        # QTableWidgetItem is a hard crash rather than a no-op.
        QTimer.singleShot(700, self, lambda: self._unflash(row))

    def _unflash(self, row: int) -> None:
        item = self.table.item(row, 0)
        if item is not None:
            item.setBackground(QBrush())

    def _current(self) -> AppOverride | None:
        item = self.table.item(self.table.currentRow(), 0)
        if item is None:
            return None
        process = item.data(Qt.ItemDataRole.UserRole)
        return next((o for o in self.overrides if o.process == process), None)

    # ---- the detail disclosure ---------------------------------------------

    def _show_detail(self) -> None:
        override = self._current()
        if override is None:
            self.detail.setVisible(False)
            return
        self.detail.setVisible(True)
        self._loading = True
        strategy = ("clipboard" if override.strategy == "clipboard-no-restore"
                    else override.strategy)
        self.strategy.setCurrentIndex(max(self.strategy.findData(strategy), 0))
        self.chunk_size.setValue(override.chunk_size)
        self.chunk_delay.setValue(override.chunk_delay_ms)
        self.paste_chord.setText(override.paste_chord)
        self.settle_delay.setValue(override.settle_delay_ms)
        self.restore_clipboard.setChecked(
            override.restore_clipboard and override.strategy != "clipboard-no-restore")
        self._loading = False

        pastes = strategy == "clipboard"
        types = strategy == "type"
        self.type_rows.setVisible(types)
        self.paste_rows.setVisible(pastes)
        self.notify_note.setVisible(strategy == "notify-only")
        self.settle_note.setVisible(pastes and override.process == "notepad.exe")
        self._check_chord()

    def _check_chord(self) -> None:
        """`parse_paste_chord` **silently drops** unrecognized parts and falls
        back to the plain paste chord, so `cmd+v` yields a working-looking row
        that pastes with the wrong chord. Echo what was parsed, and warn."""
        text = self.paste_chord.text().strip()
        # Gated on the strategy, not on whether the row happens to be on
        # screen: a chord only means anything under Paste it.
        if not text or self.strategy.currentData() != "clipboard":
            self.chord_warning.setVisible(False)
            return
        table = platform.current().capabilities.keycode_table
        unknown = [part.strip() for part in text.lower().split("+")
                   if part.strip() and part.strip() not in table.modifiers
                   and len(part.strip()) != 1]
        if unknown:
            caps = platform.current().capabilities
            parsed = (parse_paste_chord(text, table)
                      or parse_paste_chord(caps.paste_chord, table))
            names = {code: name for name, code in table.modifiers.items()}
            readable = "+".join(names.get(vk, chr(vk).lower()) for vk in parsed)
            self.chord_warning.setText(
                f"Cadent doesn't recognise {', '.join(unknown)} — it will "
                f"send {readable}.")
        self.chord_warning.setVisible(bool(unknown))

    # ---- writes ------------------------------------------------------------

    def _set_strategy(self, override: AppOverride, combo: QComboBox) -> None:
        """The in-row dropdown. Same rule as the disclosure's: only a real
        change rewrites the raw string."""
        chosen = combo.currentData()
        if chosen == override.strategy or (
                chosen == "clipboard"
                and override.strategy == "clipboard-no-restore"):
            return
        override.strategy = chosen
        self._write()
        self.reload()
        self.select(override.process)

    def _commit_strategy(self) -> None:
        """The raw `strategy` string is only rewritten when the dropdown is
        actually changed, so a hand-written `clipboard-no-restore` round-trips
        untouched. Values for the other strategy are preserved on disk, not
        wiped, so a hand-tuned row survives a round trip through the dropdown.
        """
        override = self._current()
        if override is None:
            return
        chosen = self.strategy.currentData()
        # `activated` fires even when the user re-picks what was already
        # selected. Writing then would rewrite a hand-authored
        # `clipboard-no-restore` into plain `clipboard` for nothing — the raw
        # string is only rewritten when the dropdown *actually* changed.
        if chosen == override.strategy or (
                chosen == "clipboard"
                and override.strategy == "clipboard-no-restore"):
            return
        override.strategy = chosen
        self._write()
        self.reload()

    def _commit_knobs(self) -> None:
        override = self._current()
        if override is None or getattr(self, "_loading", False):
            return
        override.chunk_size = self.chunk_size.value()
        override.chunk_delay_ms = self.chunk_delay.value()
        # Empty stores "" — the platform's own chord (Capabilities.paste_chord)
        # rather than a frozen spelling of it.
        override.paste_chord = self.paste_chord.text().strip()
        override.settle_delay_ms = self.settle_delay.value()
        if self.strategy.currentData() == "clipboard":
            # `clipboard-no-restore` and `restore_clipboard: false` are two
            # spellings of one behaviour; the checkbox writes the field and
            # leaves whichever spelling the row already used alone.
            override.restore_clipboard = self.restore_clipboard.isChecked()
        self._check_chord()
        self._write()

    def _write(self) -> None:
        """`Injector` holds the config's list *object* and `resolve_override`
        walks it fresh on every insert, so an edit is live on the next
        dictation with no engine to restart."""
        self.ctx.set("app_overrides")

    # ---- adding and removing -----------------------------------------------

    def _populate_add_suggestions(self) -> None:
        """Apps you've dictated into first, then other running processes.

        This defends the only real failure mode: an executable name that never
        matches is a silent no-op forever. Degrades cleanly to
        running-processes-only when history is off or pruned.

        Where `app_picker` is set (darwin, §5.2), the list is instead the
        running regular-activation-policy apps, rendered
        "Display Name — bundle.id" and storing the id — nobody knows their
        terminal's bundle identifier, and a mistyped one is the same silent
        no-op forever. Free text stays accepted either way.
        """
        if platform.current().capabilities.app_picker:
            # Re-listed every time the pane comes back on screen (showEvent):
            # Settings is long-lived, and a picker frozen at first open would
            # offer "running apps" from hours ago while the app just launched
            # is only addable as free text — the exact mistyped-id trap the
            # picker exists to prevent.
            typed = self.add_combo.currentText()
            self.add_combo.clear()
            for display, identity in platform.current().focused_app.running_apps():
                self.add_combo.addItem(f"{display} — {identity}", identity)
            self.add_combo.setCurrentText(typed)
            return
        suggestions: list[str] = []
        history = self.ctx.history
        if history is not None:
            try:
                for entry in history.search(""):
                    name = entry["app_name"]
                    if name and name not in suggestions:
                        suggestions.append(name)
            except Exception:
                pass
        try:
            import psutil

            running = sorted({p.info["name"] for p in psutil.process_iter(["name"])
                              if p.info.get("name")})
            suggestions += [n for n in running if n not in suggestions]
        except Exception:
            pass
        self.add_combo.addItems(suggestions[:200])
        self.add_combo.setCurrentText("")

    def _add(self) -> None:
        process = self._chosen_identity()
        if not process:
            return
        existing = next((o for o in self.overrides
                         if o.process.lower() == process), None)
        if existing is not None:
            # A duplicate add — including an app a shipped default already
            # covers — selects and flashes the existing row instead of
            # creating a second, dead one.
            self.select(existing.process, flash=True)
            return
        self.overrides.append(AppOverride(process=process, strategy="clipboard"))
        self._write()
        self.reload()
        self.select(process)
        self.add_combo.setCurrentText("")

    def _chosen_identity(self) -> str:
        """What the add affordance means: the picked row's stored identity
        ("Terminal — com.apple.Terminal" stores the id, §5.2), or whatever
        was typed. A picker row is only trusted while its caption is still
        what the field shows — typing over it is free text again."""
        text = self.add_combo.currentText().strip()
        index = self.add_combo.currentIndex()
        if index >= 0 and self.add_combo.itemData(index) \
                and text == self.add_combo.itemText(index).strip():
            return self.add_combo.itemData(index)
        return text.lower()

    def _remove(self) -> None:
        """`✕` with an Undo strip, no confirm dialog. The strip says what will
        happen, which differs by how the row was born."""
        override = self._current()
        if override is None:
            return
        self.overrides.remove(override)
        self._write()
        self.reload()
        self.undo.offer(self._removal_message(override),
                        lambda: self._restore(override))

    def _removal_message(self, override: AppOverride) -> str:
        if override.learned:
            # Deleting is "forget it", and re-learning is correct: an app that
            # failed in March and was fixed by an update deserves "delete and
            # see". No tombstone — that would invent a third state a
            # hand-editor cannot see.
            return (f"Removed {override.process} — Cadent will try typing "
                    "there again, and may re-learn it.")
        reason = platform.current().capabilities.default_override_reasons.get(
            override.process.lower())
        if reason:
            return f"Removed {override.process} — {reason}"
        return f"Removed {override.process}."

    def _restore(self, override: AppOverride) -> None:
        self.overrides.append(override)
        self._write()
        self.reload()
        self.select(override.process)

    def _restore_defaults(self) -> None:
        """Re-add any missing shipped defaults, leaving touched rows alone.

        It matters because most shipped rules **cannot be re-learned**:
        auto-learn fires only on a *detectable* SendInput failure, and
        Notepad's failure is scrambled-yet-successful input that reports fine.
        """
        present = {o.process.lower() for o in self.overrides}
        # Fresh copies: the Capabilities tuple is shared module data and these
        # rows are about to be user-owned and mutable.
        added = [replace(d)
                 for d in platform.current().capabilities.default_overrides
                 if d.process.lower() not in present]
        if not added:
            return
        self.overrides.extend(added)
        self._write()
        self.reload()


def _button(text: str, on_click, object_name: str = ""):
    button = QPushButton(text)
    if object_name:
        button.setObjectName(object_name)
    button.clicked.connect(on_click)
    return button
