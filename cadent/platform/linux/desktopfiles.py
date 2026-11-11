"""Installed applications from the XDG data dirs (spec §5.2, ADR 0009).

Pure file lookup — no bus, no compositor — so the picker and the display names
are identical on every tier. Search order, first file per id wins:
`$XDG_DATA_HOME/applications` (default `~/.local/share/applications`), each
`$XDG_DATA_DIRS` `applications/`, then the Flatpak exports.
"""

from __future__ import annotations

import configparser
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

FLATPAK_EXPORTS = (
    "~/.local/share/flatpak/exports/share",
    "/var/lib/flatpak/exports/share",
)
DEFAULT_DATA_DIRS = "/usr/local/share:/usr/share"


@dataclass(frozen=True)
class DesktopEntry:
    id: str
    name: str


def application_dirs(env: Mapping[str, str]) -> list[Path]:
    home = env.get("XDG_DATA_HOME") or "~/.local/share"
    roots = [home]
    roots += (env.get("XDG_DATA_DIRS") or DEFAULT_DATA_DIRS).split(":")
    roots += FLATPAK_EXPORTS
    dirs: list[Path] = []
    for root in roots:
        if not root:
            continue
        path = Path(root).expanduser() / "applications"
        if path not in dirs:
            dirs.append(path)
    return dirs


def _locale_keys(lang: str | None) -> list[str]:
    """`Name[...]` suffixes to try, most specific first (Desktop Entry spec:
    lang_COUNTRY@MODIFIER, lang_COUNTRY, lang@MODIFIER, lang)."""
    if not lang:
        return []
    base, _, modifier = lang.partition("@")
    base = base.split(".")[0]
    language, _, country = base.partition("_")
    keys = []
    if country and modifier:
        keys.append(f"{language}_{country}@{modifier}")
    if country:
        keys.append(f"{language}_{country}")
    if modifier:
        keys.append(f"{language}@{modifier}")
    if language:
        keys.append(language)
    return keys


def current_lang(env: Mapping[str, str]) -> str | None:
    for var in ("LC_ALL", "LC_MESSAGES", "LANGUAGE", "LANG"):
        value = env.get(var)
        if value:
            return value.split(":")[0]
    return None


def parse(path: Path, lang: str | None) -> DesktopEntry | None:
    """The entry a visible application file describes, else None."""
    parser = configparser.RawConfigParser(strict=False, interpolation=None)
    parser.optionxform = str  # keys are case-sensitive: Name vs name[de]
    try:
        parser.read_string(path.read_text(encoding="utf-8", errors="replace"))
        section = parser["Desktop Entry"]
    except (OSError, configparser.Error, KeyError):
        return None
    if section.get("Type") != "Application":
        return None
    if _truthy(section.get("NoDisplay")) or _truthy(section.get("Hidden")):
        return None
    name = next((section[f"Name[{k}]"] for k in _locale_keys(lang)
                 if section.get(f"Name[{k}]")), None) or section.get("Name")
    if not name:
        return None
    return DesktopEntry(id=desktop_id(path), name=name.strip())


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() == "true"


def desktop_id(path: Path) -> str:
    """The desktop-file id: the filename minus `.desktop`. (Nested
    `applications/a/b.desktop` ids are `a-b`; the picker's targets are
    top-level files and the rare nested one resolves the same way here.)"""
    return path.name[:-len(".desktop")]


def installed_apps(env: Mapping[str, str] | None = None) -> list[DesktopEntry]:
    """Every visible application, deduplicated by id with the earliest search
    directory winning (a hidden earlier file shadows a later one), sorted by
    display name."""
    env = os.environ if env is None else env
    lang = current_lang(env)
    seen: set[str] = set()
    entries: list[DesktopEntry] = []
    for directory in application_dirs(env):
        try:
            files = sorted(directory.rglob("*.desktop"))
        except OSError:
            continue
        for path in files:
            rel = path.relative_to(directory)
            ident = "-".join(rel.parts)[:-len(".desktop")]
            if ident in seen:
                continue
            seen.add(ident)
            entry = parse(path, lang)
            if entry:
                entries.append(DesktopEntry(ident, entry.name))
    return sorted(entries, key=lambda e: (e.name.lower(), e.id))


def display_name(identity: str, env: Mapping[str, str] | None = None) -> str | None:
    """The localized `Name=` for an id, case-insensitively, running or not."""
    wanted = identity.lower()
    for entry in installed_apps(env):
        if entry.id.lower() == wanted:
            return entry.name
    return None
