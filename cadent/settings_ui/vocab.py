"""TEXT ▸ Vocabulary & snippets (spec §5.4).

**The pane is how people work; the file stays valid, hand-editable and
re-read per dictation.** Everything here follows from that stance:

- Writing the file *is* applying it, so **no `restarts <engine>` badge belongs
  on this pane** — there is no engine to restart.
- Writes fire on field commit, so a half-typed term is never live if a
  dictation starts mid-edit, and blank rows are never written.
- Every write is a read-modify-write against current disk contents applying
  only that row's delta (`jsonstore`), so `_comment` / `_editing` / unknown
  keys / key order round-trip by construction.
- **Nothing is refused or reverted under the user's hands.** Collisions warn
  inline and are written anyway; writing two colliding keys to JSON is
  lossless and the collapse only happens at load.
"""

from __future__ import annotations

from PySide6.QtCore import QFileSystemWatcher, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import jsonstore, settings, snippets, vocabulary
from .context import PaneContext
from .widgets import (
    Notice,
    UndoStrip,
    bind_table_keys,
    caps,
    label,
    link,
    open_in_explorer,
    page_title,
    preview,
)

# The state every new user meets, and load-bearing by design: §5.4 dropped the
# seeded sample entries on purpose because one of them was live enough to
# inject placeholder contact details, and said "the pane's empty state teaches
# by example instead". The examples went and the teaching never arrived, so a
# new user met two blank boxes and an Add term button (#102). Same register as
# App overrides' own empty state — name what is not there, then teach the
# concept in one more clause — so the two panes read as one product.
#
# "Sounds like" is the half nobody guesses: the point is not a spelling variant
# but what the recogniser *mishears*, which is why the example is a word said
# aloud rather than a word typed.
EMPTY_VOCABULARY = ("No terms. Add a word Cadent keeps mishearing — a name, "
                    "an acronym, a product — with what it hears instead, and "
                    "it gets fixed in every transcript.")

EMPTY_SNIPPETS = ("No snippets. Say a short trigger phrase and Cadent types "
                  "the whole thing instead — an address, a sign-off, anything "
                  "you retype often.")

ORDER_NOTE = ("Order is priority: terms higher in the list are hinted to the "
              "speech model first.")
# What the same list means under an engine that can't be hinted at all (#72).
# Leaving ORDER_NOTE up there would be the pane quietly claiming a mechanism
# that isn't running.
NO_HINTING_NOTE = ("This speech engine can't be hinted while it listens, so "
                   "order doesn't matter here — every term is still corrected "
                   "in the transcript.")


