"""The chord recorder: a button that shows a hotkey and, when clicked,
listens for the next one (#63).

Nobody should have to know that Ctrl+Win is spelled "<ctrl>+<cmd>" — you
click, press the keys you want, and let go. The keys come from the live
hotkey listener (`PaneContext.capture_keys`), not from Qt: Qt's key events
cannot tell a right Ctrl from a left one and never see the Win key cleanly,
while the tap already speaks the keycode tables the chord is stored in. The
tap's events arrive on its own thread; a signal marshals them here.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFocusEvent, QKeyEvent
from PySide6.QtWidgets import QPushButton

from .. import a11y
from ..chord import ChordRecorder, describe_combo

LISTENING = "Press your keys…"


class ChordRecorderButton(QPushButton):
    _key_event = Signal(int, bool)      # keycode, is_down — from the hook thread

    def __init__(self, chord: str, *,
                 capture_keys: Callable[[Callable[[int, bool], None]], Callable[[], None]],
                 table,
                 on_recorded: Callable[[str], None],
                 on_problem: Callable[[str], None],
                 sided: Callable[[], bool] = lambda: False) -> None:
        super().__init__()
        self.setObjectName("ChordRecorder")
        self._chord = chord
        self._capture_keys = capture_keys
        self._table = table
        self._on_recorded = on_recorded
        self._on_problem = on_problem
        self._sided = sided
        self._recorder: ChordRecorder | None = None
        self._release: Callable[[], None] | None = None
        self._show_chord()
        self.clicked.connect(self._toggle)
        self._key_event.connect(self._on_key)

    @property
    def recording(self) -> bool:
        return self._recorder is not None

    @property
    def chord(self) -> str:
        return self._chord

    def set_chord(self, chord: str) -> None:
        self._chord = chord
        if not self.recording:
            self._show_chord()

    # ---- recording ----------------------------------------------------------

    def _toggle(self) -> None:
        self.cancel() if self.recording else self.start()

    def start(self) -> None:
        if self.recording:
            return
        self._recorder = ChordRecorder(self._table, self._sided())
        self._release = self._capture_keys(self._key_event.emit)
        self.setText(LISTENING)
        self.setToolTip("Release to set, Escape to cancel")
        self._mark(True)

    def cancel(self) -> None:
        """Stop listening and keep the chord we had."""
        self._stop()

    def _stop(self) -> None:
        if not self.recording:
            return
        self._recorder = None
        if self._release is not None:
            self._release()
            self._release = None
        self.setToolTip("")
        self._mark(False)
        self._show_chord()

    def _on_key(self, keycode: int, is_down: bool) -> None:
        if self._recorder is None:        # a late event after cancel
            return
        result = self._recorder.on_event(keycode, is_down)
        if result is None:
            return
        self._stop()
        if result.chord is not None:
            self._on_recorded(result.chord)
        elif result.problem is not None:
            self._on_problem(result.problem)

    # ---- widget plumbing ----------------------------------------------------

    def _show_chord(self) -> None:
        self.setText(describe_combo(self._chord))

    def _mark(self, recording: bool) -> None:
        self.setProperty("recording", recording)
        a11y.repolish(self)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 (Qt naming)
        if not self.recording:
            return super().keyPressEvent(event)
        # Every key is the tap's while we listen: Space or Enter here would
        # otherwise click the button and cancel the very recording it started.
        if event.key() == Qt.Key.Key_Escape:
            self.cancel()
        event.accept()

    def keyReleaseEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if not self.recording:
            return super().keyReleaseEvent(event)
        event.accept()

    def focusOutEvent(self, event: QFocusEvent) -> None:  # noqa: N802
        # Clicking elsewhere mid-recording is a cancel, not a chord.
        self.cancel()
        super().focusOutEvent(event)
