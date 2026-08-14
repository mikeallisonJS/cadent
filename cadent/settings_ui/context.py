"""What every pane needs from the app, in one object.

Passing a context rather than a dozen constructor arguments keeps the panes
addable: a new pane declares what it uses, not what the window happens to
have on hand.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .. import config as cfg
from ..config_store import ConfigStore
from ..theme.tokens import tokens


def _noop(*_args) -> None:
    return None


@dataclass
class PaneContext:
    store: ConfigStore
    tokens: dict = field(default_factory=lambda: tokens("dark"))
    devices: list[str] = field(default_factory=list)
    history: object | None = None
    # Read through `cfg` at construction rather than bound as plain defaults
    # (#119). A dataclass default is captured when the class is defined, which
    # makes "the app's data directory" un-redirectable: the test suite could
    # not stop a pane built without explicit paths from reading — and, via the
    # vocabulary pane's watcher, *watching* — the developer's own
    # %LOCALAPPDATA%/Cadent files. Same value in the app, one seam later.
    vocab_path: Path = field(default_factory=lambda: cfg.VOCAB_PATH)
    snippets_path: Path = field(default_factory=lambda: cfg.SNIPPETS_PATH)
    config_path: Path = field(default_factory=lambda: cfg.CONFIG_PATH)
    # Called after a field is written, with the field name. The app looks up
    # settings.engine_for() and restarts exactly that engine, in-process.
    applied: Callable[[str], None] = _noop
    request_theme: Callable[[str], None] = _noop
    request_move_overlay: Callable[[], None] = _noop
    request_wizard: Callable[[], None] = _noop
    # An `audio.LevelMonitor`, or None where nothing should listen. Drives the
    # General pane's microphone meter — the same feedback the wizard gives,
    # for the same question (#105).
    mic_monitor: object | None = None
    # Terms the last dictation's hotwords budget dropped (§5.4). Reads stale
    # until the first dictation of the session, which is the honest state.
    dropped_hotwords: list[str] = field(default_factory=list)

    @property
    def config(self):
        return self.store.config

    def set(self, field_name: str, value: object = None, *,
            coalesce: bool = False) -> None:
        """Write one field and tell the app which engine it touched."""
        self.store.set(field_name, value, coalesce=coalesce)
        self.applied(field_name)
