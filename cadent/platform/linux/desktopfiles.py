"""The freedesktop `.desktop` index behind app identity on Linux (ADR 0009).

`AppOverride.process` and History's `app_name` hold the **desktop-file id**
on every tier — the xdg-shell `app_id` on Wayland, and on X11 the id
resolved from `WM_CLASS` here. One key, so overrides survive the X11↔Wayland
flip on the same machine. The same index feeds the overrides pane's
installed-apps picker (`running_apps()`) and `display_name()`, which resolves
a localized `Name=` whether or not the app is running (spec §5.2).

Pure filesystem parsing; no display, no bus. Search order (spec §5.1):
`$XDG_DATA_HOME/applications` (default `~/.local/share/applications`) →
each `$XDG_DATA_DIRS` `applications/` → Flatpak exports; the first file for
an id wins, and the picker deduplicates by id.
"""

from __future__ import annotations

import configparser
import dataclasses
import logging
import os
from collections.abc import Iterable, Mapping
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULT_DATA_DIRS = ("/usr/local/share", "/usr/share")
FLATPAK_EXPORTS = ("/var/lib/flatpak/exports/share",)


@dataclasses.dataclass(frozen=True)
class DesktopEntry:
    id: str                       # "org.mozilla.firefox" (filename stem, dashes for subdirs)
    name: str                     # localized Name=, falling back to the id
    exec_name: str | None         # basename of the Exec= binary
    startup_wm_class: str | None
    hidden: bool                  # NoDisplay=true or Hidden=true or not Type=Application
    path: Path


def application_dirs(env: Mapping[str, str] | None = None) -> list[Path]:
    """The `applications/` directories in XDG search order."""
    env = os.environ if env is None else env
    home = Path(env.get("HOME") or Path.home())
    data_home = env.get("XDG_DATA_HOME") or str(home / ".local" / "share")
    data_dirs = [d for d in env.get("XDG_DATA_DIRS", "").split(":") if d] \
        or list(DEFAULT_DATA_DIRS)
    flatpak_user = str(home / ".local" / "share" / "flatpak" / "exports" / "share")
    ordered: list[Path] = []
    for base in [data_home, *data_dirs, flatpak_user, *FLATPAK_EXPORTS]:
        path = Path(base) / "applications"
        if path not in ordered:
            ordered.append(path)
    return ordered


def _entry_id(root: Path, file: Path) -> str:
    """`applications/kde4/foo.desktop` → `kde4-foo`; the freedesktop rule."""
    relative = file.relative_to(root).with_suffix("")
    return "-".join(relative.parts)


def _localized(section: Mapping[str, str], key: str,
               locales: Iterable[str]) -> str | None:
    for locale in locales:
        value = section.get(f"{key}[{locale}]")
        if value:
            return value
    return section.get(key) or None


def preferred_locales(env: Mapping[str, str] | None = None) -> list[str]:
    """`Name[de_DE]`, `Name[de]` … from `LC_MESSAGES` / `LANG`, most specific
    first; empty for C/POSIX."""
    env = os.environ if env is None else env
    raw = env.get("LC_ALL") or env.get("LC_MESSAGES") or env.get("LANG") or ""
    lang = raw.split(".")[0].split("@")[0]
    if not lang or lang in ("C", "POSIX"):
        return []
    parts = [lang]
    if "_" in lang:
        parts.append(lang.split("_")[0])
    return parts


