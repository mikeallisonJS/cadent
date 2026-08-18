"""The Settings shell: grouped sidebar, six panes, instant apply (spec §5).

**Closing the window is done.** There is no OK, no Cancel, no draft — so
there is nothing to diff and nothing to lose by closing. Live fields apply
silently and carry no badge; heavy fields apply on change, restart their
engine in-process, and say so before they do it.

**The sidebar is a `QListWidget`** with `Qt.ItemFlag.NoItemFlags` group
headings: one tab stop instead of six, arrow keys with selection following
focus (the Windows Settings model), and each pane announced as a list item
with its position-in-set. A heading reports `selectable=0` while staying
visible and enabled, so it reads as a heading and cannot be landed on.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .. import a11y, platform, settings
from ..config_store import ConfigStore
from .context import PaneContext
from .general import GeneralPane
from .history import HistoryPane
from .hotkeys import HotkeysPane
from .overrides import OverridesPane
from .speech import SpeechPane
from .vocab import VocabularyPane
from .widgets import Notice, open_in_explorer

# Which pane owns each config field, so a divergence or sanitize notice
# appears where the setting lives rather than as one strip over all six (§7.4).
FIELD_PANES: dict[str, str] = {
    "autostart": "general", "input_device": "general", "theme": "general",
    "show_overlay": "general", "overlay_snap": "general",
    "overlay_position_custom": "general", "overlay_anchor_x": "general",
    "overlay_anchor_y": "general", "setup_complete": "general",
    "hotkey": "hotkeys", "hotkey_mode": "hotkeys", "cleanup_hotkey": "hotkeys",
    "min_hold_ms": "hotkeys",
    "stt_engine": "speech", "stt_model": "speech", "stt_device": "speech",
    "cleanup_mode": "speech", "llm_model_path": "speech", "llm_max_tokens": "speech",
    "llm_runtime": "speech",
    "vocab_fuzzy_threshold": "vocabulary",
    "vocab_fuzzy_threshold_short": "vocabulary",
    "app_overrides": "overrides", "injection_method": "overrides",
    "history_enabled": "history", "history_retention_days": "history",
}
DEFAULT_NOTICE_PANE = "general"

# (group heading, [(pane title, factory attribute)])
STRUCTURE = (
    ("Workspace", (("General", "general"), ("Hotkeys", "hotkeys"))),
    ("Text", (("Speech & cleanup", "speech"),
              ("Vocabulary & snippets", "vocabulary"),
              ("App overrides", "overrides"))),
    ("Privacy", (("History", "history"),)),
)

UNREADABLE_CONFIG = ("config.json couldn't be read, so Cadent started with "
                     "default settings. Changes you make now won't survive a "
                     "restart.")

class NavRail(QListWidget):
    """The sidebar, sized by its rows rather than by a constant.

    A plain QListWidget's sizeHint is a fixed default that knows nothing about
    its items, so a minimum width is never the binding constraint — the rail
    simply sat at the default while Windows' Text size setting grew the rows
    past it, and the labels elided. §8.5 asks for layout-driven sizing, and
    this is where it has to happen.

    `sizeHintForColumn(0)` is the right measure because it is what the item
    delegate asks for, QSS `::item` padding included — the same number the
    view uses to lay the rows out in the first place.
    """

    def __init__(self, floor: int) -> None:
        super().__init__()
        self._floor = floor
        self.setMinimumWidth(floor)

    def _content_width(self) -> int:
        margins = self.viewportMargins()
        chrome = margins.left() + margins.right() + 2 * self.frameWidth()
        # The scrollbar comes out of the viewport, so a rail too short for its
        # rows loses that width from the space the labels have — ten pixels is
        # enough to put them back into ellipsis. Reserved whether or not one is
        # showing: the alternative is a rail that changes width when the window
        # is resized past the threshold.
        if self.verticalScrollBarPolicy() != Qt.ScrollBarPolicy.ScrollBarAlwaysOff:
            chrome += self.verticalScrollBar().sizeHint().width()
        return max(self._floor, self.sizeHintForColumn(0) + chrome)

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt naming)
        return QSize(self._content_width(), super().sizeHint().height())

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(self._content_width(), super().minimumSizeHint().height())


class SettingsWindow(QDialog):
    """Six panes under a grouped sidebar. No OK, no Cancel."""

    applied = Signal(str)               # a config field was written
    theme_requested = Signal(str)
    move_overlay_requested = Signal()
    wizard_requested = Signal()
    # The Speech pane's download controls. Re-emitted rather than reached for
    # through `window.speech`, so app.py keeps talking to the window it holds
    # and the pane stays a detail of it.
    model_download_requested = Signal()
    model_download_cancel_requested = Signal()
    # The tray-less footer (M6 §10.2): where no system tray hosts the icon,
    # Pause and Quit live here and the app hears them the same way it hears
    # the tray menu.
    pause_requested = Signal(bool)
    quit_requested = Signal()

    def __init__(self, store: ConfigStore, *, tokens: dict,
                 devices: list[str] | None = None, history=None,
                 high_contrast: bool = False, mic_monitor=None,
                 tray_available: bool = True, paused: bool = False,
                 parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("Root")
        self.setWindowTitle("Cadent — Settings")
        self.resize(940, 680)
        self.store = store
        self._t = tokens

        self.ctx = PaneContext(
            store=store, tokens=tokens, devices=devices or [], history=history,
            mic_monitor=mic_monitor,
            applied=self._on_applied,
            request_theme=self.theme_requested.emit,
            request_move_overlay=self.move_overlay_requested.emit,
            request_wizard=self.wizard_requested.emit)

        self.general = GeneralPane(self.ctx, high_contrast=high_contrast,
                                   tray_available=tray_available)
        self.hotkeys = HotkeysPane(self.ctx)
        self.speech = SpeechPane(self.ctx)
        self.vocabulary = VocabularyPane(self.ctx)
        self.overrides = OverridesPane(self.ctx)
        self.history = HistoryPane(self.ctx)

        self.speech.download_requested.connect(self.model_download_requested)
        self.speech.cancel_requested.connect(self.model_download_cancel_requested)

        # A floor plus row-derived sizing, never a fixed width: Windows' Text
        # size setting scales the type and a fixed 220 clips outright at 150%
        # (§8.5). A minimum alone does not fix it — see NavRail.
        self.sidebar = NavRail(int(tokens["sidebar_min_w"]))
        self.sidebar.setObjectName("Sidebar")
        self.sidebar.setAccessibleName("Settings sections")
        # The nav rail's inset. QSS padding on a QAbstractScrollArea lands on
        # the frame, not the viewport, so this is the only thing that keeps
        # the selected pill from touching the window edge.
        self.sidebar.setViewportMargins(int(tokens["sp_3"]), int(tokens["sp_5"]),
                                        int(tokens["sp_3"]), int(tokens["sp_3"]))
        self.stack = QStackedWidget()
        self._pane_rows: list[int] = []

        for index, (group, panes) in enumerate(STRUCTURE):
            heading = QListWidgetItem(group.upper())
            # NoItemFlags: visible and enabled, reports selectable=0, cannot
            # be landed on. It reads as a heading rather than a dead row.
            heading.setFlags(Qt.ItemFlag.NoItemFlags)
            # Styled on the *item*, not in QSS: a QSS attribute selector
            # matches widgets, and list items are not widgets — a
            # `QListWidget::item[heading="true"]` rule silently does nothing,
            # which is how the headings ended up clipped.
            font = QFont(self.sidebar.font())
            font.setBold(True)
            font.setPointSizeF(float(tokens["fs_caps"]))
            font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing,
                                  float(tokens["tracking_caps"]))
            heading.setFont(font)
            heading.setForeground(QColor(tokens["text_faint"]))
            # A *valid* hint — QSize(0, h) is valid where QSize(-1, h) is not,
            # and an invalid one is silently discarded. Groups after the first
            # carry their own air above them.
            heading.setSizeHint(QSize(
                0, QFontMetrics(font).height() + int(tokens["sp_2"])
                + (int(tokens["sp_4"]) if index else 0)))
            heading.setTextAlignment(Qt.AlignmentFlag.AlignLeft
                                     | Qt.AlignmentFlag.AlignBottom)
            self.sidebar.addItem(heading)
            for title, attribute in panes:
                item = QListWidgetItem(title)
                self.sidebar.addItem(item)
                self._pane_rows.append(self.sidebar.row(item))
                self.stack.addWidget(_scrolled(getattr(self, attribute), title))

        body = QVBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        self.banner = Notice(
            tokens, UNREADABLE_CONFIG,
            [("Open the file", self._open_config),
             ("Back it up and start fresh", self._start_fresh)])
        # A persistent inline error state, not a prompt — mirroring the
        # vocabulary pane rather than inventing a second pattern (§7.2).
        self.banner.setVisible(not store.readable)
        body.addWidget(self.banner)
        # The needs-permission banner (M5 §7, ADR 0012): persistent while the
        # platform's grant is missing, cleared the moment it lands. The words
        # and the action are the platform's (`Capabilities.permission`); the
        # 2 s grant poll lives in the app, which calls `refresh_permission()`
        # — the grant is given outside Cadent where no signal of ours fires.
        plat = platform.current()
        permission = plat.capabilities.permission
        self.permission_banner = Notice(
            tokens, permission.banner if permission else "",
            [(permission.action_label, plat.desktop.request_permission)]
            if permission else [])
        body.addWidget(self.permission_banner)
        self.refresh_permission()
        body.addWidget(self.stack, 1)
        # Pause and Quit, only where no tray hosts them (M6 §10.2). Runtime
        # state passed in by the app — never a Capabilities fact.
        self.footer = QFrame()
        self.footer.setObjectName("Footer")
        footer_layout = QHBoxLayout(self.footer)
        footer_layout.setContentsMargins(int(tokens["sp_4"]), int(tokens["sp_2"]),
                                         int(tokens["sp_4"]), int(tokens["sp_2"]))
        self.pause_button = QPushButton("Pause dictation")
        self.pause_button.setCheckable(True)
        self.pause_button.setChecked(paused)
        self.pause_button.toggled.connect(self.pause_requested.emit)
        self.quit_button = QPushButton("Quit Cadent")
        self.quit_button.clicked.connect(self.quit_requested.emit)
        footer_layout.addWidget(self.pause_button)
        footer_layout.addStretch()
        footer_layout.addWidget(self.quit_button)
        self.footer.setVisible(not tray_available)
        body.addWidget(self.footer)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.sidebar)
        layout.addLayout(body, 1)

        # Connected only once the notices exist: selecting the first row
        # refreshes them.
        self.sidebar.currentRowChanged.connect(self._on_row_changed)
        self.sidebar.setCurrentRow(self._pane_rows[0])
        self.refresh_notices()

    # ---- navigation --------------------------------------------------------

    def _on_row_changed(self, row: int) -> None:
        if row in self._pane_rows:
            self.stack.setCurrentIndex(self._pane_rows.index(row))
            self._sync_microphone()
            self.refresh_notices()

    def _sync_microphone(self) -> None:
        """The General pane's meter listens only while it is what you see.

        Scoped to the pane rather than to the window because Settings is
        long-lived — the microphone is open for as long as you are looking at
        the control it describes, and not a moment longer.
        """
        showing = (self.isVisible()
                   and self.stack.currentWidget().widget() is self.general)
        if showing:
            self.general.start_monitoring()
        else:
            self.general.stop_monitoring()

    def show_pane(self, attribute: str) -> None:
        """Open a named pane — used by the clickable toasts, which lead
        somewhere now instead of just informing (§5.4, §5.5)."""
        index = 0
        for _group, panes in STRUCTURE:
            for _title, name in panes:
                if name == attribute:
                    self.sidebar.setCurrentRow(self._pane_rows[index])
                    return
                index += 1

    # ---- instant apply -----------------------------------------------------

    def _on_applied(self, field: str) -> None:
        """Heavy fields announce their restart on commit; light fields stay
        silent in both channels, mirroring §5.2's own split (§8.4)."""
        announcement = settings.restart_announcement(field)
        if announcement:
            a11y.announce(self.sidebar, announcement)
            self.sidebar.setAccessibleName("Settings sections")
        if field in ("stt_engine", "stt_model"):
            # The Vocabulary pane describes what biasing *does*, which is a
            # property of the engine — so it goes stale the moment the engine
            # changes under an open window (#72). Since #111 the engine is
            # never written on its own: picking a model is what changes it, and
            # `stt_model` is the write that announces.
            self.vocabulary.refresh_order_note()
        self.refresh_notices()
        self.applied.emit(field)

    def refresh_notices(self) -> None:
        """The unreadable-file banner, plus divergence and sanitize lines.

        Each of the latter is a quiet informational line **in the affected
        pane** — no toast, no tray amber for divergence (a pending edit is not
        a fault), and no Reload button, which would drag back everything the
        watcher was rejected for (§7.4).
        """
        self.banner.setVisible(not self.store.readable)
        per_pane: dict[str, list[str]] = {}
        for field, value in self.store.divergence().items():
            per_pane.setdefault(_notice_pane(field), []).append(
                f"config.json was edited outside Cadent. `{field}` there is "
                f"`{value}` and will apply next time you start.")
        for issue in self.store.sanitized:
            per_pane.setdefault(_notice_pane(issue.field), []).append(
                f"config.json sets `{issue.field}` to `{issue.file_value}`, "
                f"which isn't valid. Using `{issue.used}`.")
        for _group, panes in STRUCTURE:
            for _title, name in panes:
                messages = per_pane.get(name, [])
                notice = getattr(self, name).notice
                notice.set_text(" ".join(messages))
                notice.setVisible(bool(messages))

    @property
    def divergence(self):
        """The pane-level notice currently carrying a message, if any."""
        for _group, panes in STRUCTURE:
            for _title, name in panes:
                notice = getattr(self, name).notice
                if notice.message.text():
                    return notice
        return getattr(self, DEFAULT_NOTICE_PANE).notice

    def refresh_permission(self) -> None:
        """Show the banner while `Capabilities.permission`'s grant is missing.
        Called by the app's grant poll; reads the live answer itself so the
        first paint is right before the poll has ticked once."""
        plat = platform.current()
        missing = bool(plat.capabilities.permission) and \
            not plat.focused_app.permission_granted()
        self.permission_banner.setVisible(missing)

    def _open_config(self) -> None:
        open_in_explorer(self.store.path)

    def _start_fresh(self) -> None:
        """The rename-aside is the **only** thing allowed to displace the
        file, and only because the user clicked it."""
        self.store.back_up_and_start_fresh()
        self.refresh_notices()

    # ---- lifecycle ---------------------------------------------------------

    def showEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        super().showEvent(event)
        self._sync_microphone()
        self.refresh_permission()

    def hideEvent(self, event) -> None:  # noqa: N802
        # Hidden is this window's common resting state, not an edge case: the
        # app keeps the instance around and re-shows it from the tray.
        self.general.stop_monitoring()
        super().hideEvent(event)

    def closeEvent(self, event) -> None:  # noqa: N802
        # Flush anything a repeating control left pending; closing is done.
        self.general.stop_monitoring()
        self.store.flush()
        super().closeEvent(event)


def _notice_pane(field: str) -> str:
    return FIELD_PANES.get(field, DEFAULT_NOTICE_PANE)


def _scrolled(pane: QWidget, title: str) -> QScrollArea:
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setWidget(pane)
    # A scroll area is focusable, so it needs a name of its own — otherwise a
    # screen reader lands on an anonymous region between the sidebar and the
    # first control.
    area.setAccessibleName(title)
    return area
