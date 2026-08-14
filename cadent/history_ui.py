"""History window: search past dictations, copy raw or cleaned text.

M3 ticket 43: per-entry raw/cleaned copy, a detail view for long dictations
(double-click), single-entry delete, and delete-all. Same basic shape as M1.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import a11y, settings
from .history import History

_PREVIEW_CHARS = 120


def _preview(text: str) -> str:
    flat = " ".join(text.split())
    return flat[:_PREVIEW_CHARS] + "…" if len(flat) > _PREVIEW_CHARS else flat


class DetailDialog(QDialog):
    """Full raw and cleaned text of one dictation, each copyable."""

    def __init__(self, row: sqlite3.Row, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        when = datetime.fromtimestamp(row["ts"]).strftime("%Y-%m-%d %H:%M")
        self.setWindowTitle(f"Cadent — Dictation {when}")
        self.resize(600, 400)
        layout = QVBoxLayout(self)
        app = settings.app_display_name(row["app_name"] or "") or "?"
        meta = f"{when} · {app} · {row['mode'] or '?'} mode"
        layout.addWidget(QLabel(meta))
        layout.addLayout(self._text_block("Raw transcript", row["raw_text"]))
        if row["cleaned_text"]:
            layout.addLayout(self._text_block("Inserted text", row["cleaned_text"]))

    def _text_block(self, title: str, text: str) -> QVBoxLayout:
        block = QVBoxLayout()
        head = QHBoxLayout()
        label = QLabel(title)
        head.addWidget(label)
        head.addStretch()
        copy_btn = QPushButton("Copy")
        # Both blocks label their button "Copy", so the visible text alone
        # announces as "Copy button" twice with nothing to tell them apart.
        copy_btn.setAccessibleName(f"Copy {title.lower()}")
        copy_btn.clicked.connect(lambda: QApplication.clipboard().setText(text))
        head.addWidget(copy_btn)
        block.addLayout(head)
        view = QPlainTextEdit(text)
        view.setReadOnly(True)
        a11y.bind_label(label, view)
        block.addWidget(view)
        return block


class HistoryWindow(QWidget):
    def __init__(self, history: History) -> None:
        super().__init__()
        self.history = history
        self._rows: list[sqlite3.Row] = []
        self.setWindowTitle("Cadent — History")
        self.resize(800, 500)

        self.search_box = QLineEdit(placeholderText="Search dictations…")
        # A placeholder is not an accessible name — Qt reports name='' for a
        # QLineEdit that has only placeholderText, and a placeholder vanishes
        # the moment anything is typed (§8.3).
        self.search_box.setAccessibleName("Search dictations")
        self.search_box.textChanged.connect(self.refresh)
        self.copy_raw_btn = QPushButton("Copy raw")
        self.copy_raw_btn.clicked.connect(lambda: self._copy_selected("raw_text"))
        self.copy_cleaned_btn = QPushButton("Copy cleaned")
        self.copy_cleaned_btn.clicked.connect(lambda: self._copy_selected("cleaned_text"))
        self.delete_btn = QPushButton("Delete")
        self.delete_btn.clicked.connect(self._delete_selected)
        self.delete_all_btn = QPushButton("Delete all…")
        self.delete_all_btn.clicked.connect(self._delete_all)

        self.table = QTableWidget(0, 4)
        self.table.setAccessibleName("Dictations")
        self.table.setHorizontalHeaderLabels(["When", "App", "Mode", "Text"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.itemSelectionChanged.connect(self._refresh_buttons)
        self.table.itemDoubleClicked.connect(self._show_detail)

        top = QHBoxLayout()
        top.addWidget(self.search_box)
        for btn in (self.copy_raw_btn, self.copy_cleaned_btn,
                    self.delete_btn, self.delete_all_btn):
            top.addWidget(btn)
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.table)
        self.refresh()

    def refresh(self) -> None:
        self._rows = self.history.search(self.search_box.text())
        self.table.setRowCount(len(self._rows))
        for i, r in enumerate(self._rows):
            when = datetime.fromtimestamp(r["ts"]).strftime("%Y-%m-%d %H:%M")
            text = _preview(r["cleaned_text"] or r["raw_text"])
            app = settings.app_display_name(r["app_name"] or "")
            for col, val in enumerate([when, app, r["mode"] or "", text]):
                self.table.setItem(i, col, QTableWidgetItem(val))
        self._refresh_buttons()

    def _selected_row(self) -> sqlite3.Row | None:
        i = self.table.currentRow()
        return self._rows[i] if 0 <= i < len(self._rows) else None

    def _refresh_buttons(self) -> None:
        row = self._selected_row()
        self.copy_raw_btn.setEnabled(row is not None)
        self.copy_cleaned_btn.setEnabled(row is not None and bool(row["cleaned_text"]))
        self.delete_btn.setEnabled(row is not None)
        self.delete_all_btn.setEnabled(bool(self._rows))

    def _copy_selected(self, column: str) -> None:
        row = self._selected_row()
        if row is not None and row[column]:
            QApplication.clipboard().setText(row[column])

    def _show_detail(self) -> None:
        row = self._selected_row()
        if row is not None:
            DetailDialog(row, self).exec()

    def _delete_selected(self) -> None:
        row = self._selected_row()
        if row is not None:
            self.history.delete(row["id"])
            self.refresh()

    def _delete_all(self) -> None:
        answer = QMessageBox.question(
            self, "Cadent — delete all history",
            "Delete every dictation from history? This cannot be undone.")
        if answer == QMessageBox.StandardButton.Yes:
            self.history.purge()
            self.refresh()
