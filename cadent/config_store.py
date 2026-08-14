"""Owns config.json: per-key delta reads, atomic writes, and the refusal to
destroy a file it cannot read (spec §7).

**Per-key delta read-modify-write, atomic, no watcher, restart-to-apply.**

This is deliberately not `vocabulary.json`'s contract. That file is re-read at
the start of every dictation, which is *why* writing it **is** applying it.
config.json is read once at startup into a live `Config` that subsystems hold
references into — the injector shares the very same `app_overrides` list — so
for config, memory is the running truth and the file is its projection.
Copying vocabulary's contract would mean a QFileSystemWatcher plus mid-session
engine restarts plus reconciling an open settings window.

Instant apply has already reduced every write to a single-field event, so the
delta costs nothing: `self.config.save()` becomes `store.set("paused", True)`.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, fields
from pathlib import Path

from .config import CONFIG_PATH, SEED_COMMENTS, Config, SanitizeIssue, parse

log = logging.getLogger(__name__)

# Trailing delay for auto-repeating controls (§7.3). Long enough to swallow a
# held spinbox arrow, short enough that focus-out is rarely what flushes.
COALESCE_MS = 400


class _QtTimer:
    """The default coalesce timer. Lazily imported so the store's logic can be
    tested — and reasoned about — without an event loop."""

    def __init__(self, interval_ms: int) -> None:
        from PySide6.QtCore import QTimer

        self._timer = QTimer()
        self._timer.setSingleShot(True)
        self._timer.setInterval(interval_ms)
        self.on_timeout = None
        self._timer.timeout.connect(self._fire)

    def _fire(self) -> None:
        if self.on_timeout is not None:
            self.on_timeout()

    def start(self) -> None:
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()


class ConfigStore:
    """The file's owner. `Config` stays a plain dataclass and the running truth."""

    def __init__(self, path: Path = CONFIG_PATH, *, timer=None,
                 coalesce_ms: int = COALESCE_MS) -> None:
        self.path = path
        self._timer = timer if timer is not None else _QtTimer(coalesce_ms)
        self._timer.on_timeout = self.flush
        self._pending: dict[str, object] = {}

        raw, self.readable = self._read()
        self.config, self.sanitized = parse(raw)
        # What we believe the file says. Divergence is measured against this,
        # never against memory: parse() type-corrects in memory and, under
        # delta writes, never writes the correction back — so a
        # memory-vs-file comparison would false-positive forever (§7.4).
        self._baseline = dict(raw)

        if self.readable and not path.exists():
            self._seed()

    # ---- reading -----------------------------------------------------------

    def _read(self) -> tuple[dict, bool]:
        """(raw dict, readable). A missing file is readable and empty; a
        malformed one is *not*, and nothing may write to it."""
        if not self.path.exists():
            return {}, True
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            log.warning("config.json is not valid JSON; running on defaults")
            return {}, False
        if not isinstance(data, dict):
            log.warning("config.json is not a JSON object; running on defaults")
            return {}, False
        return data, True

    def _seed(self) -> None:
        """A brand-new file, documented in its own words (§7.6)."""
        self._write_raw({**SEED_COMMENTS, **self.config.to_raw()})
        self._baseline = self._read()[0]

    # ---- writing -----------------------------------------------------------

    def set(self, field: str, value: object = None, *, coalesce: bool = False) -> None:
        """Apply one field and project it onto the file.

        `app_overrides` is written from the live list without a value, because
        the pane mutates that list **in place** — rebinding it would leave the
        injector holding the old one and turn every edit into a silent no-op
        until restart (§5.5, §7.7).
        """
        known = {f.name for f in fields(Config)}
        if field not in known:
            raise KeyError(f"{field} is not a config field")

        if field != "app_overrides":
            setattr(self.config, field, value)
        # The pane just set this field, so whatever the file said about it is
        # no longer news (§7.5).
        self.sanitized = [i for i in self.sanitized if i.field != field]

        if coalesce:
            self._pending[field] = self._encode(field)
            self._timer.start()
            return
        # A discrete write flushes anything pending first, so a spinbox value
        # can never land on disk after a later click.
        self._timer.stop()
        pending, self._pending = self._pending, {}
        self._merge({**pending, field: self._encode(field)})

    def set_many(self, values: dict[str, object]) -> None:
        """Apply several fields as one atomic write.

        For a gesture that settles more than one field at once — the overlay
        anchor is edge/corner plus two fractions — so "one write per gesture"
        stays literally true rather than three files hitting the disk in a row
        (§4.6, §7.3).
        """
        known = {f.name for f in fields(Config)}
        unknown = set(values) - known
        if unknown:
            raise KeyError(f"{', '.join(sorted(unknown))} are not config fields")
        for field, value in values.items():
            if field != "app_overrides":
                setattr(self.config, field, value)
        self.sanitized = [i for i in self.sanitized if i.field not in values]
        self._timer.stop()
        pending, self._pending = self._pending, {}
        self._merge({**pending,
                     **{f: self._encode(f) for f in values}})

    def flush(self) -> None:
        """Land any coalesced writes — on timeout, focus-out, window close, quit."""
        self._timer.stop()
        if not self._pending:
            return
        pending, self._pending = self._pending, {}
        self._merge(pending)

    def _encode(self, field: str) -> object:
        value = getattr(self.config, field)
        if field == "app_overrides":
            return [asdict(o) for o in value]
        return value

    def _merge(self, delta: dict[str, object]) -> None:
        """Read the file, apply the delta, write it back — atomically.

        An unreadable file is never written to: changes apply to the session
        only. The tray carries that as a fault (§3.4) and settings offers the
        one thing allowed to displace the file, `back_up_and_start_fresh`.
        """
        raw, readable = self._read()
        self.readable = readable
        if not readable:
            log.info("config.json is unreadable; %s applied to this session only",
                     ", ".join(delta))
            return
        raw.update(delta)
        self._write_raw(raw)
        self._baseline.update(delta)

    def _write_raw(self, raw: dict) -> None:
        """Temp file + os.replace. A bare write_text truncates the file if the
        process dies mid-write, and instant apply multiplies the exposure."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(raw, indent=2, ensure_ascii=False) + "\n",
                       encoding="utf-8")
        os.replace(tmp, self.path)

    # ---- the two states the panes have to show -----------------------------

    def divergence(self) -> dict[str, object]:
        """Config fields the file now sets differently from what we last saw.

        Shown as a quiet informational line in the affected pane — *"config.json
        was edited outside Cadent"* — with no toast, no tray amber (a pending
        edit is not a fault) and no Reload button, which would drag back
        everything the watcher was rejected for (§7.4).
        """
        raw, readable = self._read()
        if not readable:
            return {}
        known = {f.name for f in fields(Config)}
        return {k: v for k, v in raw.items()
                if k in known and self._baseline.get(k, _MISSING) != v}

    def back_up_and_start_fresh(self) -> Path:
        """Rename the unreadable file aside and seed a new one.

        The only thing allowed to displace the user's file, and only because
        they clicked it.
        """
        backup = self.path.with_name(self.path.name + ".broken")
        n = 1
        while backup.exists():
            n += 1
            backup = self.path.with_name(f"{self.path.name}.broken.{n}")
        os.replace(self.path, backup)
        self.readable = True
        self.sanitized = []
        self._seed()
        return backup


class _Missing:
    def __repr__(self) -> str:      # pragma: no cover - debugging aid
        return "<missing>"


_MISSING = _Missing()


__all__ = ["COALESCE_MS", "ConfigStore", "SanitizeIssue"]