def parse_entry(file: Path, root: Path,
                locales: Iterable[str] = ()) -> DesktopEntry | None:
    parser = configparser.RawConfigParser(interpolation=None, strict=False)
    parser.optionxform = str  # keys are case-sensitive ("Name[de]")
    try:
        parser.read(file, encoding="utf-8")
    except (configparser.Error, OSError, UnicodeDecodeError):
        log.debug("unreadable desktop entry %s", file, exc_info=True)
        return None
    if not parser.has_section("Desktop Entry"):
        return None
    section = parser["Desktop Entry"]
    entry_id = _entry_id(root, file)
    is_app = section.get("Type", "Application") == "Application"
    hidden = (not is_app
              or section.get("NoDisplay", "false").lower() == "true"
              or section.get("Hidden", "false").lower() == "true")
    exec_line = section.get("Exec", "")
    exec_name = None
    if exec_line:
        first = exec_line.split()[0] if exec_line.split() else ""
        # `env FOO=bar prog` and `flatpak run …` name the launcher, not the
        # app; the desktop id is the identity there anyway.
        exec_name = Path(first).name or None
    return DesktopEntry(
        id=entry_id,
        name=_localized(section, "Name", locales) or entry_id,
        exec_name=exec_name,
        startup_wm_class=section.get("StartupWMClass") or None,
        hidden=hidden,
        path=file,
    )


class DesktopIndex:
    """Every `.desktop` entry visible to this user, first-by-id wins.

    Built once and refreshed on demand (`refresh()`); the overrides pane and
    the identity resolver both read it. Directories are parameterized so
    tests build one from a temp tree."""

    def __init__(self, dirs: Iterable[Path] | None = None,
                 locales: Iterable[str] | None = None) -> None:
        self._dirs = list(dirs) if dirs is not None else application_dirs()
        self._locales = list(locales) if locales is not None else preferred_locales()
        self._entries: dict[str, DesktopEntry] | None = None

    def refresh(self) -> None:
        self._entries = None

    @property
    def entries(self) -> dict[str, DesktopEntry]:
        if self._entries is None:
            self._entries = self._scan()
        return self._entries

    def _scan(self) -> dict[str, DesktopEntry]:
        found: dict[str, DesktopEntry] = {}
        for root in self._dirs:
            if not root.is_dir():
                continue
            try:
                files = sorted(root.rglob("*.desktop"))
            except OSError:
                continue
            for file in files:
                entry = parse_entry(file, root, self._locales)
                if entry is not None and entry.id not in found:
                    found[entry.id] = entry
        return found

    # ---- what the seams ask -------------------------------------------------

    def installed_apps(self) -> list[tuple[str, str]]:
        """`(Name, id)` for every visible application, sorted by name — the
        picker's rows (spec §5.2). Identical on every tier."""
        rows = [(e.name, e.id) for e in self.entries.values() if not e.hidden]
        return sorted(rows, key=lambda pair: (pair[0].lower(), pair[1]))

    def display_name(self, identity: str) -> str | None:
        """The localized `Name=` behind a stored id, case-insensitively, or
        None — a closed app's history row still reads "Firefox"."""
        wanted = identity.lower()
        for entry in self.entries.values():
            if entry.id.lower() == wanted:
                return entry.name
        return None

    def id_for_wm_class(self, wm_class: Iterable[str]) -> str | None:
        """The desktop id an X11 `WM_CLASS` (instance, class) names: a file
        whose stem or `StartupWMClass=` matches either string,
        case-insensitively; first match by search order wins (spec §5.1)."""
        wanted = {c.lower() for c in wm_class if c}
        if not wanted:
            return None
        # Exact stem match first (the common case: "firefox" ↔ firefox.desktop),
        # then StartupWMClass — a stem hit is the more deliberate signal.
        for entry in self.entries.values():
            if entry.id.lower() in wanted:
                return entry.id
        for entry in self.entries.values():
            if entry.startup_wm_class and entry.startup_wm_class.lower() in wanted:
                return entry.id
        # Reverse-DNS ids end in the class ("org.mozilla.firefox" ↔ "firefox").
        for entry in self.entries.values():
            tail = entry.id.lower().rsplit(".", 1)[-1]
            if tail in wanted:
                return entry.id
        return None

    def id_for_exec_name(self, exe: str) -> str | None:
        wanted = exe.lower()
        for entry in self.entries.values():
            if entry.exec_name and entry.exec_name.lower() == wanted:
                return entry.id
        return None
