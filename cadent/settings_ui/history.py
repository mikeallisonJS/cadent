"""PRIVACY ▸ History: retention, next to the transcripts it governs (spec §5.6)."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import settings
from .context import PaneContext
from .widgets import Notice, caps, card, label, page_title, preview, row

_RECENT_ROWS = 25


class HistoryPane(QWidget):
    def __init__(self, ctx: PaneContext) -> None:
        super().__init__()
        self.ctx = ctx
        self.setObjectName("Pane")
        t = ctx.tokens
        layout = QVBoxLayout(self)
        layout.setContentsMargins(int(t["sp_6"]), int(t["sp_5"]),
                                  int(t["sp_6"]), int(t["sp_5"]))
        layout.setSpacing(int(t["sp_2"]))

        # A dropdown of plausible choices rather than a spinbox: the spinbox's
        # auto-repeat was the only continuous writer in the app (§7.3).
        self.retention = QComboBox()
        for days, caption in settings.RETENTION_CHOICES:
            self.retention.addItem(caption, days)
        current = self.retention.findData(ctx.config.history_retention_days)
        if current < 0:
            self.retention.addItem(
                settings.retention_label(ctx.config.history_retention_days),
                ctx.config.history_retention_days)
            current = self.retention.count() - 1
        self.retention.setCurrentIndex(current)
        self.retention.currentIndexChanged.connect(self._on_retention)

        # A quiet line for anything config.json disagrees with us
        # about in this pane's own fields (§7.4, §7.5).
        self.notice = Notice(t, "", [])
        self.notice.setVisible(False)
        layout.addWidget(self.notice)
        layout.addWidget(page_title("History"))
        layout.addWidget(card([
            row(t, "Keep dictations for", self.retention,
                desc="Older transcripts are deleted the next time Cadent "
                     "starts, and now"),
        ]))

        # Putting the list next to the retention control keeps the setting and
        # its subject in one place.
        layout.addSpacing(int(t["sp_3"]))
        layout.addWidget(caps("Recent dictations", t))
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["When", "App", "Text"])
        self.table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setAccessibleName("Recent dictations")
        self.table.verticalHeader().setVisible(False)
        layout.addWidget(self.table, 1)

        self.empty = label("No dictations yet.", "RowHint")
        layout.addWidget(self.empty)

        footer = QHBoxLayout()
        footer.addStretch()
        self.copy_button = QPushButton("Copy")
        self.copy_button.clicked.connect(self._copy)
        footer.addWidget(self.copy_button)
        layout.addLayout(footer)

        self.reload()

    def _on_retention(self) -> None:
        # The app prunes in response to the write (one owner, one prune);
        # the pane just shows the result.
        self.ctx.set("history_retention_days", self.retention.currentData())
        self.reload()

    def reload(self) -> None:
        rows = []
        if self.ctx.history is not None:
            try:
                rows = list(self.ctx.history.search(""))[:_RECENT_ROWS]
            except Exception:
                rows = []
        self._rows = rows
        self.table.setRowCount(len(rows))
        for i, entry in enumerate(rows):
            when = datetime.fromtimestamp(entry["ts"]).strftime("%Y-%m-%d %H:%M")
            text = preview(entry["cleaned_text"] or entry["raw_text"])
            app = settings.app_display_name(entry["app_name"] or "")
            for column, value in enumerate((when, app, text)):
                self.table.setItem(i, column, QTableWidgetItem(value))
        self.empty.setVisible(not rows)
        self.copy_button.setEnabled(bool(rows))

    def _copy(self) -> None:
        index = self.table.currentRow()
        if 0 <= index < len(self._rows):
            entry = self._rows[index]
            QApplication.clipboard().setText(
                entry["cleaned_text"] or entry["raw_text"])
