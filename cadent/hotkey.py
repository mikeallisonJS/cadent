"""Global push-to-talk hotkey: portable policy over the platform's HotkeyTap.

The tap's callback only feeds the chord state machine and queues actions —
OS-level hooks that block past a timeout get silently removed, so no callback
work happens on the hook thread. A worker thread performs the callbacks.
Events are never suppressed; on Windows the Start menu is masked by injecting
a dummy VK while the chord is held, and the platform filters our own injected
events before they reach the state machine.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable

from .chord import Action, ChordStateMachine, TapChord
from .platform import Platform

log = logging.getLogger(__name__)


class PushToTalk:
    def __init__(self, combo: str, mode: str,
                 on_start: Callable[[], None],
                 on_stop: Callable[[], None],
                 on_discard: Callable[[], None],
                 min_hold_s: float = 0.2,
                 cleanup_combo: str | None = None,
                 on_cleanup_toggle: Callable[[], None] | None = None,
                 platform: Platform | None = None) -> None:
        if platform is None:
            from . import platform as platform_pkg

            platform = platform_pkg.current()
        self._tap = platform.hotkey_tap
        self._keyboard = platform.keyboard
        self._sm = ChordStateMachine(combo, mode, min_hold_s)
        self._cleanup_tap = (TapChord(cleanup_combo)
                             if cleanup_combo and on_cleanup_toggle else None)
        self._handlers = {
            Action.START: on_start,
            Action.STOP: on_stop,
            Action.DISCARD: on_discard,
            Action.MASK_MENU: self._keyboard.send_mask_key,
            Action.CLEANUP_TOGGLE: on_cleanup_toggle or (lambda: None),
        }
        self._queue: queue.Queue[Action | None] = queue.Queue()
        self._worker: threading.Thread | None = None
        self._running = False

    # Runs on the tap's hook thread — must stay fast; no callback work here.
    def _on_key_event(self, keycode: int, is_down: bool, injected: bool) -> None:
        for action in self._sm.on_event(keycode, is_down, injected, time.monotonic()):
            self._queue.put(action)
        if self._cleanup_tap is not None and self._cleanup_tap.on_event(
                keycode, is_down, injected):
            self._queue.put(Action.CLEANUP_TOGGLE)

    def _run_worker(self) -> None:
        while True:
            action = self._queue.get()
            if action is None:
                return
            try:
                self._handlers[action]()
            except Exception:  # a callback crash must never kill the hotkey
                log.exception("hotkey action %s failed", action)

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._worker = threading.Thread(target=self._run_worker, daemon=True)
        self._worker.start()
        self._tap.start(self._on_key_event)

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        self._tap.stop()
        if self._worker is not None:
            self._queue.put(None)
            self._worker = None
