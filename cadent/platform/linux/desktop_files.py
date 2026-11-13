"""Installed apps and app identity from freedesktop `.desktop` files (ADR 0009).

Pure XDG lookup: no X, no bus, identical on every tier. The identity Cadent
stores — `AppOverride.process`, History's `app_name` — is the desktop-file id,
so an override written under X11 still matches after a flip to Wayland.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

FLATPAK_EXPORTS = ("~/.local/share/flatpak/exports/share",
                   "/var/lib/flatpak/exports/share")


@dataclass(frozen=True)
class Entry:
    id: str                 # "org.mozilla.firefox", "kde-org.kde.konsole"
    stem: str              # the filename without ".desktop"
    names: Mapping[str, str]  # "" → Name=, "de" → Name[de]=
    wm_class: str          # StartupWMClass=, or ""
    listed: bool           # Type=Application, neither NoDisplay nor Hidden

    def name(self, locales: tuple[str, ...]) -> str:
        for locale in locales:
            if locale in self.names:
                return self.names[locale]
        return self.names.get("", self.id)


def application_dirs(env: Mapping[str, str]) -> list[Path]:
    """`$XDG_DATA_HOME` first, then each `$XDG_DATA_DIRS`, then the Flatpak
    exports — the order that decides which file wins an id."""
    home = env.get("XDG_DATA_HOME") or "~/.local/share"
    dirs = [home]
    dirs += (env.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(":")
    dirs += FLATPAK_EXPORTS
    seen: list[Path] = []
    for d in dirs:
        if not d:
            continue
        path = Path(d).expanduser() / "applications"
        if path not in seen:
            seen.append(path)
    return seen


def locales_for(env: Mapping[str, str]) -> tuple[str, ...]:
    """The `Name[...]` keys to try, most specific first: `de_AT` → `de`."""
    raw = (env.get("LC_ALL") or env.get("LC_MESSAGES") or env.get("LANG") or "")
    raw = raw.split(".")[0].split("@")[0]
    if not raw or raw in ("C", "POSIX"):
        return ()
    lang = raw.split("_")[0]
    return (raw, lang) if lang != raw else (raw,)


def parse(text: str) -> dict[str, str]:
    """The `[Desktop Entry]` group as a flat dict; other groups (Actions) and
    comments are ignored."""
    fields: dict[str, str] = {}
    in_entry = False
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("["):
            in_entry = line == "[Desktop Entry]"
            continue
        if in_entry and "=" in line:
            key, _, value = line.partition("=")
            fields.setdefault(key.strip(), value.strip())
    return fields


def _entry(app_id: str, stem: str, fields: dict[str, str]) -> Entry:
    names = {"": fields.get("Name", "")}
    for key, value in fields.items():
        if key.startswith("Name[") and key.endswith("]"):
            names[key[5:-1]] = value
    listed = (fields.get("Type") == "Application"
              and fields.get("NoDisplay", "").lower() != "true"
              and fields.get("Hidden", "").lower() != "true")
    return Entry(app_id, stem, names, fields.get("StartupWMClass", ""), listed)


def scan(dirs: list[Path]) -> list[Entry]:
    """Every desktop file, one entry per id, the earliest directory winning —
    a file in `~/.local/share` shadows the system one of the same id."""
    entries: dict[str, Entry] = {}
    for base in dirs:
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.desktop")):
            rel = path.relative_to(base).with_suffix("")
            app_id = "-".join(rel.parts)
            if app_id in entries:
                continue
            try:
                fields = parse(path.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
            entries[app_id] = _entry(app_id, path.stem, fields)
    return list(entries.values())


class DesktopIndex:
    """The scan, cached until a search directory changes — resolving the
    focused app runs on every dictation, and a directory's mtime moves when a
    file is installed or removed in it."""

    def __init__(self, env: Mapping[str, str] | None = None) -> None:
        self._env = os.environ if env is None else env
        self._lock = threading.Lock()
        self._stamp: tuple | None = None
        self._entries: list[Entry] = []

    def _fresh(self) -> list[Entry]:
        dirs = application_dirs(self._env)
        stamp = tuple((str(d), d.stat().st_mtime_ns if d.is_dir() else None)
                      for d in dirs)
        with self._lock:
            if stamp != self._stamp:
                self._entries = scan(dirs)
                self._stamp = stamp
            return self._entries

    def resolve(self, wm_class: tuple[str, ...]) -> str | None:
        """The id of the desktop file a `WM_CLASS` belongs to: its filename
        stem or `StartupWMClass=` equals the class (case-insensitive). Class
        before instance — the class is the stable half of the pair."""
        entries = self._fresh()
        for candidate in (c.lower() for c in reversed(wm_class) if c):
            for entry in entries:
                if candidate in (entry.stem.lower(), entry.wm_class.lower()):
                    return entry.id
        return None

    def installed(self) -> list[tuple[str, str]]:
        locales = locales_for(self._env)
        apps = [(e.name(locales), e.id) for e in self._fresh() if e.listed]
        return sorted(apps, key=lambda a: (a[0].lower(), a[1]))

    def display_name(self, identity: str) -> str | None:
        locales = locales_for(self._env)
        for entry in self._fresh():
            if entry.id == identity:
                return entry.name(locales) or None
        return None