class VocabularyPane(QWidget):
    def __init__(self, ctx: PaneContext) -> None:
        super().__init__()
        self.ctx = ctx
        self.setObjectName("Pane")
        t = ctx.tokens
        self._loading = False
        self._editing_row: int | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(int(t["sp_6"]), int(t["sp_5"]),
                                  int(t["sp_6"]), int(t["sp_5"]))
        layout.setSpacing(int(t["sp_2"]))

        # A quiet line for anything config.json disagrees with us about in
        # this pane's own fields (§7.4, §7.5).
        self.notice = Notice(t, "", [])
        self.notice.setVisible(False)
        layout.addWidget(self.notice)

        layout.addWidget(page_title("Vocabulary & snippets"))

        # ---- vocabulary card ----------------------------------------------
        layout.addWidget(caps("Vocabulary", t))
        self.error = Notice(t, "", [("Open file", self._open_vocab),
                                    ("Reload", self.reload)])
        self.error.setVisible(False)
        layout.addWidget(self.error)

        # Narrows the view without touching order — order is the hint
        # budget's priority, so filtering must never rewrite it.
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter terms…")
        self.filter.setAccessibleName("Filter terms")
        self.filter.textChanged.connect(self._apply_filter)
        layout.addWidget(self.filter)

        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Term", "Sounds like"])
        # Header-click sorts the **view** only, so tidying can never silently
        # demote terms out of the hint budget (§5.4). Qt's own sorting mode
        # stays off: switching it on re-sorts immediately, which would mean
        # the pane never showed file order — and file order is the priority
        # the user is here to manage.
        self.table.horizontalHeader().setSortIndicatorShown(True)
        self.table.horizontalHeader().sectionClicked.connect(self._on_sorted)
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setAccessibleName("Vocabulary terms")
        self.table.verticalHeader().setVisible(False)
        self.table.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self.table, 1)

        self.empty = label(EMPTY_VOCABULARY, "RowHint")
        layout.addWidget(self.empty)

        # Rows render in file order, **which is biasing priority** —
        # pack_hotwords() packs in file order and drops the overflow.
        self.order_note = label(self._order_note(), "RowHint")
        layout.addWidget(self.order_note)
        self.duplicate_note = label("", "Warning")
        self.duplicate_note.setVisible(False)
        layout.addWidget(self.duplicate_note)
        self.budget_note = label("", "Warning")
        self.budget_note.setVisible(False)
        layout.addWidget(self.budget_note)

        self.undo = UndoStrip(t)
        layout.addWidget(self.undo)

        actions = QHBoxLayout()
        self.add_term = QPushButton("Add term")
        self.add_term.clicked.connect(self._add_term)
        actions.addWidget(self.add_term)
        self.move_up = QPushButton("Move up")
        self.move_up.clicked.connect(lambda: self._move(-1))
        actions.addWidget(self.move_up)
        self.move_down = QPushButton("Move down")
        self.move_down.clicked.connect(lambda: self._move(1))
        actions.addWidget(self.move_down)
        self.remove_term = QPushButton("Remove")
        self.remove_term.setObjectName("Danger")
        self.remove_term.clicked.connect(self._remove_term)
        actions.addWidget(self.remove_term)
        actions.addStretch()
        layout.addLayout(actions)

        footer = QHBoxLayout()
        self.count = label("", "RowHint", wrap=False)
        footer.addWidget(self.count)
        footer.addStretch()
        footer.addWidget(link("Open vocabulary.json", self._open_vocab))
        layout.addLayout(footer)

        # ---- snippets card: master-detail, because replacements are
        # multi-line and a table cell is the wrong shape for them.
        layout.addSpacing(int(t["sp_3"]))
        layout.addWidget(caps("Snippets", t))
        self.snippet_error = Notice(t, "", [("Open file", self._open_snippets),
                                            ("Reload", self.reload)])
        self.snippet_error.setVisible(False)
        layout.addWidget(self.snippet_error)

        self.triggers = QListWidget()
        self.triggers.setAccessibleName("Snippet triggers")
        self.triggers.currentRowChanged.connect(self._show_snippet)
        layout.addWidget(self.triggers, 1)

        # Under the list rather than under the detail box: the list is what a
        # new user looks at first, and the box below already has a placeholder.
        self.snippet_empty = label(EMPTY_SNIPPETS, "RowHint")
        layout.addWidget(self.snippet_empty)

        self.replacement = QPlainTextEdit()
        self.replacement.setAccessibleName("Cadent types")
        self.replacement.setPlaceholderText(
            "Say a trigger phrase and Cadent types this instead.")
        self.replacement.focusOutEvent = self._wrap_focus_out(
            self.replacement.focusOutEvent)
        layout.addWidget(self.replacement, 1)

        snippet_footer = QHBoxLayout()
        self.add_snippet = QPushButton("Add snippet")
        self.add_snippet.clicked.connect(self._add_snippet)
        snippet_footer.addWidget(self.add_snippet)
        self.remove_snippet = QPushButton("Remove")
        self.remove_snippet.setObjectName("Danger")
        self.remove_snippet.clicked.connect(self._remove_snippet)
        snippet_footer.addWidget(self.remove_snippet)
        snippet_footer.addStretch()
        self.snippet_count = label("", "RowHint", wrap=False)
        snippet_footer.addWidget(self.snippet_count)
        snippet_footer.addWidget(link("Open snippets.json", self._open_snippets))
        layout.addLayout(snippet_footer)

        # A watcher refreshes the tables when the files change and nothing is
        # being typed. (config.json deliberately has no watcher — different
        # apply contract, §7.1.)
        #
        # Parented to the pane (#119): a watcher is a native
        # ReadDirectoryChangesW thread, and an unparented one is owned by
        # Python, so destroying the pane does not stop the watch — it runs on
        # for however long the pane's wrapper takes to go. Parenting makes the
        # watcher's lifetime the pane's, which is what it was always meant to
        # be. This is the ownership half of #119, not its cause: the sixty
        # live watch threads that crash burned came from windows the suite
        # never destroyed at all.
        self.watcher = QFileSystemWatcher(self)
        for path in (ctx.vocab_path, ctx.snippets_path):
            if path.exists():
                self.watcher.addPath(str(path))
        self.watcher.fileChanged.connect(self._on_file_changed)

        bind_table_keys(self, self.table, self._remove_term, self.undo)
        self.reload()

    # ---- loading -----------------------------------------------------------

    def reload(self) -> None:
        self._loading = True
        try:
            self._load_vocabulary()
            self._load_snippets()
        finally:
            self._loading = False
        self._warn_on_duplicates()
        self.refresh_budget_note()

    def _load_vocabulary(self) -> None:
        terms, warning = vocabulary.load(self.ctx.vocab_path)
        unreadable = warning is not None and "not valid JSON" in warning
        self.error.setVisible(unreadable)
        if unreadable:
            self.error.set_text(warning)
        # Editing is disabled while the file is unparseable, so the UI can
        # never overwrite a file it failed to read.
        self.table.setEnabled(not unreadable)
        for button in (self.add_term, self.remove_term, self.move_up,
                       self.move_down):
            button.setEnabled(not unreadable)
        if unreadable:
            self.table.setRowCount(0)
            self.count.setText("")
            # "Nothing here yet" and "this file is broken" are different facts,
            # and the wrong one invites the user to start typing into a pane
            # whose editing has just been disabled.
            self.empty.setVisible(False)
            return

        self.table.setRowCount(len(terms))
        for i, term in enumerate(terms):
            cell = QTableWidgetItem(term.term)
            # The term as it stands on disk, so an edit knows what it is
            # replacing. Without it a rename appends and leaves the old row.
            cell.setData(Qt.ItemDataRole.UserRole, term.term)
            self.table.setItem(i, 0, cell)
            self.table.setItem(i, 1, QTableWidgetItem(", ".join(term.sounds_like)))
        self.count.setText(f"{len(terms)} term{'' if len(terms) == 1 else 's'}")
        self._reflect_row_count()
        self._sorted_view = False
        self.move_up.setEnabled(True)
        self.move_down.setEnabled(True)
        self.order_note.setText(self._order_note())
        self._apply_filter(self.filter.text())

    def _load_snippets(self) -> None:
        table, warning = snippets.load(self.ctx.snippets_path)
        unreadable = warning is not None and "not valid JSON" in warning
        self.snippet_error.setVisible(unreadable)
        if unreadable:
            self.snippet_error.set_text(warning)
        self.triggers.setEnabled(not unreadable)
        self.replacement.setReadOnly(unreadable)
        for button in (self.add_snippet, self.remove_snippet):
            button.setEnabled(not unreadable)
        if unreadable:
            self.triggers.clear()
            self.snippet_count.setText("")
            self.snippet_empty.setVisible(False)
            return

        self._raw_snippets = self._read_raw_snippets()
        self.triggers.clear()
        for trigger, replacement in self._raw_snippets.items():
            item = QListWidgetItem(f"{trigger} — {preview(replacement, 60)}")
            item.setData(Qt.ItemDataRole.UserRole, trigger)
            self.triggers.addItem(item)
        count = len(self._raw_snippets)
        self.snippet_count.setText(f"{count} snippet{'' if count == 1 else 's'}")
        self.snippet_empty.setVisible(not count)
        if count and self.triggers.currentRow() < 0:
            self.triggers.setCurrentRow(0)
        if not count:
            self.replacement.setPlainText("")

    def _read_raw_snippets(self) -> dict[str, str]:
        try:
            raw = jsonstore.read_raw(self.ctx.snippets_path)
        except jsonstore.UnreadableFile:
            return {}
        return {k: v for k, v in raw.items()
                if not k.startswith("_") and isinstance(v, str)}

    # ---- vocabulary edits --------------------------------------------------

    def _apply_filter(self, text: str) -> None:
        """Hide non-matching rows. Nothing is written and nothing moves."""
        needle = text.strip().lower()
        for i in range(self.table.rowCount()):
            term = self.table.item(i, 0)
            aliases = self.table.item(i, 1)
            haystack = ((term.text() if term else "")
                        + " " + (aliases.text() if aliases else "")).lower()
            self.table.setRowHidden(i, bool(needle) and needle not in haystack)

    def _on_sorted(self, column: int) -> None:
        """A sorted view is not a reordering.

        Move up / Move down write file order, so they are disabled while the
        view is sorted — otherwise "move this above that" would mean something
        different from what the user can see. Reload restores file order.
        """
        from PySide6.QtCore import Qt as _Qt

        ascending = not getattr(self, "_sort_ascending", False)
        self._sort_ascending = ascending
        self.table.sortItems(column, _Qt.SortOrder.AscendingOrder if ascending
                             else _Qt.SortOrder.DescendingOrder)
        self._sorted_view = True
        self.move_up.setEnabled(False)
        self.move_down.setEnabled(False)
        self.order_note.setText(
            "Sorted for reading. Order on disk — which is hint priority — is "
            "unchanged; reload the pane to reorder.")

    def _terms_in_view(self) -> list[str]:
        """The terms in *row* order — which is file order unless the user has
        sorted the view, and reordering is disabled while they have."""
        return [self.table.item(i, 0).text().strip()
                for i in range(self.table.rowCount())
                if self.table.item(i, 0)]

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if self._loading:
            return
        row = item.row()
        term_item = self.table.item(row, 0)
        alias_item = self.table.item(row, 1)
        term = (term_item.text().strip() if term_item else "")
        if not term:
            return          # blank rows are never written
        aliases = [a.strip() for a in
                   ((alias_item.text() if alias_item else "").split(","))
                   if a.strip()]
        previous = term_item.data(Qt.ItemDataRole.UserRole)
        jsonstore.upsert_term(self.ctx.vocab_path, term, aliases,
                              replacing=previous)
        term_item.setData(Qt.ItemDataRole.UserRole, term)
        self._warn_on_duplicates()

    def _warn_on_duplicates(self) -> None:
        """Inline, non-blocking, and the edit is written anyway."""
        seen: set[str] = set()
        duplicates: set[str] = set()
        for term in self._terms_in_view():
            key = snippets.normalize(term)
            if key in seen:
                duplicates.add(term)
            else:
                seen.add(key)
        self.duplicate_note.setText(
            f"{', '.join(sorted(duplicates))} normalizes the same as another "
            "term — only one will win." if duplicates else "")
        self.duplicate_note.setVisible(bool(duplicates))

    def _reflect_row_count(self) -> None:
        """Which of the two notes under the table applies.

        Read off the table rather than off the loader's list, because
        `_add_term` puts a row on screen *without* reloading — a blank row is
        never written, so there is no file state to reload from yet. Told from
        both places or "No terms" sits under the row the user is typing into.
        """
        rows = self.table.rowCount()
        self.empty.setVisible(not rows)
        self.order_note.setVisible(bool(rows))

    def _add_term(self) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        self._loading = True
        cell = QTableWidgetItem("")
        cell.setData(Qt.ItemDataRole.UserRole, None)
        self.table.setItem(row, 0, cell)
        self.table.setItem(row, 1, QTableWidgetItem(""))
        self._loading = False
        self._reflect_row_count()
        self.table.editItem(self.table.item(row, 0))

    def _remove_term(self) -> None:
        """`✕` writes immediately with an Undo strip — no confirm dialog on
        the pane's most routine action."""
        row = self.table.currentRow()
        if row < 0 or not self.table.item(row, 0):
            return
        term = self.table.item(row, 0).text().strip()
        alias_item = self.table.item(row, 1)
        aliases = [a.strip() for a in
                   ((alias_item.text() if alias_item else "").split(","))
                   if a.strip()]
        order = self._terms_in_view()
        jsonstore.remove_term(self.ctx.vocab_path, term)
        self.reload()
        self.undo.offer(f"Removed “{term}”.",
                        lambda: self._restore_term(term, aliases, order))

    def _restore_term(self, term: str, aliases: list[str],
                      order: list[str]) -> None:
        """Back to the position it was deleted from. Appending would silently
        demote it, because order is the hint budget's priority."""
        jsonstore.upsert_term(self.ctx.vocab_path, term, aliases)
        jsonstore.reorder_terms(self.ctx.vocab_path, order)
        self.reload()

    def _move(self, delta: int) -> None:
        """The drag handle's keyboard equivalent — and it says what it does,
        because reordering rewrites the hint budget's priority."""
        row = self.table.currentRow()
        order = self._terms_in_view()
        target = row + delta
        if row < 0 or not 0 <= target < len(order):
            return
        order[row], order[target] = order[target], order[row]
        jsonstore.reorder_terms(self.ctx.vocab_path, order)
        self.reload()
        self.table.setCurrentCell(target, 0)

    # ---- snippet edits -----------------------------------------------------

    def _current_trigger(self) -> str | None:
        item = self.triggers.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _show_snippet(self, _row: int) -> None:
        trigger = self._current_trigger()
        self._loading = True
        self.replacement.setPlainText(self._raw_snippets.get(trigger, "")
                                      if trigger else "")
        self._loading = False

    def _wrap_focus_out(self, original):
        def handler(event):
            original(event)
            self._commit_snippet()
        return handler

    def _commit_snippet(self) -> None:
        if self._loading:
            return
        trigger = self._current_trigger()
        if trigger is None:
            return
        text = self.replacement.toPlainText()
        if text == self._raw_snippets.get(trigger):
            return
        jsonstore.upsert_snippet(self.ctx.snippets_path, trigger, text)
        self._raw_snippets[trigger] = text

    def _add_snippet(self) -> None:
        base, trigger, n = "new trigger", "new trigger", 1
        while trigger in self._raw_snippets:
            n += 1
            trigger = f"{base} {n}"
        jsonstore.upsert_snippet(self.ctx.snippets_path, trigger, "")
        self.reload()
        for i in range(self.triggers.count()):
            if self.triggers.item(i).data(Qt.ItemDataRole.UserRole) == trigger:
                self.triggers.setCurrentRow(i)
                break

    def _remove_snippet(self) -> None:
        trigger = self._current_trigger()
        if trigger is None:
            return
        replacement = self._raw_snippets.get(trigger, "")
        jsonstore.remove_snippet(self.ctx.snippets_path, trigger)
        self.reload()
        self.undo.offer(
            f"Removed “{trigger}”.",
            lambda: self._restore_snippet(trigger, replacement))

    def _restore_snippet(self, trigger: str, replacement: str) -> None:
        jsonstore.upsert_snippet(self.ctx.snippets_path, trigger, replacement)
        self.reload()

    # ---- the hotword budget (§5.4) ----------------------------------------

    def _order_note(self) -> str:
        return (ORDER_NOTE if settings.supports_biasing(self.ctx.config.stt_engine)
                else NO_HINTING_NOTE)

    def refresh_order_note(self) -> None:
        """Called when the speech engine changes under an open window: the
        note describes the engine, not the list."""
        self.order_note.setText(self._order_note())

    def refresh_budget_note(self) -> None:
        """Surfaced **only when it actually bit**.

        Exact token counts from the model's own tokenizer, handed over by the
        pipeline — an in-pane estimate would need the tokenizer too. Silent
        for the majority who never approach it, and stale until the first
        dictation of the session, which is the honest state.
        """
        dropped = self.ctx.dropped_hotwords
        if not dropped:
            self.budget_note.setVisible(False)
            return
        self.budget_note.setText(
            f"{len(dropped)} terms past the model's hint budget — still "
            "corrected in the transcript, just not hinted. Move them up to "
            "prioritize.")
        self.budget_note.setVisible(True)

    # ---- files -------------------------------------------------------------

    def _open_vocab(self) -> None:
        open_in_explorer(self.ctx.vocab_path)

    def _open_snippets(self) -> None:
        open_in_explorer(self.ctx.snippets_path)

    def _on_file_changed(self, path: str) -> None:
        if self.table.state() == QTableWidget.State.EditingState:
            return          # never yank the table out from under a typist
        if self.replacement.hasFocus():
            return
        self.reload()
        if path not in self.watcher.files():
            self.watcher.addPath(path)      # editors replace rather than write
